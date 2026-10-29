"""Native API contract with fake CoreFoundation/Security; never touch the OS store."""
import ctypes
from types import SimpleNamespace

import pytest

from exo import macos_keychain as native


class Foundation:
    def __init__(self):
        self.objects = {}
        self.released = []

    def new(self, value):
        pointer = len(self.objects) + 100
        self.objects[pointer] = value
        return pointer

    def CFStringCreateWithCString(self, allocator, value, encoding):
        assert allocator is None and encoding == 0x08000100
        return self.new(value.decode())

    def CFDataCreate(self, allocator, value, length):
        return self.new(ctypes.string_at(value, length))

    def CFDictionaryCreateMutable(self, *_):
        return self.new({})

    def CFDictionarySetValue(self, target, key, value):
        self.objects[target][key] = value

    def CFRelease(self, value):
        self.released.append(value)


def frameworks(monkeypatch, *, update=0, add=0):
    cf = Foundation()
    calls = []
    updates = iter(update if isinstance(update, list) else [update])
    def snapshot(ref):
        return {key: cf.objects.get(value, value) for key, value in cf.objects[ref].items()}
    def modify(query, changes):
        calls.append(('update', snapshot(query), snapshot(changes)))
        return next(updates)
    def create(query, result):
        assert result is None
        calls.append(('add', snapshot(query)))
        return add
    sec = SimpleNamespace(SecItemUpdate=modify, SecItemAdd=create)
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.delenv('EXO_NO_KEYCHAIN', raising=False)
    monkeypatch.setattr(native, '_frameworks', lambda: (cf, sec))
    monkeypatch.setattr(native, '_constant', lambda library, name: name)
    monkeypatch.setattr(native, '_callbacks', lambda library, name: name)
    return cf, calls


@pytest.mark.parametrize('size', [16, 4096, 16384, 65536])
def test_native_update_preserves_full_bytes_and_releases_references(monkeypatch, size):
    cf, calls = frameworks(monkeypatch)
    value = ('fake-"\\-☃-' * size)[:size]
    native.set_generic_password('exo', 'fake-account', value)
    assert calls == [('update', {'kSecClass': 'kSecClassGenericPassword', 'kSecAttrService': 'exo',
        'kSecAttrAccount': 'fake-account', 'kSecUseAuthenticationUI': 'kSecUseAuthenticationUIFail'}, {'kSecValueData': value.encode()})]
    assert set(cf.released) == set(cf.objects) and len(cf.released) == len(cf.objects)


def test_missing_item_creates_and_duplicate_creator_race_updates_identity_only(monkeypatch):
    cf, calls = frameworks(monkeypatch, update=[-25300, 0], add=-25299)
    native.set_generic_password('exo', 'fake-account', 'fake-value')
    assert [c[0] for c in calls] == ['update', 'add', 'update']
    assert calls[1][1]['kSecValueData'] == b'fake-value'
    assert 'kSecValueData' not in calls[2][1]
    assert len(cf.released) == len(cf.objects)


@pytest.mark.parametrize('status', [-25293, -25308, -50])
def test_update_refusal_never_deletes_or_echoes_data(monkeypatch, status):
    cf, calls = frameworks(monkeypatch, update=status)
    value = 'fake-secret-do-not-echo'
    with pytest.raises(native.KeychainError) as error:
        native.set_generic_password('exo', 'fake-account', value)
    assert value not in str(error.value) and [c[0] for c in calls] == ['update']
    assert len(cf.released) == len(cf.objects)


def test_framework_failure_is_safe_and_no_plaintext_fallback(monkeypatch):
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.delenv('EXO_NO_KEYCHAIN', raising=False)
    def fail():
        raise RuntimeError('fake-secret-do-not-echo')
    monkeypatch.setattr(native, '_frameworks', fail)
    with pytest.raises(native.KeychainError) as error:
        native.set_generic_password('exo', 'fake-account', 'fake-value')
    assert 'fake-secret' not in str(error.value)


@pytest.mark.parametrize('status', [0, -25300, -25308])
def test_native_get_exact_item_no_auth_ui_and_safe_errors(monkeypatch, status):
    cf, _ = frameworks(monkeypatch)
    _, security = native._frameworks()
    queries, buffers = [], []
    value = 'fake-"\\-☃-' * 2000
    def lookup(query, output):
        queries.append(dict(cf.objects[query]))
        if status == 0:
            ref = cf.new(value.encode())
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ref
        return status
    security.SecItemCopyMatching = lookup
    cf.CFDataGetLength = lambda ref: len(cf.objects[ref])
    def pointer(ref):
        buffer = ctypes.create_string_buffer(cf.objects[ref]); buffers.append(buffer)
        return ctypes.addressof(buffer)
    cf.CFDataGetBytePtr = pointer
    if status == 0:
        assert native.get_generic_password('exo', 'fake-account') == value
    elif status == -25300:
        assert native.get_generic_password('exo', 'fake-account') is None
    else:
        with pytest.raises(native.KeychainError, match='could not read'):
            native.get_generic_password('exo', 'fake-account')
    assert queries[0]['kSecUseAuthenticationUI'] == 'kSecUseAuthenticationUIFail'
    assert queries[0]['kSecMatchLimit'] == 'kSecMatchLimitOne'
    assert len(cf.released) == len(cf.objects)


@pytest.mark.parametrize('status,removed', [(0, True), (-25300, True), (-25308, False)])
def test_native_delete_exact_identity_with_no_auth_ui(monkeypatch, status, removed):
    cf, _ = frameworks(monkeypatch)
    _, security = native._frameworks()
    queries = []
    def delete(query):
        queries.append(dict(cf.objects[query]))
        return status
    security.SecItemDelete = delete
    assert native.delete_generic_password('exo', 'fake-account') is removed
    assert queries[0]['kSecUseAuthenticationUI'] == 'kSecUseAuthenticationUIFail'
    assert 'kSecReturnData' not in queries[0] and 'kSecValueData' not in queries[0]
    assert len(cf.released) == len(cf.objects)
