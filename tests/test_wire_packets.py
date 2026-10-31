from exo import crypto
from exo.wire.packets import verify


def _signed(line: str) -> dict:
    kp = crypto.keygen()
    return {"by": "a", "pub": crypto.pubkey_b64(kp), "sig": crypto.sign(line.encode("utf-8"), kp)}


def test_verify_refuses_without_trusted_key():
    # A real signature by the key the envelope carries. Whoever made the packet chose that key, so it proves nothing.
    assert verify("hello", _signed("hello")) is False


def test_verify_roundtrip_with_trusted_key():
    signed = _signed("hello")
    assert verify("hello", signed, trusted_pub=signed["pub"]) is True
    assert verify("hello", signed, trusted_pub=crypto.pubkey_b64(crypto.keygen())) is False
    # Tampered line must fail.
    assert verify("goodbye", signed, trusted_pub=signed["pub"]) is False
