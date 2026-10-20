"""
automation_serp.py
==================
Automation: Download Product ID Excel dari S-ERP (SAP GUI Desktop).

Alur:
1. Cek VPN
2. Login portal ServeOne (sportal.serveone.id) via Selenium [HEADLESS]
3. Quick Menu → pilih -S-ERP → browser download file .sap
4. Buka file .sap → SAP GUI Desktop terbuka (SSO) [MINIMIZE otomatis]
5. Ketik ZMMR3190 di transaction box (via UIA element)
6. Form [MM] Download Excel File(Mass) muncul
7. Paksa tutup semua Excel
8. Isi Register Date via UIA element (bukan koordinat)
9. Execute (F8)
10. Tangani dialog Save As via UIA element
11. Upload ke http://127.0.0.1:5001/api/upload/product-id
12. Cleanup

Mode:
  python main.py --process serp              -> run sekali
  python main.py --process serp --wait-vpn   -> VPN watch loop
  python main.py --process serp --debug      -> Chrome window terbuka + SAP window normal

Env (.env):
  SMRO_USERNAME            = 56000026
  SMRO_PASSWORD            = password_kamu
  SERP_DASHBOARD_API       = http://127.0.0.1:5001/api/upload/product-id
  SERP_START_DATE          = YYYY/MM/DD (optional override; default 1 bulan lalu tanggal 1 sampai hari ini)
  SERP_END_DATE            = YYYY/MM/DD (optional override; default hari ini)
  SERP_FILENAME_PREFIX     = ProductID
  SMRO_VPN_CHECK_SECONDS   = 60
  SERP_RETRY_SECONDS       = 300
# Upload failure recovery (NEW)
SERP_UPLOAD_FAIL_DIR     = Path(__file__).parent / 'failed_uploads_serp'
SERP_UPLOAD_MAX_RETRIES  = int(os.getenv('SERP_UPLOAD_MAX_RETRIES', '5'))
SERP_MAX_FAILED_AGE_DAYS = int(os.getenv('SERP_MAX_FAILED_AGE_DAYS', '7'))
  SMRO_SHOW_POPUP          = true
  KEEP_BROWSER_OPEN_ON_ERROR = false
  SMRO_HEADLESS            = true        <- Chrome headless (default true)
  SAPGUI_MINIMIZE          = true        <- SAP GUI di-minimize (default true)
"""

import os
import sys
import time

# FIX V11: path resolution saat frozen (.exe)
from _paths import _app_dir, _data_dir, _env_file_path, _safe_log_path, _safe_mkdir, _safe_download_dir, _safe_fail_dir, _is_frozen
import subprocess
import threading
import shutil
import psutil
import requests
import net_tls
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from datetime import datetime, timedelta, date

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
import logging
from i18n import set_language, apply_localized_logging

# ── Setup ─────────────────────────────────────────────────────────────────────
load_dotenv(_env_file_path())
set_language(os.getenv("SMRO_LANGUAGE", "id"))

# --debug flag → Chrome window terbuka, SAP tidak di-minimize, log DEBUG
_DEBUG_MODE = '--debug' in sys.argv

logging.basicConfig(
    level=logging.DEBUG if _DEBUG_MODE else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(_safe_log_path('serp_automation.log')), encoding='utf-8'),
        logging.StreamHandler()
    ],
    force=True,
)
apply_localized_logging(logging.getLogger())

# Redam log internal library yang sangat verbose saat --debug.
# Tanpa ini Selenium/urllib3 mencetak semua HTTP request/response WebDriver
# seperti "Remote response", screenshot base64 panjang, dan stacktrace locator.
for noisy_logger in [
    'selenium',
    'selenium.webdriver.remote.remote_connection',
    'urllib3',
    'urllib3.connectionpool',
    'webdriver_manager',
    'WDM',
]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

_DOWNLOAD_DIR = _safe_download_dir('downloads_serp')
# FIX V11: _DOWNLOAD_DIR sudah di-create oleh _safe_download_dir

# Headless default TRUE (production). --debug atau SMRO_HEADLESS=false → window
_HEADLESS_ENV_DEFAULT = 'false' if _DEBUG_MODE else 'true'
_CHROME_HEADLESS      = os.getenv('SERP_HEADLESS', _HEADLESS_ENV_DEFAULT).lower() in ('1', 'true', 'yes')
# --debug wajib SAP window normal untuk audit visual.
# .env SAPGUI_MINIMIZE=true tidak boleh override debug mode.
_SAP_MINIMIZE         = False if _DEBUG_MODE else os.getenv('SAPGUI_MINIMIZE', 'true').lower() in ('1', 'true', 'yes')

def default_serp_start_date(today: datetime | None = None) -> datetime:
    """Default SERP: tanggal 1 bulan lalu (range mencakup bulan lalu + bulan berjalan)."""
    today = today or datetime.now()
    first_this_month = today.replace(day=1)
    return (first_this_month - timedelta(days=1)).replace(day=1)


def parse_serp_env_date(value: str, label: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y/%m/%d")
    except ValueError:
        raise ValueError(f"{label} harus format YYYY/MM/DD: {value}")


def resolve_date_range_from_preset(today: datetime | None = None):
    """Preset date range PER-PROSES (All Product ID/SERP punya range
    sendiri, tidak dipaksa sama dengan proses lain).

    Baca SERP_DATE_RANGE_PRESET; kalau kosong/tidak dikenal -> 'custom'
    (pakai SERP_START_DATE/SERP_END_DATE). Tidak ada fallback ke
    DATE_RANGE_PRESET global: preset SMRO tidak boleh menular ke proses ini.

    Pilihan preset (semua mundur dari hari ini, end = hari ini):
      - '1week'     : 7 hari terakhir
      - '1month'    : 30 hari terakhir
      - '3month'    : 90 hari terakhir
      - '6month'    : 180 hari terakhir
      - 'this_year' : 1 Januari tahun ini s/d hari ini

    Return (start, end, preset_code) kalau preset aktif, atau
    (None, None, preset_code) kalau 'custom' / tidak dikenal
    (→ pemanggil pakai SERP_START_DATE/SERP_END_DATE seperti biasa).
    """
    today = today or datetime.now()
    preset = (os.getenv('SERP_DATE_RANGE_PRESET') or 'custom').strip().lower()
    if preset == '1week':
        return today - timedelta(days=7), today, preset
    if preset == '1month':
        return today - timedelta(days=30), today, preset
    if preset == '3month':
        return today - timedelta(days=90), today, preset
    if preset == '6month':
        return today - timedelta(days=180), today, preset
    if preset == 'this_year':
        return datetime(today.year, 1, 1), today, preset
    return None, None, preset


CONFIG = {
    'LOGIN_START_URL'  : os.getenv('SMRO_LOGIN_START_URL', 'http://sportal.serveone.id/portal/PortalView.do'),
    'DASHBOARD_API'    : os.getenv('SERP_DASHBOARD_API', 'http://127.0.0.1:5001/api/upload/product-id'),
    'UPDATE_DASHBOARD' : os.getenv('SERP_UPDATE_DASHBOARD', 'true').lower() in ('1', 'true', 'yes'),
    'SAVE_LOCAL'       : os.getenv('SERP_SAVE_LOCAL', 'false').lower() in ('1', 'true', 'yes'),
    'LOCAL_SAVE_DIR'   : os.getenv('SERP_LOCAL_SAVE_DIR', ''),
    'USERNAME'         : os.getenv('SMRO_USERNAME', ''),
    'PASSWORD'         : os.getenv('SMRO_PASSWORD', ''),
    'DOWNLOAD_DIR'     : _DOWNLOAD_DIR,
    'DATE_RANGE_DAYS'  : int(os.getenv('SERP_DATE_RANGE_DAYS', '7')),  # legacy; default now prev month 1st → today
    'START_DATE'       : os.getenv('SERP_START_DATE', ''),
    'END_DATE'         : os.getenv('SERP_END_DATE', ''),
    'FILENAME_PREFIX'  : os.getenv('SERP_FILENAME_PREFIX', 'ProductID'),
    'VPN_CHECK_SECONDS': int(os.getenv('SMRO_VPN_CHECK_SECONDS', '60')),
    'RETRY_SECONDS'    : int(os.getenv('SERP_RETRY_SECONDS', '300')),
    'SHOW_POPUP'       : os.getenv('SMRO_SHOW_POPUP', 'true').lower() in ('1', 'true', 'yes'),
    'KEEP_OPEN_ON_ERR' : os.getenv('KEEP_BROWSER_OPEN_ON_ERROR', 'false').lower() in ('1', 'true', 'yes'),
    'SAP_TCODE'        : 'ZMMR3190',
    'SAPGUI_EXE'       : os.getenv('SAPGUI_EXE', r'C:\Program Files\SAP\FrontEnd\SAPgui\sapgui.exe'),
    'HEADLESS'         : _CHROME_HEADLESS,
    'SAP_MINIMIZE'     : _SAP_MINIMIZE,
}

logging.info(f"Mode        : {'DEBUG' if _DEBUG_MODE else 'PRODUCTION'}")
logging.info(f"Chrome      : {'headless' if CONFIG['HEADLESS'] else 'window (debug)'}")
logging.info(f"SAP GUI     : {'minimize otomatis' if CONFIG['SAP_MINIMIZE'] else 'window normal (debug)'}")


# ── Popup ─────────────────────────────────────────────────────────────────────

def upload_mime_type(file_path: Path) -> str:
    return {
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.xls': 'application/vnd.ms-excel',
        '.csv': 'text/csv',
    }.get(file_path.suffix.lower(), 'application/octet-stream')

def show_popup(title: str, message: str, success: bool = True, auto_close_ms: int = 4000):
    """
    Tampilkan popup notifikasi.
    - auto_close_ms: tutup otomatis setelah N milidetik (default 4 detik).
      Set ke 0 untuk tidak auto-close (tunggu user klik OK).

    FIX: pakai desain popup bersama (notify.py) supaya tampilan notifikasi
    SERP sama persis dengan SMRO dan Pur Info Reg.
    """
    import notify
    notify.show_popup(title, message, success=success, auto_close_ms=auto_close_ms,
                       enabled=CONFIG['SHOW_POPUP'])


# ── Helper ────────────────────────────────────────────────────────────────────

def wait_for_page_ready(driver, timeout: int = 20):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )

def save_debug_screenshot(label: str, driver=None):
    """Simpan screenshot untuk debugging — dari browser atau desktop."""
    debug_dir = _data_dir() / "debug_serp"
    debug_dir.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    path = debug_dir / f"{ts}_{safe}.png"

    if driver:
        try:
            driver.save_screenshot(str(path))
            logging.info(f"  Screenshot browser: debug_serp/{path.name}")
            try:
                (debug_dir / f"{ts}_{safe}.html").write_text(
                    driver.page_source, encoding="utf-8", errors="replace"
                )
            except Exception:
                pass
            return
        except Exception:
            pass

    # Screenshot desktop (untuk SAP GUI window)
    try:
        import pyautogui
        pyautogui.screenshot(str(path))
        logging.info(f"  Screenshot desktop: debug_serp/{path.name}")
    except Exception as e:
        logging.warning(f"  Screenshot gagal: {e}")


# ── VPN ───────────────────────────────────────────────────────────────────────

def check_vpn_connection() -> bool:
    try:
        r = requests.get(CONFIG['LOGIN_START_URL'], timeout=10, allow_redirects=True)
        if r.status_code == 200:
            logging.info("VPN terhubung")
            return True
        logging.warning(f"VPN HTTP {r.status_code}")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"VPN tidak terhubung: {e}")
        return False


# ── Paksa tutup Excel ─────────────────────────────────────────────────────────

_SERP_PRESERVE_EXCEL_PIDS = set()
_SERP_PRESERVE_SAP_PIDS = set()


def _process_pids_containing(name_part: str) -> set:
    found = set()
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if name_part in (proc.info['name'] or '').lower():
                found.add(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


def force_close_all_excel():
    """Tutup Excel yang muncul selama run; pertahankan Excel milik user."""
    closed = 0
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if 'excel' in (proc.info['name'] or '').lower():
                if proc.info['pid'] in _SERP_PRESERVE_EXCEL_PIDS:
                    continue
                logging.info(f"  Menutup Excel PID {proc.info['pid']}...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                closed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if closed:
        logging.info(f"  {closed} Excel ditutup.")
        time.sleep(2)
    else:
        logging.info("  Tidak ada Excel yang terbuka.")


# ── WebDriver (Selenium) ──────────────────────────────────────────────────────

# FIX V13: lock global supaya setup_driver tidak dipanggil bersamaan dari
# multiple thread. Serial start Chrome antar call eliminates race condition
# 'session not created: Chrome failed to start: crashed.' saat ada beberapa
# call setup_driver berdekatan (mis. SERP + SMRO berjalan berurutan di Run All).
_SERP_SETUP_DRIVER_LOCK = threading.Lock()

# FIX V16.2: registry PID chromedriver yang masih hidup di proses ini.
# taskkill /IM chromedriver.exe yang LAMA membunuh SEMUA chromedriver di
# sistem — kalau proses SMRO/PUR lain masih jalan bersamaan (Run All / manual
# + watcher), chromedriver mereka ikut terbunuh → gagal berantai + spam
# urllib3 retry. Registry ini membuat cleanup jadi targeted (zombie saja).
_SERP_LIVE_DRIVER_PIDS = set()
_SERP_LIVE_PIDS_LOCK = threading.Lock()


def _serp_register_driver_pid(driver):
    try:
        pid = int(driver.service.process.pid)
        with _SERP_LIVE_PIDS_LOCK:
            _SERP_LIVE_DRIVER_PIDS.add(pid)
    except Exception:
        pass


def _serp_unregister_driver_pid(driver):
    try:
        pid = int(driver.service.process.pid)
        with _SERP_LIVE_PIDS_LOCK:
            _SERP_LIVE_DRIVER_PIDS.discard(pid)
    except Exception:
        pass


def _serp_ps_run(command: str, timeout: int = 20) -> bool:
    """Jalankan perintah PowerShell tanpa window. Return True kalau sukses."""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            timeout=timeout,
        )
        return result.returncode == 0
    except Exception:
        return False


def _serp_is_driver_alive(driver) -> bool:
    """Cek hidup/tidak chromedriver via OS process state (tanpa HTTP call).
    FIX V16.2: menghindari urllib3.Retry spam pada driver mati."""
    try:
        service = getattr(driver, 'service', None)
        proc = getattr(service, 'process', None) if service else None
        if proc is not None:
            return proc.poll() is None
        _ = driver.title
        return True
    except Exception:
        return False


def _serp_safe_driver_quit(driver, tag: str = ""):
    """Quit driver tanpa spam urllib3 + pastikan proses tree benar-benar mati.
    FIX V16.2: quit() pada chromedriver mati memicu urllib3.Retry 3x; wrapper
    ini pre-check process state, dan force-kill by PID + by profile supaya
    chrome.exe orphan tidak menumpuk."""
    if driver is None:
        return
    try:
        if not _serp_is_driver_alive(driver):
            _serp_force_kill_driver_tree(driver, tag=tag)
            return
        try:
            driver.quit()
            _serp_unregister_driver_pid(driver)
            return
        except Exception:
            _serp_force_kill_driver_tree(driver, tag=tag)
    except Exception:
        _serp_force_kill_driver_tree(driver, tag=tag)


def _serp_force_kill_driver_tree(driver, tag: str = ""):
    """Kill proses tree chromedriver milik driver ini (by PID + by profile).
    Aman: chromedriver milik proses lain tidak tersentuh. FIX V16.2."""
    if driver is None:
        return
    profile_dir = getattr(driver, '_serp_profile_dir', None)
    pid = None
    try:
        pid = int(driver.service.process.pid)
    except Exception:
        pass
    import platform
    try:
        if pid is not None:
            if platform.system() == 'Windows':
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
            else:
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    for child in proc.children(recursive=True):
                        child.kill()
                    proc.kill()
                except Exception:
                    subprocess.run(['kill', '-9', str(pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if profile_dir:
            prof = str(profile_dir).replace("'", "''").replace('"', '')
            prof_pattern = prof.replace('`', '``').replace('[', '`[').replace(']', '`]') \
                               .replace('*', '`*').replace('?', '`?')
            _serp_ps_run(
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{prof_pattern}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
    except Exception as e:
        logging.debug(f"  {tag} Force-kill driver tree gagal (best-effort): {e}".strip())
    finally:
        _serp_unregister_driver_pid(driver)


def _serp_cleanup_zombie_chrome(profile_dir=None):
    """FIX V13 → REWRITTEN V16.2: cleanup zombie chromedriver sebelum start baru.

    Versi lama `taskkill /F /IM chromedriver.exe /T` membunuh SEMUA chromedriver
    di sistem — berbahaya kalau proses lain (SMRO/PUR) sedang jalan bersamaan.
    Versi baru (targeted):
      1. Bunuh chrome.exe yang command-line-nya memuat profile_dir yang akan
         dipakai (pemegang SingletonLock dari attempt sebelumnya).
      2. Bunuh chromedriver.exe yang PID-nya TIDAK di registry live (zombie).
      3. Fallback taskkill massal hanya kalau PowerShell gagal DAN registry
         kosong (tidak ada chromedriver live yang bisa jadi korban).
    """
    import platform
    sys_name = platform.system()

    if sys_name == 'Windows':
        if profile_dir is not None:
            try:
                prof = str(profile_dir).replace("'", "''").replace('"', '')
                prof_pattern = prof.replace('`', '``').replace('[', '`[').replace(']', '`]') \
                                   .replace('*', '`*').replace('?', '`?')
                _serp_ps_run(
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{prof_pattern}*' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                )
            except Exception:
                pass

        with _SERP_LIVE_PIDS_LOCK:
            live_pids = sorted(_SERP_LIVE_DRIVER_PIDS)
        live_list = ",".join(str(p) for p in live_pids)
        ps_ok = _serp_ps_run(
            f"$live = @({live_list}); "
            "Get-Process chromedriver -ErrorAction SilentlyContinue | "
            "Where-Object { $live -notcontains $_.Id } | "
            "Stop-Process -Force -ErrorAction SilentlyContinue"
        )
        if not ps_ok:
            with _SERP_LIVE_PIDS_LOCK:
                any_live = bool(_SERP_LIVE_DRIVER_PIDS)
            if not any_live:
                subprocess.run(
                    ['taskkill', '/F', '/IM', 'chromedriver.exe', '/T'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
    else:
        with _SERP_LIVE_PIDS_LOCK:
            live_pids = set(_SERP_LIVE_DRIVER_PIDS)
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if (proc.info['name'] or '').lower().startswith('chromedriver') \
                            and proc.info['pid'] not in live_pids:
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            subprocess.run(
                ['pkill', '-f', 'chromedriver'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


# ═══════════════════════════════════════════════════════════════════════
# FIX V24: chromedriver di jaringan kantor ber-SSL-inspection (serp).
#
# Gejala (laporan user 2026-09-14 08:20, proses All Prod ID / SERP):
#   ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate
#     verify failed: self-signed certificate in certificate chain
#   requests.exceptions.ConnectionError: Could not reach host. Are you offline?
#   host='googlechromelabs.github.io' ... latest-patch-versions-per-build.json
#
# Penyebab: SSL inspection proxy kantor me-re-sign HTTPS dengan CA korporat
# self-signed; requests/webdriver-manager hanya percaya bundle certifi ->
# download chromedriver gagal -> SERP tidak bisa start sama sekali.
#
# Solusi: sama seperti main.py — rantai fallback BERLAPIS:
#   0. chromedriver.exe manual sebelah aplikasi (offline total).
#   1. Path chromedriver yang sudah berhasil di process ini (cache).
#   2. Selenium Manager bawaan Selenium >= 4.6 (Windows certificate store
#      -> CA korporat dikenali -> lolos SSL inspection; cache reuse offline).
#   3. ChromeDriverManager() normal (jaringan normal).
#   4. ChromeDriverManager() dengan SSL verify OFF (self-signed chain).
# ═══════════════════════════════════════════════════════════════════════
_SERP_CHROMEDRIVER_INSTALL_LOCK = threading.Lock()
_SERP_CHROMEDRIVER_PATH_CACHE = None


class _SerpWdmSslVerifyOff:
    """(FIX V24) Context manager: matikan verifikasi SSL pada
    requests.Session HANYA selama blok ini aktif (mirror _WdmSslVerifyOff
    di main.py — module ini standalone, tidak import main).

    Dipakai untuk download chromedriver lewat webdriver-manager di jaringan
    kantor ber-SSL-inspection. Dipulihkan PENUH di __exit__ sehingga
    request lain (VPN check, dll) tetap diverifikasi SSL normal.
    """

    def __init__(self):
        self._orig_request = None

    def __enter__(self):
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        self._orig_request = requests.Session.request

        def _request_no_verify(session_self, method, url, **kwargs):
            kwargs.setdefault('verify', False)
            return self._orig_request(session_self, method, url, **kwargs)

        requests.Session.request = _request_no_verify
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._orig_request is not None:
            requests.Session.request = self._orig_request
        return False


def _serp_local_chromedriver_candidates():
    """(FIX V24) chromedriver.exe manual sebelah aplikasi (offline total)."""
    candidates = []
    seen = set()
    try:
        bases = [_app_dir(), Path.cwd()]
    except Exception:
        bases = [Path.cwd()]
    exe_name = 'chromedriver.exe' if os.name == 'nt' else 'chromedriver'
    for base in bases:
        try:
            p = base / exe_name
            key = str(p).lower() if os.name == 'nt' else str(p)
            if key not in seen and p.is_file():
                seen.add(key)
                candidates.append(str(p))
        except Exception:
            continue
    return candidates


def _serp_summarize_chromedriver_err(e, limit=300):
    """(FIX V24) Ringkas pesan error per strategi untuk popup GUI."""
    try:
        s = str(e).strip().replace('\n', ' | ')
    except Exception:
        s = repr(e)
    return s[:limit] + ('...' if len(s) > limit else '')


def _serp_create_chrome_with_fallback(opts):
    """(FIX V24) Buka Chrome dengan rantai fallback chromedriver berlapis.

    Mirror _create_chrome_with_fallback() di main.py untuk module SERP yang
    standalone. Kelembalian per-strategi di-log WARNING (bukan ERROR) supaya
    tidak dihitung sebagai error tahap oleh GUI; barisnya mengandung kata
    'chromedriver' sehingga di-skip dari Activity Log (raw log tetap lengkap).
    """
    global _SERP_CHROMEDRIVER_PATH_CACHE
    attempts = []

    def _try_service_path(tag, path):
        try:
            drv = webdriver.Chrome(service=Service(path), options=opts)
            logging.info(f"Chromedriver siap [{tag}]")
            return drv
        except Exception as e:
            msg = _serp_summarize_chromedriver_err(e)
            attempts.append(f"{tag}: {msg}")
            logging.warning(
                f"Chromedriver gagal lewat {tag}, coba cara berikutnya ({msg})"
            )
            return None

    # Strategi 0: chromedriver.exe manual sebelah aplikasi (offline total).
    for path in _serp_local_chromedriver_candidates():
        drv = _try_service_path('chromedriver lokal sebelah aplikasi', path)
        if drv:
            _SERP_CHROMEDRIVER_PATH_CACHE = path
            return drv

    # Strategi 1: path yang sudah terbukti berhasil di process ini.
    if _SERP_CHROMEDRIVER_PATH_CACHE:
        drv = _try_service_path('path sukses sebelumnya', _SERP_CHROMEDRIVER_PATH_CACHE)
        if drv:
            return drv

    # Strategi 2: Selenium Manager bawaan Selenium >= 4.6 (tanpa Service
    # path) — Windows certificate store -> CA korporat dikenali -> lolos
    # SSL inspection; cache ~/.cache/selenium otomatis dipakai ulang.
    try:
        drv = webdriver.Chrome(options=opts)
        logging.info("Chromedriver siap [Selenium Manager bawaan Selenium]")
        return drv
    except Exception as e:
        msg = _serp_summarize_chromedriver_err(e)
        attempts.append(f"Selenium Manager bawaan: {msg}")
        logging.warning(
            f"Chromedriver gagal lewat Selenium Manager, coba cara "
            f"berikutnya ({msg})"
        )

    # Strategi 3 & 4: webdriver-manager — normal dulu, lalu SSL verify OFF.
    for ssl_off in (False, True):
        tag = 'ChromeDriverManager SSL-off' if ssl_off else 'ChromeDriverManager'
        try:
            if ssl_off:
                with _SerpWdmSslVerifyOff():
                    with _SERP_CHROMEDRIVER_INSTALL_LOCK:
                        path = ChromeDriverManager().install()
            else:
                with _SERP_CHROMEDRIVER_INSTALL_LOCK:
                    path = ChromeDriverManager().install()
            drv = _try_service_path(tag, path)
            if drv:
                _SERP_CHROMEDRIVER_PATH_CACHE = path
                return drv
        except Exception as e:
            msg = _serp_summarize_chromedriver_err(e)
            attempts.append(f"{tag}: {msg}")
            logging.warning(
                f"Chromedriver gagal lewat {tag}, coba cara berikutnya ({msg})"
            )

    # Semua strategi gagal -> error yang bisa ditindaklanjuti user.
    raise RuntimeError(
        "GAGAL menyiapkan chromedriver lewat semua cara yang tersedia. "
        "Penyebab paling umum: jaringan kantor dengan SSL inspection "
        "(self-signed certificate in chain) memblokir download chromedriver "
        "dari googlechromelabs.github.io, atau internet sedang mati. "
        "Solusi cepat: download chromedriver.exe yang cocok dengan versi "
        "Chrome (cek di chrome://version) dari "
        "https://googlechromelabs.github.io/chrome-for-testing/ lalu taruh "
        "file itu di folder yang SAMA dengan aplikasi, kemudian jalankan "
        "ulang. Rincian per strategi:\n- " + "\n- ".join(attempts)
    )


def setup_driver():
    """
    Chrome untuk login portal & download .sap.

    HEADLESS mode (default production):
      --headless=new  : Chrome 112+ modern headless
      Download via CDP tetap berjalan normal
      Anti-bot headers tetap aktif

    DEBUG mode (--debug flag atau SMRO_HEADLESS=false):
      Chrome window terbuka seperti biasa

    FIX V13: hardening tambahan untuk mencegah 'session not created:
    Chrome failed to start: crashed.' yang sering muncul saat start Chrome
    berurutan di Run All (SERP + SMRO + PUR).
      - --user-data-dir UNIK per call (folder profile terpisah, tidak saling
        rebut SingletonLock dengan instance lain)
      - --remote-debugging-port=0 (auto-pick port bebas)
      - Cleanup zombie chromedriver.exe sebelum start (Windows: taskkill)
      - _SERP_SETUP_DRIVER_LOCK men-serialize start antar call
      - Anti-crash flags: --no-first-run, --disable-extensions, dll.
    """
    opts = Options()

    if CONFIG['HEADLESS']:
        opts.add_argument('--headless=new')
        logging.info("  Chrome: HEADLESS mode")
    else:
        logging.info("  Chrome: WINDOW mode (debug)")

    # FIX V13: profile_dir unik per call (UUID singkat) supaya tidak bentrok
    # dengan instance Chrome lain yang mungkin masih hidup (mis. dari Run All
    # sebelumnya yang crash sebelum sempat quit).
    import uuid
    import tempfile
    profile_tag = uuid.uuid4().hex[:8]
    profile_dir = Path(CONFIG['DOWNLOAD_DIR']).parent / 'chrome_profiles' / f'serp_{profile_tag}'
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        profile_dir = Path(tempfile.gettempdir()) / 'serveone_chrome_serp' / profile_tag
        profile_dir.mkdir(parents=True, exist_ok=True)

    with _SERP_SETUP_DRIVER_LOCK:
        # FIX V16.2: cleanup targeted — chrome by profile + chromedriver zombie
        # by PID. Chromedriver milik proses lain (SMRO/PUR) tidak dibunuh lagi.
        _serp_cleanup_zombie_chrome(profile_dir=profile_dir)

        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--disable-popup-blocking')
        opts.add_argument('--disable-notifications')
        opts.add_argument('--window-size=1280,900')
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_argument(
            '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        # FIX V13: anti-crash flags untuk parallel / sequential start.
        opts.add_argument('--no-first-run')
        opts.add_argument('--no-default-browser-check')
        opts.add_argument('--disable-extensions')
        opts.add_argument('--disable-plugins')
        opts.add_argument('--disable-default-apps')
        opts.add_argument('--disable-component-extensions-with-background-pages')
        opts.add_argument('--disable-hang-monitor')
        opts.add_argument('--disable-prompt-on-repost')
        opts.add_argument('--disable-backgrounding-occluded-windows')
        opts.add_argument('--disable-renderer-backgrounding')
        opts.add_argument('--disable-ipc-flood-protection')
        # FIX V13: user-data-dir unik per call + port 0 (auto-pick). Tanpa
        # ini, Chrome start pakai user-data-dir default yang sama dengan
        # instance lain -> saling rebut SingletonLock -> crash.
        opts.add_argument(f'--user-data-dir={profile_dir}')
        opts.add_argument('--remote-debugging-port=0')
        opts.add_argument('--password-store=basic')
        opts.add_argument('--use-mock-keychain')
        opts.add_experimental_option('excludeSwitches', ['enable-automation'])
        opts.add_experimental_option('useAutomationExtension', False)

        if CONFIG['KEEP_OPEN_ON_ERR'] and not CONFIG['HEADLESS']:
            opts.add_experimental_option('detach', True)

        prefs = {
            'download.default_directory'                                : str(CONFIG['DOWNLOAD_DIR']),
            'download.prompt_for_download'                              : False,
            'download.directory_upgrade'                                : True,
            # Matikan safe browsing agar tidak memblokir download .sap dari HTTP
            'safebrowsing.enabled'                                      : False,
            'safebrowsing.disable_download_protection'                  : True,
            'safebrowsing_for_trusted_sources_enabled'                  : False,
            'profile.default_content_setting_values.automatic_downloads': 1,
        }
        opts.add_experimental_option('prefs', prefs)

        # Izinkan mixed-content download (file .sap dari HTTP saat portal HTTPS)
        # Ini yang menyebabkan popup "Insecure download blocked" di Chrome
        opts.add_argument('--allow-running-insecure-content')
        opts.add_argument(
            '--unsafely-treat-insecure-origin-as-secure='
            'http://svoerpsso.serveoneep.co.kr:52100'
        )

        # FIX V24: rantai fallback chromedriver berlapis — menutup error
        # 'certificate verify failed: self-signed certificate in chain'
        # di jaringan kantor ber-SSL-inspection (laporan 2026-09-14: SERP
        # gagal start karena download driver dari googlechromelabs.github.io
        # diblokir SSL proxy kantor). Urutan: chromedriver lokal -> cache
        # process -> Selenium Manager -> WDM normal -> WDM SSL-off.
        driver = _serp_create_chrome_with_fallback(opts)
        # FIX V16.2: stash profile_dir + catat PID live supaya quit/cleanup
        # bisa targeted (by PID / by profile), bukan massal.
        try:
            driver._serp_profile_dir = str(profile_dir)
        except Exception:
            pass
        _serp_register_driver_pid(driver)

        # CDP wajib untuk headless agar download berjalan
        driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
            'behavior'    : 'allow',
            'downloadPath': str(CONFIG['DOWNLOAD_DIR'])
        })
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
        logging.info(f"  Chrome WebDriver siap (profile: serp_{profile_tag})")
        return driver


# ── Login portal ──────────────────────────────────────────────────────────────

def login_to_portal(driver) -> bool:
    try:
        logging.info("  Buka portal ServeOne...")
        driver.get(CONFIG['LOGIN_START_URL'])
        wait_for_page_ready(driver)
        wait = WebDriverWait(driver, 30)

        # Username
        user_field = None
        for by, sel in [
            (By.ID,   'userid'),
            (By.NAME, 'userid'),
            (By.ID,   'userId'),
            (By.XPATH, "//input[not(@type='hidden') and "
                       "contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                       "'abcdefghijklmnopqrstuvwxyz'),'user')]"),
        ]:
            try:
                f = wait.until(EC.presence_of_element_located((by, sel)))
                if f.is_displayed():
                    user_field = f
                    break
            except Exception:
                pass

        if not user_field:
            logging.error("  Field username tidak ditemukan")
            save_debug_screenshot("login_no_username", driver)
            return False

        user_field.clear()
        user_field.send_keys(CONFIG['USERNAME'])
        logging.info(f"  Username: {CONFIG['USERNAME']}")

        # Password
        pass_field = None
        for by, sel in [
            (By.ID,   'password'),
            (By.NAME, 'password'),
            (By.XPATH, "//input[@type='password']"),
        ]:
            try:
                f = driver.find_element(by, sel)
                if f.is_displayed():
                    pass_field = f
                    break
            except Exception:
                pass

        if not pass_field:
            logging.error("  Field password tidak ditemukan")
            save_debug_screenshot("login_no_password", driver)
            return False

        pass_field.clear()
        pass_field.send_keys(CONFIG['PASSWORD'])

        # Submit
        submitted = False
        for by, sel in [
            (By.CSS_SELECTOR, "img[onclick*='doSubmit']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.XPATH, "//*[contains(translate(@onclick,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                       "'abcdefghijklmnopqrstuvwxyz'),'dosubmit')]"),
        ]:
            try:
                btn = driver.find_element(by, sel)
                if btn.is_displayed():
                    btn.click()
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            pass_field.send_keys(Keys.ENTER)

        wait.until(lambda d: 'sportal.serveone.id' in d.current_url)
        wait_for_page_ready(driver)
        logging.info("  Login berhasil")
        return True

    except Exception as e:
        logging.error(f"  Login gagal: {e}")
        save_debug_screenshot("login_failed", driver)
        return False


def close_popup_if_exists(driver):
    try:
        handles = driver.window_handles
        if len(handles) > 1:
            for h in handles[1:]:
                driver.switch_to.window(h)
                driver.close()
            driver.switch_to.window(handles[0])
    except Exception:
        pass


# ── Quick Menu → -S-ERP → trigger download .sap ──────────────────────────────

def trigger_serp_sap_download(driver) -> bool:
    """
    Pilih -S-ERP di Quick Menu → Chrome download file .sap.

    Dari HTML portal, select element:
      <select name="systemInfo" id="systemInfo" onchange="goUrlLink(this.value);...">
        <option value="http://svoerpsso.serveoneep.co.kr:52100/...">-S-ERP</option>
      </select>

    Fix:
    1. Cari by id="systemInfo" atau name="systemInfo" (bukan keyword 'quick')
    2. Panggil goUrlLink(url) langsung via JS — jangan set .value dulu
    3. Coba semua iframe + default_content
    """
    # URL S-ERP yang diketahui dari HTML (fallback hardcode jika select tidak ketemu)
    SERP_URL_KNOWN = (
        "http://svoerpsso.serveoneep.co.kr:52100"
        "/InitechSSO/sso_serp.jsp?portal=erp&country=ID"
    )

    def _find_and_click(ctx_driver) -> bool:
        """Coba temukan select systemInfo dan trigger goUrlLink di context saat ini."""
        wait = WebDriverWait(ctx_driver, 10)

        # Cari select — prioritas: id/name "systemInfo", fallback ke option yang berisi S-ERP
        sel_el = None
        for by, sel in [
            (By.ID,    "systemInfo"),
            (By.NAME,  "systemInfo"),
            (By.XPATH, "//select[@id='systemInfo' or @name='systemInfo']"),
            (By.XPATH, "//select[.//option[contains(@value,'sso_serp')]]"),
            (By.XPATH, "//select[.//option[contains(normalize-space(.),'S-ERP')]]"),
        ]:
            try:
                el = wait.until(EC.presence_of_element_located((by, sel)))
                if el:
                    sel_el = el
                    logging.info(f"  Quick Menu ditemukan via {by}={sel!r}")
                    break
            except Exception:
                pass

        if not sel_el:
            return False

        # Ambil URL S-ERP langsung via JS tanpa iterate semua opsi (optimasi bottleneck #1)
        serp_url = None
        try:
            serp_url = ctx_driver.execute_script("""
                const sel = arguments[0];
                const opts = sel.options;
                for (let i = 0; i < opts.length; i++) {
                    const v = opts[i].value || '';
                    const t = (opts[i].text || '').trim().toUpperCase();
                    if ((t.includes('S-ERP') && !t.includes('S-MRO')) || 
                        v.toLowerCase().includes('sso_serp') ||
                        (v.toLowerCase().includes('s-erp') && !v.toLowerCase().includes('s-mro'))) {
                        return v;
                    }
                }
                return null;
            """, sel_el)
            if serp_url:
                logging.info(f"  Target S-ERP URL: {serp_url}")
        except Exception as e:
            logging.warning(f"  Gagal parse opsi via JS: {e}")

        if not serp_url:
            logging.warning("  URL S-ERP tidak ditemukan di options, pakai fallback hardcode")
            serp_url = SERP_URL_KNOWN

        logging.info(f"  Memanggil goUrlLink({serp_url!r})...")

        # Panggil goUrlLink() langsung — ini yang dipakai onchange portal
        # JANGAN set .value dulu karena tidak trigger native event
        # FIX V20: kalau JS call terblokir popup alert (popup Allow baru bisa
        # muncul tepat pada momen ini), accept dulu lalu coba SEKALI lagi.
        result = None
        for attempt in (1, 2):
            try:
                result = ctx_driver.execute_script(
                    """
                    const url = arguments[0];
                    const sel = arguments[1];
                    try {
                        if (typeof goUrlLink === 'function') {
                            goUrlLink(url);
                            return 'goUrlLink called';
                        }
                        // Fallback: set value + fire change event manual
                        if (sel) {
                            sel.value = url;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            return 'change dispatched';
                        }
                        // Last resort: navigate langsung
                        window.open(url, '_blank');
                        return 'window.open';
                    } catch(e) {
                        return 'error: ' + e.message;
                    }
                    """,
                    serp_url,
                    sel_el,
                )
                break
            except Exception as script_err:
                if attempt == 1 and "alert" in str(script_err).lower():
                    logging.warning(
                        f"  [V20] JS call terblokir dialog alert ({script_err}) — "
                        "di-accept lalu dicoba ulang sekali"
                    )
                    accept_js_alert_if_present(ctx_driver)
                    time.sleep(0.5)
                    continue
                raise
        logging.info(f"  JS result: {result}")
        return True

    try:
        logging.info("  Mencari Quick Menu -S-ERP di portal (langsung iframe portal_header)...")
        driver.switch_to.default_content()

        # Langsung masuk iframe portal_header (sudah terbukti dari log)
        try:
            driver.switch_to.frame("portal_header")
            logging.info("  Masuk iframe: portal_header")
            if _find_and_click(driver):
                driver.switch_to.default_content()
                logging.info("  -S-ERP berhasil diklik (iframe: portal_header)")
                return True
            driver.switch_to.default_content()
        except Exception:
            logging.debug("  iframe portal_header tidak ditemukan, skip")
            driver.switch_to.default_content()

        # Fallback: coba semua iframe (hanya jika portal_header gagal)
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        logging.info(f"  portal_header gagal, coba {len(iframes)} iframe lainnya...")
        for i, frame in enumerate(iframes):
            try:
                name = frame.get_attribute("name") or frame.get_attribute("id") or f"#{i}"
                if name == 'portal_header':
                    continue  # sudah dicoba di atas
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                logging.info(f"  Masuk iframe: {name}")
                if _find_and_click(driver):
                    driver.switch_to.default_content()
                    logging.info(f"  -S-ERP berhasil diklik (iframe: {name})")
                    return True
            except Exception as e:
                logging.debug(f"  iframe {i} skip: {e}")
                driver.switch_to.default_content()

        # Semua gagal — screenshot dan return False
        driver.switch_to.default_content()
        logging.error("  Quick Menu -S-ERP tidak ditemukan!")
        save_debug_screenshot("no_quickmenu", driver)
        return False

    except Exception as e:
        logging.error(f"  trigger_serp_sap_download gagal: {e}")
        save_debug_screenshot("serp_trigger_error", driver)
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return False


# ── Tunggu file .sap ──────────────────────────────────────────────────────────

def handle_chrome_insecure_download(driver, timeout: int = 10) -> bool:
    """
    Handle popup "Insecure download blocked" di Chrome.

    Chrome memblokir download file .sap (HTTP) dengan warning di download bar.
    Solusi utama: buka chrome://downloads dan klik tombol "Keep" via JS injection.
    Fallback Alt+K hanya aktif saat mode debug/non-headless untuk Chrome download bar.

    Dipanggil setelah trigger_serp_sap_download(), sebelum wait_for_sap_file().
    """
    import time as _time
    logging.info("  Menunggu dialog insecure download Chrome...")
    _time.sleep(3)  # beri waktu download bar muncul

    # Metode 1: buka chrome://downloads dan klik Keep via JS
    # FIX V20: dijalankan di bawah _DRIVER_CTX_LOCK — background Allow
    # watcher tidak boleh switch window di tengah navigasi ini.
    try:
        original_url = driver.current_url
        with _DRIVER_CTX_LOCK:
            driver.get("chrome://downloads/")
            _time.sleep(2)

            # Klik tombol "Keep" di downloads page via shadow DOM
            kept = driver.execute_script("""
                try {
                    const manager = document.querySelector('downloads-manager');
                    if (!manager) return 'no-manager';
                    const root = manager.shadowRoot;
                    if (!root) return 'no-shadow';
                    const items = root.querySelectorAll('downloads-item');
                    for (const item of items) {
                        const sr = item.shadowRoot;
                        if (!sr) continue;
                        // Cari tombol Keep / Keep anyway
                        const btns = sr.querySelectorAll('cr-button, button, paper-button');
                        for (const btn of btns) {
                            const txt = (btn.textContent || btn.innerText || '').trim().toLowerCase();
                            if (txt.includes('keep') || txt.includes('simpan')) {
                                btn.click();
                                return 'clicked:' + txt;
                            }
                        }
                    }
                    return 'no-keep-button';
                } catch(e) {
                    return 'error:' + e.message;
                }
            """)
            logging.info(f"  Chrome downloads Keep result: {kept}")

            if kept and 'clicked' in str(kept):
                logging.info("  Berhasil klik Keep di chrome://downloads")
                driver.get(original_url)
                return True

            # Fallback: navigasi balik ke portal (download sudah diizinkan atau tidak ada)
            driver.get(original_url)

    except Exception as e:
        logging.warning(f"  handle_chrome_insecure_download (method1): {e}")
        try:
            with _DRIVER_CTX_LOCK:
                driver.get(driver.current_url)
        except Exception:
            pass

    # Metode 2: pyautogui klik Keep di download bar (hanya mode window / non-headless)
    if not CONFIG['HEADLESS']:
        try:
            import pyautogui
            import pygetwindow as gw
            _time.sleep(1)
            # Tekan Tab beberapa kali untuk fokus ke tombol Keep di download bar Chrome
            pyautogui.hotkey('alt', 'k')
            _time.sleep(0.5)
            logging.info("  Sent Alt+K untuk Keep download bar")
        except Exception as e:
            logging.warning(f"  handle_chrome_insecure_download (method2): {e}")

    return False


# ── FIX V20: popup "Allow" sebelum download dimulai (All Product ID) ──────────
#
# Laporan user: "ada pop up baru yang harus di klik allow sebelum proses
# download excel dimulai di process all prod id tapi ini tidak terklik
# otomatis."
#
# Popup Allow bisa muncul di 3 lapisan yang berbeda, dan semuanya kini
# ditangani otomatis:
#   1. Chrome permission ("... ingin mengunduh beberapa file sekaligus"
#      Allow/Block)  → di-PREVENT: pref automatic_downloads (setup_driver)
#      + CDP Browser.setPermission di pregrant_chrome_download_permissions.
#   2. JS alert/confirm atau dialog IN-PAGE (ExtJS .x-window) dari halaman
#      SSO/portal → di-KLIK otomatis oleh watcher thread
#      _start_chrome_allow_popup_watcher selama fase download .sap.
#   3. Dialog desktop SAP/Windows dengan tombol Allow/OK/Izinkan (muncul
#      tepat sebelum Excel digenerate setelah Execute) → di-KLIK oleh
#      SAP popup guard Win32 yang kini diperpanjang sampai fase save
#      (guard refresh di execute() dan handle_save_dialog()).
#
# FIX V29 (laporan user 2026-09-15: "di proses all prod id, masih ada pop up
# SAP untuk meminta 'allow' yang tidak terklik otomatis"):
#   Popup yang dimaksud adalah dialog desktop "SAP GUI Security"
#   ("The system is attempting to create the file ... Do you want to grant
#   the permission to modify the parent directory and all its
#   subdirectories?" — tombol Allow / Deny / Help + checkbox "Remember My
#   Decision"). Dialog ini muncul tepat saat SAP GUI MENYIMPAN file Excel
#   (ProductID_*.xlsx) ke folder download. Penyebab guard lama melewatkannya:
#     (a) filter class '#32770' — dialog security tidak selalu ber-class itu;
#     (b) filter proses {saplogon/sapgui/saplgpad} — session yang dibuka via
#         file .sap shortcut bisa di-host proses lain (mis. sapshcut.exe);
#     (c) checkbox "Remember My Decision" tidak pernah dicentang, jadi
#         popup kembali muncul di SETIAP run.
#   V29: dialog dicocokkan lewat TITLE 'SAP GUI Security' (class apa pun,
#   proses sap-like apa pun), checkbox Remember dicentang DULU supaya SAP
#   GUI sendiri menyimpan aturan Allow permanen untuk folder download
#   (popup tidak muncul lagi di run berikutnya), lalu tombol Allow diklik.
#   Guard utama diperpanjang 120→600 detik + interval 3→1 detik, dan
#   handle_save_dialog kini menunggu file stabil sampai 60 detik sambil
#   terus me-dismiss popup (klik Allow menunda pembuatan file beberapa detik).
# ──────────────────────────────────────────────────────────────────────────────

# Lock konteks driver: background watcher boleh switch window utk cek alert,
# tapi TIDAK boleh balapan dengan main thread yang sedang navigasi
# (driver.get / switch frame). Sequences yang mengubah window aktif
# dilakukan di bawah lock ini.
_DRIVER_CTX_LOCK = threading.Lock()


def pregrant_chrome_download_permissions(driver) -> bool:
    """
    FIX V20: pre-grant permission download di Chrome via CDP supaya popup
    '... ingin mengunduh beberapa file sekaligus' (tombol Allow/Block) dan
    prompt notifikasi TIDAK MUNCUL sebelum download dimulai.

    Pref 'profile.default_content_setting_values.automatic_downloads' sudah
    dipasang di setup_driver, tapi Chrome versi baru kadang mengabaikannya.
    Browser.setPermission (CDP) mem-grant permission secara eksplisit.
    """
    granted = 0
    for perm_name in ("automatic-downloads", "notifications"):
        try:
            driver.execute_cdp_cmd("Browser.setPermission", {
                "permission": {"name": perm_name},
                "setting": "granted",
            })
            granted += 1
        except Exception as e:
            # Chrome lama / headless bisa menolak — bukan fatal, watcher tetap jalan
            logging.debug(f"  CDP setPermission '{perm_name}' gagal: {e}")
    try:
        driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(CONFIG["DOWNLOAD_DIR"]),
        })
        granted += 1
    except Exception as e:
        logging.debug(f"  CDP setDownloadBehavior gagal: {e}")
    if granted:
        logging.info(f"  Permission download Chrome di-pre-grant via CDP ({granted} item)")
    return granted > 0


def accept_js_alert_if_present(driver) -> bool:
    """
    FIX V20: kalau ada JS alert/confirm di window aktif (mis. halaman SSO
    bertanya 'Allow...?'), terima (OK/Allow) + catat teksnya ke log supaya
    bisa didiagnosis jarak jauh. Aman dipanggil kapan pun.
    """
    try:
        alert = driver.switch_to.alert
    except Exception:
        return False
    try:
        text = (alert.text or "").strip()
        logging.info(f"  [V20] JS dialog terdeteksi: {text!r} → di-accept (Allow/OK)")
        alert.accept()
        time.sleep(0.3)
        return True
    except Exception as e:
        # Alert bisa hilang di antara deteksi dan accept (race) — bukan error
        logging.debug(f"  accept_js_alert_if_present: {e}")
        return False


# JS: cari + klik tombol "Allow" di dialog IN-PAGE (ExtJS/SAP portal).
# Hanya tombol di dalam container dialog yang visible — tidak menyentuh
# tombol lain di halaman biasa.
_ALLOW_BUTTON_CLICK_JS = """
const ALLOW = ['allow','&allow','izinkan','yes','&yes','ya',
               'allow all','allow download','permit','setuju'];
const DIALOG_SELS = ['.x-window', '.x-message-box', '[role="dialog"]',
                     '.modal', '.modal-dialog', '.ui-dialog',
                     '.layerPopup', '.popup'];
// FIX V27: tombol Allow yang sama hanya diklik 1x per 10 detik. Loop
// polling download memanggil JS ini tiap ~1 detik — tanpa penanda, dialog
// yang bertahan >1 detik ter-accept BERULANG dan tiap accept bisa
// memicu export excel lagi (file terdownload 2x, laporan user di menu
// Prod. Reg. Status).
const MARK = 'data-z-autoallow-ts';
const RECLICK_MS = 10000;
const now = Date.now();
const containers = [];
for (const sel of DIALOG_SELS) {
    try {
        document.querySelectorAll(sel).forEach(el => {
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            if (r.width > 0 && r.height > 0 && st.visibility !== 'hidden'
                && st.display !== 'none') containers.push(el);
        });
    } catch (e) {}
}
let clicked = 0;
const seen = new Set();
for (const c of containers) {
    const btns = c.querySelectorAll('button, a, span, div[role="button"]');
    for (const b of btns) {
        if (seen.has(b)) continue;
        seen.add(b);
        const txt = (b.textContent || b.innerText || '').trim().toLowerCase();
        if (txt && ALLOW.includes(txt)) {
            // FIX V27: tombol ini sudah diklik <10 detik lalu? lewati.
            let ts = 0;
            try { ts = parseInt(b.getAttribute(MARK) || '0', 10) || 0; } catch (e) {}
            if (now - ts < RECLICK_MS) continue;
            try { b.setAttribute(MARK, String(now)); } catch (e) {}
            try { b.click(); clicked++; } catch (e) {
                try {
                    const ev = new MouseEvent('click', {bubbles: true, cancelable: true});
                    b.dispatchEvent(ev); clicked++;
                } catch (e2) {}
            }
        }
    }
}
return clicked;
"""


def _click_allow_button_in_page(driver) -> int:
    """
    FIX V20: cari & klik tombol 'Allow' di popup IN-PAGE (dialog ExtJS /
    SAP portal, BUKAN JS alert). Return jumlah tombol yang terklik.
    """
    try:
        clicked = driver.execute_script(_ALLOW_BUTTON_CLICK_JS)
    except Exception as e:
        # Context bisa sedang di frame chrome:// atau window lain — normal
        logging.debug(f"  _click_allow_button_in_page: {e}")
        return 0
    if clicked:
        logging.info(f"  [V20] Tombol Allow di popup halaman diklik ({clicked}x)")
    return clicked or 0


def _start_chrome_allow_popup_watcher(driver, duration: float = 90,
                                      interval: float = 1.0):
    """
    FIX V20: background watcher selama fase download .sap di Chrome
    (trigger Quick Menu → insecure download → tunggu file .sap).

    Popup "Allow" baru (JS alert / dialog dalam halaman) muncul tepat
    sebelum download dimulai dan MENGHALANGI download sampai diklik —
    sebelumnya tidak ada yang mengkliknya, wait_for_sap_file menunggu
    50 detik lalu proses gagal.

    Watcher ini tiap `interval` detik:
      1. accept JS alert/confirm di window aktif
      2. klik tombol Allow di dialog in-page
      3. kalau ada >1 window/tab, cek keduanya lalu kembalikan fokus ke
         window semula (supaya konteks main thread tidak berubah)
    Berhenti cepat begitu file .sap muncul (download sudah mulai) atau
    event stop di-set oleh caller.

    Return threading.Event — caller set() untuk menghentikan.
    """
    stop_evt = threading.Event()
    dl_dir = CONFIG["DOWNLOAD_DIR"]

    def _sap_file_exists():
        try:
            return any(f.stat().st_size > 0 for f in dl_dir.glob("*.sap"))
        except Exception:
            return False

    def _watch():
        end = time.time() + duration
        while time.time() < end and not stop_evt.is_set():
            try:
                if _sap_file_exists():
                    logging.info("  [V20] File .sap terdeteksi — Allow watcher selesai")
                    return
                if not _serp_is_driver_alive(driver):
                    logging.warning("  [V20] ChromeDriver sudah berhenti — Allow watcher dihentikan")
                    return
                with _DRIVER_CTX_LOCK:
                    # 1+2) window aktif: alert + dialog in-page
                    accept_js_alert_if_present(driver)
                    _click_allow_button_in_page(driver)
                    # 3) window/tab lain (mis. SSO dibuka window.open)
                    try:
                        handles = driver.window_handles
                        if len(handles) > 1:
                            current = driver.current_window_handle
                            for h in handles:
                                if h == current:
                                    continue
                                try:
                                    driver.switch_to.window(h)
                                except Exception:
                                    continue
                                accept_js_alert_if_present(driver)
                                _click_allow_button_in_page(driver)
                            try:
                                driver.switch_to.window(current)
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception as e:
                logging.debug(f"  [V20] allow watcher: {e}")
            time.sleep(interval)

    t = threading.Thread(target=_watch, name="ChromeAllowWatcher", daemon=True)
    t.start()
    stop_evt._watcher_thread = t
    logging.info(
        f"  [V20] Chrome Allow-popup watcher aktif ({int(duration)}s, "
        f"interval={interval}s)"
    )
    return stop_evt


def _stop_chrome_allow_popup_watcher(stop_evt, timeout: float = 3.0):
    """Stop watcher dan tunggu thread keluar sebelum driver dipakai fase lain."""
    if stop_evt is None:
        return
    stop_evt.set()
    thread = getattr(stop_evt, "_watcher_thread", None)
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=timeout)
        if thread.is_alive():
            logging.warning("  Chrome Allow watcher belum berhenti setelah timeout")


def wait_for_sap_file(before_files: set, timeout: int = 40) -> Path:
    """
    Tunggu file .sap baru di download dir atau Windows Downloads.

    Deteksi file baru dengan DUA cara:
    1. File yang belum ada sebelum trigger (current - before_files)
    2. File apapun yang mtime-nya lebih baru dari waktu fungsi dipanggil
       → ini handle kasus file lama di-replace dengan nama sama
    """
    start_time = time.time()
    deadline   = start_time + timeout

    # Snapshot mtime semua file yang sudah ada sebelum trigger
    before_mtime = {}
    for f in before_files:
        try:
            before_mtime[f] = f.stat().st_mtime
        except Exception:
            before_mtime[f] = 0

    logging.info(f"  Menunggu file .sap baru (timeout {timeout}s)...")
    logging.info(f"  File .sap sebelum trigger: {[f.name for f in before_files]}")

    while time.time() < deadline:
        # Cek di DOWNLOAD_DIR
        for f in CONFIG['DOWNLOAD_DIR'].glob('*.sap'):
            try:
                if f.stat().st_size == 0:
                    continue
                mtime = f.stat().st_mtime
                # File baru = tidak ada sebelumnya, ATAU mtime lebih baru dari start
                if f not in before_mtime or mtime > start_time:
                    logging.info(f"  File .sap ditemukan: {f.name} (mtime={mtime:.0f})")
                    return f
            except Exception:
                pass

        # Cek di Windows Downloads folder
        win_dl = Path.home() / 'Downloads'
        if win_dl.exists():
            for f in win_dl.glob('*.sap'):
                try:
                    if f.stat().st_mtime > start_time and f.stat().st_size > 0:
                        dest = CONFIG['DOWNLOAD_DIR'] / f.name
                        try:
                            f.rename(dest)
                            logging.info(f"  File .sap dipindah dari Downloads: {dest.name}")
                            return dest
                        except Exception:
                            return f
                except Exception:
                    pass

        elapsed = int(time.time() - start_time)
        if elapsed % 10 == 0:
            logging.info(f"  Masih menunggu file .sap... ({elapsed}s/{timeout}s)")
        time.sleep(1)

    logging.error(f"  Timeout {timeout}s — file .sap tidak ditemukan")
    return None


# ── Buka file .sap → SAP GUI Desktop ─────────────────────────────────────────

def open_sap_file(sap_file: Path) -> bool:
    """
    Buka file .sap → SAP GUI terbuka otomatis via SSO.

    PENTING: File .sap HARUS dibuka via os.startfile (double-click simulasi),
    BUKAN lewat subprocess sapgui.exe <path> — cara itu menyebabkan error
    "Cannot split connection string" di SAP GUI 740.

    Login SAP sudah di-handle lewat SSO S-Portal (browser), sehingga
    saat file .sap dibuka, SAP langsung masuk tanpa perlu login manual.
    """
    try:
        logging.info(f"  Membuka: {sap_file.name}")
        # os.startfile = ekuivalen double-click di Explorer
        # Ini satu-satunya cara yang benar untuk membuka .sap dengan SSO
        os.startfile(str(sap_file))
        logging.info("  SAP GUI dibuka via os.startfile (SSO)")

        # FIX V30: no-op refresh — guard persisten sudah aktif sejak awal run;
        # panggilan ini hanya memastikan guard hidup bila entry point lain
        # (test / pemanggilan langsung) belum menjalankan run_automation().
        _start_sap_popup_guard(duration=30, interval=1.0)
        logging.info("  Menunggu SAP GUI startup / SSO selesai...")
        deadline = time.time() + 30
        sap_ready = False

        while time.time() < deadline:
            try:
                import win32com.client
                sap_gui = win32com.client.GetObject("SAPGUI")
                sap_app = sap_gui.GetScriptingEngine
                for ci in range(sap_app.Children.Count):
                    conn = sap_app.Children(ci)
                    for si in range(conn.Children.Count):
                        sess = conn.Children(si)
                        title = str(sess.findById("wnd[0]").Text or "")
                        if title:
                            logging.info(f"  SAP siap: {title}")
                            sap_ready = True
                            break
                    if sap_ready:
                        break
                if sap_ready:
                    break
            except Exception:
                pass

            _auto_handle_sap_security_popup(timeout=1.5, minimize_after=False)
            time.sleep(1)

        # Jangan minimize sebelum tcode. SAP GUI 740 kadang gagal eksekusi OK-code
        # jika Easy Access langsung diminimize setelah SSO. Minimize dilakukan
        # setelah ZMMR3190 benar-benar terbuka.
        if sap_ready:
            _restore_all_sap_windows()

        if not sap_ready:
            logging.warning("  SAP COM session belum terdeteksi, lanjut coba attach di step berikutnya")

        return True
    except Exception as e:
        logging.error(f"  Gagal buka .sap: {e}")
        return False


# Cache hwnd dialog #32770 (non-SAP-Logon, tanpa tombol Allow/Yes/OK) yang
# sudah pernah di-dump sekali ke log DEBUG — supaya popup guard yang polling
# terus-menerus tidak membanjiri log dengan dump yang sama berulang kali.
_SEEN_UNKNOWN_SAP_DIALOGS = set()


def _dismiss_sap_security_popup_win32() -> bool:
    """
    Dismiss popup SAP GUI Security menggunakan Win32 API murni — tanpa pywinauto/UIA.

    Kenapa Win32-only:
    - pywinauto Desktop(backend="uia") adalah blocking call yang acquire COM apartment lock.
    - Background guard thread (_start_sap_popup_guard) juga memanggil fungsi ini terus-menerus.
    - Jika main thread dan guard thread keduanya pakai UIA bersamaan → deadlock/hang.
    - Win32 EnumWindows + EnumChildWindows tidak acquire UIA lock → aman dipanggil
      dari thread manapun secara bersamaan.

    Cara kerja:
    1. EnumWindows: scan semua top-level window milik proses SAP
    2. Cek class window '#32770' (dialog modal standar Windows) + title 'SAP Logon'
       → main window SAP Easy Access memakai class lain (bukan #32770)
    3. EnumChildWindows: cari tombol OK/Allow di dalam dialog
    4. PostMessage + SendMessage BM_CLICK ke HWND tombol
    """
    import ctypes

    user32   = ctypes.windll.user32
    BM_CLICK = 0x00F5

    SAP_PROCS    = {'saplogon.exe', 'sapgui.exe', 'saplgpad.exe',
                    # FIX V29: sapshcut.exe bisa menjadi host window session
                    # SAP GUI saat koneksi dibuka lewat file .sap shortcut.
                    'sapshcut.exe'}
    OK_TEXTS     = {
        'ok', '&ok', 'allow', '&allow', 'yes', '&yes', 'lanjut', 'izinkan',
        'continue', '&continue', 'allow access', 'allow once', 'allow always',
        'temporarily allow access', 'always allow', 'allow this session',
        # FIX V20: varian tombol Allow tambahan (popup baru yang dilaporkan
        # user sebelum Excel download dimulai)
        'ya', '&ya', 'accept', '&accept', 'grant', 'permit',
        'allow all', '&allow all', 'izinkan semua', 'boleh',
    }
    DIALOG_CLASS = '#32770'  # class dialog modal standar Windows

    found_and_clicked = []
    seen_unknown_dialogs = _SEEN_UNKNOWN_SAP_DIALOGS

    def _get_window_text(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ''
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _get_class_name(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _dump_children(dialog_hwnd):
        """Kumpulkan (class, text) semua child control — untuk diagnosa popup tak dikenal."""
        items = []

        def child_cb(hwnd, _):
            try:
                items.append((_get_class_name(hwnd), _get_window_text(hwnd)))
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumChildWindows(dialog_hwnd, WNDENUMPROC(child_cb), 0)
        return items

    def _find_ok_button(dialog_hwnd):
        """Cari hwnd tombol Allow/Yes/OK di dalam dialog (read-only, tanpa klik)."""
        found = []

        def child_cb(hwnd, _):
            txt = _get_window_text(hwnd).strip().lower()
            if txt in OK_TEXTS:
                found.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumChildWindows(dialog_hwnd, WNDENUMPROC(child_cb), 0)
        return found[0] if found else None

    def _send_click_sync(hwnd) -> None:
        """
        FIX V30: klik tombol secara SINKRON tapi tahan-hang.

        Window SAP yang sedang modal / thread message-pump-nya sibuk bisa
        membuat SendMessageW biasa menggantungkan thread guard sampai SAP
        merespons. Kirim lewat SendMessageTimeoutW dengan flag
        SMTO_ABORTIFHUNG (timeout 500ms) — kalau SAP tidak merespons dalam
        500ms, panggilan balik dan guard lanjut hidup (scan berikutnya akan
        mencoba klik lagi). Fallback ke SendMessageW bila API timeout tidak
        tersedia (stub di test / Windows sangat lama).

        Dipakai sebagai konfirmasi sinkron SETELAH PostMessageW async.
        """
        SMTO_ABORTIFHUNG = 0x0002
        try:
            res      = ctypes.c_ulong()
            send_tmo = getattr(user32, 'SendMessageTimeoutW', None)
            if send_tmo is not None:
                send_tmo(hwnd, BM_CLICK, 0, 0, SMTO_ABORTIFHUNG, 500,
                         ctypes.byref(res))
                return
        except Exception:
            pass
        try:
            user32.SendMessageW(hwnd, BM_CLICK, 0, 0)
        except Exception:
            pass

    def _click_ok_in_dialog(dialog_hwnd, allow_enter_fallback=True):
        hwnd_btn = _find_ok_button(dialog_hwnd)

        if hwnd_btn is not None:
            user32.PostMessageW(hwnd_btn, BM_CLICK, 0, 0)
            time.sleep(0.05)
            user32.SendMessageW(hwnd_btn, BM_CLICK, 0, 0)
            return True

        if not allow_enter_fallback:
            return False

        # Fallback: Enter langsung ke dialog jika tidak ada tombol OK ditemukan
        WM_KEYDOWN = 0x0100
        WM_KEYUP   = 0x0101
        VK_RETURN  = 0x0D
        user32.PostMessageW(dialog_hwnd, WM_KEYDOWN, VK_RETURN, 0)
        user32.PostMessageW(dialog_hwnd, WM_KEYUP,   VK_RETURN, 0)
        return True

    def enum_cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True

            title      = _get_window_text(hwnd).strip()
            class_name = _get_class_name(hwnd)
            title_low  = title.lower()

            # FIX V29: dialog "SAP GUI Security" (Allow/Deny/Help) dicocokkan
            # lewat TITLE, bukan hanya class '#32770' — dialog security versi
            # SAP GUI baru tidak selalu ber-class #32770 dan itulah salah satu
            # penyebab popup Allow tidak pernah terklik oleh guard lama.
            is_std_dialog   = class_name == DIALOG_CLASS
            is_security_dlg = 'sap gui security' in title_low

            # Main window SAP Easy Access memakai class 'SapWiMainWindow' /
            # bukan #32770 dan title-nya bukan 'SAP GUI Security' → aman di-skip.
            if not is_std_dialog and not is_security_dlg:
                return True

            # Pastikan milik proses SAP
            pid_ref = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ref))
            try:
                proc_name = (psutil.Process(int(pid_ref.value)).name() or '').lower()
            except Exception:
                proc_name = ''

            # FIX V29: proses sap-like apa pun diterima (prefiks 'sap' —
            # mencakup sapshcut.exe dan varian exe SAP GUI baru), dan dialog
            # ber-title 'SAP GUI Security' diproses walau nama proses gagal
            # dibaca (psutil error) — judul spesifik itu milik dialog
            # security SAP GUI, bukan window lain.
            proc_is_sap = (proc_name in SAP_PROCS) or proc_name.startswith('sap')
            if not proc_is_sap and not is_security_dlg:
                return True

            if title_low == 'sap logon':
                # Popup SAP Logon klasik (mis. saat SSO connect) — perilaku lama,
                # boleh fallback Enter kalau tombol OK/Allow tidak ditemukan.
                logging.info(
                    f"  SAP security popup (Win32): title='{title}' "
                    f"class='{class_name}' proc='{proc_name}'"
                )
                if _click_ok_in_dialog(hwnd, allow_enter_fallback=True):
                    logging.info("  SAP security popup dismissed via Win32")
                    found_and_clicked.append(hwnd)
                return True

            if is_security_dlg:
                # FIX V30 (KEPUTUSAN USER 2026-09-15: "Remember My Decision
                # tidak membuat pop up hilang, saya mau dia bisa mendeteksi
                # pop up SAP jika muncul dan selalu klik Allow"):
                # dialog "SAP GUI Security" — popup Allow/Deny/Help yang muncul
                # saat SAP GUI menyimpan Excel ke folder download.
                #   1. Checkbox "Remember My Decision" SENGAJA TIDAK dicentang
                #      — popup dibiarkan tetap muncul (bukan tujuan kita
                #      menghilangkannya); guard cukup klik Allow SETIAP kali
                #      popup ini muncul, di run mana pun, berkali-kali pun.
                #   2. Setelah klik Allow, dialog diverifikasi benar-benar
                #      tertutup; kalau masih terlihat → klik ulang (maks 3x
                #      per scan). Scan berikutnya dari guard persisten akan
                #      mencoba lagi — jadi Allow SELALU diusahakan terklik.
                #   3. TIDAK ada fallback Enter di sini — tombol default dialog
                #      bisa saja 'Deny'.
                children    = _dump_children(hwnd)
                child_texts = [t for _, t in children if t.strip()]
                logging.info(
                    f"  [V30] SAP GUI Security popup terdeteksi: title='{title}' "
                    f"class='{class_name}' proc='{proc_name}' children={child_texts}"
                )
                ok_btn = _find_ok_button(hwnd)
                if ok_btn is not None:
                    still_visible = True
                    for attempt in range(1, 4):
                        user32.PostMessageW(ok_btn, BM_CLICK, 0, 0)
                        time.sleep(0.15)
                        _send_click_sync(ok_btn)
                        time.sleep(0.15)
                        try:
                            still_visible = bool(user32.IsWindowVisible(hwnd))
                        except Exception:
                            still_visible = False
                        if not still_visible:
                            break
                        logging.info(
                            f"  [V30] Dialog masih terlihat — klik Allow ulang "
                            f"(percobaan {attempt}/3)"
                        )
                    if still_visible:
                        logging.warning(
                            "  [V30] SAP GUI Security masih terlihat setelah 3x "
                            "klik Allow — guard persisten akan terus mencoba di "
                            "scan berikutnya"
                        )
                    else:
                        logging.info(
                            "  [V30] SAP GUI Security → tombol Allow diklik via "
                            "Win32 (dialog tertutup; popup boleh muncul lagi — "
                            "akan di-klik Allow setiap kali)"
                        )
                    found_and_clicked.append(hwnd)
                    seen_unknown_dialogs.discard(hwnd)
                else:
                    # Tombol Allow tidak terdeteksi sebagai child window standar
                    # — dump lengkap ke log (WARNING) supaya bisa didiagnosis
                    # jarak jauh dari file log user.
                    logging.warning(
                        "  [V30] Tombol Allow tidak ditemukan di SAP GUI Security "
                        f"popup — children={child_texts}"
                    )
                return True

            # Dialog #32770 lain milik proses SAP dengan title BUKAN 'SAP Logon'.
            # Cek dulu (read-only, murah) apakah ada tombol Allow/Yes/OK.
            ok_btn = _find_ok_button(hwnd)

            if ok_btn is not None:
                # Ini popup yang perlu ditindaklanjuti — mis. SAP GUI Scripting
                # Security ("A script is attempting to connect/access ...")
                # atau dialog informasi setelah Generate selesai. Dump + log
                # di INFO karena ini kejadian penting & jarang berulang.
                children    = _dump_children(hwnd)
                child_texts = [t for _, t in children if t.strip()]
                logging.info(
                    f"  SAP popup lain terdeteksi (Win32): title='{title}' "
                    f"class='{class_name}' proc='{proc_name}' children={child_texts}"
                )
                user32.PostMessageW(ok_btn, BM_CLICK, 0, 0)
                time.sleep(0.05)
                user32.SendMessageW(ok_btn, BM_CLICK, 0, 0)
                logging.info(f"  SAP popup '{title}' di-Allow/OK via Win32")
                found_and_clicked.append(hwnd)
                seen_unknown_dialogs.discard(hwnd)
            else:
                # Kemungkinan ini dialog SAP yang MEMANG sedang dipakai script
                # via SAP GUI Scripting (mis. dialog Save/Generate ZMMR3190),
                # dan akan terus terlihat selama beberapa puluh detik. Tidak
                # ada tombol Allow/Yes/OK untuk diklik di sini, jadi cukup
                # di-dump SEKALI per hwnd di level DEBUG — supaya tidak
                # membanjiri log setiap kali popup guard polling.
                if hwnd not in seen_unknown_dialogs:
                    seen_unknown_dialogs.add(hwnd)
                    children    = _dump_children(hwnd)
                    child_texts = [t for _, t in children if t.strip()]
                    logging.debug(
                        f"  SAP dialog #32770 lain (tanpa tombol Allow/Yes/OK, "
                        f"di-skip): title='{title}' children={child_texts}"
                    )

        except Exception as e:
            logging.debug(f"  _dismiss_sap_security_popup_win32 cb: {e}")
        return True

    try:
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception as e:
        logging.debug(f"  _dismiss_sap_security_popup_win32: {e}")

    return bool(found_and_clicked)


def _auto_handle_sap_security_popup(timeout: int = 20, minimize_after: bool = True) -> bool:
    """
    Wrapper backward-compatible untuk _dismiss_sap_security_popup_win32.

    Implementasi lama memakai pywinauto UIA yang menyebabkan deadlock antar thread
    (main thread + background guard thread keduanya acquire UIA COM lock bersamaan).
    Sekarang delegasi ke Win32-only implementation yang sepenuhnya thread-safe.

    FIX V30: loop ini sekarang MENGURAS (drain) popup yang muncul BERUNTUN
    — setelah satu popup berhasil di-dismiss, scan langsung diulang sampai
    tidak ada lagi yang terlihat (atau deadline). Popup SAP GUI Security bisa
    muncul 2-3x berturut-turut saat satu folder di-grant, dan tiap popup
    harus di-klik Allow.
    """
    deadline      = time.time() + timeout
    dismissed_any = False
    while time.time() < deadline:
        if _dismiss_sap_security_popup_win32():
            dismissed_any = True
            # beri waktu dialog menutup / popup berikutnya muncul, lalu scan lagi
            time.sleep(0.2)
            continue
        break
    if dismissed_any and minimize_after and CONFIG['SAP_MINIMIZE']:
        _minimize_all_sap_windows()
    return dismissed_any


# ── FIX V30: guard popup SAP PERSISTEN (always-on) ───────────────────────────
# KEPUTUSAN USER (2026-09-15): "Remember My Decision tidak membuat pop up
# hilang, saya mau dia bisa mendeteksi pop up SAP jika muncul dan selalu klik
# Allow" — jadi guard TIDAK lagi dibatasi durasi (8-600 detik versi lama
# EXPIRED di tengah run panjang All Prod ID dan popup yang muncul setelahnya
# tidak pernah terklik). Sekarang SATU thread daemon jalan TERUS selama
# automation berjalan: mendeteksi popup SAP setiap kali muncul dan SELALU
# mengklik Allow. Guard dihentikan hanya di akhir run_automation().

_POPUP_GUARD_THREAD  = None    # thread guard persisten (singleton)
_POPUP_GUARD_STOP    = None    # threading.Event utk menghentikan guard
_POPUP_GUARD_LOCK    = threading.Lock()
_POPUP_GUARD_INTERVAL = 0.5    # jeda antar scan (detik) — responsif, Win32 murah


def _start_sap_popup_guard(duration: int = None, interval: float = None):
    """
    FIX V30: jalankan watcher popup SAP security sebagai guard PERSISTEN.

    Perilaku baru (V30):
    - SATU thread daemon singleton, jalan SELAMA automation berjalan — tanpa
      expiry. Semua panggilan _start_sap_popup_guard(duration=..., ...)
      lama (di open_sap_file / enter_transaction / execute / handle_save_dialog
      / navigate_and_download / close_sap_after_success) kini cuma memastikan
      guard hidup → no-op refresh kalau guard persisten sudah aktif.
    - Param `duration` dan `interval` DIIABAIKAN (dipertahankan di signature
      demi kompatibilitas pemanggil lama); guard memakai _POPUP_GUARD_INTERVAL.
    - Setiap siklus: kuras semua popup SAP yang terlihat (drain) — klik Allow
      pada tiap popup, termasuk yang muncul BERUNTUN.
    - Dihentikan lewat _stop_sap_popup_guard() di akhir run_automation()
      (daemon thread juga ikut mati saat proses exit).

    PENTING: Guard ini TIDAK minimize SAP window secara paksa di background.
    Minimize agresif dari thread ini menyebabkan COM session object menjadi
    disconnected/stale saat main thread sedang melakukan COM call — error:
    "The object invoked has disconnected from its clients."
    Minimize hanya dilakukan oleh main thread setelah COM call selesai.
    """
    global _POPUP_GUARD_THREAD, _POPUP_GUARD_STOP
    with _POPUP_GUARD_LOCK:
        t = _POPUP_GUARD_THREAD
        if t is not None and t.is_alive():
            # Guard persisten sudah aktif — semua fase (browser, .sap, SAP GUI,
            # execute, save, upload, close) sudah tercakup. No-op refresh.
            logging.debug("  [V30] SAP popup guard persisten sudah aktif (refresh no-op)")
            return t

        stop = threading.Event()

        def _guard():
            while not stop.is_set():
                try:
                    # Kuras semua popup SAP yang terlihat — klik Allow di tiap
                    # popup, termasuk yang muncul beruntun. Win32-only → aman
                    # dipanggil bersamaan dengan COM call di main thread.
                    _auto_handle_sap_security_popup(timeout=1.0, minimize_after=False)
                except Exception:
                    pass
                stop.wait(_POPUP_GUARD_INTERVAL)

        t = threading.Thread(target=_guard, name="SAPPopupGuard", daemon=True)
        _POPUP_GUARD_STOP   = stop
        _POPUP_GUARD_THREAD = t
        t.start()
        logging.info(
            f"  [V30] SAP popup guard PERSISTEN aktif (interval={_POPUP_GUARD_INTERVAL}s, "
            "tanpa expiry) — deteksi + klik Allow SETIAP kali popup SAP muncul"
        )
        return t


def _stop_sap_popup_guard(timeout: float = 2.0):
    """
    FIX V30: hentikan guard popup persisten dengan rapi.

    Dipanggil di akhir run_automation() (finally) supaya guard tidak ikut
    polling selamanya saat automation selesai tapi proses (GUI app / worker)
    masih hidup. Aman dipanggil berkali-kali; guard bisa di-start ulang
    setelahnya (run berikutnya / VPN retry loop baru).
    """
    global _POPUP_GUARD_THREAD, _POPUP_GUARD_STOP
    with _POPUP_GUARD_LOCK:
        t    = _POPUP_GUARD_THREAD
        stop = _POPUP_GUARD_STOP
        if stop is not None:
            stop.set()
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
            logging.info("  [V30] SAP popup guard persisten dihentikan (run selesai)")
        _POPUP_GUARD_THREAD = None
        _POPUP_GUARD_STOP   = None


def _restore_all_sap_windows():
    """
    Restore SAP main window via Win32 API.

    Jangan restore SAP Logon 740. Window launcher ini bisa membuat SetForegroundWindow
    block lama dan menahan alur sebelum OK-code dikirim.
    """
    try:
        import ctypes
        SW_RESTORE = 9
        user32     = ctypes.windll.user32

        def _callback(hwnd, lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True

                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                title_low = title.lower()

                pid_ref = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ref))
                proc_name = ''
                try:
                    proc_name = (psutil.Process(int(pid_ref.value)).name() or '').lower()
                except Exception:
                    pass

                is_sap_process = proc_name in {'saplogon.exe', 'sapgui.exe', 'saplgpad.exe'}
                is_sap_main_window = (
                    is_sap_process
                    and 'sap logon' not in title_low
                    and (
                        'easy access' in title_low
                        or 'serveone area menu' in title_low
                        or 'zmmr' in title_low
                        or 'download excel' in title_low
                    )
                )

                if is_sap_main_window:
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.BringWindowToTop(hwnd)
                    # SetForegroundWindow kadang hang/ditolak Windows jika dipanggil ke
                    # SAP Logon/launcher. Untuk main SAP cukup best-effort.
                    try:
                        user32.SetForegroundWindow(hwnd)
                    except Exception:
                        pass
                    logging.info(f"  SAP main window restored: '{title}'")
                    time.sleep(0.1)
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(_callback), 0)

    except Exception as e:
        logging.warning(f"  _restore_all_sap_windows: {e}")


def _minimize_all_sap_windows():
    """
    Minimize semua window SAP via Win32 API.
    Bekerja tanpa import pywinauto — lebih ringan.
    """
    try:
        import ctypes
        SW_MINIMIZE = 6
        user32      = ctypes.windll.user32

        def _callback(hwnd, lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    # Hanya minimize window SAP asli. Jangan minimize Chrome/Outlook/PDF
                    # walaupun judulnya mengandung "Serveone".
                    title_low = title.lower()
                    pid_ref = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ref))
                    proc_name = ''
                    try:
                        proc_name = (psutil.Process(int(pid_ref.value)).name() or '').lower()
                    except Exception:
                        pass

                    is_sap_process = proc_name in {'saplogon.exe', 'sapgui.exe', 'saplgpad.exe'}
                    is_sap_title = (
                        title_low.startswith('sap ')
                        or title_low.startswith('sapgui')
                        or title_low.startswith('sap gui')
                        or 'sap logon' in title_low
                        or 'easy access' in title_low
                        or 'zmmr' in title_low
                    )

                    if (
                        is_sap_process
                        and is_sap_title
                        and 'gdi+ window' not in title_low
                        and 'graphics multiplexer' not in title_low
                        and not any(skip in title_low for skip in [
                            'google chrome', 'microsoft edge', 'firefox', 'outlook',
                            'acrobat', '.pdf', '.png', '.jpg', '.jpeg', 'visual studio code',
                            'whatsapp image', 'debug_serp'
                        ])
                    ):
                        user32.ShowWindow(hwnd, SW_MINIMIZE)
                        logging.info(f"  SAP window minimized: '{title}'")
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(_callback), 0)

    except Exception as e:
        logging.warning(f"  _minimize_all_sap_windows: {e}")


# ── SAP GUI via pywinauto UIA ─────────────────────────────────────────────────

class SapGuiAutomation:
    """
    Kontrol SAP GUI Desktop via Windows UI Automation (UIA).
    Menggunakan AutomationId, Name, ControlType — bukan koordinat.

    Cara kerja dengan SAP minimize:
    - Alur utama memakai SAP GUI Scripting COM, sehingga tidak perlu fokus/mouse/keyboard
    - SAP window tetap minimized/background saat SAPGUI_MINIMIZE=true
    - Dialog Windows/SAP security ditangani via UIA Invoke/ValuePattern
    """

    # FIX V16.1: cache probe ketersediaan scripting di CLASS level.
    # Tanpa ini, setiap instance baru me-refresh probe negatif 3x2 detik
    # (6+ detik) dan mencetak warning "SAP GUI Scripting tidak tersedia"
    # LAGI — user melihat notifikasi itu berkali-kali sebelum tcode terkirim.
    # Dengan cache class: probe + warning tepat SATU KALI per proses.
    _scripting_available_cls = None

    def __init__(self):
        from pywinauto import Application, Desktop
        self.Desktop  = Desktop
        self.App      = Application
        self.app      = None
        self.main_win = None
        self.sap_sess = None
        # FIX V16: hasil probe SATU-KALI ketersediaan SAP GUI Scripting COM.
        #   None  = belum pernah diprobe
        #   True  = GetObject("SAPGUI") + engine.Children OK → boleh pakai COM
        #   False = scripting tidak terinstall/disabled → mode UIA penuh,
        #           JANGAN panggil _get_sap_session()/findById sama sekali
        #           (tiap call = 1 exception COM → 13x per run bikin lambat
        #           & log kotor).
        # FIX V16.1: inisialisasi dari cache class supaya instance kedua
        # (kalau ada) tidak me-refresh probe / print warning dobel.
        self.scripting_available = SapGuiAutomation._scripting_available_cls

    # ── Probe ketersediaan SAP GUI Scripting ─────────────────────────────────

    @staticmethod
    def _sap_scripting_mode() -> str:
        """FIX V18: mode SAP GUI Scripting untuk All Product ID, dibaca dari
        env SAP_SCRIPTING_MODE (bisa diatur di Settings → All Product ID).

        Nilai:
          - 'scripting' (alias: on/yes/true/1/com) → PAKSAKAN jalur COM.
            All Product ID kembali memakai SAP GUI Scripting seperti
            permintaan user ("kembalikan lagi All Prod ID dengan scripting").
          - 'uia' (alias: off/no/false/0)         → paksa mode UI Automation.
          - 'auto' (default)                      → deteksi otomatis via probe.
        """
        raw = (os.getenv('SAP_SCRIPTING_MODE') or 'auto').strip().lower()
        if raw in ('scripting', 'on', 'yes', 'true', '1', 'com', 'sap'):
            return 'scripting'
        if raw in ('uia', 'ui', 'off', 'no', 'false', '0', 'none'):
            return 'uia'
        return 'auto'

    def _probe_scripting(self) -> bool:
        """
        Probe SATU KALI (hasil di-cache) apakah SAP GUI Scripting COM tersedia.

        FIX V18 — mengembalikan All Product ID ke jalur SAP GUI Scripting:
        1. SAP_SCRIPTING_MODE dari Settings:
             'uia'       → langsung False (paksa UI Automation).
             'scripting' → jalur COM dipaksakan: kalau engine bisa diakses
                           (walau session belum naik) → True. Kalau engine
                           benar-benar tidak bisa diakses → fallback UIA
                           dgn satu warning jelas (graceful, tidak error).
             'auto'      → deteksi (default).
        2. FIX BUG V16/V17 (false-negative permanen): probe lama
           meng-cache False kalau dalam 3x2 detik pertama session SAP belum
           naik (SAP masih SSO/login setelah file .sap dibuka). Padahal
           scripting TERPASANG. Akibatnya seluruh run dipaksa mode UIA
           padahal COM tersedia — inilah kenapa All Product ID "kehilangan"
           scripting-nya. Sekarang: engine bisa diakses tapi session belum
           ada → dianggap TERSEDIA (True); session-nya akan ditunggu oleh
           find_sap_window()/ _get_sap_session() sampai timeout.
        3. False hanya dikembalikan kalau GetObject("SAPGUI") /
           GetScriptingEngine benar-benar raise (scripting tidak terinstall
           atau disabled) — di mesin begitu, mode UIA tetap dipakai seperti
           V16 supaya flow tetap jalan.
        """
        if self.scripting_available is not None:
            return self.scripting_available

        mode = self._sap_scripting_mode()
        if mode == 'uia':
            self.scripting_available = False
            SapGuiAutomation._scripting_available_cls = False
            logging.info(
                "  SAP GUI Scripting DIMATIKAN dari pengaturan "
                "(SAP_SCRIPTING_MODE=uia) — mode UI Automation"
            )
            return False

        forced = (mode == 'scripting')
        if forced:
            logging.info(
                "  Mode SAP GUI Scripting DIPAKSAKAN dari pengaturan "
                "(SAP_SCRIPTING_MODE=scripting) — All Product ID pakai jalur COM"
            )

        last_err = None
        engine_ok_no_session = False
        for attempt in range(1, 4):
            try:
                import win32com.client
                sap_gui = win32com.client.GetObject("SAPGUI")
                engine = sap_gui.GetScriptingEngine
                # Engine harus punya minimal 1 connection/session — kalau
                # 0, scripting "terpasang" tapi session SAP belum naik
                # (SAP masih SSO/login). JANGAN cache False (bug V16/V17) —
                # tandai engine OK, session akan ditunggu pemanggil.
                if int(engine.Children.Count) > 0:
                    self.scripting_available = True
                    SapGuiAutomation._scripting_available_cls = True
                    logging.info("  SAP GUI Scripting tersedia (mode COM)")
                    return True
                engine_ok_no_session = True
                last_err = RuntimeError(
                    "GetScriptingEngine bisa diakses tapi session SAP belum aktif "
                    "(SAP masih startup/login SSO)"
                )
            except Exception as e:
                last_err = e
            if attempt < 3:
                time.sleep(2)

        # Engine terakses walau session belum ada → scripting TERPASANG.
        # Anggap tersedia; find_sap_window() akan menunggu session COM
        # sampai timeout-nya (bukan langsung lompat ke mode UIA).
        if engine_ok_no_session:
            self.scripting_available = True
            SapGuiAutomation._scripting_available_cls = True
            logging.info(
                "  SAP GUI Scripting terpasang (engine OK); session SAP belum "
                "siap — ditunggu di langkah berikutnya (mode COM)"
            )
            return True

        self.scripting_available = False
        SapGuiAutomation._scripting_available_cls = False
        if forced:
            logging.warning(
                f"  SAP_SCRIPTING_MODE=scripting tapi SAP GUI Scripting COM tidak "
                f"bisa diakses ({last_err}). Fallback: mode UI Automation. "
                "Pastikan SAP GUI Scripting diaktifkan di SAP GUI Options "
                "(Accessibility & Scripting → Scripting → Enable) dan di server."
            )
        else:
            logging.warning(
                f"  SAP GUI Scripting tidak tersedia ({last_err}) — "
                "pakai mode UI Automation penuh (non-COM). "
                "Notifikasi ini hanya muncul sekali per proses. "
                "Aktifkan SAP_SCRIPTING_MODE=scripting di Settings kalau "
                "scripting seharusnya tersedia."
            )
        return False

    # ── Window management ─────────────────────────────────────────────────────

    def _win32_show(self, cmd: int):
        """Kirim ShowWindow command ke SAP window handle via Win32 API."""
        try:
            import ctypes
            if self.main_win:
                ctypes.windll.user32.ShowWindow(self.main_win.handle, cmd)
                ctypes.windll.user32.SetForegroundWindow(self.main_win.handle)
                time.sleep(0.3)
        except Exception as e:
            logging.debug(f"  _win32_show({cmd}): {e}")

    def _restore(self):
        """
        Restore window SAP hanya jika SAP_MINIMIZE=False.

        Alur utama memakai SAP GUI Scripting COM sehingga tidak perlu focus/keyboard/mouse.
        Ini menjaga SAP tetap minimized/background saat SAP_MINIMIZE=True.
        """
        if CONFIG['SAP_MINIMIZE']:
            _auto_handle_sap_security_popup(timeout=1, minimize_after=True)
            return

        import ctypes
        SW_RESTORE = 9
        try:
            if self.main_win:
                hwnd = self.main_win.handle
                user32 = ctypes.windll.user32
                _auto_handle_sap_security_popup(timeout=1, minimize_after=False)
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                _auto_handle_sap_security_popup(timeout=1, minimize_after=False)
                time.sleep(0.3)
        except Exception as e:
            logging.debug(f"  _restore: {e}")
            self._win32_show(SW_RESTORE)

    def _minimize(self):
        """Minimize window SAP kembali setelah interaksi selesai."""
        if not CONFIG['SAP_MINIMIZE']:
            return
        SW_MINIMIZE = 6
        self._win32_show(SW_MINIMIZE)
        logging.debug("  SAP window minimized")

    def _uia_foreground_sap(self):
        """
        FIX V16: Restore + foreground window SAP via Win32 (mode UIA, tanpa COM).

        Dipakai jalur non-scripting (pyautogui/clipboard paste) yang butuh
        window SAP benar-benar menerima input keyboard/mouse. Pattern yang
        sama dengan yang sebelumnya diduplikasi di Strategy C/D lama.
        """
        import ctypes
        try:
            if self.main_win:
                hwnd = self.main_win.handle
                user32 = ctypes.windll.user32
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                user32.BringWindowToTop(hwnd)
                try:
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                time.sleep(0.5)
            else:
                _restore_all_sap_windows()
                time.sleep(0.5)
        except Exception as e:
            logging.debug(f"  _uia_foreground_sap: {e}")

    # ── Cari & attach ke window SAP ───────────────────────────────────────────

    def find_sap_window(self, timeout: int = 60) -> bool:
        """
        Tunggu SAP siap.

        Prioritas utama adalah SAP GUI Scripting COM karena bisa bekerja saat SAP minimized.
        UIA window scan hanya fallback ringan; scanning semua descendants/window terlalu lambat
        dan sebelumnya membuat flow tertahan lama di SAP Easy Access.
        """
        logging.info("  Menunggu SAP GUI session siap...")
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                sess = self._get_sap_session()
                title = str(sess.findById("wnd[0]").Text or "")
                logging.info(f"  SAP session ditemukan via COM: '{title}'")
                return True
            except Exception as e:
                logging.debug(f"  SAP COM belum siap: {e}")

            try:
                desktop = self.Desktop(backend="uia")
                for w in desktop.windows():
                    try:
                        title = w.window_text() or ''
                        title_low = title.lower()
                        class_name = (w.class_name() or '').lower()
                        if ('sap' in title_low or 'easy access' in title_low) and 'shell_traywnd' not in class_name:
                            self.app = self.App(backend="uia").connect(handle=w.handle)
                            self.main_win = self.app.window(handle=w.handle)
                            logging.info(f"  Window SAP ditemukan via UIA: '{title}'")
                            # Jangan return dulu jika COM belum siap; tunggu sebentar agar tcode bisa dikirim.
                            break
                    except Exception:
                        pass
            except Exception:
                pass

            # FIX V16: window SAP sudah ter-attach via UIA dan probe
            # mengonfirmasi SAP GUI Scripting TIDAK tersedia → tidak ada
            # gunanya menunggu COM sampai timeout. Mode UIA penuh cukup
            # (enter_transaction/fill_register_date/execute punya jalur
            # non-COM). Tanpa fix ini, find_sap_window burn timeout penuh
            # (45-60 detik) lalu return False → flow gagal sebelum
            # enter_transaction sempat dipanggil.
            #
            # FIX V18: probe sekarang hanya False kalau scripting benar-benar
            # TIDAK terpasang. Kalau scripting terpasang tapi session belum
            # naik, probe = True → loop terus menunggu session COM (bukan
            # lompat ke UIA) → All Product ID kembali ke jalur scripting.
            if self.main_win is not None and not self._probe_scripting():
                logging.info("  Mode UI Automation penuh: lanjut tanpa SAP GUI Scripting")
                return True

            _auto_handle_sap_security_popup(timeout=0.5, minimize_after=False)
            time.sleep(1)

        # FIX V18: timeout tercapai. Kalau window SAP sudah ter-attach via UIA,
        # jangan langsung gagal — lanjut dgn mode UIA (enter_transaction /
        # fill_register_date / execute punya jalur non-COM). Kalau window-nya
        # pun tidak ada, baru return False.
        if self.main_win is not None:
            logging.warning(
                f"  Session COM tidak muncul dalam {timeout} detik — lanjut mode "
                "UI Automation (window SAP ter-attach)"
            )
            return True

        logging.error(f"  SAP session tidak siap dalam {timeout} detik")
        return False

    def refresh_active_window(self):
        """Re-attach ke window SAP yang aktif (setelah navigate tcode)."""
        try:
            desktop = self.Desktop(backend="uia")
            for w in desktop.windows():
                try:
                    title = w.window_text()
                    if any(kw in title for kw in ['SAP', 'ZMMR', 'Download Excel', 'Product']):
                        self.app      = self.App(backend="uia").connect(handle=w.handle)
                        self.main_win = self.app.window(handle=w.handle)
                        logging.info(f"  Window aktif: '{title}'")
                        return True
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"  refresh_active_window: {e}")
        return False

    # ── SAP GUI Scripting COM helpers ──────────────────────────────────────────

    def _get_sap_session(self):
        """
        Ambil SAP GUI Scripting session aktif.

        PENTING: Session TIDAK di-cache (sap_sess selalu None).
        COM object yang di-cache bisa menjadi stale/disconnected jika SAP
        window di-minimize atau ada jeda antar COM call — menyebabkan error:
        "The object invoked has disconnected from its clients."

        Solusi: selalu ambil session baru dari GetObject("SAPGUI") setiap kali.
        Overhead-nya kecil (<50ms) tapi jauh lebih reliable.
        """
        import win32com.client
        sap_gui = win32com.client.GetObject("SAPGUI")
        sap_app = sap_gui.GetScriptingEngine

        for ci in range(sap_app.Children.Count):
            conn = sap_app.Children(ci)
            for si in range(conn.Children.Count):
                sess = conn.Children(si)
                try:
                    title = sess.findById("wnd[0]").Text
                    logging.debug(f"  SAP session: '{title}'")
                except Exception:
                    pass
                return sess

        raise RuntimeError("SAP GUI Scripting session tidak ditemukan")

    def _sap_set_text(self, candidates: list, value: str, label: str) -> bool:
        """Set Text pada field SAP GUI Scripting dari daftar ID kandidat."""
        sess = self._get_sap_session()
        for field_id in candidates:
            try:
                ctrl = sess.findById(field_id)
                ctrl.Text = value
                logging.info(f"    {label}: {field_id} = {value}")
                return True
            except Exception:
                pass
        return False

    # ── Ketik transaction code ────────────────────────────────────────────────

    def enter_transaction(self, tcode: str) -> bool:
        """
        Kirim tcode ke SAP GUI Desktop.

        FIX V16: di komputer yang TIDAK menginstall SAP GUI Scripting,
        _get_sap_session() raise exception dari awal. Implementasi lama
        memanggil _get_sap_session() di baris pertama attempt-loop →
        exception langsung lompat ke except terluar → Strategy C
        (typewrite) dan D (clipboard paste) yang ada di dalam try-block
        yang sama TIDAK PERNAH tereksekusi (dead code).

        Strategi (V16):
        0. Probe scripting SATU KALI: self._probe_scripting().
        1. Scripting tersedia → Strategi A (OK-code via COM) dan
           B (StartTransaction) seperti biasa, verifikasi navigasi via COM.
        2. Scripting TIDAK tersedia (atau A/B belum memicu navigasi) →
           Strategi C: clipboard paste via UIA (Ctrl+A → Ctrl+V → Enter)
           — JALUR UTAMA mode non-COM karena paste atomic (tidak bisa
           kepotong walau fokus berpindah sesaat).
        3. Fallback terakhir: Strategi D pyautogui typewrite per karakter.
        4. Verifikasi navigasi: via COM (scripting tersedia) atau via judul
           window UIA (scripting tidak tersedia) — BUKAN sess.findById
           yang ikut raise di mode non-COM.
        """
        tcode_nav = f'/n{tcode}'
        logging.info(f"  Entering T-code {tcode}...")
        _restore_all_sap_windows()
        # FIX V30: panggilan ini kini no-op refresh (guard persisten aktif
        # sepanjang run; scan-nya Win32-only — bukan UIA — jadi tidak bentrok
        # dengan COM call tcode). Pemanggilan tetap dipertahankan sebagai
        # pengaman bila fungsi ini dipakai di luar alur run_automation().
        if CONFIG['SAP_MINIMIZE']:
            _start_sap_popup_guard(duration=90, interval=0.2)
        last_error = None

        scripting = self._probe_scripting()

        # ── Verifikasi navigasi via COM (hanya dipakai saat scripting tersedia) ──
        def _title(sess):
            try:
                return str(sess.findById("wnd[0]").Text or "")
            except Exception:
                return ""

        def _looks_navigated_com(sess):
            title = _title(sess)
            title_low = title.lower()
            if tcode.lower() in title_low or "download excel" in title_low or "mass" in title_low:
                logging.info(f"    SAP screen verified: '{title}'")
                return True
            # "Serveone Area Menu" adalah bagian dari Easy Access — jangan dianggap sudah navigasi
            still_on_menu = (
                "easy access" in title_low
                or "sap menu" in title_low
                or "serveone area menu" in title_low
                or not title
            )
            if not still_on_menu:
                logging.info(f"    SAP screen changed: '{title}'")
                return True
            return False

        # ── Verifikasi navigasi via UIA window title (tanpa COM) ──
        # FIX V16: judul window SAP berubah setelah navigasi tcode (window
        # sama re-title ATAU report terbuka sebagai window baru). Scan SEMUA
        # window desktop yang terlihat SAP-ish, bukan cuma main_win, supaya
        # window report baru juga terdeteksi.
        def _looks_navigated_uia():
            titles = []
            try:
                if self.main_win:
                    t = str(self.main_win.window_text() or "")
                    if t:
                        titles.append(t)
            except Exception:
                pass
            try:
                for w in self.Desktop(backend="uia").windows():
                    try:
                        t = str(w.window_text() or "")
                        if not t:
                            continue
                        tl = t.lower()
                        cl = (w.class_name() or "").lower()
                        if 'shell_traywnd' in cl:
                            continue
                        if any(kw in tl for kw in ('sap', 'zmmr', 'download excel', 'product', 'mass')):
                            titles.append(t)
                    except Exception:
                        continue
            except Exception:
                pass
            for title in titles:
                title_low = title.lower()
                if tcode.lower() in title_low or "download excel" in title_low or "mass" in title_low:
                    logging.info(f"    SAP screen verified (UIA): '{title}'")
                    return True
                still_on_menu = (
                    "easy access" in title_low
                    or "sap menu" in title_low
                    or "serveone area menu" in title_low
                    or not title
                )
                if not still_on_menu:
                    logging.info(f"    SAP screen changed (UIA): '{title}'")
                    return True
            return False

        def _check_navigated():
            """Verifikasi gabungan: COM dulu (kalau tersedia), lalu UIA."""
            if scripting:
                try:
                    sess = self._get_sap_session()
                    if _looks_navigated_com(sess):
                        return True
                except Exception as com_err:
                    logging.debug(f"    cek navigasi COM gagal: {com_err}")
            return _looks_navigated_uia()

        for attempt in range(1, 4):
            try:
                # Dismiss popup security SEBELUM kirim tcode — popup bisa
                # mengambil fokus dan membuat SetFocus/sendVKey gagal.
                for _ in range(3):
                    dismissed = _auto_handle_sap_security_popup(timeout=2, minimize_after=False)
                    if not dismissed:
                        break
                    time.sleep(0.3)

                # ── Jalur COM: hanya kalau scripting tersedia ─────────────
                if scripting:
                    sess = self._get_sap_session()
                    before_title = _title(sess)
                    logging.info(f"    T-code attempt {attempt}: current='{before_title}'")

                    # Strategi A: OK-code via COM + verifikasi teks benar-benar masuk
                    try:
                        okcd = sess.findById("wnd[0]/tbar[0]/okcd")
                        okcd.SetFocus()
                        okcd.Text = ""
                        time.sleep(0.2)
                        okcd.Text = tcode_nav
                        # VERIFIKASI: cek apakah teks benar-benar tertulis
                        # Jika SAP Scripting disabled di server, Text akan tetap ""
                        actual_text = str(okcd.Text or "").strip()
                        logging.info(f"    OK-code set: '{tcode_nav}' → actual: '{actual_text}'")
                        if actual_text.lower() == tcode_nav.lower():
                            _auto_handle_sap_security_popup(timeout=2, minimize_after=False)
                            wnd = sess.findById("wnd[0]")
                            wnd.SetFocus()
                            wnd.sendVKey(0)
                            logging.info("    Tcode via OK-code + Enter dikirim")
                        else:
                            logging.warning(
                                f"    OK-code tidak tertulis (SAP Scripting mungkin disabled di server) "
                                f"— actual: '{actual_text}'"
                            )
                    except Exception as okcd_err:
                        last_error = okcd_err
                        logging.warning(f"    OK-code COM gagal: {okcd_err}")

                    # Cek navigasi setelah strategi A
                    time.sleep(1)
                    if _check_navigated():
                        self._minimize()
                        self.refresh_active_window()
                        return True

                    # Strategi B: StartTransaction
                    try:
                        _auto_handle_sap_security_popup(timeout=3, minimize_after=False)
                        sess = self._get_sap_session()
                        sess.StartTransaction(tcode)
                        logging.info("    Tcode via SAP StartTransaction() dikirim")
                        time.sleep(1.5)
                        if _check_navigated():
                            self._minimize()
                            self.refresh_active_window()
                            return True
                    except Exception as st_err:
                        logging.warning(f"    StartTransaction gagal: {st_err}")
                else:
                    logging.info(
                        f"    T-code attempt {attempt} (mode UI Automation, tanpa COM)"
                    )

                # ── Strategi C: clipboard paste via UIA ────────────────────
                # JALUR UTAMA saat scripting tidak tersedia; fallback saat
                # scripting tersedia tapi A/B belum memicu navigasi.
                # Paste atomic — tidak bisa kepotong di tengah walau fokus
                # berpindah (mis. popup security muncul sesaat).
                if not scripting:
                    logging.info("    Clipboard paste (jalur utama mode non-COM)...")
                try:
                    import pyautogui
                    import pyperclip

                    self._uia_foreground_sap()

                    # Klik OK-code box via pywinauto child_window (lebih reliable dari koordinat)
                    clicked_okcd = False
                    try:
                        okcd_ctrl = self.main_win.child_window(
                            auto_id="okcd", control_type="Edit"
                        )
                        okcd_ctrl.click_input()
                        clicked_okcd = True
                        logging.info("    Klik OK-code box via pywinauto child_window")
                    except Exception:
                        pass

                    if not clicked_okcd:
                        # Koordinat approx OK-code box SAP Easy Access (kiri atas toolbar)
                        pyautogui.click(150, 91)
                        logging.info("    Klik OK-code box via koordinat pyautogui")
                        time.sleep(0.3)

                    # Select all + PASTE (bukan typewrite) + enter
                    pyautogui.hotkey('ctrl', 'a')
                    time.sleep(0.15)
                    pyperclip.copy(tcode_nav)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.4)
                    pyautogui.press('enter')
                    logging.info(f"    Tcode via clipboard paste: '{tcode_nav}'")
                    time.sleep(2)

                    if _check_navigated():
                        if CONFIG['SAP_MINIMIZE']:
                            _minimize_all_sap_windows()
                        self.refresh_active_window()
                        return True

                except Exception as paste_err:
                    last_error = paste_err
                    logging.warning(f"    Clipboard paste gagal: {paste_err}")

                # ── Strategi D: pyautogui typewrite (fallback terakhir) ────
                # Typewrite mengetik per karakter — bisa kepotong kalau fokus
                # berpindah, tapi tetap dipertahankan sebagai variasi terakhir
                # kalau clipboard paste tidak memicu navigasi.
                try:
                    import pyautogui

                    self._uia_foreground_sap()

                    clicked_okcd = False
                    try:
                        okcd_ctrl = self.main_win.child_window(
                            auto_id="okcd", control_type="Edit"
                        )
                        okcd_ctrl.click_input()
                        clicked_okcd = True
                    except Exception:
                        pass
                    if not clicked_okcd:
                        pyautogui.click(150, 91)
                        time.sleep(0.3)

                    pyautogui.hotkey('ctrl', 'a')
                    time.sleep(0.1)
                    pyautogui.typewrite(tcode_nav, interval=0.05)
                    time.sleep(0.3)
                    pyautogui.press('enter')
                    logging.info(f"    Tcode via pyautogui keyboard: '{tcode_nav}'")
                    time.sleep(2)

                    if _check_navigated():
                        if CONFIG['SAP_MINIMIZE']:
                            _minimize_all_sap_windows()
                        self.refresh_active_window()
                        return True

                except Exception as kb_err:
                    last_error = kb_err
                    logging.warning(f"    pyautogui keyboard gagal: {kb_err}")

                # Cek verifikasi akhir sebelum retry attempt berikutnya
                for _ in range(6):
                    _auto_handle_sap_security_popup(timeout=0.3, minimize_after=False)
                    time.sleep(0.5)
                    if _check_navigated():
                        self._minimize()
                        self.refresh_active_window()
                        return True

                logging.warning(f"    T-code belum trigger (attempt {attempt})")

            except Exception as e:
                last_error = e
                logging.warning(f"    enter_transaction attempt {attempt} gagal: {e}")
                time.sleep(1)

        logging.error(f"  enter_transaction gagal: T-code {tcode} tidak membuka report ({last_error})")
        save_debug_screenshot("sap_enter_tcode_failed")
        return False

    # ── Utility: cari Edit control ────────────────────────────────────────────

    # ── Isi Register Date ─────────────────────────────────────────────────────

    def fill_register_date(self, date_from_str: str, date_to_str: str) -> bool:
        """
        Isi Register Date FROM dan TO.

        FIX V16: dua jalur —
        - SAP GUI Scripting tersedia → isi via COM (tanpa focus/keyboard,
          SAP bisa tetap minimized). Jika ID field berbeda, scan semua
          GuiCTextField/GuiTextField dan pilih pasangan field tanggal pertama.
        - Scripting TIDAK tersedia → jalur UIA (_fill_register_date_uia):
          lookup Edit control via auto_id (SAP mengekspos ID scripting
          sebagai AutomationId), isi via click + Ctrl+A + clipboard paste.
          Fallback: scan descendants(control_type="Edit") diurutkan by posisi.
        """
        logging.info(f"  Isi Register Date: {date_from_str} s/d {date_to_str}")
        if self._probe_scripting():
            try:
                return self._fill_register_date_com(date_from_str, date_to_str)
            except Exception as e:
                logging.error(f"  fill_register_date gagal via SAP GUI Scripting COM: {e}")
                save_debug_screenshot("sap_fill_date_failed")
                # Jangan langsung menyerah — session COM bisa saja disconnect
                # walau scripting tersedia; coba jalur UIA.
                logging.info("  Mencoba jalur UI Automation untuk isi tanggal...")
                if self._fill_register_date_uia(date_from_str, date_to_str):
                    return True
                return False
        return self._fill_register_date_uia(date_from_str, date_to_str)

    def _fill_register_date_com(self, date_from_str: str, date_to_str: str) -> bool:
        """Jalur COM (SAP GUI Scripting tersedia)."""
        sess = self._get_sap_session()
        wnd = sess.findById("wnd[0]")

        # Kandidat umum SAP selection-screen:
        # S_* / SO_* memakai -LOW dan -HIGH untuk range.
        from_candidates = [
            # S_ZREDT = field Register Date di ZMMR3190 (confirmed dari log)
            "wnd[0]/usr/ctxtS_ZREDT-LOW",
            "wnd[0]/usr/ctxtS_DATUM-LOW",
            "wnd[0]/usr/ctxtSO_DATUM-LOW",
            "wnd[0]/usr/ctxtP_DATUM-LOW",
            "wnd[0]/usr/ctxtS_DATE-LOW",
            "wnd[0]/usr/ctxtSO_DATE-LOW",
            "wnd[0]/usr/ctxtS_ERDAT-LOW",
            "wnd[0]/usr/ctxtSO_ERDAT-LOW",
            "wnd[0]/usr/ctxtS_REGDT-LOW",
            "wnd[0]/usr/ctxtSO_REGDT-LOW",
        ]
        to_candidates = [
            # S_ZREDT = field Register Date di ZMMR3190 (confirmed dari log)
            "wnd[0]/usr/ctxtS_ZREDT-HIGH",
            "wnd[0]/usr/ctxtS_DATUM-HIGH",
            "wnd[0]/usr/ctxtSO_DATUM-HIGH",
            "wnd[0]/usr/ctxtP_DATUM-HIGH",
            "wnd[0]/usr/ctxtS_DATE-HIGH",
            "wnd[0]/usr/ctxtSO_DATE-HIGH",
            "wnd[0]/usr/ctxtS_ERDAT-HIGH",
            "wnd[0]/usr/ctxtSO_ERDAT-HIGH",
            "wnd[0]/usr/ctxtS_REGDT-HIGH",
            "wnd[0]/usr/ctxtSO_REGDT-HIGH",
        ]

        from_ok = self._sap_set_text(from_candidates, date_from_str, "Register Date FROM")
        to_ok = self._sap_set_text(to_candidates, date_to_str, "Register Date TO")

        if not (from_ok and to_ok):
            logging.warning("  Field tanggal belum cocok via kandidat ID, scan field SAP...")
            date_fields = []

            def collect_date_fields(container):
                try:
                    children = container.Children
                    for i in range(children.Count):
                        child = children(i)
                        try:
                            child_type = str(child.Type)
                            child_id = str(child.Id)
                            child_name = str(getattr(child, "Name", "") or "")
                            child_text = str(getattr(child, "Text", "") or "")
                            if child_type in ("GuiCTextField", "GuiTextField"):
                                haystack = f"{child_id} {child_name}".lower()
                                looks_date = (
                                    "date" in haystack
                                    or "datum" in haystack
                                    or "erdat" in haystack
                                    or "reg" in haystack
                                    or child_text == ""
                                    or (len(child_text) == 10 and "." in child_text)
                                )
                                if looks_date:
                                    try:
                                        left = int(child.ScreenLeft)
                                        top = int(child.ScreenTop)
                                    except Exception:
                                        left = top = 0
                                    date_fields.append((top, left, child_id, child))
                            collect_date_fields(child)
                        except Exception:
                            pass
                except Exception:
                    pass

            collect_date_fields(wnd.findById("usr"))
            date_fields.sort(key=lambda item: (item[0], item[1]))

            for idx, (_, _, field_id, field) in enumerate(date_fields):
                logging.info(f"    DateField[{idx}] {field_id}")

            if not from_ok and len(date_fields) >= 1:
                date_fields[0][3].Text = date_from_str
                from_ok = True
                logging.info(f"    Register Date FROM: {date_fields[0][2]} = {date_from_str}")

            if not to_ok and len(date_fields) >= 2:
                date_fields[1][3].Text = date_to_str
                to_ok = True
                logging.info(f"    Register Date TO: {date_fields[1][2]} = {date_to_str}")

        if not (from_ok and to_ok):
            raise RuntimeError("Field Register Date FROM/TO tidak ditemukan via SAP GUI Scripting")

        logging.info("  Register Date terisi")
        # Minimize SETELAH semua COM call selesai
        self._minimize()
        return True

    def _fill_register_date_uia(self, date_from_str: str, date_to_str: str) -> bool:
        """
        FIX V16: isi Register Date via UI Automation — dipakai saat SAP GUI
        Scripting tidak tersedia.

        Lookup prioritas:
        1. child_window(auto_id=...) — SAP GUI mengekspos ID elemen scripting
           (mis. "ctxtS_ZREDT-LOW") sebagai AutomationId di tree UIA.
        2. Scan descendants(control_type="Edit") — hanya field dengan
           auto_id/nama mengandung keyword tanggal (date/datum/zredt/erdat/reg)
           ATAU value berformat tanggal (10 char ada '.'). Field kosong tanpa
           keyword TIDAK diikutkan (terlalu ambigu — bisa salah isi field lain).
           Diurutkan by posisi (top, left) → 2 field pertama = FROM/TO.

        Pengisian: click → Ctrl+A → paste (atomic) → TAB (commit value);
        fallback set_edit_text (UIA ValuePattern, tanpa perlu focus).
        Verifikasi: value field harus mengandung 8 digit (SAP bisa reformat
        urutan DD.MM.YYYY ↔ YYYY.MM.DD sesuai profil user).
        """
        try:
            import pyautogui
            import pyperclip
        except ImportError as e:
            logging.error(f"  pyautogui/pyperclip tidak tersedia untuk jalur UIA: {e}")
            return False

        if not self.main_win and not self.refresh_active_window():
            logging.error("  Window SAP tidak aktif — tidak bisa isi tanggal via UIA")
            return False

        # Window SAP harus foreground supaya click/paste sampai ke field
        self._uia_foreground_sap()

        from_auto_ids = [
            "ctxtS_ZREDT-LOW", "S_ZREDT-LOW",
            "ctxtS_DATUM-LOW", "S_DATUM-LOW",
            "ctxtSO_DATUM-LOW", "SO_DATUM-LOW",
            "ctxtS_DATE-LOW", "S_DATE-LOW",
            "ctxtSO_DATE-LOW",
            "ctxtS_ERDAT-LOW", "ctxtSO_ERDAT-LOW",
            "ctxtS_REGDT-LOW", "ctxtSO_REGDT-LOW",
        ]
        to_auto_ids = [aid.replace("-LOW", "-HIGH") for aid in from_auto_ids]

        def _find_edit(auto_ids):
            for aid in auto_ids:
                try:
                    ctrl = self.main_win.child_window(
                        auto_id=aid, control_type="Edit"
                    )
                    if ctrl.exists(timeout=0.8):
                        return ctrl, aid
                except Exception:
                    continue
            return None, None

        def _refind_by_aid(aid):
            """FIX V16.1: cari ulang control antar attempt (stale-safe).
            Kalau SAP me-render ulang layar (round-trip PAI/PBO), referensi
            UIA lama bisa stale — re-find via auto_id memberi referensi segar.
            """
            def _f():
                try:
                    c = self.main_win.child_window(auto_id=aid, control_type="Edit")
                    if c.exists(timeout=0.5):
                        return c
                except Exception:
                    pass
                return None
            return _f

        def _fill_edit(ctrl, value, label, refind=None):
            """
            FIX V16.1: isi Edit control SAP dengan verify + RETRY per field.

            Bug lama (V16): paste → sleep(0.15) → TAB. TAB terkirim saat SAP
            masih memproses paste ke date-field bermask → karakter TERAKHIR
            terpotong (bukti: TO terisi "2026.08.2", harusnya "2026.08.28").
            Tanpa retry, verify gagal (7 digit) → langsung menyerah.

            Sekarang per attempt:
              1. Pre-verify — kalau field sudah terisi benar (attempt sebelumnya
                 sukses tapi read lambat), skip paste (idempotent).
              2. click → Ctrl+A → Delete (bersihkan isi lama/parsial)
              3. Ctrl+V (paste atomic) → tunggu 0.45s → TAB (commit) → 0.45s
              4. Verify 8 digit — gagal → warning + retry (max 3x), control
                 di-re-find via `refind` supaya tidak stale.
            Fallback terakhir: set_edit_text (UIA ValuePattern).
            """
            def _read():
                try:
                    texts = ctrl.texts() or ['']
                    return str(texts[0] if texts else '')
                except Exception:
                    return ''

            def _verify():
                cur = _read()
                digits = ''.join(ch for ch in cur if ch.isdigit())
                return len(digits) == 8, cur

            for paste_attempt in range(1, 4):
                # Pre-verify idempotent — hemat 1 paste kalau sudah benar
                ok, cur = _verify()
                if ok:
                    logging.info(f"    {label} = {cur.strip()} (sudah terisi, attempt {paste_attempt})")
                    return True

                # Re-find control kalau memungkinkan (hindari stale reference)
                if refind is not None:
                    fresh = refind()
                    if fresh is not None:
                        ctrl = fresh

                try:
                    ctrl.click_input()
                    time.sleep(0.25)
                    pyautogui.hotkey('ctrl', 'a')
                    time.sleep(0.15)
                    # FIX V16.1: kosongkan field dulu supaya retry mulai
                    # bersih (sisa parsial attempt sebelumnya ikut terhapus)
                    pyautogui.press('delete')
                    time.sleep(0.15)
                    pyperclip.copy(value)
                    pyautogui.hotkey('ctrl', 'v')
                    # FIX V16.1: kasih waktu SAP memproses paste SEBELUM TAB —
                    # dulu cuma 0.15s, karakter terakhir sering terpotong.
                    time.sleep(0.45)
                    pyautogui.press('tab')   # commit value di SAP
                    # FIX V16.1: round-trip PAI/PBO SAP butuh waktu — jangan
                    # baca/isi field berikutnya saat masih in-flight.
                    time.sleep(0.45)
                except Exception as paste_err:
                    logging.debug(f"    isi {label} paste #{paste_attempt} gagal: {paste_err}")
                    time.sleep(0.4)
                    continue

                ok, cur = _verify()
                if ok:
                    logging.info(f"    {label} = {cur.strip()} (paste #{paste_attempt})")
                    return True
                logging.warning(
                    f"    Verify {label} paste #{paste_attempt}: '{cur.strip()}' belum 8 digit — retry..."
                )
                time.sleep(0.4)

            # Fallback terakhir: ValuePattern (tanpa keyboard)
            try:
                ctrl.set_edit_text(value)
                time.sleep(0.3)
                ok, cur = _verify()
                if ok:
                    logging.info(f"    {label} = {cur.strip()} (ValuePattern)")
                    return True
                logging.debug(f"    verify {label} ValuePattern: '{cur}' belum 8 digit")
            except Exception as vp_err:
                logging.debug(f"    isi {label} via ValuePattern gagal: {vp_err}")
            return False

        # ── Prioritas 1: lookup via auto_id ──
        from_ctrl, from_aid = _find_edit(from_auto_ids)
        to_ctrl, to_aid = _find_edit(to_auto_ids)

        if from_ctrl is not None and to_ctrl is not None:
            logging.info(f"    Field tanggal via UIA auto_id: {from_aid} / {to_aid}")
            ok_from = _fill_edit(
                from_ctrl, date_from_str, "Register Date FROM",
                refind=_refind_by_aid(from_aid),
            )
            # FIX V16.1: settle SEBELUM isi field TO — commit TAB di FROM
            # memicu round-trip PAI/PBO SAP; klik/paste ke TO saat round-trip
            # masih in-flight adalah penyebab utama paste TO terpotong.
            time.sleep(0.6)
            ok_to = _fill_edit(
                to_ctrl, date_to_str, "Register Date TO",
                refind=_refind_by_aid(to_aid),
            )
            if ok_from and ok_to:
                logging.info("  Register Date terisi via UIA (auto_id)")
                self._minimize()
                return True
            logging.warning("    Pengisian via auto_id tidak terverifikasi, coba scan posisi...")
        else:
            logging.info("    Field tanggal tidak ketemu via auto_id, scan Edit control...")

        # ── Prioritas 2: scan posisi (strict — hanya field yang jelas tanggal) ──
        try:
            edits = self.main_win.descendants(control_type="Edit")
        except Exception as e:
            logging.error(f"  Scan Edit control gagal: {e}")
            return False

        date_edits = []
        for ctrl in edits:
            try:
                aid = (ctrl.element_info.automation_id or '').lower()
                name = (ctrl.element_info.name or '').lower()
                texts = ctrl.texts() or ['']
                val = str(texts[0] if texts else '')
                haystack = f"{aid} {name}".lower()
                looks_date = (
                    any(k in haystack for k in ('date', 'datum', 'zredt', 'erdat', 'regdt'))
                    or (len(val) == 10 and '.' in val)
                )
                if looks_date:
                    rect = ctrl.rectangle()
                    date_edits.append((rect.top, rect.left, ctrl, aid or name))
            except Exception:
                continue

        date_edits.sort(key=lambda item: (item[0], item[1]))
        for idx, item in enumerate(date_edits[:6]):
            logging.info(f"    DateField(UIA)[{idx}] {item[3]} @({item[0]},{item[1]})")

        if len(date_edits) >= 2:
            ok_from = _fill_edit(date_edits[0][2], date_from_str, "Register Date FROM")
            # FIX V16.1: settle antar field (round-trip SAP) — sama seperti jalur auto_id
            time.sleep(0.6)
            ok_to   = _fill_edit(date_edits[1][2], date_to_str, "Register Date TO")
            if ok_from and ok_to:
                logging.info("  Register Date terisi via UIA (scan posisi)")
                self._minimize()
                return True
            logging.error("  Verifikasi field tanggal via UIA gagal (value tidak terbaca 8 digit)")
            save_debug_screenshot("sap_fill_date_uia_failed")
            return False

        logging.error("  Field Register Date FROM/TO tidak ditemukan via UIA (butuh minimal 2 field tanggal)")
        save_debug_screenshot("sap_fill_date_uia_failed")
        return False

    # ── Execute (F8) ──────────────────────────────────────────────────────────

    def execute(self) -> bool:
        """
        Execute report (F8).

        FIX V16: dua jalur —
        - SAP GUI Scripting tersedia → sendVKey(8) via COM (window SAP tidak
          perlu foreground).
        - Scripting TIDAK tersedia → kirim F8 via pywinauto type_keys
          (lebih reliable, tidak butuh OS focus ketat) dengan fallback
          pyautogui.press('f8') setelah window SAP di-foreground-kan.

        Tunggu 5 detik setelah execute — SAP perlu waktu query ke DB
        sebelum dialog Save muncul. Minimize SETELAH execute selesai.
        """
        logging.info("  Execute (F8)...")
        # FIX V20: guard dipanggil TEPAT sebelum F8 — popup Allow muncul persis
        # di fase ini (setelah Execute, sebelum Excel digenerate).
        # FIX V30: kini no-op refresh — guard persisten sudah aktif sejak awal
        # run, jadi celah "guard expired" versi lama tidak ada lagi.
        _start_sap_popup_guard(duration=150, interval=1.0)
        if self._probe_scripting():
            try:
                sess = self._get_sap_session()
                sess.findById("wnd[0]").sendVKey(8)
                logging.info("  Execute via SAP GUI Scripting sendVKey(8)")
                # Tunggu SAP query selesai, dialog Save akan muncul
                time.sleep(5)
                self._minimize()
                return True
            except Exception as e:
                logging.warning(f"  Execute via COM gagal: {e} — fallback UIA...")

        # ── Jalur UIA (tanpa COM) ──
        if not self.main_win and not self.refresh_active_window():
            logging.error("  execute gagal: window SAP tidak aktif")
            return False
        # F8 harus benar-benar sampai ke window SAP
        self._uia_foreground_sap()
        try:
            sent = False
            try:
                self.main_win.set_focus()
                time.sleep(0.2)
                self.main_win.type_keys('{F8}')
                sent = True
                logging.info("  Execute via pywinauto type_keys('{F8}')")
            except Exception as tk_err:
                logging.debug(f"  type_keys F8 gagal: {tk_err}")
                import pyautogui
                pyautogui.press('f8')
                sent = True
                logging.info("  Execute via pyautogui.press('f8')")
            # Tunggu SAP query selesai, dialog Save akan muncul
            time.sleep(5)
            self._minimize()
            return sent
        except Exception as e:
            logging.error(f"  execute gagal (UIA): {e}")
            return False

    # ── Tangani dialog Save As ────────────────────────────────────────────────

    def _try_handle_sap_filename_screen(self, save_path: str) -> bool:
        """
        Setelah Execute, SAP kadang tidak membuka dialog Windows "Save As",
        tetapi menampilkan layar/dialog SAP sendiri untuk input nama file.
        Tangani via SAP GUI Scripting COM tanpa mouse/keyboard.

        FIX V16: kalau SAP GUI Scripting tidak tersedia, pakai jalur UIA
        (_try_handle_sap_filename_screen_uia) — SAP GUI mengekspos elemen
        scripting (ctxtDY_PATH dll.) sebagai AutomationId di tree UIA,
        jadi dialog tetap bisa ditemukan & diisi tanpa COM.
        """
        if not self._probe_scripting():
            return self._try_handle_sap_filename_screen_uia(save_path)
        try:
            sess = self._get_sap_session()
        except Exception:
            return False

        candidate_roots = ["wnd[1]", "wnd[0]"]

        def walk(container, fields, buttons):
            try:
                children = container.Children
                for i in range(children.Count):
                    child = children(i)
                    try:
                        child_type = str(child.Type)
                        child_id = str(child.Id)
                        child_text = str(getattr(child, "Text", "") or "")
                        child_name = str(getattr(child, "Name", "") or "")
                        haystack = f"{child_id} {child_name} {child_text}".lower()

                        if child_type in ("GuiCTextField", "GuiTextField"):
                            if any(k in haystack for k in [
                                "file", "filename", "path", "directory", "local", "excel",
                                "rlgrap", "p_file", "p_path", "p_fname", "dy_path", "dy_filename"
                            ]) or child_text == "":
                                fields.append(child)

                        if child_type in ("GuiButton", "GuiToolbarButton"):
                            buttons.append(child)

                        walk(child, fields, buttons)
                    except Exception:
                        pass
            except Exception:
                pass

        for root_id in candidate_roots:
            try:
                root = sess.findById(root_id)
                root_title = str(getattr(root, "Text", "") or "")
                fields = []
                buttons = []
                walk(root, fields, buttons)

                if not fields:
                    continue

                logging.info(f"  SAP filename screen terdeteksi: {root_id} '{root_title}'")
                for idx, field in enumerate(fields):
                    try:
                        logging.info(f"    SAPFileField[{idx}] {field.Id} text='{getattr(field, 'Text', '')}'")
                    except Exception:
                        pass

                # Deteksi DY_PATH + DY_FILENAME terpisah (kasus ZMMR3190) vs single field
                path_field     = None
                filename_field = None
                single_field   = None

                for field in fields:
                    fid = str(field.Id).lower()
                    if "dy_path" in fid:
                        path_field = field
                    elif "dy_filename" in fid:
                        filename_field = field
                    elif any(k in fid for k in ["p_file", "p_path", "p_fname", "filename", "path"]):
                        single_field = field

                if path_field and filename_field:
                    # SAP dialog dengan DY_PATH + DY_FILENAME terpisah
                    from pathlib import Path as _Path
                    sp        = _Path(save_path)
                    dir_part  = str(sp.parent) + '\\'
                    file_part = sp.name
                    path_field.Text     = dir_part
                    filename_field.Text = file_part
                    logging.info(f"    DY_PATH     = {dir_part}")
                    logging.info(f"    DY_FILENAME = {file_part}")
                elif single_field:
                    single_field.Text = save_path
                    logging.info(f"    Single field = {save_path}")
                else:
                    fields[-1].Text = save_path
                    logging.info(f"    Fallback field = {save_path}")

                # Dismiss popup security sebelum konfirmasi
                _dismiss_sap_security_popup_win32()
                time.sleep(0.2)

                # Dump semua tombol yang ditemukan untuk debugging
                for idx_b, btn in enumerate(buttons):
                    try:
                        txt_d = str(getattr(btn, "Text",    "") or "").strip()
                        tip_d = str(getattr(btn, "Tooltip", "") or "").strip()
                        bid_d = str(getattr(btn, "Id",      "") or "").strip()
                        logging.info(f"    Button[{idx_b}] id='{bid_d}' txt='{txt_d}' tip='{tip_d}'")
                    except Exception as e:
                        logging.info(f"    Button[{idx_b}] dump error: {e}")

                # Cari tombol Generate/OK/Save
                # Tombol Generate di dialog SAP ZMMR3190 biasanya id btn[8] atau tooltip "Generate"
                pressed = False
                for btn in buttons:
                    try:
                        txt = str(getattr(btn, "Text",    "") or "").strip().lower()
                        tip = str(getattr(btn, "Tooltip", "") or "").strip().lower()
                        bid = str(getattr(btn, "Id",      "") or "").lower()
                        if (
                            txt in ("ok", "save", "simpan", "continue", "lanjut",
                                    "execute", "generate", "transfer")
                            or "generate" in tip
                            or "execute"  in tip
                            or "transfer" in tip
                            or "save"     in tip
                            or "btn[0]"  in bid
                            or "btn[8]"  in bid
                            or "btn[11]" in bid
                        ):
                            _dismiss_sap_security_popup_win32()
                            btn.Press()
                            logging.info(f"    SAP tombol ditekan: txt='{txt}' tip='{tip}' id='{bid}'")
                            time.sleep(2)
                            pressed = True
                            return True
                    except Exception:
                        pass

                if not pressed:
                    # Fallback: tekan Enter via sendVKey — ini ekuivalen klik Generate
                    # di dialog SAP yang defaultnya Enter = tombol pertama (Generate)
                    _dismiss_sap_security_popup_win32()
                    root.sendVKey(0)
                    logging.info("    SAP filename dikonfirmasi via sendVKey(0)")
                    time.sleep(2)
                    return True

            except Exception:
                pass

        return False

    def _try_handle_sap_filename_screen_uia(self, save_path: str) -> bool:
        """
        FIX V16: tangani layar filename SAP (DY_PATH/DY_FILENAME / P_FILE)
        via UIA — dipakai saat SAP GUI Scripting tidak tersedia.

        Cara kerja:
        - Scan top-level window desktop (backend UIA) yang judulnya terlihat
          seperti dialog/report SAP (sap/zmmr/generate/download excel/product/
          mass/export/save) dan BUKAN window main SAP yang sedang aktif
          (dialog SAP muncul sebagai window modal terpisah).
        - Dalam window kandidat, cari Edit control dengan AutomationId yang
          cocok pola field file (dy_path/dy_filename/p_file/p_path/p_fname/
          filename/rlgrap) — SAP GUI mengekspos ID scripting sebagai
          AutomationId di tree UIA.
        - Isi field: set_edit_text (ValuePattern, tanpa focus) dengan fallback
          click + Ctrl+A + paste. Deteksi DY_PATH+DY_FILENAME terpisah vs
          single field — sama dengan logika versi COM.
        - Klik tombol Generate/OK/Save via descendants(control_type="Button")
          yang cocok teks/tooltip; fallback tekan Enter ke dialog (Enter di
          dialog SAP = tombol default = Generate).

        Catatan: dialog "Save As" Windows native TIDAK ditangani di sini —
        itu sudah ditangani handle_save_dialog() Strategi B via UIA. Method
        ini hanya untuk layar filename DI DALAM window SAP.
        """
        from pathlib import Path as _Path

        FILE_FIELD_PATTERNS = ('dy_path', 'dy_filename', 'p_file', 'p_path',
                               'p_fname', 'filename', 'rlgrap')
        DIALOG_TITLE_KWS = ('sap', 'zmmr', 'generate', 'download excel',
                            'product', 'mass', 'export', 'save')
        SKIP_TITLE_KWS = ('google chrome', 'microsoft edge', 'firefox',
                          'outlook', 'acrobat', 'visual studio code',
                          'taskbar', 'claude', 'whatsapp', 'notepad')

        try:
            windows = self.Desktop(backend="uia").windows()
        except Exception as e:
            logging.debug(f"  UIA dialog scan gagal: {e}")
            return False

        main_hwnd = None
        try:
            if self.main_win:
                main_hwnd = self.main_win.handle
        except Exception:
            main_hwnd = None

        for w in windows:
            try:
                title = str(w.window_text() or '')
                title_low = title.lower()
                if not title:
                    continue
                class_low = (w.class_name() or '').lower()
                if 'shell_traywnd' in class_low:
                    continue
                if any(s in title_low for s in SKIP_TITLE_KWS):
                    continue
                if not any(k in title_low for k in DIALOG_TITLE_KWS):
                    continue
                # Lewati window main SAP — dialog filename muncul sebagai
                # window modal TERPISAH (setara wnd[1] di scripting).
                if main_hwnd is not None and w.handle == main_hwnd:
                    continue

                app = self.App(backend="uia").connect(handle=w.handle)
                win = app.window(handle=w.handle)

                try:
                    edits = win.descendants(control_type="Edit")
                except Exception:
                    continue
                if not edits:
                    continue

                # Filter field yang relevan dengan filename/path
                file_edits = []
                for ed in edits:
                    try:
                        aid = (ed.element_info.automation_id or '').lower()
                        if any(p in aid for p in FILE_FIELD_PATTERNS):
                            file_edits.append((aid, ed))
                    except Exception:
                        continue
                if not file_edits:
                    continue

                logging.info(f"  SAP filename screen terdeteksi via UIA: '{title}'")
                for aid, ed in file_edits:
                    logging.info(f"    SAPFileField(UIA) auto_id='{aid}'")

                path_field = None
                filename_field = None
                single_field = None
                for aid, ed in file_edits:
                    if 'dy_path' in aid:
                        path_field = ed
                    elif 'dy_filename' in aid:
                        filename_field = ed
                    elif single_field is None:
                        single_field = ed

                def _fill(ed, value):
                    """Isi Edit: ValuePattern dulu (tanpa focus), fallback paste."""
                    try:
                        ed.set_edit_text(value)
                        return True
                    except Exception:
                        try:
                            import pyautogui
                            import pyperclip
                            ed.click_input()
                            time.sleep(0.15)
                            pyautogui.hotkey('ctrl', 'a')
                            time.sleep(0.1)
                            pyperclip.copy(value)
                            pyautogui.hotkey('ctrl', 'v')
                            time.sleep(0.15)
                            return True
                        except Exception:
                            return False

                if path_field is not None and filename_field is not None:
                    # SAP dialog dengan DY_PATH + DY_FILENAME terpisah (kasus ZMMR3190)
                    sp = _Path(save_path)
                    dir_part = str(sp.parent) + '\\'
                    file_part = sp.name
                    if _fill(path_field, dir_part) and _fill(filename_field, file_part):
                        logging.info(f"    DY_PATH     = {dir_part}")
                        logging.info(f"    DY_FILENAME = {file_part}")
                    else:
                        continue
                elif single_field is not None:
                    if not _fill(single_field, save_path):
                        continue
                    logging.info(f"    Single field = {save_path}")
                else:
                    if not _fill(file_edits[-1][1], save_path):
                        continue
                    logging.info(f"    Fallback field = {save_path}")

                # Dismiss popup security sebelum konfirmasi
                _dismiss_sap_security_popup_win32()
                time.sleep(0.2)

                # Cari tombol Generate/OK/Save via UIA
                pressed = False
                try:
                    for btn in win.descendants(control_type="Button"):
                        try:
                            name = (btn.window_text() or '').strip().lower()
                            tip = ''
                            try:
                                tip = (btn.element_info.help_text or '').strip().lower()
                            except Exception:
                                pass
                            if (name in ('ok', 'save', 'simpan', 'continue', 'lanjut',
                                         'execute', 'generate', 'transfer')
                                    or 'generate' in tip or 'execute' in tip
                                    or 'save' in tip or 'transfer' in tip):
                                try:
                                    btn.click_input()
                                except Exception:
                                    btn.iface_invoke.Invoke()
                                logging.info(f"    SAP tombol ditekan (UIA): '{name}'")
                                pressed = True
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

                if not pressed:
                    # Fallback: tekan Enter ke dialog — ekuivalen klik Generate
                    # di dialog SAP yang defaultnya Enter = tombol pertama.
                    try:
                        import pyautogui
                        win.set_focus()
                        time.sleep(0.2)
                        pyautogui.press('enter')
                        logging.info("    SAP filename dikonfirmasi via Enter (UIA)")
                    except Exception:
                        continue
                time.sleep(2)
                return True
            except Exception:
                continue

        return False

    def handle_save_dialog(self, filename: str) -> Path:
        """
        Tangani dialog simpan file setelah Execute.

        ZMMR3190 menghasilkan dua kemungkinan dialog:
        A) SAP native dialog (wnd[1]) — field path di dalam SAP window
        B) Windows Save As dialog — dialog standar Windows

        Urutan penanganan:
        1. Coba SAP COM (wnd[1]/wnd[0]) dulu — paling reliable, tanpa keyboard
        2. Jika tidak ada, tunggu Windows Save As dialog via UIA
        3. Konfirmasi overwrite jika ada
        4. Minimize SAP setelah semua selesai
        """
        save_path = str(CONFIG['DOWNLOAD_DIR'] / filename)
        logging.info(f"  Menunggu dialog Save setelah Execute → {save_path}")

        # FIX V20: guard dipanggil SELAMA menunggu dialog Save (60 detik).
        # FIX V30: kini no-op refresh — guard persisten sudah aktif sejak awal
        # run dan tetap aktif sampai run selesai (upload & close SAP ikut
        # tercakup), menutup celah popup Allow yang muncul di antara fase.
        _start_sap_popup_guard(duration=90, interval=1.0)

        deadline     = time.time() + 60
        dialog_found = False

        while time.time() < deadline:
            # ── Strategi A: SAP native dialog via COM ─────────────────────────
            if self._try_handle_sap_filename_screen(save_path):
                logging.info("  Dialog Save via SAP COM — OK")
                dialog_found = True
                break

            # ── Strategi B: Windows Save As dialog via UIA ────────────────────
            SAVE_TITLES = ['save as', 'simpan sebagai', 'speichern unter',
                           'save file', 'export', 'open', 'file download']
            SKIP_TITLES = ['google chrome', 'microsoft edge', 'firefox',
                           'outlook', 'acrobat', 'visual studio code', 'taskbar',
                           'claude', 'whatsapp']

            try:
                desktop = self.Desktop(backend="uia")
                for w in desktop.windows():
                    try:
                        title     = w.window_text() or ''
                        title_low = title.lower()

                        # Skip aplikasi non-SAP
                        if any(s in title_low for s in SKIP_TITLES):
                            continue

                        # Harus mengandung kata kunci Save
                        if not any(kw in title_low for kw in SAVE_TITLES):
                            continue

                        logging.info(f"  Windows Save As dialog: '{title}'")
                        dlg_app = self.App(backend="uia").connect(handle=w.handle)
                        dlg_win = dlg_app.window(handle=w.handle)

                        # Cari filename field
                        filename_field = None
                        for auto_id in ['1148', '1001', 'FileNameControlHost', 'FILENAME']:
                            try:
                                f = dlg_win.child_window(auto_id=auto_id, control_type="Edit")
                                if f.exists() and f.is_visible():
                                    filename_field = f
                                    logging.info(f"    Filename field AutoId='{auto_id}'")
                                    break
                            except Exception:
                                pass

                        if not filename_field:
                            edits = dlg_win.descendants(control_type="Edit")
                            if edits:
                                filename_field = edits[-1]
                                logging.info("    Filename field fallback: Edit[-1]")

                        if not filename_field:
                            continue

                        # Set path via clipboard (paling reliable untuk path panjang)
                        try:
                            import pyperclip
                            pyperclip.copy(save_path)
                            filename_field.set_focus()
                            time.sleep(0.1)
                            filename_field.type_keys('^a^v', with_spaces=False)
                        except Exception:
                            try:
                                filename_field.set_edit_text(save_path)
                            except Exception:
                                filename_field.iface_value.SetValue(save_path)
                        logging.info(f"    Path: {save_path}")
                        time.sleep(0.3)

                        # Klik Save/OK
                        save_clicked = False
                        for btn in dlg_win.descendants(control_type="Button"):
                            try:
                                name = (btn.window_text() or '').strip().lower()
                                if any(k in name for k in ['save', 'simpan', 'ok', 'open']):
                                    try:
                                        btn.iface_invoke.Invoke()
                                    except Exception:
                                        from pywinauto.controls.uia_controls import ButtonWrapper
                                        ButtonWrapper(btn.element_info).invoke()
                                    save_clicked = True
                                    logging.info(f"    Klik tombol: '{name}'")
                                    break
                            except Exception:
                                pass

                        if not save_clicked:
                            filename_field.type_keys('{ENTER}')
                            logging.info("    Fallback: Enter")

                        time.sleep(1.5)

                        # Konfirmasi overwrite
                        try:
                            for cw in self.Desktop(backend="uia").windows():
                                cw_title = (cw.window_text() or '').lower()
                                if any(k in cw_title for k in
                                       ['confirm', 'replace', 'overwrite', 'exist', 'already']):
                                    confirm_app = self.App(backend="uia").connect(handle=cw.handle)
                                    confirm_win = confirm_app.window(handle=cw.handle)
                                    for btn in confirm_win.descendants(control_type="Button"):
                                        n = (btn.window_text() or '').strip().lower()
                                        if any(k in n for k in ['yes', 'ok', 'replace']):
                                            try:
                                                btn.iface_invoke.Invoke()
                                            except Exception:
                                                from pywinauto.controls.uia_controls import ButtonWrapper
                                                ButtonWrapper(btn.element_info).invoke()
                                            logging.info(f"    Overwrite confirm: '{n}'")
                                            time.sleep(0.5)
                                            break
                        except Exception:
                            pass

                        dialog_found = True
                        break
                    except Exception as e:
                        logging.debug(f"  Windows dialog check: {e}")
            except Exception:
                pass

            if dialog_found:
                break
            time.sleep(0.5)

        if not dialog_found:
            logging.warning("  Dialog Save tidak terdeteksi dalam 60 detik")
            save_debug_screenshot("sap_no_save_dialog")

        # Minimize SAP setelah seluruh proses save selesai
        self._minimize()

        time.sleep(3)

        # FIX V29: tunggu file Excel target muncul + stabil (maks 60 detik).
        # Klik Generate/Save memicu popup "SAP GUI Security" (Allow/Deny) yang
        # MENAHAN pembuatan file sampai di-dismiss — file baru muncul beberapa
        # detik setelah guard mengklik Allow. Pemeriksaan lama (sekali saja
        # setelah sleep 3 detik) menghasilkan kegagalan palsu "File tidak
        # berhasil disimpan" padahal popup sudah teratasi.
        # Stabil = ukuran file target tidak berubah antar 2 polling (file tidak
        # sedang ditulis). Nama file memuat timestamp detik, jadi tabrakan
        # dengan file run sebelumnya praktis tidak mungkin.
        target_path   = CONFIG['DOWNLOAD_DIR'] / filename
        # Kalau dialog Save sendiri tidak pernah terdeteksi, tidak ada gunanya
        # menunggu file 60 detik (perilaku lama: langsung gagal cepat).
        deadline_wait = time.time() + (60 if dialog_found else 0)
        last_size     = -1
        while time.time() < deadline_wait:
            # Popup bisa muncul telat (setelah dialog save ditutup) — terus
            # di-dismiss selama menunggu file, jangan hanya andalkan guard.
            _dismiss_sap_security_popup_win32()
            try:
                cur_size = target_path.stat().st_size if target_path.exists() else 0
            except OSError:
                cur_size = 0
            if cur_size > 0 and cur_size == last_size:
                logging.info(
                    f"  [V29] File Excel stabil: {filename} ({cur_size} bytes)"
                )
                break
            last_size = cur_size
            time.sleep(2)

        return self._find_downloaded_file(filename)

    def _find_downloaded_file(self, filename: str) -> Path:
        """Cari file hasil download."""
        target = CONFIG['DOWNLOAD_DIR'] / filename
        if target.exists() and target.stat().st_size > 0:
            return target

        candidates = sorted(
            [f for f in CONFIG['DOWNLOAD_DIR'].glob('*.xls*') if f.stat().st_size > 0],
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if candidates:
            latest = candidates[0]
            if latest != target:
                try:
                    latest.rename(target)
                    return target
                except Exception:
                    return latest

        win_dl = Path.home() / 'Downloads'
        if win_dl.exists():
            cutoff   = time.time() - 120
            win_xlsx = sorted(
                [f for f in win_dl.glob('*.xls*') if f.stat().st_mtime > cutoff],
                key=lambda p: p.stat().st_mtime, reverse=True
            )
            if win_xlsx:
                src = win_xlsx[0]
                try:
                    src.rename(target)
                    return target
                except Exception:
                    return src

        return None


# ── Orchestrate SAP GUI ───────────────────────────────────────────────────────

def navigate_and_download_via_sap_gui() -> Path:
    try:
        from pywinauto import Application, Desktop
    except ImportError as e:
        logging.error(f"pywinauto tidak terinstall: {e}")
        logging.error("Jalankan: pip install pywinauto pywin32")
        return None

    today = datetime.now()
    # FIX V18: Date Range PER-PROSES. Kalau preset selain 'custom' dipilih
    # untuk All Product ID, range SERP mengikuti preset itu saja (dihitung
    # mundur dari hari ini) dan SERP_START_DATE/SERP_END_DATE diabaikan.
    # Kalau 'custom', perilaku lama: SERP_START_DATE/SERP_END_DATE atau
    # default (tanggal 1 bulan lalu s/d hari ini).
    preset_start, preset_end, preset_code = resolve_date_range_from_preset(today)
    if preset_start is not None:
        date_from = preset_start
        date_to = preset_end
        logging.info(f"  Date Range preset All Product ID '{preset_code}' (dari Settings, khusus proses ini)")
    else:
        date_from = parse_serp_env_date(CONFIG.get('START_DATE'), 'SERP_START_DATE') or default_serp_start_date(today)
        date_to = parse_serp_env_date(CONFIG.get('END_DATE'), 'SERP_END_DATE') or today

    if date_from.date() > date_to.date():
        logging.error("  SERP_START_DATE tidak boleh lebih besar dari SERP_END_DATE/hari ini")
        return None

    date_from_str = date_from.strftime('%Y.%m.%d')
    date_to_str = date_to.strftime('%Y.%m.%d')
    logging.info(f"  Default range All Product ID: {date_from_str} s/d {date_to_str}")

    filename      = f"{CONFIG['FILENAME_PREFIX']}_{today.strftime('%Y%m%d_%H%M%S')}.xlsx"

    # FIX V29→V30: dulu guard durasi 600s (masih bisa EXPIRED di tengah run
    # panjang); sekarang no-op refresh — guard PERSISTEN aktif sejak awal
    # run_automation() dan tidak pernah expired. Popup "SAP GUI Security"
    # (Allow/Deny) saat SAP GUI menyimpan Excel akan terdeteksi + di-klik
    # Allow SETIAP kali muncul, di fase mana pun.
    _start_sap_popup_guard(duration=600, interval=1.0)
    sap = SapGuiAutomation()

    if not sap.find_sap_window(timeout=45):
        save_debug_screenshot("sap_window_not_found")
        return None

    if not sap.enter_transaction(CONFIG['SAP_TCODE']):
        return None

    force_close_all_excel()

    if not sap.fill_register_date(date_from_str, date_to_str):
        return None

    if not sap.execute():
        return None

    result = sap.handle_save_dialog(filename)
    if not result:
        logging.error("  File tidak berhasil disimpan")
        save_debug_screenshot("sap_download_failed")
        return None

    logging.info(f"  File: {result.name} ({result.stat().st_size / 1024:.1f} KB)")
    return result


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_to_dashboard(file_path: Path, max_retries: int = None) -> bool:
    """Upload Excel ke dashboard dengan exponential backoff.
    Saat gagal, file dipindah ke failed_uploads_serp/ untuk retry di run berikutnya.
    Log messages mengandung keyword "upload ke" / "upload gagal" / "upload sukses"
    supaya GUI parser di app.py menampilkan status upload ke user.
    """
    if max_retries is None:
        max_retries = SERP_UPLOAD_MAX_RETRIES if 'SERP_UPLOAD_MAX_RETRIES' in globals() else 5
    # Exponential backoff (detik). Untuk 5xx, PythonAnywhere butuh 1-3 menit recovery.
    backoff_5xx = [15, 30, 60, 120, 180]
    backoff_429 = [30, 60, 120, 240, 300]
    backoff_other = [15, 20, 30, 60, 60]
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            # FIX: gunakan "Local Import ke SQLite:" (bukan "Upload:") supaya keyword
            # "upload ke" di GUI parser match dan user melihat "Mulai upload" di log GUI.
            file_size_kb = file_path.stat().st_size / 1024
            logging.info(f"  Local Import ke SQLite: {file_path.name} ({file_size_kb:.1f} KB, attempt {attempt}/{max_retries})")
            with open(file_path, 'rb') as f:
                # FIX V25: POST lewat net_tls — kebal proxy korporat TLS-interception.
                # Endpoint tidak berubah, tapi requests lama gagal verifikasi
                # sertifikat re-sign CA korporat (tidak ada di bundle certifi)
                # sehingga upload gagal 5x attempt lalu popup Output Gagal.
                # FIX V35: sinyal sync (?sync=1 + header X-Sync-Upload: 1) —
                # forward-compatible; dashboard lama mengabaikan (tetap 202 queued),
                # dashboard baru memproses langsung sebelum membalas respons.
                _base = str(CONFIG['DASHBOARD_API'])
                from _paths import assert_local_dashboard_api
                assert_local_dashboard_api(_base)
                _sep = '&' if '?' in _base else '?'
                r = net_tls.tls_resilient_post(
                    f"{_base}{_sep}sync=1",
                    files={'file': (
                        file_path.name, f,
                        upload_mime_type(file_path)
                    )},
                    headers={'X-Sync-Upload': '1'},
                    timeout=CONFIG.get('UPLOAD_HTTP_TIMEOUT_S', 600),
                )
            elapsed = time.time() - t0
            if 200 <= r.status_code < 300:
                try:
                    result = r.json()
                except ValueError:
                    result = {}
                logging.info(
                    f"  Upload sukses HTTP {r.status_code}: {result.get('message', 'OK')} | "
                    f"added={result.get('added', 0)} updated={result.get('updated', 0)} | "
                    f"{elapsed:.1f}s"
                )
                return True
            # 5xx retryable dengan exponential backoff
            if r.status_code >= 500:
                body_preview = r.text[:300].replace('\n', ' ').replace('\r', '')
                server = r.headers.get('Server', '?')
                if attempt < max_retries:
                    delay = backoff_5xx[min(attempt - 1, len(backoff_5xx) - 1)]
                    # FIX: gunakan "Upload gagal HTTP" (bukan "Upload HTTP") supaya keyword
                    # "upload gagal" di GUI parser match dan user melihat status gagal.
                    logging.warning(
                        f"  Upload gagal HTTP {r.status_code} ({elapsed:.1f}s, server={server}). "
                        f"Body: {body_preview[:150]}. "
                        f"Retry dalam {delay}s... (attempt {attempt}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                logging.error(
                    f"  Upload gagal HTTP {r.status_code} setelah {max_retries}x. "
                    f"Body: {body_preview}"
                )
                # PINDAHKAN file ke failed_uploads_serp/ (BUKAN dihapus)
                try:
                    fail_dir = SERP_UPLOAD_FAIL_DIR if 'SERP_UPLOAD_FAIL_DIR' in globals() else (Path(__file__).parent / 'failed_uploads_serp')
                    fail_dir.mkdir(parents=True, exist_ok=True)
                    dest = fail_dir / file_path.name
                    if dest.exists():
                        dest = fail_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                    file_path.rename(dest)
                    logging.info(f"  File gagal upload dipindah ke: {dest}")
                except Exception as mv_err:
                    logging.error(f"  Gagal pindah file ke failed_uploads_serp/: {mv_err}")
                return False
            # 429 Rate Limited
            if r.status_code == 429 and attempt < max_retries:
                delay = backoff_429[min(attempt - 1, len(backoff_429) - 1)]
                retry_after = r.headers.get('Retry-After')
                if retry_after:
                    try: delay = max(delay, int(retry_after))
                    except Exception: pass
                logging.warning(f"  Upload gagal HTTP 429 (rate limited), retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            # 4xx lainnya (permanent, tidak retry)
            logging.error(f"  Upload gagal HTTP {r.status_code} (permanent): {r.text[:300]}")
            return False
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                err_short = str(e).replace('\n', ' ')[:200]
                logging.warning(f"  Upload gagal koneksi: {err_short}. Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  Upload error setelah {max_retries}x: {e}")
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                logging.warning(f"  Upload error: {e}. Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  Upload error: {e}")
            return False
    return False


def save_local_copy(file_path: Path, local_dir: str) -> bool:
    if not local_dir:
        logging.error("  Folder lokal belum diatur")
        return False
    try:
        dest_dir = Path(local_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file_path.name
        if dest.exists():
            dest = dest_dir / f"{dest.stem}_{int(time.time())}{dest.suffix}"
        shutil.copy2(file_path, dest)
        logging.info(f"  File disimpan lokal: {dest}")
        return True
    except Exception as e:
        logging.error(f"  Simpan lokal gagal: {e}")
        return False

# ── Cleanup ───────────────────────────────────────────────────────────────────

def _close_excel_file(file_path: Path):
    """
    Tutup semua Excel sebelum hapus file hasil download SAP.

    SAP menulis file langsung ke disk — tidak membuka Excel secara interaktif,
    sehingga tidak perlu cari workbook spesifik via COM.
    Cukup panggil force_close_all_excel() yang sudah terbukti cepat (<1 detik).
    """
    logging.info(f"  Menutup Excel (jika ada) sebelum hapus: {file_path.name}")
    force_close_all_excel()


def cleanup_downloads(preserve_names=None):
    preserve_names = set(preserve_names or ())
    for p in CONFIG['DOWNLOAD_DIR'].iterdir():
        if (p.is_file() and p.name not in preserve_names
                and p.suffix.lower() in {'.xlsx', '.xls', '.sap', '.crdownload', '.tmp'}):
            try:
                p.unlink()
                logging.info(f"  Hapus: {p.name}")
            except Exception as e:
                logging.warning(f"  Gagal hapus {p.name}: {e}")


def close_sap_after_success():
    """
    Tutup semua proses SAP GUI setelah upload berhasil.

    TIDAK pakai COM wnd[0].close() — call itu memicu popup security baru
    "A script is attempting to access SAP GUI" tepat saat guard sudah berhenti,
    sehingga popup tidak ter-handle dan SAP menggantung menunggu klik manual.

    Solusi bersih:
    1. Aktifkan guard singkat untuk dismiss popup SAP yang masih tersisa
    2. Langsung terminate semua proses SAP via psutil — cepat, tidak memicu popup
    """
    logging.info("  Menutup SAP GUI setelah proses...")

    # FIX V30: no-op refresh — guard persisten masih aktif di fase ini (baru
    # dihentikan di finally run_automation), jadi popup security yang muncul
    # saat penutupan SAP tetap otomatis di-klik Allow.
    _start_sap_popup_guard(duration=8, interval=0.3)
    time.sleep(1)  # beri waktu guard dismiss popup yang sudah muncul

    # Terminate semua proses SAP langsung — tanpa COM call baru
    sap_procs = ['saplogon.exe', 'sapgui.exe', 'saplgpad.exe', 'sapmsas.exe']
    killed = 0
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if ((proc.info['name'] or '').lower() in sap_procs
                    and proc.info['pid'] not in _SERP_PRESERVE_SAP_PIDS):
                logging.info(f"    Terminate SAP: {proc.info['name']} (PID {proc.info['pid']})")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if killed:
        logging.info(f"  {killed} proses SAP ditutup.")
    else:
        logging.info("  Tidak ada proses SAP yang ditemukan.")


# ── Main run ──────────────────────────────────────────────────────────────────

def run_automation() -> bool:
    """Entry point utama. Mendukung retry otomatis kalau VPN putus di tengah proses SAP.
    SAP bergantung VPN secara langsung — kalau VPN putus, SAP crash total dan harus
    dimulai dari awal (download .sap lagi, buka SAP lagi). Berbeda dengan browser
    (Selenium) yang cukup login ulang, SAP tidak punya cara "reconnect" — harus restart.
    """
    MAX_VPN_RETRIES = 3  # maks berapa kali restart setelah VPN reconnect

    # FIX V30: guard popup SAP PERSISTEN — aktif sejak AWAL run sampai run
    # selesai (semua attempt + SEMUA fase: browser, .sap, SAP GUI startup,
    # tcode, execute, save, tunggu file, upload, close SAP). Popup
    # "SAP GUI Security" (Allow) yang muncul di fase mana pun dan kapan pun
    # — termasuk berkali-kali — SELALU terdeteksi dan di-klik Allow.
    # (Keputusan user: popup TIDAK dibuat hilang; cukup selalu di-klik Allow.)
    _start_sap_popup_guard()

    try:
        for attempt in range(1, MAX_VPN_RETRIES + 2):  # +2: attempt pertama + N retry
            result = _run_automation_once()
            if result:
                return True

            # Cek apakah kegagalan disebabkan VPN putus
            if not check_vpn_connection():
                if attempt > MAX_VPN_RETRIES:
                    logging.error(
                        f"SERP: VPN putus lagi setelah {MAX_VPN_RETRIES}x retry. Menyerah."
                    )
                    return False
                logging.warning(
                    f"SERP: VPN terputus saat proses SAP (attempt {attempt}/{MAX_VPN_RETRIES + 1}). "
                    f"Menunggu VPN tersambung kembali sebelum restart automation..."
                )
                # FIX V23: VPN wait dibatasi max_wait — kalau menyerah, batalkan
                # run SERP ini (return False) supaya worker EXIT dan Run All
                # sequential / jadwal berikutnya tetap jalan.
                if not _wait_vpn_reconnect_serp():
                    logging.error(
                        "SERP: VPN tidak kembali dalam batas waktu. Run SERP dibatalkan — "
                        "proses/jadwal berikutnya tetap jalan."
                    )
                    return False
                logging.info(
                    f"SERP: VPN sudah tersambung lagi. "
                    f"Memulai ulang automation dari awal (attempt {attempt + 1}/{MAX_VPN_RETRIES + 1})..."
                )
                continue  # restart dari awal
            else:
                # Bukan masalah VPN — kegagalan biasa, tidak perlu retry
                return False
        return False
    finally:
        # Guard dihentikan saat run selesai — tidak polling selamanya kalau
        # proses induk (GUI app / worker) masih hidup setelah run ini.
        _stop_sap_popup_guard()


def _wait_vpn_reconnect_serp(interval: int = 30, max_wait: float = None) -> bool:
    """Tunggu sampai VPN tersambung kembali, cek tiap `interval` detik.

    FIX V23: sebelumnya loop INFINITE — kalau VPN putus di tengah proses
    SERP dan tidak pernah kembali, worker menggantung selamanya menunggu
    (log masih bertambah tiap 30s sehingga watchdog GUI tidak menyadarinya)
    dan Run All sequential MACET di step SERP sampai hard timeout 1 jam
    — persis laporan user "process all prod id selesai tapi tidak lanjut
    proses berikutnya". Sekarang dibatasi max_wait (default env
    SERP_VPN_MAX_WAIT_SECONDS, 900s / 15 menit): return True kalau VPN
    kembali, False kalau menyerah — caller membatalkan run ini supaya
    proses/jadwal berikutnya tetap jalan."""
    import time as _time
    if max_wait is None:
        try:
            max_wait = float(os.getenv('SERP_VPN_MAX_WAIT_SECONDS', '900'))
        except Exception:
            max_wait = 900.0
    deadline = _time.time() + max_wait
    while True:
        _time.sleep(interval)
        if check_vpn_connection():
            logging.info("VPN terhubung kembali.")
            return True
        if _time.time() >= deadline:
            logging.error(
                f"  VPN BELUM kembali setelah {int(max_wait)}s. Berhenti menunggu — "
                f"run SERP ini dibatalkan supaya proses/jadwal berikutnya tetap jalan."
            )
            return False
        logging.info(f"  Masih menunggu VPN... (cek lagi dalam {interval}s)")


def _run_automation_once() -> bool:
    """Satu kali run automation SERP. Dipanggil oleh run_automation() dengan retry logic."""
    logging.info("=" * 70)
    logging.info("S-ERP ZMMR3190 AUTOMATION DIMULAI")
    logging.info(f"Waktu : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Chrome: {'headless' if CONFIG['HEADLESS'] else 'window'} | "
                 f"SAP: {'minimize' if CONFIG['SAP_MINIMIZE'] else 'window normal'}")
    logging.info("=" * 70)

    global _SERP_PRESERVE_EXCEL_PIDS, _SERP_PRESERVE_SAP_PIDS
    _SERP_PRESERVE_EXCEL_PIDS = _process_pids_containing('excel')
    _SERP_PRESERVE_SAP_PIDS = _process_pids_containing('sap')
    try:
        run_start_names = {p.name for p in CONFIG['DOWNLOAD_DIR'].iterdir() if p.is_file()}
    except Exception:
        run_start_names = set()
    driver  = None
    success = False

    try:
        logging.info("\n[1/6] Cek VPN...")
        if not check_vpn_connection():
            show_popup("VPN Required", "VPN tidak terhubung!\nConnect ke VPN dulu.", success=False)
            return False

        logging.info("\n[2/6] Setup Chrome...")
        driver = setup_driver()

        logging.info("\n[3/6] Login portal...")
        if not login_to_portal(driver):
            show_popup("Login Gagal", "Gagal login ke portal ServeOne", success=False)
            return False
        close_popup_if_exists(driver)

        logging.info("\n[4/6] Download .sap via Quick Menu -S-ERP...")
        try:
            driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
                'behavior': 'allow', 'downloadPath': str(CONFIG['DOWNLOAD_DIR'])
            })
        except Exception:
            pass

        # Hapus file .sap lama agar tidak confuse dengan file baru
        sap_before = set(CONFIG['DOWNLOAD_DIR'].glob('*.sap'))
        for old_sap in sap_before:
            try:
                old_sap.unlink()
                logging.info(f"  Hapus file .sap lama: {old_sap.name}")
            except Exception as e:
                logging.warning(f"  Gagal hapus {old_sap.name}: {e}")
        sap_before = set()  # folder sudah bersih

        # FIX V20: pre-grant permission download (anti popup 'Allow' Chrome)
        # SEBELUM trigger download dimulai — popup Allow baru muncul tepat
        # sebelum proses download excel dimulai dan mem-blokir download sampai
        # diklik manual.
        pregrant_chrome_download_permissions(driver)

        # FIX V20: watcher background — auto-klik popup Allow (JS alert /
        # dialog in-page) selama fase trigger + insecure download + tunggu
        # file .sap. Berhenti begitu file .sap muncul.
        allow_stop = _start_chrome_allow_popup_watcher(driver, duration=90, interval=1.0)

        try:
            if not trigger_serp_sap_download(driver):
                show_popup("S-ERP Gagal", "Gagal trigger download .sap", success=False)
                return False

            # Handle Chrome "Insecure download blocked" popup for .sap file
            handle_chrome_insecure_download(driver, timeout=15)

            sap_file = wait_for_sap_file(sap_before, timeout=50)
        finally:
            # Jangan biarkan watcher menyentuh Selenium lagi saat alur sudah
            # pindah ke SAP. Thread lama pada driver mati memicu retry localhost.
            _stop_chrome_allow_popup_watcher(allow_stop)

        if not sap_file:
            save_debug_screenshot("sap_file_missing", driver)
            show_popup("Download Gagal", "File .sap tidak terdownload (timeout 40 detik).\nCek debug_serp/", success=False)
            return False

        logging.info(f"  File .sap: {sap_file.name}")

        logging.info(f"\n[5/6] SAP GUI → {CONFIG['SAP_TCODE']} → Excel...")
        # Popup guard aktif SEBELUM buka .sap — popup bisa muncul saat SAP startup
        _start_sap_popup_guard(duration=180, interval=2.0)
        if not open_sap_file(sap_file):
            show_popup("SAP GUI Gagal", "Gagal membuka SAP GUI", success=False)
            return False

        downloaded = navigate_and_download_via_sap_gui()
        if not downloaded:
            show_popup("Download Gagal", f"Gagal download dari {CONFIG['SAP_TCODE']}", success=False)
            return False

        logging.info("\n[6/6] Output file...")
        # ── Simpan / upload (boleh gagal, cleanup tetap dijalankan) ──
        output_ok = True
        if CONFIG['SAVE_LOCAL']:
            output_ok = save_local_copy(downloaded, CONFIG['LOCAL_SAVE_DIR']) and output_ok
        if CONFIG['UPDATE_DASHBOARD']:
            output_ok = upload_to_dashboard(downloaded) and output_ok
        else:
            logging.info("  Upload dashboard dilewati sesuai setting")

        # ── Cleanup SELALU dijalankan: tutup Excel, hapus file, tutup SAP ──
        # Ini harus terjadi bahkan jika upload gagal, supaya:
        # 1. Excel tidak menggantung terbuka
        # 2. Proses berikutnya (SMRO, Pur Info Reg) bisa berjalan normal
        # 3. File temp tidak menumpuk
        downloaded_name = downloaded.name
        _close_excel_file(downloaded)
        try:
            if downloaded.exists():
                downloaded.unlink()
                logging.info(f"  File kerja dihapus: {downloaded_name}")
            else:
                logging.info(f"  File kerja sudah dipindah untuk retry: {downloaded_name}")
        except Exception as e:
            logging.warning(f"  Gagal hapus file kerja {downloaded_name}: {e}")
        cleanup_downloads(preserve_names=run_start_names)
        close_sap_after_success()

        if not output_ok:
            show_popup("Output Gagal", f"Upload/simpan gagal setelah retry.\nFile: {downloaded_name}\nExcel & SAP sudah ditutup.", success=False)
            return False

        success = True

        msg = (
            f"S-ERP Automation Berhasil!\n\n"
            f"File  : {downloaded_name}\n"
            f"Waktu : {datetime.now().strftime('%H:%M:%S')}\n"
            f"Upload: local-only dashboard"
        )
        logging.info("\n" + "=" * 70)
        logging.info("SELESAI SUKSES")
        logging.info("=" * 70)
        show_popup("Sukses", msg, success=True)
        return True

    except Exception as e:
        logging.error(f"\nError: {e}", exc_info=True)
        save_debug_screenshot("run_automation_error", driver)
        show_popup("Error", f"Terjadi error:\n{e}", success=False)
        return False

    finally:
        if driver:
            try:
                if CONFIG['KEEP_OPEN_ON_ERR'] and not success and not CONFIG['HEADLESS']:
                    logging.info("Browser dibiarkan terbuka (debug mode).")
                else:
                    # FIX V16.2: safe quit — anti urllib3 spam pada driver mati
                    # + bersihkan chrome.exe orphan by profile.
                    _serp_safe_driver_quit(driver)
            except Exception:
                pass


# ── VPN Watch Loop ────────────────────────────────────────────────────────────

def wait_for_vpn_and_run():
    vpn_interval = CONFIG['VPN_CHECK_SECONDS']
    retry_secs   = CONFIG['RETRY_SECONDS']

    logging.info("\n" + "=" * 70)
    logging.info("  VPN WATCH MODE  –  S-ERP ZMMR3190 Automation")
    logging.info(f"  Chrome   : {'headless' if CONFIG['HEADLESS'] else 'window'}")
    logging.info(f"  SAP GUI  : {'minimize otomatis' if CONFIG['SAP_MINIMIZE'] else 'window normal'}")
    logging.info(f"  Cek VPN  : tiap {vpn_interval} detik")
    logging.info(f"  Retry    : tiap {retry_secs} detik")
    logging.info("  Ctrl+C untuk berhenti")
    logging.info("=" * 70 + "\n")

    vpn_was_connected    = False
    run_done_for_session = False
    attempt              = 0

    while True:
        try:
            vpn_now = check_vpn_connection()

            if not vpn_now:
                if vpn_was_connected:
                    logging.info("VPN terputus. Standby...")
                vpn_was_connected    = False
                run_done_for_session = False
                attempt              = 0
                time.sleep(vpn_interval)
                continue

            if not vpn_was_connected:
                logging.info("VPN CONNECT — memulai automation...")
                vpn_was_connected    = True
                run_done_for_session = False
                attempt              = 0

            if run_done_for_session:
                time.sleep(vpn_interval)
                continue

            attempt += 1
            logging.info(f"Percobaan #{attempt}...")

            # FIX V38: watcher long-lived hanya service standby. Sebelum run,
            # hormati automation lain yang sedang memegang presence.
            try:
                import run_presence as _rp
                if _rp.is_held():
                    logging.info("Automation lain sedang berjalan. VPN watcher SERP menunggu giliran.")
                    time.sleep(vpn_interval)
                    continue
                _rp.acquire()
            except Exception:
                _rp = None
            try:
                _run_ok = run_automation()
            finally:
                if _rp:
                    _rp.release()
            if _run_ok:
                run_done_for_session = True
                attempt = 0
                logging.info("Sukses. Menunggu VPN reconnect.\n")
                time.sleep(vpn_interval)
            else:
                if not check_vpn_connection():
                    vpn_was_connected    = False
                    run_done_for_session = False
                    attempt              = 0
                    time.sleep(vpn_interval)
                else:
                    logging.warning(f"Gagal. Retry dalam {retry_secs} detik...")
                    waited = 0
                    while waited < retry_secs:
                        chunk = min(vpn_interval, retry_secs - waited)
                        time.sleep(chunk)
                        waited += chunk
                        if not check_vpn_connection():
                            vpn_was_connected    = False
                            run_done_for_session = False
                            attempt              = 0
                            break

        except KeyboardInterrupt:
            logging.info("Dihentikan (Ctrl+C)")
            break
        except Exception as e:
            logging.error(f"VPN watch error: {e}", exc_info=True)
            time.sleep(vpn_interval)


# ── Single instance lock ──────────────────────────────────────────────────────

_mutex = None

def acquire_single_instance_lock() -> bool:
    global _mutex
    if os.name != 'nt':
        return True
    import ctypes
    k      = ctypes.windll.kernel32
    _mutex = k.CreateMutexW(None, False, "Global\\SERPAutomationWatcher")
    if _mutex and k.GetLastError() == 183:
        logging.warning("Instance lain sudah berjalan.")
        return False
    return True


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    if '--watch' in args or '--wait-vpn' in args:
        if not acquire_single_instance_lock():
            sys.exit(0)
        wait_for_vpn_and_run()
    else:
        run_automation()
