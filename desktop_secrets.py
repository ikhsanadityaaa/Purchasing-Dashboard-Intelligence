"""Per-Windows-user SMRO secret storage using Microsoft DPAPI (never plaintext JSON).

This deliberately rejects password persistence on non-Windows systems.  The
encrypted file is not portable to another Windows account/PC.  Passwords are
injected only into the child automation worker environment when required.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

class _BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _crypt(data: bytes, decrypt: bool = False, purpose: bytes = b'ServeoneDashboardDesktop.SMRO.v1') -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Encrypted credentials require Windows DPAPI')
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    inp, keep_alive = _blob(data)
    out = _BLOB()
    entropy, keep_entropy = _blob(purpose)
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p,
            ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_BLOB)]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        ok = crypt32.CryptUnprotectData(ctypes.byref(inp), None, ctypes.byref(entropy),
                                         None, None, flags, ctypes.byref(out))
    else:
        crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR,
            ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_BLOB)]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        ok = crypt32.CryptProtectData(ctypes.byref(inp), 'Serveone Dashboard SMRO',
                                      ctypes.byref(entropy), None, None, flags,
                                      ctypes.byref(out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> tuple[str, str]:
        if not self.path.is_file():
            return '', ''
        try:
            username, password = _crypt(self.path.read_bytes(), decrypt=True).decode('utf-8').split('\n', 1)
            return username, password
        except Exception as exc:
            raise RuntimeError('Saved SMRO credentials cannot be decrypted by this Windows user') from exc

    def status(self) -> dict:
        username, password = self.load()
        return {'configured': bool(username and password), 'username': username}

    def save(self, username: str, password: str) -> dict:
        username = str(username or '').strip()
        password = str(password or '')
        if not username or not password or '\n' in username:
            raise ValueError('SMRO ID and password are required')
        encrypted = _crypt((username + '\n' + password).encode('utf-8'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + '.tmp')
        try:
            tmp.write_bytes(encrypted)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        return {'configured': True, 'username': username}
