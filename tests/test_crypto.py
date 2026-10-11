import os
import stat

import pytest

from exo import crypto


def test_canonical_is_stable():
    assert crypto.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


def test_sign_verify_roundtrip():
    key = crypto.keygen()
    doc = crypto.sign_doc({"x": 1}, key)
    assert crypto.verify_doc(doc, crypto.pubkey_b64(key))
    assert not crypto.verify_doc({**doc, "x": 2}, crypto.pubkey_b64(key))
    assert not crypto.verify_doc(doc, crypto.pubkey_b64(crypto.keygen()))


def test_save_and_load_key(tmp_path):
    key = crypto.keygen()
    path = crypto.save_key(key, tmp_path / "keys" / "me.key")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert bytes(crypto.load_key(path)) == bytes(key)
    assert crypto.load_pub(path.with_suffix(".pub")) == crypto.pubkey_b64(key)
    with pytest.raises(FileExistsError):
        crypto.save_key(key, path)


def test_load_key_rejects_open_mode(tmp_path):
    path = crypto.save_key(crypto.keygen(), tmp_path / "k.key")
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError):
        crypto.load_key(path)
