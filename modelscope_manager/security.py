from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _windows_api():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def protect(text: str, *, allow_machine_fallback: bool = False) -> str:
    """Encrypt sensitive text with Windows DPAPI.

    Account Token and web-session callers use current-user scope only.  The
    optional machine fallback is reserved for non-account compatibility data
    such as the random device identifier and the LAN-only WebDAV password.
    """
    if not text:
        return ""
    crypt32, kernel32 = _windows_api()
    last_error = 0
    candidates = [("u:", 1)]
    if allow_machine_fallback:
        candidates.append(("m:", 5))
    for prefix, flags in candidates:
        source, keepalive = _blob(text.encode("utf-8"))
        target = DATA_BLOB()
        if crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(target)):
            try:
                raw = ctypes.string_at(target.pbData, target.cbData)
                return prefix + base64.b64encode(raw).decode("ascii")
            finally:
                kernel32.LocalFree(target.pbData)
                del keepalive
        last_error = ctypes.get_last_error()
    raise ctypes.WinError(last_error)


def protect_compatible(text: str) -> str:
    """Protect non-account compatibility data, allowing machine-scope fallback."""
    return protect(text, allow_machine_fallback=True)


def unprotect(value: str) -> str:
    if not value:
        return ""
    if len(value) > 2 and value[1] == ":":
        prefix, value = value[:2], value[2:]
    else:
        prefix = "u:"
    flags = 5 if prefix == "m:" else 1
    source, keepalive = _blob(base64.b64decode(value))
    target = DATA_BLOB()
    crypt32, kernel32 = _windows_api()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(target.pbData)
        del keepalive


def store_secret(text: str, *, plaintext: bool = False, allow_machine_fallback: bool = False) -> str:
    """Store a secret either explicitly as plaintext or with DPAPI."""
    if not text:
        return ""
    if plaintext:
        return "p:" + text
    return protect(text, allow_machine_fallback=allow_machine_fallback)


def load_secret(value: str) -> str:
    """Load a secret written by :func:`store_secret` or older DPAPI storage."""
    if value.startswith("p:"):
        return value[2:]
    return unprotect(value)
