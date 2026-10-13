"""Opt-in, single-stream mTLS witness transport. No provisioning or discovery."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import socket
import ssl
import tempfile
import time
from pathlib import Path

from exo import crypto
from . import AuthorityError, HASH, canonical, digest, validate_identity, verify_checkpoint, verify_checkpoint_successor
from .witness import WitnessConflict, WitnessJournal, _check_receipt, verify_receipt

MAX_BODY = 8192
MAX_HEADERS = 4096
MAX_LINE = 1024
MAX_EXPORT_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 5.0


def _document(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AuthorityError("duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(AuthorityError("nonfinite JSON")))
        canonical(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AuthorityError("invalid JSON document") from exc


def read_document(path: Path):
    with Path(path).open("rb") as source:
        raw = source.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise AuthorityError("configuration document exceeds transport limit")
    return _document(raw)


def _bounded(value):
    raw = canonical(value)
    if len(raw) > MAX_BODY:
        raise AuthorityError("transport document exceeds limit")
    return raw


def _pin(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise AuthorityError("TLS certificate pin must be lowercase SHA-256 hex")
    return value


def _timeout(value):
    if not isinstance(value, (float, int)) or isinstance(value, bool) or not 0.05 <= value <= 60:
        raise AuthorityError("request timeout must be between 0.05 and 60 seconds")
    return float(value)


def _context(ca, cert, key, *, server):
    # A dedicated trust store only: do not load system CAs or environment configuration.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    if not server:
        ctx.check_hostname = True
    ctx.load_verify_locations(cafile=str(ca))
    ctx.load_cert_chain(str(cert), str(key))
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


def _peer(sock, expected):
    if hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest() != expected:
        raise AuthorityError("TLS peer certificate is not pinned")
    if sock.selected_alpn_protocol() not in (None, "http/1.1"):
        raise AuthorityError("unsupported TLS application protocol")


class _Wire:
    """Small strict HTTP subset, with a shared absolute deadline (including handshake)."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        self.buffer = bytearray()

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("witness request deadline exceeded")
        self.sock.settimeout(left)

    def recv(self, count):
        self.remaining()
        raw = self.sock.recv(count)
        if not raw:
            raise AuthorityError("incomplete HTTP message")
        return raw

    def line(self):
        while True:
            end = self.buffer.find(b"\r\n")
            if end >= 0:
                if end + 2 > MAX_LINE:
                    raise AuthorityError("HTTP line exceeds limit")
                raw = bytes(self.buffer[:end])
                del self.buffer[:end + 2]
                return raw
            if len(self.buffer) >= MAX_LINE:
                raise AuthorityError("HTTP line exceeds limit")
            self.buffer.extend(self.recv(min(512, MAX_LINE - len(self.buffer))))

    def message(self):
        first = self.line()
        total = len(first) + 2
        headers = {}
        for _ in range(32):
            line = self.line()
            total += len(line) + 2
            if total > MAX_HEADERS:
                raise AuthorityError("HTTP headers exceed limit")
            if not line:
                break
            if b":" not in line:
                raise AuthorityError("invalid HTTP header")
            name, value = line.split(b":", 1)
            if not re.fullmatch(rb"[A-Za-z0-9-]+", name):
                raise AuthorityError("invalid HTTP header name")
            name = name.lower()
            if name in headers or any(c < 32 or c > 126 for c in value):
                raise AuthorityError("invalid or duplicate HTTP header")
            headers[name] = value.strip()
        else:
            raise AuthorityError("too many HTTP headers")
        if (b"transfer-encoding" in headers or b"expect" in headers or
                headers.get(b"content-type") != b"application/json" or
                not re.fullmatch(rb"[0-9]{1,5}", headers.get(b"content-length", b""))):
            raise AuthorityError("unsupported HTTP framing")
        size = int(headers[b"content-length"])
        if size > MAX_BODY:
            raise AuthorityError("HTTP body exceeds limit")
        if len(self.buffer) > size:
            raise AuthorityError("HTTP pipelining is not supported")
        while len(self.buffer) < size:
            self.buffer.extend(self.recv(size - len(self.buffer)))
        return first, _document(bytes(self.buffer))

    def send(self, first, document, *, host=None):
        raw = _bounded(document)
        header = (first + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(raw)) +
                  "\r\nConnection: close\r\n" + (f"Host: {host}\r\n" if host else "") + "\r\n").encode("ascii")
        self.remaining()
        self.sock.sendall(header + raw)


def verify_head(identity, genesis, envelope, witness_pub, nonce):
    """Validate a response to *this* fresh challenge; cached envelopes are not fresh heads."""
    fields = {"version", "type", "stream_id", "identity_hash", "genesis_hash", "nonce", "receipt", "pub", "sig"}
    _bounded(envelope)
    if (not isinstance(envelope, dict) or set(envelope) != fields or
            type(envelope["version"]) is not int or envelope["version"] != 1 or
            envelope["type"] != "exo-ledger-witness-head" or
            envelope["stream_id"] != identity["stream_id"] or envelope["identity_hash"] != digest(identity) or
            envelope["genesis_hash"] != digest(genesis) or envelope["nonce"] != nonce or
            not isinstance(nonce, str) or not HASH.fullmatch(nonce) or envelope["pub"] != witness_pub or
            not crypto.verify_doc(envelope, witness_pub)):
        raise AuthorityError("untrusted or replayed witness head")
    _check_receipt(identity, envelope["receipt"], witness_pub)
    if envelope["receipt"]["seq"] == 0:
        verify_receipt(identity, genesis, envelope["receipt"], witness_pub)
    return envelope["receipt"]


def _configuration(identity, genesis, witness_pub):
    identity = validate_identity(identity)
    _bounded(identity)
    _bounded(genesis)
    verify_checkpoint(identity, genesis)
    if genesis["seq"] != 0:
        raise AuthorityError("pinned genesis checkpoint required")
    try:
        if not isinstance(witness_pub, str) or len(crypto.unb64(witness_pub)) != 32:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise AuthorityError("invalid pinned witness key") from exc
    if witness_pub == identity["checkpoint_pub"] or any(witness_pub == w["pub"] for w in identity["writers"].values()):
        raise AuthorityError("independent witness key required")
    return identity, json.loads(canonical(genesis))


class WitnessServer:
    """One connection at a time; no workers, request queue growth, or automatic state creation."""
    def __init__(self, journal, genesis, *, ca, cert, tls_key, client_cert_sha256,
                 host="127.0.0.1", port=0, timeout=DEFAULT_TIMEOUT):
        self.journal = journal
        self.identity, self.genesis = _configuration(journal.identity, genesis, journal.pub)
        verify_receipt(self.identity, self.genesis, journal.lookup(0), journal.pub)
        self.timeout, self.client_pin = _timeout(timeout), _pin(client_cert_sha256)
        self.context = _context(ca, cert, tls_key, server=True)
        self.listener = socket.create_server((host, port), backlog=4)
        self.listener.settimeout(0.2)
        self.address = self.listener.getsockname()
        self.closed = False

    def close(self):
        self.closed = True
        self.listener.close()

    def serve_forever(self):
        while not self.closed:
            try:
                self.serve_once()
            except socket.timeout:
                continue
            except OSError:
                if not self.closed:
                    raise

    def serve_once(self):
        connection, _ = self.listener.accept()
        deadline = time.monotonic() + self.timeout
        with connection:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                with self.context.wrap_socket(connection, server_side=True) as tls:
                    _peer(tls, self.client_pin)
                    wire = _Wire(tls, deadline)
                    try:
                        first, body = wire.message()
                        if first == b"POST /v1/observe HTTP/1.1":
                            if not isinstance(body, dict) or set(body) != {"checkpoint"}:
                                raise AuthorityError("checkpoint envelope required")
                            receipt = self.journal.observe(body["checkpoint"])
                            verify_receipt(self.identity, body["checkpoint"], receipt, self.journal.pub)
                            response = {"receipt": receipt}
                        elif first == b"POST /v1/head HTTP/1.1":
                            if (not isinstance(body, dict) or set(body) != {"nonce"} or
                                    not isinstance(body["nonce"], str) or not HASH.fullmatch(body["nonce"])):
                                raise AuthorityError("fresh nonce required")
                            receipt = self.journal.head()
                            _check_receipt(self.identity, receipt, self.journal.pub)
                            response = crypto.sign_doc({
                                "version": 1, "type": "exo-ledger-witness-head",
                                "stream_id": self.identity["stream_id"], "identity_hash": digest(self.identity),
                                "genesis_hash": digest(self.genesis), "nonce": body["nonce"],
                                "receipt": receipt, "pub": self.journal.pub,
                            }, self.journal.key)
                        else:
                            raise AuthorityError("unsupported witness endpoint")
                        wire.send("HTTP/1.1 200 OK", response)
                    except WitnessConflict:
                        wire.send("HTTP/1.1 409 Conflict", {"error": "checkpoint conflicts with witnessed prefix"})
                    except AuthorityError:
                        wire.send("HTTP/1.1 400 Bad Request", {"error": "invalid witness request or unavailable state"})
            except (OSError, AuthorityError, TimeoutError):
                # Lost replies are resolved by identical observe retries; never reseed.
                return


class WitnessClient:
    def __init__(self, identity, genesis, witness_pub, *, host, port, server_hostname,
                 ca, cert, tls_key, server_cert_sha256, timeout=DEFAULT_TIMEOUT):
        self.identity, self.genesis = _configuration(identity, genesis, witness_pub)
        if (not isinstance(host, str) or not host or any(c.isspace() for c in host) or
                any(c in host for c in "/@?#") or not isinstance(server_hostname, str) or
                not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", server_hostname) or
                type(port) is not int or not 1 <= port <= 65535):
            raise AuthorityError("explicit host, port and TLS hostname required")
        # Numeric connect addresses avoid blocking DNS and make endpoint selection explicit.
        import ipaddress
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise AuthorityError("connect host must be a configured numeric IP address") from exc
        self.host, self.port, self.hostname = host, port, server_hostname
        self.pub, self.pin, self.timeout = witness_pub, _pin(server_cert_sha256), _timeout(timeout)
        self.context = _context(ca, cert, tls_key, server=False)

    def _request(self, path, body):
        _bounded(body)
        deadline = time.monotonic() + self.timeout
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as connection:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                with self.context.wrap_socket(connection, server_hostname=self.hostname) as tls:
                    _peer(tls, self.pin)
                    wire = _Wire(tls, deadline)
                    wire.send(f"POST {path} HTTP/1.1", body, host=self.hostname)
                    first, reply = wire.message()
                    if first != b"HTTP/1.1 200 OK":
                        raise AuthorityError("witness rejected request")
                    return reply
        except (OSError, TimeoutError) as exc:
            raise AuthorityError("witness connection failed; observe outcome may be unknown, retry identical checkpoint") from exc

    def observe(self, checkpoint, previous=None):
        verify_checkpoint(self.identity, checkpoint)
        reply = self._request("/v1/observe", {"checkpoint": checkpoint})
        if not isinstance(reply, dict) or set(reply) != {"receipt"}:
            raise AuthorityError("invalid witness receipt envelope")
        verify_receipt(self.identity, checkpoint, reply["receipt"], self.pub, previous)
        return reply["receipt"]

    def head(self):
        nonce = secrets.token_hex(32)
        return verify_head(self.identity, self.genesis, self._request("/v1/head", {"nonce": nonce}), self.pub, nonce)

    def publish(self, path):
        """Validate the whole exported prefix before publishing; spool only bounded checkpoints."""
        with tempfile.TemporaryFile(mode="w+b") as spool:
            previous = None
            count = 0
            total = 0
            with Path(path).open("rb") as source:
                while raw := source.readline(MAX_BODY + 2):
                    total += len(raw)
                    if total > MAX_EXPORT_BYTES:
                        raise AuthorityError("checkpoint export exceeds total byte limit")
                    if len(raw) > MAX_BODY + 1 or not raw.endswith(b"\n"):
                        raise AuthorityError("checkpoint export line exceeds limit or lacks newline")
                    checkpoint = _document(raw)
                    if previous is None:
                        if checkpoint != self.genesis:
                            raise AuthorityError("export must start at pinned genesis")
                    else:
                        verify_checkpoint_successor(self.identity, checkpoint, previous)
                    spool.write(_bounded(checkpoint) + b"\n")
                    previous, count = checkpoint, count + 1
            if not count:
                raise AuthorityError("empty checkpoint export")
            receipt = self.head()
            if receipt["seq"] >= count:
                raise AuthorityError("witness head is ahead of the supplied export")
            spool.seek(0)
            for raw in spool:
                checkpoint = _document(raw)
                if checkpoint["seq"] < receipt["seq"]:
                    continue
                if checkpoint["seq"] == receipt["seq"]:
                    verify_receipt(self.identity, checkpoint, receipt, self.pub)
                else:
                    receipt = self.observe(checkpoint, receipt)
            return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("serve", "head", "publish"):
        p = sub.add_parser(command)
        for name in ("identity", "genesis", "ca", "cert", "tls-key"):
            p.add_argument("--" + name, type=Path, required=True)
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, required=True)
        if command == "serve":
            p.add_argument("--journal", type=Path, required=True)
            p.add_argument("--witness-key", type=Path, required=True)
            p.add_argument("--client-cert-sha256", required=True)
        else:
            p.add_argument("--witness-pub", type=Path, required=True)
            p.add_argument("--server-hostname", required=True)
            p.add_argument("--server-cert-sha256", required=True)
        if command == "publish":
            p.add_argument("--checkpoints", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        identity, genesis = read_document(args.identity), read_document(args.genesis)
        options = dict(ca=args.ca, cert=args.cert, tls_key=args.tls_key, timeout=args.timeout,
                       host=args.host, port=args.port)
        if args.command == "serve":
            journal = WitnessJournal(args.journal, identity, crypto.load_key(args.witness_key))
            server = WitnessServer(journal, genesis, client_cert_sha256=args.client_cert_sha256, **options)
            try:
                server.serve_forever()
            finally:
                server.close()
        else:
            client = WitnessClient(identity, genesis, crypto.load_pub(args.witness_pub),
                                   server_hostname=args.server_hostname,
                                   server_cert_sha256=args.server_cert_sha256, **options)
            result = client.publish(args.checkpoints) if args.command == "publish" else client.head()
            print(canonical(result).decode())
    except KeyboardInterrupt:
        return 130
    except (AuthorityError, OSError, ValueError) as exc:
        parser.exit(1, f"exo-witness: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
