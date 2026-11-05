"""Store.verify must not report an empty or inverted range as a clean log."""

from nacl.signing import SigningKey

from exo.bus import wire
from exo.bus.store import Store


def _store_with(tmp_path, count):
    store = Store(tmp_path / "log.sqlite")
    key = SigningKey.generate()
    _, head = store.head()
    for i in range(count):
        env = wire.finish_envelope(wire.make_envelope("alpha", "beta", f"m{i}", head), key)
        _, head = store.append(env)
    return store


def test_verify_inverted_range_is_not_ok(tmp_path):
    store = _store_with(tmp_path, 3)
    result = store.verify(3, 1)
    assert result["ok"] is False
    assert result["checked"] == 0
    assert result["from"] == 3 and result["to"] == 1


def test_verify_beyond_the_head_is_not_ok(tmp_path):
    store = _store_with(tmp_path, 3)
    result = store.verify(4, 9)
    assert result["ok"] is False
    assert result["checked"] == 0


def test_verify_empty_store_default_is_ok_empty(tmp_path):
    store = Store(tmp_path / "log.sqlite")
    result = store.verify()
    assert result["ok"] is True
    assert result["checked"] == 0
    assert result["empty"] is True


def test_verify_explicit_empty_range_on_empty_store_is_not_ok(tmp_path):
    store = Store(tmp_path / "log.sqlite")
    assert store.verify(1, 0)["ok"] is False
    assert store.verify(2)["ok"] is False


def test_verify_clamps_to_past_the_head(tmp_path):
    store = _store_with(tmp_path, 3)
    result = store.verify(1, 999)
    assert result == {"ok": True, "from": 1, "to": 3, "checked": 3, "head": store.head()[1]}


def test_verify_full_range_still_ok(tmp_path):
    store = _store_with(tmp_path, 3)
    result = store.verify()
    assert result["ok"] is True
    assert result["checked"] == 3
