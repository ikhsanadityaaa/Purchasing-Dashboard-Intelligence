"""
_paths.py — Shared path resolution helper for ServeOne Automation.

FIX V11: Saat di-package sebagai .exe dengan PyInstaller, `Path(__file__).parent`
menunjuk ke temporary `_MEIPASS` directory yang read-only dan dihapus saat app exit.
File ini menyediakan helper functions untuk resolve path yang benar:
  - _app_dir()    → lokasi .exe (atau folder source code saat dev mode)
  - _data_dir()   → lokasi writable untuk data user (LOCALAPPDATA)
  - _resource_path() → lokasi resource bundled (untuk icon, dll)

Semua file automation (main.py, automation_serp.py, automation_pur_info_reg.py, app.py)
harus import helper ini untuk path resolution.
"""
import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """Cek apakah app berjalan sebagai PyInstaller .exe."""
    return getattr(sys, 'frozen', False)


def _app_dir() -> Path:
    """Lokasi .exe (frozen) atau folder source code (dev mode).

    Saat frozen: return folder tempat .exe berada (mis. C:\\ServeOne\\)
    Saat dev: return folder tempat file Python berada
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    """Lokasi writable untuk data user (logs, downloads, .env, failed_uploads).

    Windows: %LOCALAPPDATA%\\ServeOneAutomation\\
    Linux/Mac: ~/.serveone/

    Folder ini selalu writable (tidak seperti Program Files atau _MEIPASS).
    """
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        data_dir = base / 'ServeoneDashboardDesktop'
    else:
        data_dir = Path.home() / '.serveone'
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _resource_path(relative_path: str) -> Path:
    """Lokasi resource bundled (icon, template, dll).

    Saat frozen: resource ada di sys._MEIPASS (PyInstaller temp folder)
    Saat dev: resource ada di folder source code
    """
    if _is_frozen():
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / relative_path


def _env_file_path() -> Path:
    """Lokasi file .env.

    Cek urutan:
    1. Folder .exe (untuk portable .exe yang ditaruh di USB/desktop)
    2. _data_dir() (fallback untuk Program Files yang read-only)
    """
    # Cek dulu di folder .exe (portable mode)
    return _data_dir() / '.env'


def _safe_mkdir(path: Path) -> Path:
    """Buat directory dengan fallback ke _data_dir() kalau gagal.

    Return path yang berhasil dibuat (bisa beda dari input kalau fallback).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except (PermissionError, OSError):
        # Fallback: pakai data dir
        fallback = _data_dir() / path.name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _safe_log_path(filename: str) -> Path:
    """Lokasi file log yang pasti writable.

    Selalu pakai _data_dir() untuk log — tidak perlu di folder .exe.
    """
    return _data_dir() / filename


def _safe_download_dir(subdir: str) -> Path:
    """Lokasi folder download yang pasti writable.

    Pakai _data_dir() / subdir supaya Excel download tidak hilang di _MEIPASS.
    """
    download_dir = _data_dir() / subdir
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def _safe_fail_dir(subdir: str) -> Path:
    """Lokasi folder failed_uploads yang pasti writable."""
    fail_dir = _data_dir() / subdir
    fail_dir.mkdir(parents=True, exist_ok=True)
    return fail_dir


def assert_local_dashboard_api(url: str) -> None:
    """Prevent sending Excel rows anywhere except an on-device Flask endpoint."""
    from urllib.parse import urlsplit
    parsed = urlsplit(str(url))
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or not parsed.path.startswith('/api/upload/'):
        raise ValueError('Local Import blocked: destination must be localhost /api/upload/.')
