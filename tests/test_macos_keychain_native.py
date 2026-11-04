"""Opt-in round trip against the real macOS Keychain (CI macOS runners only)."""
from __future__ import annotations

import os
import secrets
import sys
import uuid

import pytest

from exo.macos_keychain import (
    delete_generic_password,
    get_generic_password,
    set_generic_password,
)

pytestmark = pytest.mark.skipif(
    sys.platform != 'darwin' or os.environ.get('EXO_NATIVE_KEYCHAIN_TEST') != '1',
    reason='requires darwin and EXO_NATIVE_KEYCHAIN_TEST=1',
)


@pytest.fixture(autouse=True)
def _enable_real_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root conftest sets EXO_NO_KEYCHAIN=1; this test must call Security.framework."""
    monkeypatch.delenv('EXO_NO_KEYCHAIN', raising=False)


def _synthetic_utf8(nbytes: int) -> str:
    """Return a UTF-8 string whose encoded length is exactly ``nbytes``."""
    if nbytes <= 0:
        return ''
    # ASCII hex: one byte per character in UTF-8.
    raw = secrets.token_hex((nbytes + 1) // 2)
    return raw[:nbytes]


def test_native_keychain_large_generic_password_round_trip() -> None:
    service = f'exo-ci-native-test-{uuid.uuid4()}'
    account = f'native-account-{uuid.uuid4()}'
    first = _synthetic_utf8(16384)
    second = _synthetic_utf8(65536)
    try:
        set_generic_password(service, account, first)
        assert get_generic_password(service, account) == first

        set_generic_password(service, account, second)
        assert get_generic_password(service, account) == second

        assert delete_generic_password(service, account) is True
        assert get_generic_password(service, account) is None
    finally:
        delete_generic_password(service, account)
