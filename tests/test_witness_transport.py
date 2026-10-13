"""Real ephemeral loopback mTLS; no daemon or credentials survive these tests."""
import copy
import contextlib
import hashlib
import json
import socket
import ssl
import subprocess
import sys
import threading
import time

import pytest

from exo import crypto
from exo.ledger_authority import AuthorityError, canonical
from exo.ledger_authority.journal import AuthorityJournal
from exo.ledger_authority.witness import WitnessJournal, verify_receipt
from exo.ledger_authority import witness_transport as wt


def openssl(*args):
    subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    path = tmp_path_factory.mktemp("witness-tls")
    ca, ca_key = path / "ca.pem", path / "ca.key"
    openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", ca_key)
    openssl("req", "-sha256", "-new", "-x509", "-key", ca_key, "-out", ca, "-days", "1", "-subj", "/CN=throwaway-witness-CA")
    results = {"ca": ca}
    for name, purpose in (("server", "serverAuth"), ("client", "clientAuth"), ("other-client", "clientAuth"), ("other-server", "serverAuth")):
        key, cert, csr, ext = (path / (name + suffix) for suffix in (".key", ".pem", ".csr", ".ext"))
        openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key)
        openssl("req", "-new", "-key", key, "-out", csr, "-subj", "/CN=" + name)
        ext.write_text(f"basicConstraints=CA:FALSE\nextendedKeyUsage={purpose}\nsubjectAltName=DNS:witness.test\n")
        openssl("x509", "-sha256", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key, "-CAcreateserial", "-out", cert, "-days", "1", "-extfile", ext)
        results[name] = {"cert": cert, "key": key, "pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()}
    return results


@pytest.fixture
def state(tmp_path):
    writer, checkpoint_key, witness_key = crypto.keygen(), crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "transport-test", "checkpoint_pub": crypto.pubkey_b64(checkpoint_key),
                "writers": {"agent": {"pub": crypto.pubkey_b64(writer), "author": "agent", "authors": ["agent"]}}}
    authority = AuthorityJournal.provision(tmp_path / "authority.sqlite", identity, {"agent": writer}, checkpoint_key)
    genesis = authority.head()
    witness = WitnessJournal.provision(tmp_path / "witness.sqlite", identity, witness_key, genesis)
    return authority, witness, identity, genesis, writer, checkpoint_key, witness_key


def append(authority, operation="one", text="PRIVATE_EVENT_MUST_NOT_TRAVEL"):
    head = authority.head()
    return authority.append("agent", operation, {"seq": head["seq"], "chain": head["chain"]},
                            {"author": "agent", "kind": "note", "body": {"text": text}, "session": None, "refs": []})["checkpoint"]


def alias_public_key(pub):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    assert len(pub) == 44 and pub.endswith("=")
    last = alphabet.index(pub[-2])
    assert last & 3 == 0
    alias = pub[:-2] + alphabet[last | 1] + "="
    assert alias != pub and crypto.unb64(alias) == crypto.unb64(pub)
    return alias


@pytest.mark.parametrize("source", ["checkpoint", "writer"])
@pytest.mark.parametrize("alias", [False, True])
def test_witness_key_separation_uses_decoded_bytes(state, tmp_path, source, alias):
    identity = copy.deepcopy(state[2])
    key = state[5] if source == "checkpoint" else state[4]
    pub = crypto.pubkey_b64(key)
    candidate = alias_public_key(pub) if alias else pub

    # The client must refuse a pinned witness key with identical signing authority.
    with pytest.raises(AuthorityError, match="independent witness key"):
        wt._configuration(state[2], state[3], candidate)

    # The service must also reject an identity that spells its authority key
    # differently from the canonical public key derived from the witness seed.
    if source == "checkpoint":
        identity["checkpoint_pub"] = candidate
    else:
        identity["writers"]["agent"]["pub"] = candidate
    genesis = AuthorityJournal._checkpoint(identity, state[5], 0, None, crypto.GENESIS,
                                           crypto.GENESIS, crypto.GENESIS, None)
    journal_path = tmp_path / "same-key-witness.sqlite"
    with pytest.raises(AuthorityError, match="independent witness key"):
        WitnessJournal.provision(journal_path, identity, key, genesis)
    assert not journal_path.exists()
    with pytest.raises(AuthorityError, match="independent key"):
        WitnessJournal(state[1].path, identity, key)

    distinct = crypto.pubkey_b64(state[6])
    assert wt._configuration(state[2], state[3], distinct)[0] == state[2]
    assert WitnessJournal(state[1].path, state[2], state[6]).head() == state[1].head()


@contextlib.contextmanager
def running(state, certs, **overrides):
    options = dict(ca=certs["ca"], cert=certs["server"]["cert"], tls_key=certs["server"]["key"],
                   client_cert_sha256=certs["client"]["pin"], timeout=0.5)
    options.update(overrides)
    server = wt.WitnessServer(state[1], state[3], **options)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.close()
        thread.join(2)
        assert not thread.is_alive()


def client(state, certs, server, **overrides):
    options = dict(host="127.0.0.1", port=server.address[1], server_hostname="witness.test", ca=certs["ca"],
                   cert=certs["client"]["cert"], tls_key=certs["client"]["key"],
                   server_cert_sha256=certs["server"]["pin"], timeout=1)
    options.update(overrides)
    return wt.WitnessClient(state[2], state[3], crypto.pubkey_b64(state[6]), **options)


def test_durable_observe_fresh_head_restart_and_private_body_absent(state, certificates):
    checkpoint = append(state[0])
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        initial = c.head()
        receipt = c.observe(checkpoint, initial)
        assert c.head() == receipt == c.observe(checkpoint, initial)
        verify_receipt(state[2], checkpoint, receipt, c.pub, initial)
        assert "PRIVATE_EVENT" not in canonical(receipt).decode()
    reopened = WitnessJournal(state[1].path, state[2], state[6])
    reopened_state = (state[0], reopened, *state[2:])
    with running(reopened_state, certificates) as server:
        assert client(reopened_state, certificates, server).head() == receipt


def test_reply_loss_commits_once_then_identical_retry(state, certificates):
    checkpoint = append(state[0])
    def fault(point):
        if point == "after_commit":
            raise OSError("lost network reply")
    state[1].fault = fault
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        with pytest.raises(AuthorityError):
            c.observe(checkpoint)
        receipt = c.observe(checkpoint)
        assert receipt == c.head() == state[1].lookup(1)


@pytest.mark.parametrize("point,committed", [("before_commit", False), ("after_commit", True)])
def test_abrupt_process_exit_recovers_prefix_then_identical_retry(state, certificates, point, committed):
    checkpoint = append(state[0])
    initial = state[1].head()
    script = '''
import json, os, sys
from nacl.signing import SigningKey
from exo import crypto
from exo.ledger_authority.witness import WitnessJournal
data = json.load(sys.stdin)
def crash(point):
    if point == data["point"]:
        os._exit(73)
journal = WitnessJournal(data["path"], data["identity"],
                         SigningKey(crypto.unb64(data["key"])), fault=crash)
journal.observe(data["checkpoint"])
raise AssertionError("crash hook did not run")
'''
    proc = subprocess.run([sys.executable, "-c", script], input=json.dumps({
        "path": str(state[1].path), "identity": state[2], "key": crypto.b64(bytes(state[6])),
        "checkpoint": checkpoint, "point": point}), text=True, capture_output=True, timeout=10)
    assert proc.returncode == 73, proc.stdout + proc.stderr
    reopened = WitnessJournal(state[1].path, state[2], state[6])
    assert reopened.head()["seq"] == int(committed)
    assert reopened.lookup(0) == initial
    reopened_state = (state[0], reopened, *state[2:])
    with running(reopened_state, certificates) as server:
        c = client(reopened_state, certificates, server)
        assert c.head()["seq"] == int(committed)
        receipt = c.observe(checkpoint, initial)
        assert receipt == c.observe(checkpoint, initial) == c.head()
        verify_receipt(state[2], checkpoint, receipt, c.pub, initial)


def test_gap_fork_and_wrong_previous_receipt(state, certificates, tmp_path):
    one, two = append(state[0]), append(state[0], "two")
    fork = AuthorityJournal.provision(tmp_path / "fork.sqlite", state[2], {"agent": state[4]}, state[5])
    alternate = append(fork, text="a different history")
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        initial = c.head()
        with pytest.raises(AuthorityError):
            c.observe(two)
        receipt = c.observe(one, initial)
        with pytest.raises(AuthorityError):
            c.observe(alternate)
        with pytest.raises(AuthorityError):
            c.observe(two, initial)
        # A bad local previous receipt cannot make the valid durable receipt disappear.
        assert c.observe(two, receipt)["seq"] == 2


@pytest.mark.parametrize("change", ["hostname", "server-pin", "client-pin", "missing-client-cert", "untrusted-ca"])
def test_mutual_tls_requires_exact_pinned_identities(state, certificates, change, tmp_path):
    with running(state, certificates) as server:
        options = {}
        if change == "hostname":
            options["server_hostname"] = "wrong.test"
        elif change == "server-pin":
            options["server_cert_sha256"] = certificates["other-server"]["pin"]
        elif change == "client-pin":
            options.update(cert=certificates["other-client"]["cert"], tls_key=certificates["other-client"]["key"])
        elif change == "untrusted-ca":
            ca, key = tmp_path / "other-ca.pem", tmp_path / "other-ca.key"
            openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key)
            openssl("req", "-sha256", "-new", "-x509", "-key", key, "-out", ca, "-days", "1", "-subj", "/CN=other-CA")
            options["ca"] = ca
        c = client(state, certificates, server, **options)
        if change == "missing-client-cert":
            c.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            c.context.load_verify_locations(str(certificates["ca"]))
        with pytest.raises(AuthorityError):
            c.head()
        assert state[1].head()["seq"] == 0


def test_nonce_replay_wrong_identity_and_wrong_witness_key(state, certificates):
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        nonce = "a" * 64
        envelope = c._request("/v1/head", {"nonce": nonce})
        assert wt.verify_head(state[2], state[3], envelope, c.pub, nonce) == c.head()
        for bad_nonce, bad_pub in (("b" * 64, c.pub), (nonce, crypto.pubkey_b64(crypto.keygen()))):
            with pytest.raises(AuthorityError):
                wt.verify_head(state[2], state[3], envelope, bad_pub, bad_nonce)
        changed = {**state[2], "stream_id": "other-stream"}
        with pytest.raises(AuthorityError):
            wt.verify_head(changed, state[3], envelope, c.pub, nonce)


@pytest.mark.parametrize("nonce", ["", "a" * 63, "A" * 64, "g" * 64, 0, True, [], {}])
def test_query_head_rejects_invalid_nonce_before_network(state, certificates, monkeypatch, nonce):
    endpoint = type("Endpoint", (), {"address": ("127.0.0.1", 1)})()
    c = client(state, certificates, endpoint)
    monkeypatch.setattr(c, "_request", lambda *args: pytest.fail("invalid nonce initiated network"))
    with pytest.raises(AuthorityError, match="fresh nonce"):
        c.query_head(nonce)


def test_public_query_head_envelopes_fresh_defaults_and_legacy_head(state, certificates, monkeypatch):
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        first, second = c.query_head(), c.query_head()
        assert first["nonce"] != second["nonce"]
        assert wt.verify_head(state[2], state[3], first, c.pub, first["nonce"]) == c.head()
        supplied = c.query_head("a" * 64)
        assert supplied["nonce"] == "a" * 64
        assert supplied["receipt"] == second["receipt"] == c.head()
        monkeypatch.setattr(c, "_request", lambda *args: supplied)
        with pytest.raises(AuthorityError, match="replayed"):
            c.query_head("b" * 64)


def test_missing_corrupt_state_and_changed_key_refuse_startup(state, certificates, tmp_path):
    with pytest.raises(AuthorityError):
        WitnessJournal(tmp_path / "absent.sqlite", state[2], state[6])
    assert not (tmp_path / "absent.sqlite").exists()
    with pytest.raises(AuthorityError):
        WitnessJournal(state[1].path, state[2], crypto.keygen())
    import sqlite3
    with sqlite3.connect(state[1].path) as db:
        db.execute("UPDATE witnessed SET receipt='{}' WHERE seq=0")
    with pytest.raises(AuthorityError):
        WitnessJournal(state[1].path, state[2], state[6])
    with pytest.raises(AuthorityError):
        with running(state, certificates):
            pass


def raw_request(c, server, raw):
    with socket.create_connection(server.address, timeout=1) as connection:
        with c.context.wrap_socket(connection, server_hostname=c.hostname) as tls:
            tls.settimeout(1)
            tls.sendall(raw)
            return tls.recv(wt.MAX_BODY + wt.MAX_HEADERS)


@pytest.mark.parametrize("raw", [
    b"GET /v1/head HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
    b"POST /v1/head?query=1 HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
    b"POST /v1/head HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n{}",
    b"POST /v1/head HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}",
    b"POST /v1/head HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 8193\r\n\r\n",
    b"POST /v1/head HTTP/1.1\r\n" + b"X" * 1100 + b"\r\n\r\n",
    b"POST /v1/head HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\nxyz",
    b"POST /v1/observe HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"body\":{}}",
])
def test_bounded_strict_request_parser_rejects_malformed(state, certificates, raw):
    with running(state, certificates) as server:
        reply = raw_request(client(state, certificates, server), server, raw)
        assert b"400 Bad Request" in reply
        assert state[1].head()["seq"] == 0


def test_handshake_and_slow_request_timeout_do_not_block_next_client(state, certificates):
    with running(state, certificates, timeout=0.1) as server:
        c = client(state, certificates, server)
        with socket.create_connection(server.address, timeout=1) as idle:
            started = time.monotonic()
            assert idle.recv(1) == b""
            assert time.monotonic() - started < 1
        with socket.create_connection(server.address, timeout=1) as connection:
            with c.context.wrap_socket(connection, server_hostname=c.hostname) as tls:
                tls.sendall(b"POST /v1/head HTTP/1.1\r\nContent-Type:")
                assert tls.recv(1) == b""
        assert c.head()["seq"] == 0


@contextlib.contextmanager
def malicious_server(certificates, response):
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(1)
    ctx = wt._context(certificates["ca"], certificates["server"]["cert"], certificates["server"]["key"], server=True)
    def run():
        try:
            connection, _ = listener.accept()
            with connection, ctx.wrap_socket(connection, server_side=True) as tls:
                tls.settimeout(1)
                tls.recv(8192)
                if response is None:
                    time.sleep(0.25)
                else:
                    tls.sendall(response)
        except OSError:
            pass
    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield type("Endpoint", (), {"address": listener.getsockname()})()
    finally:
        listener.close()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("response", [
    b"HTTP/1.1 302 Found\r\nContent-Type: application/json\r\nContent-Length: 2\r\nLocation: https://elsewhere/\r\n\r\n{}",
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 99999\r\n\r\n",
    b"HTTP/1.1 200 OK\r\n" + b"x" * 1100 + b"\r\n\r\n",
    None,
])
def test_client_response_bounds_no_redirects_and_deadline(state, certificates, response, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://must-never-be-used.invalid")
    with malicious_server(certificates, response) as server:
        c = client(state, certificates, server, timeout=0.1)
        with pytest.raises(AuthorityError):
            c.head()


def export(path, checkpoints):
    path.write_bytes(b"".join(canonical(cp) + b"\n" for cp in checkpoints))


def test_export_prevalidation_resume_and_witness_ahead_refusal(state, certificates, tmp_path):
    one, two = append(state[0]), append(state[0], "two")
    path = tmp_path / "checkpoints.jsonl"
    export(path, [state[3], one, two])
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        c.observe(one)
        assert c.publish(path)["seq"] == 2
        assert c.publish(path)["seq"] == 2
        export(path, [state[3], one])
        with pytest.raises(AuthorityError, match="ahead"):
            c.publish(path)


@pytest.mark.parametrize("bad", ["gap", "private-event", "empty", "total-limit", "line-limit"])
def test_export_rejects_entire_bad_input_before_network(state, certificates, tmp_path, monkeypatch, bad):
    one, two = append(state[0]), append(state[0], "two")
    path = tmp_path / "bad.jsonl"
    export(path, [state[3], one, two])
    if bad == "gap":
        export(path, [state[3], two])
    elif bad == "private-event":
        export(path, [state[3], one, {"event": {"body": "PRIVATE"}, "checkpoint": two}])
    elif bad == "empty":
        path.write_bytes(b"")
    elif bad == "total-limit":
        monkeypatch.setattr(wt, "MAX_EXPORT_BYTES", len(canonical(state[3])) + 1)
    else:
        path.write_bytes(b"x" * (wt.MAX_BODY + 2))
    c = client(state, certificates, type("Endpoint", (), {"address": ("127.0.0.1", 1)})())
    monkeypatch.setattr(c, "_request", lambda *a: pytest.fail("bad export initiated network operation"))
    with pytest.raises(AuthorityError):
        c.publish(path)


def test_cli_missing_state_never_provisions(state, certificates, tmp_path):
    identity, genesis, key = (tmp_path / name for name in ("identity.json", "genesis.json", "witness.key"))
    identity.write_bytes(canonical(state[2]))
    genesis.write_bytes(canonical(state[3]))
    key.write_text(crypto.b64(bytes(state[6])))
    key.chmod(0o600)
    missing = tmp_path / "not-created.sqlite"
    with pytest.raises(SystemExit) as exc:
        wt.main(["serve", "--identity", str(identity), "--genesis", str(genesis), "--journal", str(missing),
                 "--witness-key", str(key), "--ca", str(certificates["ca"]), "--cert", str(certificates["server"]["cert"]),
                 "--tls-key", str(certificates["server"]["key"]), "--client-cert-sha256", certificates["client"]["pin"], "--port", "9443"])
    assert exc.value.code == 1
    assert not missing.exists()


def test_strict_json_duplicate_fields_and_configuration_limits():
    with pytest.raises(AuthorityError):
        wt._document(b'{"nonce":"a","nonce":"b"}')
    with pytest.raises(AuthorityError):
        wt._document(b'{"bad":NaN}')


def test_replayed_head_over_valid_mtls_is_rejected_by_new_client_nonce(state, certificates, monkeypatch):
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        envelope = c._request("/v1/head", {"nonce": "a" * 64})
        monkeypatch.setattr(wt.crypto, "sign_doc", lambda *args: envelope)
        with pytest.raises(AuthorityError, match="replayed"):
            c.head()


@pytest.mark.parametrize("headers", [
    b"".join(b"X-" + str(i).encode() + b": " + b"x" * 900 + b"\r\n" for i in range(5)),
    b"".join(b"X-" + str(i).encode() + b": x\r\n" for i in range(33)),
])
def test_total_header_and_count_limits(state, certificates, headers):
    raw = (b"POST /v1/head HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\n" + headers + b"\r\n{}")
    with running(state, certificates) as server:
        assert b"400 Bad Request" in raw_request(client(state, certificates, server), server, raw)
        assert state[1].head()["seq"] == 0


def test_missing_established_journal_during_runtime_is_refused(state, certificates):
    with running(state, certificates) as server:
        c = client(state, certificates, server)
        assert c.head()["seq"] == 0
        state[1].path.unlink()
        with pytest.raises(AuthorityError):
            c.head()
        assert not state[1].path.exists()
