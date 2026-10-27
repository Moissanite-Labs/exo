from exo import crypto
from exo.wire.packets import verify


def _signed(line: str) -> dict:
    kp = crypto.keygen()
    return {"by": "a", "pub": crypto.pubkey_b64(kp), "sig": crypto.sign(line.encode("utf-8"), kp)}


def test_verify_refuses_without_trusted_key():
    # The public key embedded in the envelope is attacker-controlled; never trust it.
    assert verify("hello", {"by": "attacker", "pub": "ATTACKERPUB", "sig": "deadbeef"}) is False


def test_verify_roundtrip_with_trusted_key():
    signed = _signed("hello")
    assert verify("hello", signed, trusted_pub=signed["pub"]) is True
    assert verify("hello", signed, trusted_pub="WRONGPUB") is False
    # Omitting the trusted key must fail even when the embedded pub is genuine.
    assert verify("hello", signed) is False
    # Tampered line must fail.
    assert verify("goodbye", signed, trusted_pub=signed["pub"]) is False
