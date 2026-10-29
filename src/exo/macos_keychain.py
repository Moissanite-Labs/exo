"""Write large generic passwords through Apple's Security framework, without CLI arguments.

The security tool's interactive parser has a 4096-byte line buffer. OAuth bundles
must not pass through that parser. SecItemUpdate/Add take explicit byte lengths.
"""
from __future__ import annotations

import ctypes
import os
import sys


class KeychainError(Exception):
    """Safe fixed message; never include data or native exception details."""


def _frameworks():
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    pointer = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = pointer
    cf.CFDataCreate.argtypes = [pointer, pointer, ctypes.c_long]
    cf.CFDataCreate.restype = pointer
    cf.CFDictionaryCreateMutable.argtypes = [pointer, ctypes.c_long, pointer, pointer]
    cf.CFDictionaryCreateMutable.restype = pointer
    cf.CFDictionarySetValue.argtypes = [pointer, pointer, pointer]
    cf.CFDictionarySetValue.restype = None
    cf.CFRelease.argtypes = [pointer]
    cf.CFRelease.restype = None
    security.SecItemUpdate.argtypes = [pointer, pointer]
    security.SecItemUpdate.restype = ctypes.c_int32
    security.SecItemAdd.argtypes = [pointer, pointer]
    security.SecItemAdd.restype = ctypes.c_int32
    security.SecItemCopyMatching.argtypes = [pointer, ctypes.POINTER(pointer)]
    security.SecItemCopyMatching.restype = ctypes.c_int32
    security.SecItemDelete.argtypes = [pointer]
    security.SecItemDelete.restype = ctypes.c_int32
    cf.CFDataGetLength.argtypes = [pointer]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetBytePtr.argtypes = [pointer]
    cf.CFDataGetBytePtr.restype = pointer
    return cf, security


def _constant(library, name):
    return ctypes.c_void_p.in_dll(library, name).value


def _callbacks(library, name):
    return ctypes.addressof(ctypes.c_byte.in_dll(library, name))


def set_generic_password(service: str, account: str, value: str) -> None:
    """Replace one item atomically, or create it. No password travels through argv or a file.

    Security-framework success is required; unavailable/locked/refused storage
    raises a fixed error. The existing item is not deleted before an update.
    """
    if sys.platform != 'darwin' or os.environ.get('EXO_NO_KEYCHAIN'):
        raise KeychainError('The macOS Keychain is unavailable.')
    owned = []
    try:
        cf, security = _frameworks()

        def retain_created(ref):
            if not ref:
                raise KeychainError('The macOS Keychain could not prepare the credential.')
            owned.append(ref)
            return ref

        def text(value):
            return retain_created(cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100))

        def dictionary():
            return retain_created(cf.CFDictionaryCreateMutable(None, 0,
                _callbacks(cf, 'kCFTypeDictionaryKeyCallBacks'), _callbacks(cf, 'kCFTypeDictionaryValueCallBacks')))

        query = dictionary()
        cf.CFDictionarySetValue(query, _constant(security, 'kSecClass'), _constant(security, 'kSecClassGenericPassword'))
        cf.CFDictionarySetValue(query, _constant(security, 'kSecUseAuthenticationUI'), _constant(security, 'kSecUseAuthenticationUIFail'))
        cf.CFDictionarySetValue(query, _constant(security, 'kSecAttrService'), text(service))
        cf.CFDictionarySetValue(query, _constant(security, 'kSecAttrAccount'), text(account))
        data = value.encode('utf-8')
        buffer = ctypes.create_string_buffer(data)
        password = retain_created(cf.CFDataCreate(None, ctypes.cast(buffer, ctypes.c_void_p), len(data)))
        changes = dictionary()
        cf.CFDictionarySetValue(changes, _constant(security, 'kSecValueData'), password)
        result = security.SecItemUpdate(query, changes)
        if result == -25300:  # errSecItemNotFound: create, retaining service/account identity
            cf.CFDictionarySetValue(query, _constant(security, 'kSecValueData'), password)
            result = security.SecItemAdd(query, None)
            if result == -25299:  # errSecDuplicateItem: another process created it; update that exact item
                # Rebuild the identity-only query; kSecValueData must not constrain matching.
                identity = dictionary()
                cf.CFDictionarySetValue(identity, _constant(security, 'kSecClass'), _constant(security, 'kSecClassGenericPassword'))
                cf.CFDictionarySetValue(identity, _constant(security, 'kSecUseAuthenticationUI'), _constant(security, 'kSecUseAuthenticationUIFail'))
                cf.CFDictionarySetValue(identity, _constant(security, 'kSecAttrService'), text(service))
                cf.CFDictionarySetValue(identity, _constant(security, 'kSecAttrAccount'), text(account))
                result = security.SecItemUpdate(identity, changes)
        if result != 0:
            raise KeychainError('The macOS Keychain refused the credential. Unlock it and try signing in again.')
    except KeychainError:
        raise
    except Exception:
        raise KeychainError('The macOS Keychain could not save the credential. Try signing in again.') from None
    finally:
        # Dictionaries retain their entries. Release each reference created above exactly once.
        if owned:
            for ref in reversed(owned):
                cf.CFRelease(ref)


def _identity(cf, security, service, account, owned):
    def keep(ref):
        if not ref:
            raise KeychainError('The macOS Keychain could not prepare the request.')
        owned.append(ref)
        return ref
    query = keep(cf.CFDictionaryCreateMutable(None, 0, _callbacks(cf, 'kCFTypeDictionaryKeyCallBacks'),
                                             _callbacks(cf, 'kCFTypeDictionaryValueCallBacks')))
    cf.CFDictionarySetValue(query, _constant(security, 'kSecClass'), _constant(security, 'kSecClassGenericPassword'))
    cf.CFDictionarySetValue(query, _constant(security, 'kSecUseAuthenticationUI'), _constant(security, 'kSecUseAuthenticationUIFail'))
    for key, value in (('kSecAttrService', service), ('kSecAttrAccount', account)):
        ref = keep(cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100))
        cf.CFDictionarySetValue(query, _constant(security, key), ref)
    return query


def get_generic_password(service: str, account: str) -> str | None:
    """Read exactly one item using the same application identity as native writes."""
    if sys.platform != 'darwin' or os.environ.get('EXO_NO_KEYCHAIN'):
        raise KeychainError('The macOS Keychain is unavailable.')
    owned = []
    try:
        cf, security = _frameworks()
        query = _identity(cf, security, service, account, owned)
        cf.CFDictionarySetValue(query, _constant(security, 'kSecReturnData'), _constant(cf, 'kCFBooleanTrue'))
        cf.CFDictionarySetValue(query, _constant(security, 'kSecMatchLimit'), _constant(security, 'kSecMatchLimitOne'))
        found = ctypes.c_void_p()
        status = security.SecItemCopyMatching(query, ctypes.byref(found))
        if status == -25300:
            return None
        if found.value:
            owned.append(found.value)
        if status != 0 or not found.value:
            raise KeychainError('The macOS Keychain could not read the credential. Unlock it or sign in again.')
        length = cf.CFDataGetLength(found.value)
        if not 0 <= length <= 1024 * 1024:
            raise KeychainError('The macOS Keychain credential has an invalid size.')
        return ctypes.string_at(cf.CFDataGetBytePtr(found.value), length).decode('utf-8')
    except KeychainError:
        raise
    except Exception:
        raise KeychainError('The macOS Keychain could not read the credential. Unlock it or sign in again.') from None
    finally:
        if owned:
            for ref in reversed(owned):
                cf.CFRelease(ref)


def delete_generic_password(service: str, account: str) -> bool:
    """Delete only the selected service/account; a missing item is already cleared."""
    if sys.platform != 'darwin' or os.environ.get('EXO_NO_KEYCHAIN'):
        raise KeychainError('The macOS Keychain is unavailable.')
    owned = []
    try:
        cf, security = _frameworks()
        query = _identity(cf, security, service, account, owned)
        return security.SecItemDelete(query) in (0, -25300)
    except Exception:
        raise KeychainError('The macOS Keychain could not delete the credential.') from None
    finally:
        if owned:
            for ref in reversed(owned):
                cf.CFRelease(ref)
