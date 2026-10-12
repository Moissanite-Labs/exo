"""Seat client: post, inbox, thread, verify, tail. Stdlib plus PyNaCl; no web framework needed."""
from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from nacl.signing import SigningKey

from exo import crypto as exo_crypto

from . import wire
from .config import ClientConfig


class BusError(Exception):
    """A relay call failed. `status` is the HTTP status (0 for transport errors)."""

    def __init__(self, message: str, status: int = 0, data=None):
        super().__init__(message)
        self.status, self.data = status, data


def key_path(seat: str, keys_dir) -> Path:
    return Path(keys_dir) / f"{seat}.key"


def keygen(seat: str, keys_dir) -> tuple[Path, str]:
    """Write `<keys_dir>/<seat>.key` (0600) and `<seat>.pub`. Refuses to overwrite. Returns (path, pubkey)."""
    os.makedirs(keys_dir, mode=0o700, exist_ok=True)
    key = exo_crypto.keygen()
    try:
        path = exo_crypto.save_key(key, key_path(seat, keys_dir))
    except FileExistsError:
        raise BusError(f"{key_path(seat, keys_dir)} exists; refusing to overwrite") from None
    return path, wire.pubkey_b64(key)


class Client:
    def __init__(self, url: str | None = None, seat: str | None = None, key: SigningKey | None = None,
                 keys_dir=None, timeout: float = 30):
        cfg = ClientConfig.from_env()
        self.url = (url or cfg.url).rstrip("/")
        self.seat = seat if seat is not None else cfg.seat
        self.keys_dir = Path(keys_dir) if keys_dir else cfg.keys_dir
        self.timeout = timeout
        self._key = key

    @classmethod
    def from_env(cls, **overrides) -> "Client":
        return cls(**overrides)

    @property
    def key(self) -> SigningKey:
        if self._key is None:
            if not self.seat:
                raise BusError("no seat: pass seat= or set EXO_BUS_SEAT")
            path = key_path(self.seat, self.keys_dir)
            if not path.exists():
                raise BusError(f"no key for {self.seat} at {path}: run keygen")
            self._key = wire.load_signing_key(path)
        return self._key

    @property
    def pubkey(self) -> str:
        return wire.pubkey_b64(self.key)

    # --- transport ----------------------------------------------------------------
    def api(self, method: str, path: str, body=None, auth: bool = False):
        """One JSON round trip. Returns (status, parsed body)."""
        headers = {"Content-Type": "application/json", "User-Agent": f"exo-bus-client/1 ({self.seat or 'anon'})"}
        if auth:
            headers.update(wire.request_headers(self.seat, self.key, method, path.split("?")[0]))
        req = urllib.request.Request(self.url + path, method=method, headers=headers,
                                     data=wire.canonical(body) if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"error": raw.decode(errors="replace")}
        except (urllib.error.URLError, OSError) as e:
            raise BusError(f"{method} {path}: {e}") from None

    def must(self, method: str, path: str, body=None, auth: bool = False):
        status, data = self.api(method, path, body, auth)
        if status >= 400:
            raise BusError(f"{method} {path}: {status} {data}", status, data)
        return data

    # --- reads --------------------------------------------------------------------
    def head(self) -> dict:
        return self.must("GET", "/v1/head")

    def registry_doc(self) -> dict:
        return self.must("GET", "/v1/registry")

    def registry(self) -> dict[str, dict]:
        return {s["seat"]: s for s in self.registry_doc()["seats"]}

    def inbox(self, since: int = 0, limit: int = 200) -> list[dict]:
        return self.must("GET", f"/v1/inbox/{quote(self.seat or '')}?since={int(since)}&limit={int(limit)}", auth=True)

    def messages(self, since: int = 0, to: str | None = None, thread: str | None = None, limit: int = 200) -> list[dict]:
        q = {"since": int(since), "limit": int(limit)}
        if to:
            q["to"] = to
        if thread:
            q["thread"] = thread
        return self.must("GET", f"/v1/messages?{urlencode(q)}", auth=True)

    def thread(self, thread_id: str, limit: int = 500) -> list[dict]:
        return self.messages(thread=thread_id, limit=limit)

    def checkpoints(self) -> list[dict]:
        return self.must("GET", "/v1/checkpoints")

    # --- posting ------------------------------------------------------------------
    def post(self, to, body: str, kind: str = "msg", thread: str | None = None, enc_to: str | None = None,
             attach: list | None = None, retries: int = 5) -> dict:
        """Sign and append. Rebuilds on the new head after a 409. Returns {seq, id, head}."""
        to = [to] if isinstance(to, str) else list(to)
        enc = None
        if enc_to:
            pub = self.registry().get(enc_to, {}).get("pubkey")
            if not pub:
                raise BusError(f"unknown seat {enc_to!r}")
            body, enc = wire.encrypt_body(body, self.key, enc_to, pub)
        prev = self.head()["head"]
        for _ in range(retries):
            env = wire.make_envelope(self.seat, to, body, prev, kind, thread, enc, attach)
            status, data = self.api("POST", "/v1/messages", wire.finish_envelope(env, self.key))
            if status == 201:
                return data
            if status != 409:
                raise BusError(f"post refused: {status} {data}", status, data)
            prev = data["head"]  # someone else won the head; rebuild on top of it
        raise BusError("post failed: head kept moving", 409)

    def post_sym2p(self, to, line: str, kind: str = "msg", thread: str | None = None, **kw) -> dict:
        """Post a SYM-2P packet as the structured body `{"fmt":"sym2p","data":line}`."""
        from exo.protocols.sym2p.bus import wrap
        return self.post(to, wrap(line), kind, thread, **kw)

    # --- reading bodies -----------------------------------------------------------
    def signer_of(self, env: dict, reg: dict, relay_pub: str | None = None) -> str | None:
        """The sender seat's registry key when that key signed the envelope, else None. The relay's key is never
        accepted as a seat (D-040): ``relay_pub`` is ignored here and kept only for older callers."""
        pub = reg.get(env["from"], {}).get("pubkey")
        return pub if pub and wire.verify_doc(wire.core(env), pub) else None

    @staticmethod
    def relay_signed(env: dict, relay_pub: str | None) -> bool:
        """True when the relay signed a post it composed for the seat (marked, D-040): the log is intact, but the
        seat itself did not sign it."""
        return bool(relay_pub) and wire.relay_composed(env) and wire.verify_doc(wire.core(env), relay_pub)

    def decrypt(self, env: dict, reg: dict | None = None, relay_pub: str | None = None) -> str:
        """Plaintext of an envelope encrypted to me. The sender's X25519 key is whichever key signed it."""
        reg = reg if reg is not None else self.registry()
        signer = self.signer_of(env, reg) or (relay_pub if self.relay_signed(env, relay_pub) else None)
        if not signer:
            return "<unverifiable encrypted body>"
        return wire.decrypt_body(env["body"], env["enc"], self.key, signer)

    def text(self, env: dict, reg: dict | None = None, relay_pub: str | None = None, render: bool = True) -> str:
        """Human text of an envelope: decrypted if addressed to me, SYM-2P rendered as plain English."""
        body = env["body"]
        if env.get("enc"):
            if env["enc"].get("to") != self.seat:
                return f"<encrypted to {env['enc'].get('to')}>"
            body = self.decrypt(env, reg, relay_pub)
        return render_body(body) if render else body

    # --- verification ---------------------------------------------------------------
    def verify(self, page: int = 500) -> dict:
        """Download the log and recompute every id, signature and chain link."""
        reg, head = self.registry(), self.head()
        prev, seq, n = wire.GENESIS, 0, 0
        while True:
            batch = self.messages(since=seq, limit=page)
            for env in batch:
                c = wire.core(env)
                if env["prev"] != prev or wire.envelope_id(c) != env["id"]:
                    return {"ok": False, "seq": env["seq"], "error": "chain or id mismatch"}
                if not (self.signer_of(env, reg) or self.relay_signed(env, head.get("relay_pubkey"))):
                    return {"ok": False, "seq": env["seq"], "error": "signature does not verify"}
                prev, seq, n = wire.chain_hash(prev, env["id"]), env["seq"], n + 1
            if len(batch) < page:
                break
        ok = prev == head["head"] and seq == head["seq"]
        return {"ok": ok, "checked": n, "head": prev, "relay_head": head["head"]}

    # --- live stream ----------------------------------------------------------------
    def tail(self):
        """Yield envelopes addressed to me as they arrive. Minimal RFC 6455 client, stdlib only."""
        u = urlsplit(self.url)
        path = f"/v1/stream?seat={quote(self.seat or '')}"
        sock = socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)))
        if u.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        hdrs = wire.request_headers(self.seat, self.key, "GET", "/v1/stream")
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nUser-Agent: exo-bus-client/1\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {wire.b64(os.urandom(16))}\r\nSec-WebSocket-Version: 13\r\n"
               + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n")
        sock.sendall(req.encode())
        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise BusError("connection closed during handshake")
                buf += chunk
            status, _, buf = buf.partition(b"\r\n\r\n")
            if b" 101 " not in status.split(b"\r\n")[0]:
                raise BusError(f"websocket refused: {status.decode(errors='replace').splitlines()[0]}")

            def read(n):
                nonlocal buf
                while len(buf) < n:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("stream closed")
                    buf += chunk
                out, buf = buf[:n], buf[n:]
                return out

            def send(opcode, payload=b""):
                mask = os.urandom(4)
                sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
                             + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

            while True:
                b0, b1 = read(2)
                opcode, length = b0 & 0x0F, b1 & 0x7F
                if length == 126:
                    length = int.from_bytes(read(2), "big")
                elif length == 127:
                    length = int.from_bytes(read(8), "big")
                payload = read(length)
                if opcode == 0x9:
                    send(0xA, payload)
                elif opcode == 0x8:
                    return
                elif opcode == 0x1:
                    yield json.loads(payload)
        finally:
            sock.close()


def render_body(body: str) -> str:
    """Plain English for a structured body (SYM-2P today); any other body comes back unchanged."""
    if not body.startswith("{"):
        return body
    try:
        from exo.protocols.sym2p.bus import render_body as sym2p_render
    except ImportError:
        return body
    try:
        return sym2p_render(body)
    except Exception:  # a malformed packet must never hide the raw text from a person
        return body


def format_line(env: dict, text: str) -> str:
    thread = f" thread={env['thread']}" if env.get("thread") else ""
    return f"#{env.get('seq', '?')} {env['ts'][:19]}Z {env['from']} -> {','.join(env['to'])} [{env['kind']}]{thread}\n  {text}"
