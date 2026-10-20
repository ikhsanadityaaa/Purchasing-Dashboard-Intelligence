import os
import sys

# FIX V11: path resolution saat frozen (.exe)
from _paths import _app_dir, _data_dir, _env_file_path, _safe_log_path, _safe_mkdir, _safe_download_dir, _safe_fail_dir, _is_frozen
import time
import calendar
import re
import shutil
import subprocess
import requests
import net_tls
import tkinter as tk
from tkinter import messagebox
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, date, timedelta
import logging
from i18n import set_language, apply_localized_logging
import threading
import queue

_single_instance_mutex = None

load_dotenv(_env_file_path())  # FIX V11: explicit path supaya tidak cari di CWD
set_language(os.getenv("SMRO_LANGUAGE", "id"))


class _WorkerTagFilter(logging.Filter):
    """FIX: otomatis menyisipkan '[W1]'/'[W2]' di depan SETIAP baris log yang
    dipancarkan dari sebuah worker thread (thread yang sudah punya atribut
    worker_id). Ini menutup celah supaya log dari fungsi-fungsi shared
    (login, navigasi browser, klik tombol, dll) yang dipanggil dari banyak
    worker tetap konsisten ber-tag, tanpa perlu mengubah ratusan baris
    logging.info(...) satu per satu.

    Baris yang SUDAH punya tag manual '[W1] '/'[W2] ' di awal pesan tidak
    akan ditambah tag dobel.
    """

    def filter(self, record):
        worker_id = getattr(threading.current_thread(), 'worker_id', None)
        if worker_id is not None:
            msg = record.getMessage()
            # Cek apakah pesan sudah dimulai dengan tag manual yang sama
            # (mengabaikan leading whitespace seperti "  [W1] ...")
            stripped = msg.lstrip()
            if not stripped.startswith(f"[W{worker_id}]"):
                record.msg = f"[W{worker_id}] {record.msg}"
                record.args = None
        return True


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(_safe_log_path('smro_automation.log')), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logging.getLogger().addFilter(_WorkerTagFilter())
apply_localized_logging(logging.getLogger())

CONFIG = {
    'LOGIN_URL': 'http://sso.serveone.co.kr:6061/loginFormPage.jsp',
    'LOGIN_START_URL': os.getenv('SMRO_LOGIN_START_URL', 'http://sportal.serveone.id/portal/PortalView.do'),
    'SMRO_BASE_URL': 'http://svobpp.serveone.co.kr/irj/portal',
    'SMRO_SEARCH_CLIENT_ODR_URL': os.getenv(
        'SMRO_SEARCH_CLIENT_ODR_URL',
        'http://svobpp.serveone.co.kr/irj/servlet/prt/portal/prtroot/'
        'pcd!3aportal_content!2fmdcs!2frole!2fgsi!2findonesia!2frole.localsd!'
        '2fFolder_12!2fFolder!2fpage.SD100_001'
    ),
    'SMRO_SEARCH_OP_UNIT_URL': os.getenv(
        'SMRO_SEARCH_OP_UNIT_URL',
        'http://svobpp.serveone.co.kr/irj/servlet/prt/portal/prtroot/'
        'pcd!3aportal_content!2fmdcs!2frole!2fgsi!2findonesia!2frole.localsd!'
        '2fFolder_9!2fMall!2fpage.MC200_001_1'
    ),
    'DASHBOARD_API': os.getenv('DASHBOARD_API_URL', 'http://127.0.0.1:5001/api/upload/smro'),
    # FIX V35: timeout HTTP per-attempt untuk upload. Naik dari 300s ke 600s
    # supaya aman jika dashboard kelak menghormati sinyal sync (?sync=1) dan
    # memproses file besar secara langsung sebelum membalas respons.
    'UPLOAD_HTTP_TIMEOUT_S': int(os.getenv('UPLOAD_HTTP_TIMEOUT_S', '600')),
    'UPDATE_DASHBOARD': os.getenv('SMRO_UPDATE_DASHBOARD', 'true').lower() in ('1', 'true', 'yes'),
    'SAVE_LOCAL': os.getenv('SMRO_SAVE_LOCAL', 'false').lower() in ('1', 'true', 'yes'),
    'LOCAL_SAVE_DIR': os.getenv('SMRO_LOCAL_SAVE_DIR', ''),
    'USERNAME': os.getenv('SMRO_USERNAME', ''),
    'PASSWORD': os.getenv('SMRO_PASSWORD', ''),
    'DOWNLOAD_DIR': _safe_download_dir('downloads'),
    'HEADLESS': os.getenv('SMRO_HEADLESS', 'true').lower() in ('1', 'true', 'yes'),
    'START_DATE': os.getenv('SMRO_START_DATE', '2024/01/01'),
    'END_DATE': os.getenv('SMRO_END_DATE', ''),
    # Preset date range (dropdown di GUI). Kalau selain 'custom',
    # START_DATE & END_DATE otomatis dihitung mundur dari hari ini (end=today).
    # Pilihan: '1week' | '1month' | '3month' | '6month' | 'this_year' | 'custom'.
    # Kalau 'custom', pakai START_DATE/END_DATE manual seperti biasa.
    # Per-proses (SMRO_DATE_RANGE_PRESET). Tidak ada fallback ke
    # DATE_RANGE_PRESET global: fallback itu membuat preset SMRO menular
    # ke proses lain saat worker jadwal membaca .env lama.
    'DATE_RANGE_PRESET': (os.getenv('SMRO_DATE_RANGE_PRESET') or 'custom').strip().lower(),
    'SEARCH_LIMIT_ROWS': int(os.getenv('SMRO_SEARCH_LIMIT_ROWS', '10000')),
    'KEEP_BROWSER_OPEN_ON_ERROR': os.getenv('SMRO_KEEP_BROWSER_OPEN_ON_ERROR', 'false').lower() in ('1', 'true', 'yes'),
    'VPN_CHECK_SECONDS': int(os.getenv('SMRO_VPN_CHECK_SECONDS', '60')),
    'RETRY_SECONDS': int(os.getenv('SMRO_RETRY_SECONDS', '300')),
    # FIX: batas waktu tiap step (SERP/SMRO/PUR) saat dijalankan lewat Run All
    # sequential. Kalau satu step hang (mis. SAP GUI/COM automation macet saat
    # baru boot & VPN belum stabil), step tsb dipaksa berhenti sebelum
    # timeout ini supaya step berikutnya tetap bisa jalan.
    'RUN_ALL_STEP_TIMEOUT_SECONDS': int(os.getenv('SMRO_RUN_ALL_STEP_TIMEOUT_SECONDS', '3600')),
    'AFTERNOON_RUN_TIME': os.getenv('SMRO_AFTERNOON_RUN_TIME', '15:00'),
    'PARALLEL_WORKERS': int(os.getenv('SMRO_PARALLEL_WORKERS', '2')),
    # FIX: default diturunkan dari 2 -> 1. Server dashboard (PythonAnywhere
    # free/hacker tier) umumnya tidak mendukung multi-threading penuh, jadi
    # 2 upload bersamaan dari W1+W2 sering membuat salah satunya antre
    # terlalu lama dan berakhir HTTP 504 (gateway timeout) — terlihat dari
    # log: upload sukses biasanya selesai 3-22s, sedangkan yang gagal 504
    # selalu menghabiskan 65-108s sebelum gateway menyerah. Upload diserialkan
    # (1 di satu waktu) lewat semaphore di UploadManager; download tetap
    # paralel karena tidak menyentuh server dashboard.
    'MAX_CONCURRENT_UPLOADS': int(os.getenv('SMRO_MAX_CONCURRENT_UPLOADS', '1')),
    # ── Upload failure recovery (NEW) ──
    # Folder untuk menyimpan file Excel yang GAGAL upload. File ini akan
    # dipertahankan dan di-retry di akhir run. Sebelumnya file dihapus
    # bahkan saat upload gagal — data hilang selamanya saat server down.
    'UPLOAD_FAIL_DIR': _safe_fail_dir('failed_uploads'),
    # Maksimum retry khusus untuk HTTP 5xx (default 5, sebelumnya 3)
    'UPLOAD_MAX_RETRIES': int(os.getenv('SMRO_UPLOAD_MAX_RETRIES', '5')),
    # FIX V33: batas waktu menunggu SEMUA upload async selesai di akhir run.
    # Sebelumnya wait_all(timeout=1800) dipanggil tapi hasilnya diabaikan —
    # proses bisa exit padahal masih ada file yang belum terupload (thread
    # daemon dibunuh begitu proses exit) dan batch tetap dianggap sukses.
    'UPLOAD_FINISH_WAIT_SECONDS': int(os.getenv('SMRO_UPLOAD_FINISH_WAIT_SECONDS', '3600')),
    # Maksimum umur file di failed_uploads/ (hari).
    'MAX_FAILED_AGE_DAYS': int(os.getenv('SMRO_MAX_FAILED_AGE_DAYS', '7')),
    # FIX V34: maksimum umur file backup di backup_failed/ (hari). Folder ini
    # hanya terisi kalau file gagal dipindah ke failed_uploads/ (safety net),
    # tapi tanpa cleanup bisa menumpuk bertahun-tahun dan memenuhi disk.
    'MAX_BACKUP_AGE_DAYS': int(os.getenv('SMRO_MAX_BACKUP_AGE_DAYS', '7')),
    # Auto-save local copy jika upload gagal? Default true (safety net).
    'AUTO_SAVE_LOCAL_ON_FAIL': os.getenv('SMRO_AUTO_SAVE_LOCAL_ON_FAIL', 'true').lower() in ('1', 'true', 'yes'),
    'AUTO_SAVE_LOCAL_DIR': os.getenv('SMRO_AUTO_SAVE_LOCAL_DIR', str(_data_dir() / 'backup_failed')),
}

# Cleanup tidak boleh berjalan saat module di-import oleh GUI atau test.
def cleanup_old_failed_uploads():
    try:
        cutoff = time.time() - CONFIG['MAX_FAILED_AGE_DAYS'] * 86400
        for file_path in CONFIG['UPLOAD_FAIL_DIR'].iterdir():
            try:
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    file_path.unlink()
                    logging.info(f'Cleanup file failed lama: {file_path.name}')
            except Exception:
                pass
    except Exception as e:
        logging.warning(f'Gagal cleanup UPLOAD_FAIL_DIR: {e}')


# FIX V34: backup_failed/ (AUTO_SAVE_LOCAL_DIR) sebelumnya tidak pernah
# dibersihkan — file backup dari upload yang gagal bertahun-tahun lama bisa
# memenuhi disk (keluhan user: "disk saya sering penuh"). Sekarang dibersihkan
# dengan batas umur yang sama seperti failed_uploads/.
def cleanup_old_backup_files():
    try:
        backup_dir = Path(CONFIG['AUTO_SAVE_LOCAL_DIR'])
        cutoff = time.time() - CONFIG['MAX_BACKUP_AGE_DAYS'] * 86400
        if not backup_dir.exists():
            return
        for file_path in backup_dir.iterdir():
            try:
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    file_path.unlink()
                    logging.info(f'Cleanup file backup lama: {file_path.name}')
            except Exception:
                pass
    except Exception as e:
        logging.warning(f'Gagal cleanup AUTO_SAVE_LOCAL_DIR: {e}')


# ── FIX V33: pemulihan file yang belum terupload ke endpoint ─────────────────
# Keluhan: "proses search client odr terhenti ketika excel berhasil di download
# dan dianggap sukses upload padahal belum di upload ke endpoint". Tiga celah
# ditutup helper-helper ini:
#   1. File Excel yang tertinggal di folder worker (run sebelumnya mati/ter-kill
#      saat upload async masih berjalan) tadinya DIHAPUS oleh
#      cleanup_worker_dirs() — sekarang dipindah ke UPLOAD_FAIL_DIR lalu
#      diupload ulang di awal run.
#   2. File di failed_uploads/ tadinya TIDAK PERNAH di-reupload di run
#      berikutnya (komentar lama menyesatkan) dan dihapus otomatis setelah 7
#      hari — sekarang di-requeue di awal setiap run.
#   3. Pemulihan hanya dilakukan di titik yang BEBAS race: awal run (belum ada
#      thread upload) dan setelah wait_all() selesai — tidak di tengah run
#      supaya tidak memindahkan file yang sedang dibaca thread upload.

_EXCEL_EXTS = {'.xlsx', '.xls', '.csv'}


def _in_fail_dir(file_path: Path) -> bool:
    try:
        return file_path.resolve().parent == Path(CONFIG['UPLOAD_FAIL_DIR']).resolve()
    except Exception:
        return False


def collect_orphan_uploads() -> int:
    """Pindahkan file Excel hasil download yang BELUM TERUPLOAD (run sebelumnya
    mati/ter-kill sebelum upload selesai) dari folder download worker ke
    UPLOAD_FAIL_DIR, supaya bisa diupload ulang di run ini. Return jumlah file."""
    fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
    if not fail_dir:
        return 0
    try:
        fail_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return 0
    roots = [CONFIG['DOWNLOAD_DIR']]
    try:
        roots += [d for d in CONFIG['DOWNLOAD_DIR'].iterdir()
                  if d.is_dir() and d.name.startswith('worker_')]
    except Exception:
        pass
    moved = 0
    for root in roots:
        try:
            candidates = [p for p in root.iterdir()
                          if p.is_file() and p.suffix.lower() in _EXCEL_EXTS
                          and p.stem.startswith('smro_')]
        except Exception:
            continue
        for src in candidates:
            # File >= 1KB dianggap lengkap; yang lebih kecil kemungkinan korup.
            try:
                if src.stat().st_size < 1024:
                    continue
            except Exception:
                continue
            dest = fail_dir / src.name
            if dest.exists():
                dest = fail_dir / f"{src.stem}_{int(time.time())}{src.suffix}"
            try:
                shutil.move(str(src), str(dest))
                moved += 1
                logging.info(f'Requeue upload: {src.name} dipulihkan dari run sebelumnya')
            except Exception as e:
                logging.warning(f'Gagal memulihkan {src.name}: {e}')
    if moved:
        logging.info(f'Total {moved} file Excel belum terupload dipulihkan untuk diupload ulang.')
    return moved


def requeue_failed_uploads(upload_manager) -> int:
    """Upload ulang SEMUA file di failed_uploads/ (termasuk hasil pemulihan
    collect_orphan_uploads) di awal run.

    FIX V34 (KEPUTUSAN USER): SEQUENTIAL + BLOCKING — satu file DITUNGGU
    sampai upload benar-benar selesai (tidak peduli lamanya) sebelum file
    berikutnya. Sukses -> file langsung dihapus (di dalam UploadManager.upload).
    Gagal -> file tetap aman di failed_uploads/ untuk run berikutnya.
    Dulu: semua file dilempar ke antrean async sekaligus (burst) — antrean
    menumpuk di server (folder upload_job dashboard) dan di disk lokal.

    Return jumlah file yang diproses."""
    fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
    if not fail_dir:
        return 0
    try:
        files = sorted(p for p in fail_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in _EXCEL_EXTS)
    except Exception:
        return 0
    ok_count = 0
    for p in files:
        logging.info(f'Requeue upload (blocking): {p.name} — menunggu sampai selesai...')
        if upload_manager.upload(p):
            ok_count += 1
    if files:
        logging.info(
            f'Requeue upload selesai: {ok_count}/{len(files)} file berhasil diupload ulang.'
            + (f' {len(files) - ok_count} file tetap di failed_uploads/ untuk run berikutnya.'
               if ok_count < len(files) else ''))
    return len(files)


def requeue_pending_uploads_sync() -> int:
    """Versi sinkron untuk jalur sequential (tanpa UploadManager): upload ulang
    file di failed_uploads/ satu per satu. Sukses -> file dihapus."""
    fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
    if not fail_dir:
        return 0
    try:
        files = sorted(p for p in fail_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in _EXCEL_EXTS)
    except Exception:
        return 0
    ok_count = 0
    for p in files:
        logging.info(f'Requeue upload (sequential): {p.name}')
        if upload_to_dashboard(p, max_retries=2):
            ok_count += 1
            try:
                p.unlink()
            except Exception:
                pass
    if files:
        logging.info(
            f'Requeue upload (sequential): {ok_count}/{len(files)} file berhasil diupload ulang.')
    return ok_count


def park_unuploaded_file(file_path: Path) -> bool:
    """Pindahkan file yang gagal/belum terupload ke UPLOAD_FAIL_DIR (JANGAN
    dihapus) supaya bisa diupload ulang di run berikutnya. Return True kalau
    file aman tersimpan di fail dir (atau memang sudah di sana)."""
    if _in_fail_dir(file_path):
        return True
    fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
    if not fail_dir:
        return False
    try:
        fail_dir.mkdir(parents=True, exist_ok=True)
        dest = fail_dir / file_path.name
        if dest.exists():
            dest = fail_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
        shutil.move(str(file_path), str(dest))
        logging.info(f'File belum terupload dipindah ke: {dest.name}')
        return True
    except Exception as e:
        logging.error(f'Gagal memindahkan file belum terupload {file_path.name}: {e}')
        return False


# ─────────────────────────────── SINGLE INSTANCE ──────────────────────────────

def acquire_single_instance_lock(name: str = "SMROAutomationWatcher") -> bool:
    global _single_instance_mutex
    if os.name != "nt":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    _single_instance_mutex = kernel32.CreateMutexW(None, False, f"Global\\{name}")
    if _single_instance_mutex == 0:
        return True
    if kernel32.GetLastError() == 183:
        logging.warning("SMRO watcher sudah berjalan. Instance baru dihentikan.")
        return False
    return True


# ───────────────────────────────── UI POPUP ───────────────────────────────────

def show_popup(title: str, message: str, success: bool = True, enabled_override: bool = None):
    # FIX: pakai desain popup bersama (notify.py) supaya tampilan notifikasi
    # SMRO sama persis dengan SERP dan Pur Info Reg.
    # enabled_override: kalau diisi (bukan None), pakai nilai ini langsung
    # (dipakai popup ringkasan Run All yang mau baca SMRO_SHOW_POPUP dari
    # environ proses induk, bukan dari environ proses saat ini).
    enabled = enabled_override if enabled_override is not None else (
        os.getenv('SMRO_SHOW_POPUP', 'true').lower() in ('1', 'true', 'yes')
    )
    import notify
    notify.show_popup(title, message, success=success, auto_close_ms=4000, enabled=enabled)


# ───────────────────────────── HELPER SELENIUM ────────────────────────────────

def is_driver_alive(driver) -> bool:
    """Cek apakah ChromeDriver session masih hidup.

    FIX V15: implementasi lama pakai `driver.title` yang melakukan HTTP call
    ke ChromeDriver. Kalau ChromeDriver process sudah mati, HTTP call ini
    memicu urllib3.Retry default (total=3, backoff_factor=...) — TIGA retry
    dengan backoff 5+ detik tiap retry → 15+ detik log spam
    "Retrying (Retry(total=N, ...)) after connection broken by
    'NewConnectionError'" per call. save_debug_artifacts memanggil ini →
    spam berkali-kali.

    Implementasi baru: cek OS-level process state chromedriver.exe via
    `driver.service.process.poll()`. Tidak ada HTTP call, tidak ada urllib3
    retry. poll() return None kalau process masih hidup, exit code kalau
    sudah mati.

    Fallback ke HTTP probe hanya kalau service.process tidak tersedia
    (mis. driver dibuat via Remote() bukan local Service).
    """
    try:
        service = getattr(driver, 'service', None)
        proc = getattr(service, 'process', None) if service else None
        if proc is not None:
            # poll() return None kalau process hidup, exit code kalau mati.
            # Tidak ada HTTP call → tidak ada urllib3 spam.
            return proc.poll() is None
        # Tidak ada service.process (mis. Remote WebDriver). HTTP probe
        # sebagai fallback. Catatan: ini BISA memicu urllib3 retry kalau
        # driver mati, jadi prefer path di atas kalau bisa.
        _ = driver.title
        return True
    except Exception:
        return False


# FIX: ChromeDriverManager().install() TIDAK thread-safe. Kalau dipanggil
# bersamaan dari beberapa worker paralel (mode SMRO Parallel), beberapa
# thread bisa baca/tulis cache driver (~/.wdm) di waktu yang sama dan
# menghasilkan file chromedriver yang rusak/tidak lengkap. Gejalanya persis:
# "session not created: Chrome failed to start: crashed" pada SALAH SATU
# worker, biasanya yang kebetulan start bersamaan dengan worker lain --
# BUKAN soal RAM/CPU. Lock ini memastikan hanya satu thread yang boleh
# resolve/download chromedriver dalam satu waktu; hasilnya (path) di-cache
# supaya panggilan berikutnya tidak perlu install() lagi sama sekali.
_CHROMEDRIVER_INSTALL_LOCK = threading.Lock()
_CHROMEDRIVER_PATH_CACHE = None


# ═════════════════════════════════════════════════════════════════════════
# FIX V24: chromedriver di jaringan kantor ber-SSL-inspection.
#
# Gejala (laporan user 2026-09-14 08:20):
#   ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate
#     verify failed: self-signed certificate in certificate chain
#   requests.exceptions.MaxRetryError: HTTPSConnectionPool(
#     host='googlechromelabs.github.io', port=443) ... Max retries exceeded
#   requests.exceptions.ConnectionError: Could not reach host. Are you offline?
#
# Penyebab: jaringan kantor memasang SSL inspection proxy yang me-re-sign
# semua koneksi HTTPS dengan CA sertifikat korporat (self-signed). Python
# requests / webdriver-manager hanya mengenal bundle CA "certifi" ->
# verifikasi rantai sertifikat GAGAL -> ChromeDriverManager().install()
# gagal -> automation tidak bisa start sama sekali (padahal Chrome & portal
# SAP internal aman, karena portal berjalan di HTTP, bukan HTTPS publik).
#
# Solusi: buka Chrome lewat rantai fallback BERLAPIS di
# _create_chrome_with_fallback(). Urutan strategi:
#   0. driver_path eksplisit (parameter lama) + chromedriver.exe manual yang
#      ditaruh sebelah aplikasi (escape hatch offline total, tanpa jaringan).
#   1. Path chromedriver yang sudah terbukti berhasil di process ini
#      (cache in-memory — worker paralel berikutnya instant).
#   2. Selenium Manager BAWAAN Selenium >= 4.6 (webdriver.Chrome TANPA
#      Service path): binary Rust-nya membaca Windows certificate store
#      (bukan certifi), jadi CA korporat DIKENALI -> download chromedriver
#      LOLOS SSL inspection. Cache di ~/.cache/selenium juga otomatis
#      dipakai ulang tanpa jaringan sama sekali.
#   3. ChromeDriverManager() normal (jaringan rumah / tanpa SSL inspection).
#   4. ChromeDriverManager() dengan verifikasi SSL DIMATIKAN khusus untuk
#      sesi download driver (self-signed certificate chain) — patch scoped
#      pada requests.Session.request + pemulihan penuh saat keluar blok.
# Kalau SEMUA strategi gagal -> RuntimeError berisi ringkasan tiap strategi
# + petunjuk manual (download chromedriver.exe, taruh sebelah aplikasi).
# ═══════════════════════════════════════════════════════════════════════

class _WdmSslVerifyOff:
    """(FIX V24) Context manager: matikan verifikasi SSL pada
    requests.Session HANYA selama blok ini aktif.

    Dipakai untuk download chromedriver lewat webdriver-manager di jaringan
    kantor ber-SSL-inspection, tempat CA korporat tidak terdapat di bundle
    certifi Python. Prinsipnya:
    - Scoped & dipulihkan PENUH di __exit__ — semua request di luar blok
      tetap diverifikasi SSL normal (VPN check, dsb. tidak terpengaruh).
    - Window patching hanya beberapa detik saat install() berjalan, dan
      selalu di dalam _CHROMEDRIVER_INSTALL_LOCK (tidak tumpang tindih
      antar worker / antar strategi).
    - urllib3.InsecureRequestWarning diredam supaya log tidak banjir
      warning tiap request verify=False.
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


def _local_chromedriver_candidates():
    """(FIX V24) Cari chromedriver.exe yang ditaruh MANUAL sebelah aplikasi.

    Escape hatch offline total: kalau jaringan kantor memblokir semua
    download (atau internet mati), user cukup download chromedriver versi
    yang cocok dengan Chrome terpasang, taruh di folder yang sama dengan
    .exe / script, dan automation akan memakainya TANPA menyentuh jaringan
    sama sekali. Dicari di: folder aplikasi (_app_dir: folder .exe saat
    frozen, folder source saat dev), lalu current working directory.
    """
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


def _summarize_chromedriver_err(e, limit=300):
    """(FIX V24) Ringkas pesan error per strategi supaya ringkasan akhir
    tetap terbaca di popup GUI (tanpa full traceback panjang)."""
    try:
        s = str(e).strip().replace('\n', ' | ')
    except Exception:
        s = repr(e)
    return s[:limit] + ('...' if len(s) > limit else '')


def _create_chrome_with_fallback(opts, driver_path=None):
    """(FIX V24) Buka Chrome dengan rantai fallback chromedriver berlapis.

    Dipanggil setup_driver(). Return webdriver.Chrome yang hidup, atau
    raise RuntimeError dengan ringkasan semua strategi + solusi manual.

    Catatan logging: kegagalan PER-STRATEGI di-log di level WARNING (bukan
    ERROR) supaya:
    - GUI Activity Log tetap memperlihatkan usaha fallback ke user
      (baris mengandung kata 'chromedriver' di-skip map GUI — raw log file
      tetap lengkap);
    - _track_run_all_stage di app.py TIDAK menganggapnya error tahap
      (hanya baris ' - ERROR - ' / marker eksplisit yang dihitung).
    """
    global _CHROMEDRIVER_PATH_CACHE
    attempts = []

    def _try_service_path(tag, path):
        try:
            drv = webdriver.Chrome(service=Service(path), options=opts)
            logging.info(f"Chromedriver siap [{tag}]")
            return drv
        except Exception as e:
            msg = _summarize_chromedriver_err(e)
            attempts.append(f"{tag}: {msg}")
            logging.warning(
                f"Chromedriver gagal lewat {tag}, coba cara berikutnya ({msg})"
            )
            return None

    # Strategi 0a: path eksplisit dari pemanggil (perilaku lama tetap
    # dihormati — kalau path itu rusak sekarang ada fallback, bukan crash).
    if driver_path:
        drv = _try_service_path('driver_path eksplisit', driver_path)
        if drv:
            return drv

    # Strategi 0b: chromedriver.exe manual sebelah aplikasi (offline total).
    for path in _local_chromedriver_candidates():
        drv = _try_service_path('chromedriver lokal sebelah aplikasi', path)
        if drv:
            _CHROMEDRIVER_PATH_CACHE = path
            return drv

    # Strategi 1: path yang sudah terbukti berhasil di process ini.
    if _CHROMEDRIVER_PATH_CACHE:
        drv = _try_service_path('path sukses sebelumnya', _CHROMEDRIVER_PATH_CACHE)
        if drv:
            return drv

    # Strategi 2: Selenium Manager bawaan Selenium >= 4.6 (tanpa Service
    # path) — pakai Windows certificate store -> CA korporat dikenali ->
    # download driver LOLOS SSL inspection; cache ~/.cache/selenium juga
    # otomatis dipakai ulang tanpa jaringan sama sekali.
    try:
        drv = webdriver.Chrome(options=opts)
        logging.info("Chromedriver siap [Selenium Manager bawaan Selenium]")
        return drv
    except Exception as e:
        msg = _summarize_chromedriver_err(e)
        attempts.append(f"Selenium Manager bawaan: {msg}")
        logging.warning(
            f"Chromedriver gagal lewat Selenium Manager, coba cara "
            f"berikutnya ({msg})"
        )

    # Strategi 3 & 4: webdriver-manager — normal dulu, lalu SSL verify OFF
    # (urutan ini menjaga perilaku lama di jaringan normal: WDM tetap
    # menjadi pilihan pertama sebelum hack SSL diperlukan).
    for ssl_off in (False, True):
        tag = 'ChromeDriverManager SSL-off' if ssl_off else 'ChromeDriverManager'
        try:
            if ssl_off:
                # FIX V24: jaringan kantor self-signed chain -> verifikasi
                # dimatikan HANYA selama blok download driver ini.
                with _WdmSslVerifyOff():
                    with _CHROMEDRIVER_INSTALL_LOCK:
                        path = ChromeDriverManager().install()
            else:
                with _CHROMEDRIVER_INSTALL_LOCK:
                    path = ChromeDriverManager().install()
            drv = _try_service_path(tag, path)
            if drv:
                _CHROMEDRIVER_PATH_CACHE = path
                return drv
        except Exception as e:
            msg = _summarize_chromedriver_err(e)
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


def _is_chromedriver_dead(err_msg: str, driver=None) -> bool:
    """Deteksi ChromeDriver crash dari pesan error DAN status driver aktif."""
    indicators = ['refused', 'invalid session id', 'cannot connect',
                  'target machine actively refused', 'connection refused',
                  'no connection', 'disconnected', 'not reachable',
                  'session not created', 'session deleted']
    if any(w in err_msg for w in indicators):
        return True
    if driver is not None and not is_driver_alive(driver):
        return True
    return False


def wait_for_page_ready(driver, timeout: int = 15):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


def find_first_visible(driver, locators):
    for by, selector in locators:
        try:
            for element in driver.find_elements(by, selector):
                if element.is_displayed():
                    return element
        except Exception:
            continue
    return None


def set_field_value(driver, element, value: str):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
    time.sleep(0.2)
    try:
        element.click()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(value)
        element.send_keys(Keys.TAB)
    except Exception:
        driver.execute_script(
            """
            const el = arguments[0], val = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            el.focus();
            if (setter) setter.call(el, val); else el.value = val;
            for (const ev of ['input', 'change', 'blur'])
                el.dispatchEvent(new Event(ev, { bubbles: true }));
            """,
            element, value,
        )


def find_input_by_label(driver, labels, timeout: int = 15):
    def locate(d):
        lower_labels = [l.lower() for l in labels]
        direct_xpaths = []
        for label in lower_labels:
            compact = label.replace(".", "").replace(" ", "")
            direct_xpaths.append(
                "//input[not(@type='hidden') and ("
                f"contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{compact}') or "
                f"contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{compact}') or "
                f"contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{label}') or "
                f"contains(translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{label}') or "
                f"contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{label}')"
                ")]"
            )
        for xpath in direct_xpaths:
            el = find_first_visible(d, [(By.XPATH, xpath)])
            if el:
                return el
        return d.execute_script(
            """
            const labels = arguments[0].map(l => l.toLowerCase());
            const compact = labels.map(l => l.replace(/[.\\s_-]/g,''));
            const visible = e => {
                const s=window.getComputedStyle(e), r=e.getBoundingClientRect();
                return s.visibility!=='hidden'&&s.display!=='none'&&r.width>0&&r.height>0;
            };
            const textOf = e => [e.id,e.name,e.title,e.placeholder,e.getAttribute('aria-label'),e.getAttribute('lsdata')]
                .filter(Boolean).join(' ').toLowerCase();
            const inputs = Array.from(document.querySelectorAll('input,textarea')).filter(e=>{
                const t=(e.getAttribute('type')||'text').toLowerCase();
                return !['hidden','button','submit','image','checkbox','radio'].includes(t)&&visible(e);
            });
            for(const el of inputs){
                const h=textOf(el), c=h.replace(/[.\\s_-]/g,'');
                if(labels.some(l=>h.includes(l))||compact.some(l=>c.includes(l))) return el;
            }
            const labelNodes = Array.from(document.querySelectorAll('label,span,div,td,th'))
                .filter(visible)
                .map(e=>({element:e,text:(e.innerText||e.textContent||'').trim().toLowerCase()}))
                .filter(i=>i.text&&labels.some(l=>i.text.includes(l)));
            let best=null,bestScore=Infinity;
            for(const inp of inputs){
                const ir=inp.getBoundingClientRect();
                for(const item of labelNodes){
                    const lr=item.element.getBoundingClientRect();
                    const sameRow=Math.abs((lr.top+lr.height/2)-(ir.top+ir.height/2))<=24;
                    const before=lr.left<=ir.left+8;
                    const above=lr.bottom<=ir.top+6&&ir.top-lr.bottom<=80;
                    if(!sameRow&&!above) continue;
                    if(!before&&!above) continue;
                    const score=Math.max(0,ir.left-lr.right)+Math.abs(ir.top-lr.top)*5;
                    if(score<bestScore){best=inp;bestScore=score;}
                }
            }
            return best;
            """,
            lower_labels,
        )
    return WebDriverWait(driver, timeout).until(locate)


def click_sap_button_by_id(driver, element_id: str, timeout: int = 15):
    last_error = None
    try:
        button = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, element_id))
        )
        WebDriverWait(driver, timeout).until(lambda d: button.is_displayed())
    except Exception as e:
        last_error = e
        button = driver.execute_script(
            """
            const id = arguments[0];
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const textOf = e => [e.id, e.title, e.value, e.innerText, e.textContent, e.getAttribute('lsdata')]
                .filter(Boolean).join(' ').toLowerCase();
            const exact = document.getElementById(id);
            if (exact && visible(exact)) return exact;
            return Array.from(document.querySelectorAll("a,button,input,[role='button'],[ct='B'],span,div"))
                .find(e => visible(e) && /so\\s*search|search/.test(textOf(e))) || null;
            """,
            element_id,
        )
        if not button:
            raise TimeoutException(f"Button not found: {element_id}. Last error: {last_error}")

    driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", button)
    time.sleep(0.2)

    click_errors = []
    for click_method in (
        "actionchains",
        "native_click",
        "js_click",
        "mouse_events",
        "keyboard_enter",
    ):
        try:
            if click_method == "actionchains":
                ActionChains(driver).move_to_element(button).pause(0.1).click(button).perform()
            elif click_method == "native_click":
                button.click()
            elif click_method == "js_click":
                driver.execute_script("arguments[0].focus(); arguments[0].click();", button)
            elif click_method == "mouse_events":
                driver.execute_script(
                    """
                    const e = arguments[0];
                    e.focus?.();
                    for (const ev of ['mouseover','mousemove','mousedown','mouseup','click']) {
                        e.dispatchEvent(new MouseEvent(ev, {bubbles:true,cancelable:true,view:window}));
                    }
                    """,
                    button,
                )
            else:
                button.send_keys(Keys.ENTER)
            time.sleep(0.4)
            return
        except Exception as e:
            click_errors.append(f"{click_method}: {type(e).__name__}: {e}")

    raise RuntimeError(f"Gagal klik SAP button {element_id}. " + " | ".join(click_errors))


def click_popup_button(driver, labels, timeout: int = 20, exclude_labels=None):
    labels = [l.lower() for l in labels]
    exclude_labels = [l.lower() for l in (exclude_labels or [])]

    def locate(d):
        return d.execute_script(
            """
            const labels=arguments[0], excl=arguments[1];
            const visible=e=>{const s=window.getComputedStyle(e),r=e.getBoundingClientRect();
                return s.visibility!=='hidden'&&s.display!=='none'&&r.width>0&&r.height>0;};
            const textOf=e=>[e.innerText,e.textContent,e.value,e.title,e.alt,
                e.getAttribute('aria-label'),e.getAttribute('lsdata'),e.id,e.className]
                .filter(Boolean).join(' ').replace(/\\s+/g,' ').trim().toLowerCase();
            const candidates=Array.from(document.querySelectorAll(
                "a,button,input[type='button'],input[type='submit'],span,div,td,[ct='B'],[ct='LN'],.lsButton,.lsLink,[role='button']"
            )).filter(visible);
            for(const el of candidates){
                const h=textOf(el);
                if(!h) continue;
                if(excl.some(l=>h.includes(l))) continue;
                if(labels.some(l=>h.includes(l)))
                    return el.closest("a,button,[role='button'],[ct='B'],[ct='LN'],input")||el;
            }
            return null;
            """,
            labels, exclude_labels,
        )

    el = WebDriverWait(driver, timeout).until(lambda d: locate(d) or False)
    driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", el)
    time.sleep(0.2)
    try:
        ActionChains(driver).move_to_element(el).pause(0.1).click(el).perform()
    except Exception:
        driver.execute_script(
            "const e=arguments[0]; e.focus?.(); for(const ev of ['mouseover','mousedown','mouseup','click']) "
            "e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));",
            el,
        )


def select_all_visible_options(driver, timeout: int = 20) -> int:
    end_time = time.time() + timeout
    while time.time() < end_time:
        count = driver.execute_script(
            """
            const visible=e=>{const s=window.getComputedStyle(e),r=e.getBoundingClientRect();
                return s.visibility!=='hidden'&&s.display!=='none'&&r.width>0&&r.height>0;};
            const textOf=e=>[e.innerText,e.textContent,e.title,e.getAttribute('aria-label'),
                e.getAttribute('lsdata'),e.id].filter(Boolean).join(' ').replace(/\\s+/g,' ').trim().toLowerCase();
            const selectAllBtn=Array.from(document.querySelectorAll(
                "button,a,[ct='B'],.lsButton,[role='button'],input[type='button'],input[type='submit']"
            )).find(e=>visible(e)&&/select\\s*all|check\\s*all|\\ball\\b/.test(textOf(e))&&!/deselect|uncheck|clear/.test(textOf(e)));
            if(selectAllBtn){selectAllBtn.click();return 1;}
            let count=0;
            const boxes=Array.from(document.querySelectorAll("input[type='checkbox']")).filter(visible);
            for(const b of boxes){if(!b.checked&&!b.disabled){b.click();count++;}}
            if(count>0) return count;
            const sapChecks=Array.from(document.querySelectorAll(
                "[ct='CB'],.lsCheckBox,.urCheckBox,[role='checkbox'],img[src*='checkbox'],span[id*='-toggle'],td[id*='-sel']"
            )).filter(visible);
            for(const e of sapChecks){
                if(!/deselect|uncheck/.test(textOf(e))){e.click();count++;}
            }
            return count;
            """
        )
        if count:
            return int(count)
        time.sleep(0.5)
    return 0


# ───────────────────────── POPUP IFRAME HANDLING ──────────────────────────────

def switch_to_visible_popup_frame(driver, timeout: int = 30):
    end_time = time.time() + timeout
    last_error = None
    while time.time() < end_time:
        try:
            driver.switch_to.default_content()
            popup_frames = driver.find_elements(
                By.CSS_SELECTOR,
                "iframe[lspopup='true'], iframe[id^='URLSPW-'], iframe[name^='URLSPW-']"
            )
            for frame in popup_frames:
                try:
                    is_visible = driver.execute_script(
                        """
                        const f=arguments[0];
                        const s=window.getComputedStyle(f);
                        const r=f.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' && r.width>10 && r.height>10;
                        """,
                        frame,
                    )
                    if not is_visible:
                        continue
                    driver.switch_to.frame(frame)
                    has_content = driver.execute_script(
                        "return document.body && document.body.children.length > 0;"
                    )
                    if has_content:
                        frame_id = frame.get_attribute('id') or frame.get_attribute('name') or '?'
                        logging.info(f"  Masuk ke popup iframe: {frame_id}")
                        return True
                    driver.switch_to.default_content()
                except Exception as e:
                    last_error = e
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception as e:
            last_error = e
        time.sleep(0.5)
    raise TimeoutException(f"Iframe popup SAP tidak ditemukan setelah {timeout}s. Error terakhir: {last_error}")


def set_popup_max_selection_count(driver, value: str = "9999"):
    try:
        field = driver.execute_script(
            """
            const visible=e=>{const s=window.getComputedStyle(e),r=e.getBoundingClientRect();
                return s.visibility!=='hidden'&&s.display!=='none'&&r.width>0&&r.height>0;};
            const inputs=Array.from(document.querySelectorAll("input[type='text'],input:not([type])")).filter(visible);
            return inputs.find(i=>/^\\d+$/.test((i.value||'').trim())&&(i.value||'').trim()==='10')
                || inputs[0] || null;
            """
        )
        if not field:
            return False
        set_field_value(driver, field, value)
        logging.info(f"  Maximum Selection Count diubah ke {value}")
        return True
    except Exception as e:
        logging.warning(f"  Gagal set Maximum Selection Count: {e}")
        return False


# ────────────────────── OP UNIT MULTI BUTTON ──────────────────────────────────

def click_op_unit_multi_button(driver, timeout: int = 20):
    exact_id = "aaaa.SearchOrderCompView.LinkToAction1"
    try:
        button = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, exact_id))
        )
        WebDriverWait(driver, timeout).until(lambda d: button.is_displayed() and button.is_enabled())
        driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", button)
        time.sleep(0.2)
        try:
            ActionChains(driver).move_to_element(button).pause(0.1).click(button).perform()
        except Exception:
            driver.execute_script(
                "const e=arguments[0]; e.focus?.(); for(const ev of ['mouseover','mousedown','mouseup','click']) "
                "e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));",
                button,
            )
        logging.info(f"  Tombol Multi Op. Unit diklik: {exact_id}")
        return
    except Exception as e:
        logging.warning(f"  Tombol Multi Op. Unit ID pasti tidak ditemukan ({exact_id}), coba fallback posisi: {e}")

    def locate(d):
        return d.execute_script(
            """
            const visible=e=>{const s=window.getComputedStyle(e),r=e.getBoundingClientRect();
                return s.visibility!=='hidden'&&s.display!=='none'&&r.width>0&&r.height>0;};
            const textOf=e=>[e.innerText,e.textContent,e.title,e.getAttribute('aria-label'),
                e.getAttribute('lsdata'),e.id].filter(Boolean).join(' ').replace(/\\s+/g,' ').trim().toLowerCase();
            const labels=Array.from(document.querySelectorAll('span,label,div,td')).filter(visible)
                .filter(e=>/op\\.?\\s*unit|operation\\s*unit|operating\\s*unit/.test(textOf(e)));
            const label=labels[0];
            if(!label) return null;
            const lr=label.getBoundingClientRect();
            const controls=Array.from(document.querySelectorAll(
                "button,a,[ct='B'],.lsButton,[role='button'],input[type='button'],img,span[id*='LinkToAction']"
            )).filter(visible).map(e=>({element:e,rect:e.getBoundingClientRect(),text:textOf(e)}))
            .filter(i=>{
                const mid=(i.rect.top+i.rect.bottom)/2, lmid=(lr.top+lr.bottom)/2;
                return Math.abs(mid-lmid)<45&&i.rect.left>lr.right&&!/so\\s*search|excel|detail|mass/.test(i.text);
            }).sort((a,b)=>a.rect.left-b.rect.left);
            const multi=controls.find(i=>/multi|multiple|multi\\s*selection/.test(i.text));
            if(multi) return multi.element;
            if(controls.length>=2) return controls[controls.length-2].element;
            return controls[0]?.element||null;
            """
        )

    button = WebDriverWait(driver, timeout).until(lambda d: locate(d) or False)
    driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", button)
    time.sleep(0.2)
    try:
        ActionChains(driver).move_to_element(button).pause(0.1).click(button).perform()
    except Exception:
        driver.execute_script(
            "const e=arguments[0]; e.focus?.(); for(const ev of ['mouseover','mousedown','mouseup','click']) "
            "e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));",
            button,
        )
    logging.info("  Tombol Multi Op. Unit diklik (fallback posisi)")


def select_all_operation_units(driver):
    try:
        logging.info("  Memilih semua Op. Unit dari popup value-help")
        op_unit_field = find_input_by_label(
            driver,
            ["Op. Unit", "Op Unit", "Operation Unit", "Operating Unit"],
            timeout=20,
        )
        set_field_value(driver, op_unit_field, "")
        click_op_unit_multi_button(driver, timeout=20)
        logging.info("  Menunggu popup iframe Op. Unit terbuka...")
        switch_to_visible_popup_frame(driver, timeout=30)
        set_popup_max_selection_count(driver, "9999")
        logging.info("  Klik tombol Search di popup Op. Unit...")
        try:
            click_popup_button(driver, ["Search", "Find"], timeout=20, exclude_labels=["SO Search"])
            logging.info("  Tombol Search popup diklik")
        except Exception as e:
            logging.warning(f"  Tombol Search popup tidak ditemukan: {e}")
        time.sleep(3)
        selected_count = select_all_visible_options(driver, timeout=25)
        if selected_count <= 0:
            raise RuntimeError("Tidak menemukan checkbox/Select All di popup Op. Unit")
        logging.info(f"  Select all Op. Unit ({selected_count} klik/opsi)")
        time.sleep(1)
        click_popup_button(
            driver,
            ["Confirm", "OK", "Apply", "Transfer", "Copy"],
            timeout=20,
            exclude_labels=["Cancel", "Close", "Delete"],
        )
        time.sleep(2)
        driver.switch_to.default_content()
        switch_to_search_client_odr_context(driver, timeout=20)
        logging.info("  Semua Op. Unit dikonfirmasi")
        return True
    except Exception as e:
        logging.error(f"  Gagal memilih semua Op. Unit: {e}")
        save_debug_artifacts(driver, "select_all_op_units_failed")
        try:
            driver.switch_to.default_content()
            switch_to_search_client_odr_context(driver, timeout=10)
        except Exception:
            pass
        return False


# ─────────────────────────── DATE RANGE HELPERS ───────────────────────────────

def parse_smro_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Format tanggal tidak valid: {value}. Gunakan YYYY/MM/DD.")


def format_smro_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def add_months(value: date, months: int) -> date:
    idx = value.month - 1 + months
    year = value.year + idx // 12
    month = idx % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def iter_month_ranges(start_date: date, end_date: date):
    """Yield range per bulan penuh."""
    current = start_date
    while current <= end_date:
        next_month = add_months(date(current.year, current.month, 1), 1)
        month_end = min(next_month - timedelta(days=1), end_date)
        yield current, month_end
        current = month_end + timedelta(days=1)


def iter_day_ranges(start_date: date, end_date: date, days: int):
    """Yield range per N hari."""
    current = start_date
    while current <= end_date:
        current_end = min(current + timedelta(days=days - 1), end_date)
        yield current, current_end
        current = current_end + timedelta(days=1)


def _resolve_date_range_from_preset():
    """
    FIX V13: Hitung (start_date, end_date) berdasarkan DATE_RANGE_PRESET.

    FIX V18: preset PER-PROSES — fungsi ini hanya untuk Search Client Odr
    (env SMRO_DATE_RANGE_PRESET; fallback DATE_RANGE_PRESET global era V17).
    SERP & Pur Info Reg membaca preset mereka sendiri di modul masing-masing.

    Pilihan preset (semua mundur dari hari ini, end_date selalu = hari ini):
      - '1week'     : 7 hari terakhir
      - '1month'    : 1 bulan terakhir (30 hari)
      - '3month'    : 3 bulan terakhir (90 hari)
      - '6month'    : 6 bulan terakhir (180 hari)
      - 'this_year' : 1 Januari tahun ini s/d hari ini
      - 'custom'    : pakai SMRO_START_DATE / SMRO_END_DATE manual dari .env

    Return (start_date, end_date) sebagai datetime.date.
    """
    from datetime import date as _date
    preset = CONFIG.get('DATE_RANGE_PRESET', 'custom')
    today = _date.today()
    if preset == '1week':
        start = today - timedelta(days=7)
        logging.info(f"  Date Range preset Search Client Odr '{preset}': {start} s/d {today}")
        return start, today
    if preset == '1month':
        # 1 bulan terakhir: mundur 30 hari supaya selalu konsisten panjangnya
        start = today - timedelta(days=30)
        logging.info(f"  Date Range preset Search Client Odr '{preset}': {start} s/d {today}")
        return start, today
    if preset == '3month':
        # 3 bulan terakhir: mundur ~90 hari
        start = today - timedelta(days=90)
        logging.info(f"  Date Range preset Search Client Odr '{preset}': {start} s/d {today}")
        return start, today
    if preset == '6month':
        # 6 bulan terakhir: mundur ~180 hari
        start = today - timedelta(days=180)
        logging.info(f"  Date Range preset Search Client Odr '{preset}': {start} s/d {today}")
        return start, today
    if preset == 'this_year':
        start = _date(today.year, 1, 1)
        logging.info(f"  Date Range preset Search Client Odr '{preset}': {start} s/d {today}")
        return start, today
    # 'custom' atau nilai tidak dikenal -> pakai START_DATE/END_DATE manual
    logging.info("  Date Range preset Search Client Odr 'custom' (tanggal manual dari Settings)")
    start = parse_smro_date(CONFIG["START_DATE"])
    end = parse_smro_date(CONFIG["END_DATE"]) if CONFIG["END_DATE"].strip() else today
    return start, end


def get_download_date_ranges():
    """
    Default: 1 Jan 2024 s/d hari ini, per bulan.
    Split otomatis jika kena limit:
      1 bulan → 2 minggu → 1 minggu

    FIX V13: baca DATE_RANGE_PRESET. Kalau bukan 'custom', start/end dihitung
    otomatis mundur dari hari ini (end = today). Pilihan preset lihat
    _resolve_date_range_from_preset().
    """
    start, end = _resolve_date_range_from_preset()
    if start > end:
        raise ValueError("SMRO_START_DATE tidak boleh lebih besar dari SMRO_END_DATE/hari ini")
    return [(d1, d2, "month") for d1, d2 in iter_month_ranges(start, end)]


def split_range_if_limit(date_from: date, date_to: date, range_level: str):
    """
    Pecah range jika hasil search menyentuh SEARCH_LIMIT_ROWS:
      month  → 2week (14 hari)
      2week  → 1week (7 hari)
      1week  → stop, data sudah tidak bisa dipecah lebih kecil
    """
    if range_level == "month":
        return [(d1, d2, "2week") for d1, d2 in iter_day_ranges(date_from, date_to, 14)]
    if range_level == "2week":
        return [(d1, d2, "1week") for d1, d2 in iter_day_ranges(date_from, date_to, 7)]
    # 1week → tidak dipecah lagi, download tetap dilanjutkan dengan warning
    return None


def set_search_date_range(driver, date_from: date, date_to: date):
    date_from_text = format_smro_date(date_from)
    date_to_text = format_smro_date(date_to)
    try:
        date_from_field = driver.find_element(By.ID, "aaaa.SearchOrderCompView.DATE_FROM")
    except Exception:
        date_from_field = find_input_by_label(driver,
            ["Date From", "Create Date From", "Creation Date From", "SO Date From", "Order Date From"], timeout=8)
    set_field_value(driver, date_from_field, date_from_text)

    try:
        date_to_field = driver.find_element(By.ID, "aaaa.SearchOrderCompView.DATE_TO")
    except Exception:
        date_to_field = find_input_by_label(driver,
            ["Date To", "Create Date To", "Creation Date To", "SO Date To", "Order Date To"], timeout=8)
    set_field_value(driver, date_to_field, date_to_text)
    logging.info(f"  Date range: {date_from_text} - {date_to_text}")


# ───────────────────────── DOWNLOAD FILE HANDLING ─────────────────────────────

def get_blob_iframes(driver):
    """Return list of current blob: iframe src URLs in the page (recursive)."""
    try:
        return driver.execute_script(
            """
            function findBlobIframes(doc) {
                let blobs = [];
                try {
                    const iframes = doc.querySelectorAll('iframe');
                    for (const iframe of iframes) {
                        if (iframe.src && iframe.src.startsWith('blob:')) {
                            blobs.push(iframe.src);
                        }
                        try {
                            if (iframe.contentDocument) {
                                blobs = blobs.concat(findBlobIframes(iframe.contentDocument));
                            }
                        } catch(e) { /* cross-origin, skip */ }
                    }
                } catch(e) {}
                return blobs;
            }
            return findBlobIframes(document);
        """
        ) or []
    except Exception:
        return []


def classify_download_bytes(file_bytes: bytes, mime: str = "") -> tuple[str | None, str]:
    head = (file_bytes or b"")[:512].lstrip()
    lower_head = head.lower()
    lower_mime = (mime or "").lower()
    if not file_bytes:
        return None, "file kosong"
    if lower_head.startswith(b"<") or b"<html" in lower_head or "html" in lower_mime or "htm" in lower_mime:
        return None, "HTML/error page"
    if head.startswith(b"PK"):
        return ".xlsx", "xlsx zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return ".xls", "xls binary"
    if "csv" in lower_mime or b"," in head[:200]:
        return ".csv", "csv"
    if "spreadsheet" in lower_mime or "excel" in lower_mime or "xlsx" in lower_mime:
        return ".xlsx", f"mime {mime}"
    return None, "format tidak dikenal"

def validate_download_file(file_path: Path) -> bool:
    try:
        with file_path.open("rb") as f:
            sample = f.read(4096)
        ext, reason = classify_download_bytes(sample, "")
        if ext is None:
            logging.warning(f"  File download ditolak: {file_path.name} ({reason})")
            return False
        if file_path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
            return True
        logging.warning(f"  File download ditolak: ekstensi tidak valid {file_path.name}")
        return False
    except Exception as e:
        logging.warning(f"  Validasi file download gagal: {file_path.name}: {e}")
        return False

def upload_mime_type(file_path: Path) -> str:
    return {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
    }.get(file_path.suffix.lower(), "application/octet-stream")

def download_blob_as_file(driver, blob_url: str, dest_dir: Path, timeout: int = 60) -> Path | None:
    """
    Fetch a blob: URL via JS and save the bytes to dest_dir as .xlsx.
    Returns the saved Path, or None on failure.
    """
    try:
        # Set async script timeout sebelum execute_script async
        # execute_script() tidak menerima kwarg script_timeout — harus set via driver
        prev_timeout = driver.timeouts.script
        driver.set_script_timeout(timeout)
        try:
            b64 = driver.execute_script(
                """
                const url = arguments[0];
                return new Promise((resolve, reject) => {
                    fetch(url)
                        .then(r => r.blob())
                        .then(blob => {
                            const reader = new FileReader();
                            reader.onloadend = () => {
                                // result is "data:<mime>;base64,<data>"
                                resolve(reader.result);
                            };
                            reader.onerror = () => reject('FileReader error');
                            reader.readAsDataURL(blob);
                        })
                        .catch(e => reject(String(e)));
                });
                """,
                blob_url,
            )
        finally:
            try:
                driver.set_script_timeout(prev_timeout)
            except Exception:
                pass
        if not b64 or not isinstance(b64, str):
            return None
        # Strip data URL prefix: "data:...;base64,"
        if ";base64," not in b64:
            return None
        raw_b64 = b64.split(";base64,", 1)[1]
        import base64 as _b64
        file_bytes = _b64.b64decode(raw_b64)
        mime = b64.split(";base64,")[0].split("data:")[-1].lower()
        ext, reason = classify_download_bytes(file_bytes, mime)
        if not ext:
            logging.info(f"  Blob bukan file Excel: {reason} (mime={mime}, {len(file_bytes)/1024:.1f} KB)")
            return None
        ts = int(time.time())
        dest_path = dest_dir / f"blob_download_{ts}{ext}"
        dest_path.write_bytes(file_bytes)
        logging.info(f"  Blob download disimpan: {dest_path.name} ({len(file_bytes)/1024:.1f} KB, {reason}, mime={mime})")
        return dest_path
    except Exception as e:
        logging.warning(f"  Gagal download blob URL: {e}")
        return None


# ── FIX V20: popup "Allow" sebelum download dimulai (Chrome layer) ─────────────

# JS yang mencari + mengklik tombol "Allow" di dialog IN-PAGE (ExtJS/SAP
# portal). Hanya tombol di dalam container dialog yang visible — tidak
# menyentuh tombol lain di halaman biasa.
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


def pregrant_download_permissions(driver) -> bool:
    """FIX V20: pre-grant permission download Chrome via CDP supaya popup
    '... ingin mengunduh beberapa file sekaligus' (tombol Allow/Block) dan
    prompt notifikasi TIDAK MUNCUL sebelum download dimulai.

    Dipanggil otomatis oleh setup_driver() untuk setiap browser yang dibuat
    (termasuk saat reopen setelah VPN reconnect). Aman dipanggil ulang.
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
            # Chrome lama / beberapa build headless menolak — bukan fatal,
            # watcher alert/Allow tetap berjalan sebagai jaring pengaman.
            logging.debug(f"  CDP setPermission '{perm_name}' gagal: {e}")
    if granted:
        logging.info(f"  Permission download Chrome di-pre-grant via CDP ({granted} item)")
    return granted > 0


def accept_js_alert_if_present(driver) -> bool:
    """FIX V20: kalau ada JS alert/confirm di window aktif (mis. portal/SSO
    bertanya 'Allow...?'), terima + catat teksnya ke log supaya bisa
    didiagnosis jarak jauh. Aman dipanggil kapan pun — return False kalau
    memang tidak ada alert."""
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


def click_allow_button_in_page(driver) -> int:
    """FIX V20: cari & klik tombol 'Allow' di popup IN-PAGE (dialog ExtJS /
    SAP portal, BUKAN JS alert). Popup portal biasa: .x-window /
    .x-message-box / [role=dialog] — tombolnya button/a/span berteks Allow.

    Return jumlah tombol yang terklik (0 = tidak ada popup Allow).
    """
    try:
        clicked = driver.execute_script(_ALLOW_BUTTON_CLICK_JS)
    except Exception as e:
        # Context bisa sedang di frame lain / chrome:// — normal, bukan error
        logging.debug(f"  click_allow_button_in_page: {e}")
        return 0
    if clicked:
        logging.info(f"  [V20] Tombol Allow di popup halaman diklik ({clicked}x)")
    return clicked or 0


def clear_download_allow_popups(driver):
    """FIX V20: satu pintu bersih untuk menghilangkan SEMUA jenis popup yang
    menghalangi download di Chrome: (1) JS alert/confirm → accept,
    (2) dialog in-page dengan tombol Allow/Izinkan → klik.

    Dipanggil dari loop polling download (wait_for_new_download dan
    verifikasi klik download) — popup apa pun yang muncul akan hilang dalam
    ≤1 detik tanpa menunggu timeout 10 menit."""
    accept_js_alert_if_present(driver)
    click_allow_button_in_page(driver)


def wait_for_new_download(download_dir: Path, before_files, timeout: int = 120,
                          driver=None, before_blobs=None):
    """
    Tunggu file download baru selesai.
    Strategi 1: deteksi blob: iframe baru lalu fetch langsung (untuk SAP Web Dynpro).
    Strategi 2: polling folder download seperti biasa.

    FIX V20: kedua loop polling kini juga meng-clear popup 'Allow' (JS alert /
    dialog in-page) — kalau popup itu muncul tepat setelah tombol download
    diklik, download terblokir sampai popup di-klik; sebelumnya polling hanya
    diam menunggu sampai timeout 600 detik.
    """
    deadline = time.time() + timeout
    before_paths = {str(p.resolve()).lower() for p in before_files}
    accepted = {".xlsx", ".xls", ".csv"}
    rejected_exts = {".htm", ".html"}
    min_valid_size = 1 * 1024  # 1 KB — tolak HTML error pages, cukup untuk CSV header
    before_blobs = set(before_blobs or [])

    # Strategi 1: blob: iframe interception (recursive)
    if driver is not None:
        blob_deadline = time.time() + 60  # naikkan dari 30 ke 60 detik
        blob_check_count = 0
        while time.time() < blob_deadline:
            blob_check_count += 1
            # FIX V20: clear popup Allow tiap ~3 detik (murah — cek alert +
            # scan dialog). Iterasi pertama SELALU supaya popup yang muncul
            # tepat setelah klik download langsung dihilangkan.
            if blob_check_count % 6 == 1:
                try:
                    clear_download_allow_popups(driver)
                except Exception:
                    pass
            current_blobs = set(get_blob_iframes(driver))
            new_blobs = current_blobs - before_blobs
            if blob_check_count % 6 == 0:  # log setiap ~3 detik
                logging.info(f"  Blob check #{blob_check_count}: {len(current_blobs)} blob(s) found, {len(new_blobs)} new")
            if new_blobs:
                blob_url = next(iter(new_blobs))
                logging.info(f"  Blob iframe baru terdeteksi, download langsung: {blob_url[:60]}...")
                saved = download_blob_as_file(driver, blob_url, download_dir, timeout=60)
                if saved and saved.stat().st_size > 0:
                    return saved
                logging.info("  Menunggu file Chrome download sampai selesai...")
                break
            time.sleep(0.5)

    # Strategi 2: polling folder download Chrome.
    # Jangan klik tombol download lagi di sini; caller sudah klik. Klik ulang bikin file duplikat dan partial download tersisa.
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        # FIX V20: clear popup Allow tiap ~3 detik (murah — cuma cek alert +
        # scan dialog). Popup yang muncul terlambat pun tetap diklik, dan
        # download yang tertahan langsung dilepas.
        if driver is not None and poll_count % 3 == 1:
            try:
                clear_download_allow_popups(driver)
            except Exception:
                pass
        all_files = [p for p in download_dir.iterdir()
                     if p.is_file() and str(p.resolve()).lower() not in before_paths]
        partial = [p for p in all_files if p.suffix.lower() in {".crdownload", ".tmp"}]
        # Tolak file .htm/.html (biasanya halaman error SAP), filter juga ukuran minimal
        finished = [p for p in all_files if p.suffix.lower() in accepted and p.stat().st_size >= min_valid_size]
        rejected = [p for p in all_files if p.suffix.lower() in rejected_exts]
        if rejected:
            # .htm/.html muncul — ini berarti SAP return error page, bukan file Excel
            logging.warning(f"  Terdeteksi file HTML error: {rejected[0].name} ({rejected[0].stat().st_size / 1024:.1f} KB). Download dianggap gagal.")
            return None
        if finished and not partial:
            newest = max(finished, key=lambda p: p.stat().st_mtime)
            if validate_download_file(newest):
                return newest
            return None
        time.sleep(1)
    return None


def cleanup_downloaded_excel_files(include_completed: bool = True):
    """Hapus file download sementara. File Excel final bisa dipertahankan untuk audit."""
    exts = {".crdownload", ".tmp", ".htm", ".html"}
    if include_completed:
        exts.update({".xlsx", ".xls", ".csv"})
    deleted = 0
    for fp in CONFIG["DOWNLOAD_DIR"].iterdir():
        if fp.is_file() and fp.suffix.lower() in exts:
            try:
                size = fp.stat().st_size
                fp.unlink()
                deleted += 1
                logging.info(f"  Dihapus: {fp.name} ({size/1024:.1f} KB)")
            except Exception as e:
                logging.warning(f"  Gagal hapus {fp.name}: {e}")
    if deleted:
        logging.info(f"  Total dihapus: {deleted} file")


# ──────────────────────── SEARCH RESULT HELPERS ───────────────────────────────

def table_has_data_rows(driver):
    return bool(driver.execute_script(
        """
        const tbody=document.querySelector("[id$='Table_BasicList-contentTBody']");
        if(!tbody) return false;
        return Array.from(tbody.querySelectorAll("tr")).some(row=>{
            if(!row.hasAttribute("rr")||row.getAttribute("rr")==="-1") return false;
            if((row.innerHTML||"").includes("EMPTYROW")) return false;
            return (row.innerText||row.textContent||"").replace(/\\s+/g,"").length>0;
        });
        """
    ))


def get_search_result_count(driver):
    # Priority: read directly from the SAP MessageBar element (most reliable)
    try:
        direct = driver.execute_script(
            """
            const ids = ['aaaa..WD_MessageArea-txt', 'aaaa..WD_MessageArea', 'WD_MessageArea-txt'];
            for (const id of ids) {
                const el = document.getElementById(id);
                if (el) return (el.innerText || el.textContent || '').trim();
            }
            const mb = document.querySelector('.lsMessageBar__text, .lsMessageBar');
            if (mb) return (mb.innerText || mb.textContent || '').trim();
            return null;
            """
        )
        if direct:
            m = re.search(r"([0-9][0-9,.]*)\s+items?\s+searched", direct, re.IGNORECASE)
            if m:
                return int(m.group(1).replace(",", "").replace(".", ""))
    except Exception:
        pass
    # Fallback: scan full page text
    try:
        text = driver.execute_script(
            """
            const body=document.body?document.body.innerText:"";
            const spans=Array.from(document.querySelectorAll("span,div,td"))
                .map(e=>e.innerText||e.textContent||"").filter(Boolean).join("\n");
            return body+"\n"+spans;
            """
        ) or ""
    except Exception:
        return None
    for pattern in [
        r"([0-9][0-9,.]*)\s+items?\s+searched",
        r"([0-9][0-9,.]*)\s+rows?\s+searched",
        r"([0-9][0-9,.]*)\s+items?\s+found",
        r"([0-9][0-9,.]*)\s+rows?\s+found",
    ]:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            try:
                return int(matches[-1].replace(",", "").replace(".", ""))
            except ValueError:
                return None
    return None


def wait_for_search_result(driver, timeout: int = 45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            loading = driver.execute_script(
                """
                const l=document.querySelector("#ur-loading");
                if(!l) return false;
                const s=window.getComputedStyle(l),r=l.getBoundingClientRect();
                return s.display!=="none"&&s.visibility!=="hidden"&&r.top>-1000&&r.width>0&&r.height>0;
                """
            )
            if loading:
                time.sleep(1)
                continue
        except Exception:
            pass
        count = get_search_result_count(driver)
        if count is not None:
            return count
        if table_has_data_rows(driver):
            return None
        time.sleep(1)
    return None


# ───────────────────────── WINDOW & FRAME SWITCHING ───────────────────────────

def safe_current_url(driver) -> str:
    try:
        return driver.current_url
    except Exception:
        return "[window tidak aktif/tertutup]"


def wait_for_url_in_any_window(driver, expected_text: str, timeout: int = 90, stable_seconds: int = 5):
    deadline = time.time() + timeout
    matched_window = None
    matched_since = None
    last_urls = []

    while time.time() < deadline:
        last_urls = []
        matched_this_round = False

        for window in list(driver.window_handles):
            try:
                driver.switch_to.window(window)
                url = driver.current_url
                last_urls.append(url)

                if expected_text in url:
                    matched_this_round = True
                    if matched_window != window:
                        matched_window = window
                        matched_since = time.time()
                        logging.info(f"  Kandidat window SMRO: {url}")
                    if time.time() - matched_since >= stable_seconds:
                        _ = driver.title
                        return window

                if "error.jsp" in url.lower():
                    logging.error(f"SSO mengarah ke halaman error: {url}")
                    return None
                if "validation.jsp" in url or "sendPostData.jsp" in url:
                    try:
                        driver.execute_script(
                            "if(document.readyState==='complete'&&typeof validation==='function') validation();"
                        )
                    except Exception:
                        pass
            except Exception:
                if matched_window == window:
                    matched_window = None
                    matched_since = None
                continue

        if not matched_this_round:
            matched_window = None
            matched_since = None
        time.sleep(1)

    logging.error("URL terakhir saat menunggu SMRO: " + " | ".join(last_urls))
    return None


def switch_to_window_with_url(driver, expected_text: str, timeout: int = 10, stable_seconds: int = 1):
    window = wait_for_url_in_any_window(driver, expected_text, timeout=timeout, stable_seconds=stable_seconds)
    if not window:
        return False
    driver.switch_to.window(window)
    driver.switch_to.default_content()
    return True


def switch_to_context_with_element(driver, locators, timeout: int = 15):
    def locate(d):
        # FIX V16.2: kalau chromedriver sudah mati, raise RuntimeError (BUKAN
        # WebDriverException) supaya WebDriverWait tidak retry — tanpa ini,
        # tiap iterasi retry men-spam HTTP call ke port chromedriver yang sudah
        # closed (urllib3.Retry 3x per call) selama timeout penuh.
        if not is_driver_alive(d):
            raise RuntimeError("ChromeDriver mati (Chrome crash) saat mencari elemen")
        d.switch_to.default_content()
        el = find_first_visible(d, locators)
        if el:
            return el
        for frame in d.find_elements(By.TAG_NAME, "iframe"):
            try:
                d.switch_to.default_content()
                d.switch_to.frame(frame)
                el = find_first_visible(d, locators)
                if el:
                    return el
            except Exception:
                continue
        d.switch_to.default_content()
        return False
    return WebDriverWait(driver, timeout).until(locate)


def switch_to_search_client_odr_context(driver, timeout: int = 30):
    if not switch_to_window_with_url(driver, "svobpp.serveone.co.kr", timeout=timeout):
        return False
    wait_for_page_ready(driver, timeout=timeout)
    driver.switch_to.default_content()

    if "SD100_001" in safe_current_url(driver):
        return True

    frame_xpath = (
        "//iframe[contains(@id,'SD100_001') or contains(@name,'SD100_001') "
        "or contains(@src,'SD100_001') or contains(@src,'page.SD100_001')]"
    )
    try:
        frame = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, frame_xpath))
        )
        driver.switch_to.frame(frame)
        try:
            wait_for_page_ready(driver, timeout=timeout)
        except Exception:
            pass
        logging.info("  Masuk ke iframe Search Client Odr")
        return True
    except Exception:
        driver.switch_to.default_content()
        return "Search Client Odr" in driver.page_source or "page.SD100_001" in driver.page_source


# ─────────────────────────── DEBUG ARTIFACTS ──────────────────────────────────

def save_debug_artifacts(driver, label: str):
    # FIX V12: kalau driver None (e.g. setup_driver() crash sebelum Chrome
    # sempat start — 'session not created: Chrome failed to start: crashed'),
    # jangan coba save_screenshot/page_source karena akan muncul error
    # 'NoneType' object has no attribute 'save_screenshot' yang menambah noise
    # di log tanpa nilai diagnostik. Cukup catat labelnya saja supaya tetap
    # ada jejak debug di log.
    if driver is None:
        logging.warning(
            f"Debug artifacts dilewati (driver None) untuk label: {label}"
        )
        return
    # FIX V15: kalau ChromeDriver process sudah mati (Chrome closed/crashed
    # sebelum save_debug_artifacts dipanggil), panggilan driver.save_screenshot
    # dan driver.page_source akan memicu urllib3.Retry default 3x dengan
    # backoff 5+ detik tiap retry → 30+ detik log spam "Retrying (Retry(total=N,
    # ...)) after connection broken by 'NewConnectionError'" PER call.
    # total: ~60+ detik spam untuk save_screenshot + page_source.
    #
    # Pre-flight check is_driver_alive (yang sekarang pakai OS-level process
    # state, bukan HTTP probe) untuk skip cleanly tanpa HTTP call.
    if not is_driver_alive(driver):
        logging.warning(
            f"Debug artifacts dilewati (ChromeDriver session closed/tidak responsif) untuk label: {label}"
        )
        return
    debug_dir = _data_dir() / "debug"
    debug_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in label)
    try:
        path = debug_dir / f"{timestamp}_{safe}.png"
        driver.save_screenshot(str(path))
        logging.info(f"Debug screenshot saved: {path}")
    except Exception as e:
        # FIX V15: jangan log full urllib3 traceback. Cukup pesan singkat.
        err_short = str(e)
        if 'NewConnectionError' in err_short or 'Max retries exceeded' in err_short:
            err_short = "ChromeDriver session closed (connection refused)"
        logging.warning(f"Gagal menyimpan screenshot debug: {err_short}")
    try:
        path = debug_dir / f"{timestamp}_{safe}.html"
        path.write_text(driver.page_source, encoding="utf-8", errors="replace")
        logging.info(f"Debug HTML saved: {path}")
    except Exception as e:
        err_short = str(e)
        if 'NewConnectionError' in err_short or 'Max retries exceeded' in err_short:
            err_short = "ChromeDriver session closed (connection refused)"
        logging.warning(f"Gagal menyimpan HTML debug: {err_short}")


def safe_driver_quit(driver, tag: str = ""):
    """Quit driver TANPA memicu urllib3 retry spam jika session sudah dead.

    FIX V15: driver.quit() pada session yang sudah mati juga memicu urllib3
    Retry dengan backoff. Wrapper ini pre-check is_driver_alive dulu; kalau
    process sudah mati, skip quit() sama sekali (process memang sudah gone).

    FIX V16.2: apapun hasilnya (quit sukses / driver mati / quit gagal),
    pastikan proses tree chromedriver+chrome benar-benar bersih via
    force_kill_driver_tree (by PID + by profile) dan PID dihapus dari
    registry. Tanpa ini, chrome.exe ORPHAN menumpuk setelah tiap kegagalan
    → RAM terkuras → Chrome makin lambat (laporan user 2026-08-28).
    """
    if driver is None:
        return
    try:
        if not is_driver_alive(driver):
            logging.info(f"{tag} Driver sudah mati — bersihkan sisa proses tree".strip())
            force_kill_driver_tree(driver, tag=tag)
            return
        try:
            driver.quit()
            _unregister_driver_pid(driver)
            return
        except Exception:
            # quit() gagal (mis. Chrome hang) → force-kill tree by PID+profile.
            logging.info(f"{tag} quit() gagal — force-kill proses tree".strip())
            force_kill_driver_tree(driver, tag=tag)
    except Exception as e:
        err_short = str(e)
        if 'NewConnectionError' in err_short or 'Max retries exceeded' in err_short:
            err_short = "ChromeDriver session closed (connection refused)"
        logging.info(f"{tag} Driver quit() gagal (diabaikan): {err_short}".strip())
        force_kill_driver_tree(driver, tag=tag)


# ──────────────────────────── BROWSER SETUP ───────────────────────────────────

# FIX V13: lock global supaya setup_driver tidak dipanggil bersamaan dari
# multiple thread parallel. Walaupun ChromeDriverManager install sudah di-lock,
# Chrome binary sendiri juga rawan crash kalau dua instance start di saat
# bersamaan dengan user-data-dir default yang sama (saling rebut lock file
# profile). Stagger start dengan lock ini memastikan hanya satu Chrome yang
# sedang dalam proses start pada satu waktu — kena biaya ~2 detik per worker,
# tapi eliminasi 'session not created: Chrome failed to start: crashed.'
_SETUP_DRIVER_LOCK = threading.Lock()

# FIX V16.2: registry PID chromedriver yang MASIH DIPAKAI oleh proses ini.
#
# BUG KRITIS yang diperbaiki: cleanup lama `taskkill /F /IM chromedriver.exe /T`
# membunuh SEMUA chromedriver.exe di sistem — TERMASUK milik worker lain yang
# masih sehat. Di mode parallel 2 worker, urutan kejadiannya (log user
# 2026-08-28 09:05-09:10):
#   1. W1 start Chrome (chromedriver#1 jalan, W1 mulai login).
#   2. W2 start Chrome → setup_driver W2 memanggil cleanup → taskkill MEMBUNUH
#      chromedriver#1 milik W1 → login W1 mati di tengah jalan.
#   3. W1 retry → setup_driver W1 → taskkill MEMBUNUH chromedriver milik W2 →
#      ping-pong saling bunuh di setiap retry.
#   4. Tiap kematian memicu urllib3.Retry spam 13-40 detik per HTTP call
#      ("NewConnectionError ... actively refused" di port chromedriver mati).
#   5. Chrome.exe orphan menumpuk (quit tak pernah jalan) → RAM habis →
#      "Chrome jadi sangat lambat sekali".
#   6. W2 habis 3 retry → menyerah → BARU SETELAH ITU W1 berhasil login
#      (tak ada lagi pembunuh) — persis seperti log user.
#
# Registry ini membuat cleanup jadi TARGETED: hanya proses yang TIDAK dipakai
# session hidup yang boleh dibunuh.
_LIVE_DRIVER_PIDS = set()
_LIVE_DRIVER_PIDS_LOCK = threading.Lock()


def _register_driver_pid(driver):
    """Catat PID chromedriver yang masih hidup (dipanggil setup_driver setelah
    Chrome berhasil start). Dipakai _cleanup_zombie_chrome supaya cleanup tidak
    membunuh chromedriver yang masih dipakai worker lain."""
    try:
        pid = int(driver.service.process.pid)
        with _LIVE_DRIVER_PIDS_LOCK:
            _LIVE_DRIVER_PIDS.add(pid)
    except Exception:
        pass


def _unregister_driver_pid(driver):
    """Hapus PID chromedriver dari registry (dipanggil setelah quit/kill)."""
    try:
        pid = int(driver.service.process.pid)
        with _LIVE_DRIVER_PIDS_LOCK:
            _LIVE_DRIVER_PIDS.discard(pid)
    except Exception:
        pass


def _ps_run(command: str, timeout: int = 20) -> bool:
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


def _kill_chrome_by_profile(profile_dir, tag: str = "") -> bool:
    """Bunuh chrome.exe yang memakai profile_dir ini (pemegang SingletonLock).
    Presisi by command-line match — chrome.exe milik worker lain tidak tersentuh.
    """
    if not profile_dir:
        return False
    try:
        prof = str(profile_dir).replace("'", "''").replace('"', '')
        # Escape karakter wildcard -like supaya path dicocokkan literal.
        prof_pattern = prof.replace('`', '``').replace('[', '`[').replace(']', '`]') \
                           .replace('*', '`*').replace('?', '`?')
        return _ps_run(
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{prof_pattern}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
    except Exception as e:
        logging.debug(f"{tag} Kill chrome by profile gagal (best-effort): {e}".strip())
        return False


def force_kill_driver_tree(driver, tag: str = ""):
    """Kill proses tree chromedriver MILIK driver ini saja (by PID + by profile),
    bukan by image name. Aman untuk parallel: chromedriver/chrome worker lain
    tidak tersentuh.

    FIX V16.2: ini menggantikan pola `taskkill /IM chromedriver.exe` yang
    membunuh semua instance di sistem. Dua lapis:
      1. taskkill /F /T /PID <pid chromedriver> → bunuh chromedriver + seluruh
         chrome.exe anak-anaknya (kalau chromedriver masih hidup).
      2. Kill chrome.exe by profile_dir → bersihkan chrome.exe ORPHAN yang
         ditinggalkan chromedriver yang sudah mati ( mereka masih memegang
         SingletonLock profile → tanpa ini, Chrome start berikutnya crash
         'session not created: Chrome failed to start: crashed').
    """
    if driver is None:
        return
    profile_dir = getattr(driver, '_serveone_profile_dir', None)
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
            _kill_chrome_by_profile(profile_dir, tag=tag)
        logging.debug(f"{tag} Force-kill driver tree PID={pid} profile={profile_dir}".strip())
    except Exception as e:
        logging.debug(f"{tag} Force-kill driver tree gagal (best-effort): {e}".strip())
    finally:
        _unregister_driver_pid(driver)


# FIX V13 → REWRITTEN V16.2: cleanup zombie SEBELUM start baru — sekarang
# TARGETED & aman untuk parallel worker:
#   1. Bunuh chrome.exe yang command-line-nya memuat profile_dir yang akan kita
#      pakai (pemegang lock profile dari attempt sebelumnya yang gagal).
#   2. Bunuh chromedriver.exe yang PID-nya TIDAK ada di _LIVE_DRIVER_PIDS
#      (zombie) — chromedriver yang masih dipakai worker lain DILINDUNGI.
#   3. Fallback taskkill /IM massal HANYA kalau PowerShell tidak tersedia DAN
#      registry live kosong (tidak ada worker lain yang bisa jadi korban).
def _cleanup_zombie_chrome(profile_dir=None):
    import platform
    sys_name = platform.system()

    if sys_name == 'Windows':
        # (1) chrome.exe pemegang lock profile_dir yang akan dipakai.
        if profile_dir is not None:
            _kill_chrome_by_profile(profile_dir)

        # (2) chromedriver.exe zombie = PID tidak ada di registry live.
        with _LIVE_DRIVER_PIDS_LOCK:
            live_pids = sorted(_LIVE_DRIVER_PIDS)
        live_list = ",".join(str(p) for p in live_pids)
        ps_ok = _ps_run(
            f"$live = @({live_list}); "
            "Get-Process chromedriver -ErrorAction SilentlyContinue | "
            "Where-Object { $live -notcontains $_.Id } | "
            "Stop-Process -Force -ErrorAction SilentlyContinue"
        )
        if ps_ok and live_pids:
            logging.debug(f"Zombie chromedriver cleanup: live PID dilindungi ({live_list})")

        # (3) Fallback: hanya berani taskkill massal kalau TIDAK ada chromedriver
        # live (registry kosong). Kalau PowerShell gagal dan ada worker lain
        # hidup, LEWATI cleanup — lebih aman skip daripada membunuh worker lain.
        if not ps_ok:
            with _LIVE_DRIVER_PIDS_LOCK:
                any_live = bool(_LIVE_DRIVER_PIDS)
            if not any_live:
                subprocess.run(
                    ['taskkill', '/F', '/IM', 'chromedriver.exe', '/T'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
            else:
                logging.debug(
                    "Zombie chromedriver cleanup dilewati "
                    "(PowerShell tidak tersedia, ada chromedriver live lain yang dilindungi)"
                )
    else:
        # Linux/Mac: bunuh hanya chromedriver yang TIDAK live (zombie).
        with _LIVE_DRIVER_PIDS_LOCK:
            live_pids = set(_LIVE_DRIVER_PIDS)
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


def setup_driver(download_dir=None, driver_path=None, profile_tag=None):
    """
    Setup Chrome WebDriver dengan hardening penuh untuk mencegah error
    'session not created: Chrome failed to start: crashed.' yang sering terjadi
    saat multiple worker parallel start Chrome bersamaan.

    FIX V13:
    - Setiap call pakai --user-data-dir UNIK (folder terpisah per profile_tag/
      worker), jadi tidak ada lagi saling rebut lock file profile Chrome.
    - --remote-debugging-port=0 supaya Chrome otomatis pilih port bebas,
      tidak bentrok dengan instance lain.
    - Cleanup zombie chromedriver.exe SEBELUM start baru (Windows: taskkill).
    - _SETUP_DRIVER_LOCK men-serialize start Chrome antar thread supaya tidak
      barengan (stagger start ~2 detik per worker, eliminasi crash parallel).
    - Tambah flag anti-crash tambahan: --no-first-run, --no-default-browser-check,
      --disable-extensions, --disable-plugins, --disable-default-apps,
      --disable-component-extensions-with-background-pages, --disable-features=...
    """
    if download_dir is None:
        download_dir = CONFIG['DOWNLOAD_DIR']
    download_dir = Path(download_dir)
    download_dir.mkdir(exist_ok=True)

    # FIX V13: profile_dir unik per call. Kalau profile_tag diberikan (mis.
    # worker_1), pakai itu supaya reuseable antar retry di worker yang sama.
    # Kalau tidak, pakai UUID singkat supaya selalu unik.
    import uuid
    if profile_tag is None:
        profile_tag = uuid.uuid4().hex[:8]
    profile_dir = download_dir.parent / 'chrome_profiles' / profile_tag
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback: pakai system temp dir supaya tetap jalan walau DOWNLOAD_DIR
        # read-only atau penuh.
        import tempfile
        profile_dir = Path(tempfile.gettempdir()) / 'serveone_chrome' / profile_tag
        profile_dir.mkdir(parents=True, exist_ok=True)

    # FIX V13: serial start antar thread. Kalau 2 worker start barengan, salah
    # satunya sering crash walau user-data-dir beda — Chrome binary sendiri
    # ada window race saat init. Lock ini memaksa start berurutan.
    with _SETUP_DRIVER_LOCK:
        # FIX V16.2: cleanup zombie SEBELUM start baru — sekarang TARGETED:
        # chrome.exe by profile_dir + chromedriver zombie by PID (yang tidak
        # ada di registry live). Chromedriver worker lain TIDAK dibunuh lagi.
        _cleanup_zombie_chrome(profile_dir=profile_dir)

        opts = Options()
        if CONFIG['HEADLESS']:
            opts.add_argument('--headless=new')
        for arg in [
            '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
            '--disable-popup-blocking', '--disable-background-networking',
            '--disable-component-update', '--disable-sync', '--disable-notifications',
            '--disable-features=PushMessaging,NotificationTriggers,InsecureDownloadWarnings,TranslateUI,ChromeWhatsNewUI',
            '--unsafely-treat-insecure-origin-as-secure=http://svobpp.serveone.co.kr',
            '--window-size=1920,1080', '--disable-blink-features=AutomationControlled',
            '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            # FIX V13: anti-crash flags untuk parallel start. Tanpa ini, Chrome
            # versi baru sering crash saat start barengan dari folder yang sama
            # atau saat ada instance Chrome lain yang masih hidup di latar.
            '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', '--disable-plugins', '--disable-default-apps',
            '--disable-component-extensions-with-background-pages',
            '--disable-hang-monitor', '--disable-prompt-on-repost',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-ipc-flood-protection',
            # FIX V13: user-data-dir unik per call + remote-debugging-port 0
            # (port auto-pick). INI PENYEBAB UTAMA 'session not created':
            # parallel Chrome start pakai user-data-dir default Chrome yang
            # SAMA -> saling rebut lock SingletonLock di folder profile ->
            # salah satunya crash sebelum sempat bikin session. Dengan
            # profile_dir unik per call, lock tidak pernah di-contest.
            f'--user-data-dir={profile_dir}',
            '--remote-debugging-port=0',
            '--password-store=basic',
            '--use-mock-keychain',
        ]:
            opts.add_argument(arg)
        opts.add_experimental_option('excludeSwitches', ['enable-automation'])
        opts.add_experimental_option('useAutomationExtension', False)
        if CONFIG['KEEP_BROWSER_OPEN_ON_ERROR']:
            opts.add_experimental_option('detach', True)
        opts.add_experimental_option('prefs', {
            'download.default_directory': str(download_dir),
            'download.prompt_for_download': False,
            'download.directory_upgrade': True,
            'safebrowsing.enabled': False,  # False agar tidak block download dari HTTP
            'plugins.always_open_pdf_externally': True,
            # FIX V20: auto-allow "download beberapa file sekaligus" — tanpa ini
            # Chrome memunculkan prompt Allow/Block setelah download ke-2, dan
            # proses per-bulan (Pur Info Reg) / per-batch yang download berkali-
            # kali akan TERBLOKIR diam-diam sampai popup diklik manual.
            'profile.default_content_setting_values.automatic_downloads': 1,
        })

        # FIX V24: rantai fallback chromedriver berlapis — menutup error
        # 'certificate verify failed: self-signed certificate in chain'
        # saat ChromeDriverManager download driver di jaringan kantor
        # ber-SSL-inspection. Urutan: path eksplisit -> chromedriver.exe
        # lokal -> cache process -> Selenium Manager (Windows cert store)
        # -> ChromeDriverManager normal -> ChromeDriverManager SSL-off.
        driver = _create_chrome_with_fallback(opts, driver_path=driver_path)
        # FIX V16.2: catat PID chromedriver yang hidup + stash profile_dir di
        # driver object supaya cleanup/quit/force-kill bisa TARGETED (by PID /
        # by profile) dan tidak pernah menyentuh chromedriver worker lain.
        try:
            driver._serveone_profile_dir = str(profile_dir)
        except Exception:
            pass
        _register_driver_pid(driver)
        driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
            'behavior': 'allow', 'downloadPath': str(download_dir)
        })
        # FIX V20: pre-grant permission via CDP supaya popup Chrome
        # "... ingin mengunduh beberapa file sekaligus" (Allow/Block) tidak
        # pernah muncul sebelum download dimulai. Pref di atas kadang
        # diabaikan Chrome versi baru; CDP ini penutup celahnya.
        pregrant_download_permissions(driver)
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
            '''
        })
        logging.info(f"Chrome WebDriver initialized (profile: {profile_tag})")
        return driver


# ───────────────────────────── VPN CHECK ──────────────────────────────────────

def check_vpn_connection():
    try:
        resp = requests.get(CONFIG['LOGIN_START_URL'], timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            logging.info("✓ VPN terhubung ke server ServeOne")
            return True
        logging.warning(f"⚠ Server respond dengan status: {resp.status_code}")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"✗ VPN tidak terhubung: {e}")
        return False


# ──────────────────────────────── LOGIN ───────────────────────────────────────

def wait_for_fingerprint_redirect(driver, timeout: int = 40):
    end_time = time.time() + timeout
    last_url = ""
    while time.time() < end_time:
        # FIX V16.2: kalau ChromeDriver mati di tengah (Chrome crash / dibunuh),
        # BERHENTI SEGERA. Tanpa ini, tiap iterasi loop memanggil current_url /
        # title / page_source / execute_script pada driver mati — tiap call
        # memicu urllib3.Retry 3x (13-40 detik spam "Retrying (Retry(total=N,...))"
        # ke port chromedriver yang sudah closed) dan loop jalan sampai timeout
        # penuh = menit-menit log spam yang persis terlihat di log user.
        if not is_driver_alive(driver):
            logging.warning("ChromeDriver mati saat menunggu fingerprint redirect — berhenti menunggu")
            return False
        try:
            wait_for_page_ready(driver, timeout=10)
        except Exception:
            pass
        try:
            current_url = driver.current_url or ""
            title = driver.title or ""
            page = driver.page_source or ""
            last_url = current_url
            is_fingerprint = (
                "FingerPrint" in title
                or "putFingerPrint" in page
                or "cmd=putfingerprint" in page
                or "/nxs" in page
                or "INISAFE Nexess" in page
            )
            has_login_form = (
                "idPasswordLogin" in current_url
                or "loginFormPage" in current_url
                or "password" in page.lower()
                or "userid" in page.lower()
                or "user id" in page.lower()
            )
            if has_login_form and not is_fingerprint:
                logging.info("Login form sudah siap")
                return True
            if is_fingerprint:
                logging.info("Halaman FingerPrint terdeteksi, menunggu redirect login...")
                try:
                    driver.execute_script(
                        "if (typeof putFingerPrint === 'function') { try { putFingerPrint(); } catch(e) {} }"
                    )
                except Exception:
                    pass
            time.sleep(1)
        except Exception:
            time.sleep(1)
    logging.warning(f"Fingerprint/login redirect belum selesai. URL terakhir: {last_url}")
    return False


def login_to_portal(driver):
    try:
        logging.info("Melakukan login ke portal...")
        if not CONFIG['PASSWORD']:
            logging.error("Password kosong. Isi SMRO_PASSWORD di file .env terlebih dahulu.")
            return False

        driver.get(CONFIG['LOGIN_START_URL'])
        wait_for_fingerprint_redirect(driver, timeout=45)
        wait_for_page_ready(driver)

        wait = WebDriverWait(driver, 30)
        username_locators = [
            (By.ID, 'userid'), (By.NAME, 'userid'),
            (By.ID, 'userId'), (By.NAME, 'userId'),
            (By.XPATH, "//input[not(@type='hidden') and ("
             "contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'user') or "
             "contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'user') or "
             "contains(translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'user') or "
             "contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'user') or "
             "contains(translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'id'))]"),
        ]
        password_locators = [
            (By.ID, 'password'), (By.NAME, 'password'),
            (By.XPATH, "//input[@type='password' or "
             "contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pass') or "
             "contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pass')]"),
        ]
        login_button_locators = [
            (By.CSS_SELECTOR, "img[onclick*='doSubmit']"),
            (By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"),
            (By.XPATH, "//*[self::button or self::a or self::img or self::input]["
             "contains(translate(@onclick,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'dosubmit') or "
             "contains(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login') or "
             "contains(translate(@alt,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login') or "
             "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]"),
        ]

        try:
            username_field = switch_to_context_with_element(driver, username_locators, timeout=30)
        except TimeoutException:
            driver.switch_to.default_content()
            if 'sportal.serveone.id' in driver.current_url or 'PortalView.do' in driver.current_url:
                logging.info("Sudah berada di portal, login dilewati.")
                return True
            raise

        username_field.clear()
        username_field.send_keys(CONFIG['USERNAME'])
        logging.info(f"  Username: {CONFIG['USERNAME']}")

        password_field = find_first_visible(driver, password_locators)
        if not password_field:
            password_field = switch_to_context_with_element(driver, password_locators, timeout=10)
        password_field.clear()
        password_field.send_keys(CONFIG['PASSWORD'])
        logging.info("  Password: [HIDDEN]")

        login_button = find_first_visible(driver, login_button_locators)
        if login_button:
            login_button.click()
            logging.info("  Tombol login diklik")
        else:
            password_field.send_keys(Keys.ENTER)
            logging.info("  Tombol login tidak ditemukan, submit dengan Enter")

        # FIX V16.2: cek driver hidup DI DALAM predicate supaya WebDriverWait
        # berhenti segera kalau Chrome crash di tengah redirect (tidak menunggu
        # 30 detik timeout sambil spam retry HTTP ke chromedriver yang mati).
        def _login_redirect_ok(d):
            if not is_driver_alive(d):
                return True  # hentikan wait segera; pengecekan setelah ini yang memutuskan
            url = d.current_url or ""
            return 'sportal.serveone.id' in url or 'PortalView.do' in url

        wait.until(_login_redirect_ok)
        if not is_driver_alive(driver):
            logging.error("Login gagal: Chrome/ChromeDriver mati saat proses login")
            return False
        wait_for_page_ready(driver)
        logging.info("Login berhasil")
        return True

    except TimeoutException as e:
        logging.error(f"Login gagal karena form/redirect tidak ditemukan: {e}")
        save_debug_artifacts(driver, "login_failed")
        return False
    except Exception as e:
        logging.error(f"Login gagal: {e}")
        save_debug_artifacts(driver, "login_error")
        return False


# ──────────────────────────── POPUP CLOSE ─────────────────────────────────────

def close_popup_if_exists(driver):
    try:
        windows = driver.window_handles
        if len(windows) > 1:
            # Chrome 152 dapat hang lalu mematikan session saat driver.close()
            # dipanggil pada popup SSO. Biarkan window hidup; pilih window utama.
            driver.switch_to.window(windows[0])
            logging.info("  Popup SSO dibiarkan terbuka; fokus kembali ke window utama")
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, "button[class*='close'], img[alt*='close'], .x-tool-close"):
                if btn.is_displayed():
                    btn.click()
                    time.sleep(1)
                    break
        except Exception:
            pass
        return True
    except Exception as e:
        logging.warning(f"⚠ Tidak ada popup atau gagal menutup: {e}")
        return False


# ─────────────────────────── NAVIGATION ──────────────────────────────────────

def navigate_to_smro(driver):
    try:
        logging.info("Membuka menu SMRO...")
        wait = WebDriverWait(driver, 20)

        driver.switch_to.default_content()
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.NAME, "portal_header")))
        logging.info("  Masuk ke iframe portal_header")

        quick_menu = wait.until(EC.element_to_be_clickable((
            By.XPATH, "//select[option[contains(normalize-space(.),'Quick Menu')]]"
        )))
        select = Select(quick_menu)
        smro_options = [
            opt for opt in select.options
            if "sso_smro.jsp" in (opt.get_attribute("value") or "") or "S-MRO" in (opt.text or "")
        ]

        if not smro_options:
            available = [f"{o.text.strip()} => {o.get_attribute('value')}" for o in select.options]
            logging.error("Option Quick Menu tersedia: " + " | ".join(available))
            raise TimeoutException("Option -S-MRO tidak ditemukan di Quick Menu")

        smro_url = smro_options[0].get_attribute("value")
        if not smro_url:
            raise TimeoutException("Option -S-MRO ditemukan, tapi value URL kosong")

        old_handles = set(driver.window_handles)
        driver.execute_script(
            """
            const qm=arguments[0], url=arguments[1];
            qm.value=url;
            if(typeof goUrlLink==='function') goUrlLink(url);
            else qm.dispatchEvent(new Event('change',{bubbles:true}));
            qm.selectedIndex=0;
            """,
            quick_menu, smro_url,
        )
        logging.info(f"  Quick Menu S-MRO dibuka: {smro_url}")
        driver.switch_to.default_content()

        try:
            WebDriverWait(driver, 20).until(
                lambda d: len(set(d.window_handles) - old_handles) > 0
                or "svobpp.serveone.co.kr" in d.current_url
            )
        except TimeoutException:
            logging.warning("Popup SMRO tidak terbuka, mencoba buka URL SMRO di tab saat ini.")
            driver.get(smro_url)

        new_handles = set(driver.window_handles) - old_handles
        if new_handles:
            driver.switch_to.window(next(iter(new_handles)))
            logging.info(f"  Window SMRO/SSO aktif: {driver.current_url}")

        smro_window = wait_for_url_in_any_window(driver, "svobpp.serveone.co.kr", timeout=120)
        if not smro_window:
            raise TimeoutException(f"SMRO belum terbuka. URL aktif: {safe_current_url(driver)}")
        driver.switch_to.window(smro_window)
        wait_for_page_ready(driver, timeout=20)
        logging.info(f"Halaman SMRO terbuka: {driver.current_url}")
        return True

    except TimeoutException as e:
        logging.error(f"Navigasi ke SMRO gagal: {e}")
        save_debug_artifacts(driver, "navigate_smro_failed")
        return False
    except Exception as e:
        logging.error(f"Navigasi ke SMRO gagal: {e}")
        save_debug_artifacts(driver, "navigate_smro_error")
        return False


def open_search_client_odr(driver):
    try:
        logging.info("Membuka Search Client Odr...")
        if not switch_to_window_with_url(driver, "svobpp.serveone.co.kr", timeout=30):
            logging.error("Window SMRO tidak aktif saat membuka Search Client Odr")
            save_debug_artifacts(driver, "search_client_no_smro_window")
            return False

        target_url = CONFIG['SMRO_SEARCH_CLIENT_ODR_URL']
        opened = False
        try:
            opened = driver.execute_script(
                """
                const url=arguments[0];
                if(typeof openTab==='function'){
                    openTab(url,'Search Client Odr.','SD100_001',window.mainUrl||'','doRefresh');
                    return true;
                }
                return false;
                """,
                target_url,
            )
        except Exception as e:
            logging.warning(f"  Tidak bisa membuka via openTab, fallback direct URL: {e}")

        if not opened:
            driver.get(target_url)
            logging.info(f"  Direct URL Search Client Odr dibuka: {target_url}")
        else:
            logging.info("  Search Client Odr dibuka via framework tab")

        wait_for_page_ready(driver, timeout=30)
        WebDriverWait(driver, 60).until(
            lambda d: (
                "SD100_001" in d.current_url
                or "Search Client Odr" in d.page_source
                or "page.SD100_001" in d.page_source
                or len(d.find_elements(By.XPATH, "//iframe[contains(@src,'SD100_001')]")) > 0
                or len(d.find_elements(By.ID, "tab_ifrm_SD100_001")) > 0
            )
        )
        logging.info(f"Search Client Odr siap: {driver.current_url}")
        return True

    except Exception as e:
        logging.error(f"Gagal membuka Search Client Odr: {e}")
        save_debug_artifacts(driver, "open_search_client_odr_failed")
        return False


# ─────────────────────────── SEARCH OP UNIT FLOW ──────────────────────────────

def switch_to_search_op_unit_context(driver, timeout: int = 30):
    if not switch_to_window_with_url(driver, "svobpp.serveone.co.kr", timeout=timeout):
        return False

    end_time = time.time() + timeout
    last_error = None
    while time.time() < end_time:
        try:
            driver.switch_to.default_content()
            if "MC200_001_1" in safe_current_url(driver):
                return True
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            for frame in frames:
                try:
                    driver.switch_to.default_content()
                    frame_id = " ".join([
                        frame.get_attribute("id") or "",
                        frame.get_attribute("name") or "",
                        frame.get_attribute("src") or "",
                    ])
                    if "MC200_001_1" not in frame_id and "page.MC200_001_1" not in frame_id:
                        continue
                    driver.switch_to.frame(frame)
                    logging.info("  Masuk ke iframe Search Op. Unit")
                    return True
                except Exception as e:
                    last_error = e
                    continue
            for frame in frames:
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(frame)
                    found = driver.execute_script(
                        "return !!document.getElementById('aaaa.OptCheckCompView.Button') || "
                        "(document.body && document.body.innerText.includes('Op. Unit ID'));"
                    )
                    if found:
                        logging.info("  Masuk ke iframe Search Op. Unit via fallback")
                        return True
                except Exception as e:
                    last_error = e
                    continue
        except Exception as e:
            last_error = e
        time.sleep(0.5)

    logging.error(f"Gagal masuk context Search Op. Unit: {last_error}")
    return False


def open_search_op_unit(driver):
    try:
        logging.info("Membuka Search Op. Unit...")
        if not switch_to_window_with_url(driver, "svobpp.serveone.co.kr", timeout=30):
            logging.error("Window SMRO tidak aktif saat membuka Search Op. Unit")
            save_debug_artifacts(driver, "search_op_unit_no_smro_window")
            return False

        target_url = CONFIG["SMRO_SEARCH_OP_UNIT_URL"]
        opened = False
        try:
            opened = driver.execute_script(
                """
                const url = arguments[0];
                if (typeof openTab === 'function') {
                    openTab(url, 'Search Op. Unit', 'MC200_001_1', window.mainUrl || '', 'doRefresh');
                    return true;
                }
                return false;
                """,
                target_url,
            )
        except Exception as e:
            logging.warning(f"  Gagal openTab Search Op. Unit, fallback direct URL: {e}")

        if not opened:
            driver.get(target_url)
            logging.info(f"  Direct URL Search Op. Unit dibuka: {target_url}")
        else:
            logging.info("  Search Op. Unit dibuka via framework tab")

        time.sleep(3)
        if not switch_to_search_op_unit_context(driver, timeout=40):
            save_debug_artifacts(driver, "search_op_unit_context_failed")
            return False

        logging.info("Search Op. Unit terbuka")
        return True

    except Exception as e:
        logging.error(f"Membuka Search Op. Unit gagal: {e}")
        save_debug_artifacts(driver, "open_search_op_unit_failed")
        return False


def click_search_op_unit_button(driver, timeout: int = 25):
    end_time = time.time() + timeout
    last_error = None
    while time.time() < end_time:
        try:
            if not switch_to_search_op_unit_context(driver, timeout=5):
                time.sleep(0.5)
                continue
            button = driver.execute_script(
                """
                const btn = document.getElementById('aaaa.OptCheckCompView.Button');
                if (!btn) return null;
                btn.scrollIntoView({block:'center', inline:'center'});
                return btn;
                """
            )
            if not button:
                time.sleep(0.5)
                continue
            try:
                ActionChains(driver).move_to_element(button).pause(0.1).click(button).perform()
            except Exception:
                pass
            driver.execute_script(
                """
                const e = arguments[0];
                e.focus?.();
                for (const ev of ['mouseover','mousedown','mouseup','click']) {
                    e.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
                }
                """,
                button,
            )
            logging.info("Tombol Search Op. Unit diklik")
            return True
        except Exception as e:
            last_error = e
        time.sleep(0.5)

    save_debug_artifacts(driver, "search_op_unit_button_failed")
    raise TimeoutException(f"Tombol Search Op. Unit gagal diklik: {last_error}")


def switch_to_search_op_unit_result_context(driver, timeout: int = 30):
    end_time = time.time() + timeout
    last_error = None
    while time.time() < end_time:
        try:
            if not switch_to_search_op_unit_context(driver, timeout=5):
                time.sleep(0.5)
                continue
            nested = None
            try:
                nested = driver.find_element(By.ID, "aaaa.OptCheckCompView.IFrame")
            except Exception:
                try:
                    nested = driver.find_element(
                        By.XPATH,
                        "//iframe[contains(@src,'mm.pdk.mc200.mc200_001') or contains(@name,'aaaa.OptCheckCompView.IFrame')]"
                    )
                except Exception as e:
                    last_error = e
            if not nested:
                time.sleep(0.5)
                continue
            driver.switch_to.frame(nested)
            try:
                wait_for_page_ready(driver, timeout=10)
            except Exception:
                pass
            ok = driver.execute_script(
                """
                const txt = document.body ? document.body.innerText : '';
                return (
                    txt.includes('Op. Unit ID') ||
                    txt.includes('Item(s) Searched') ||
                    !!document.querySelector('.x-grid-cell, .x-grid-row, .x-grid-view')
                );
                """
            )
            if ok:
                logging.info("  Masuk ke nested iframe hasil Search Op. Unit")
                return True
        except Exception as e:
            last_error = e
        time.sleep(0.5)

    logging.error(f"Gagal masuk nested iframe hasil Search Op. Unit: {last_error}")
    return False


def collect_op_unit_ids_from_grid(driver):
    if not switch_to_search_op_unit_result_context(driver, timeout=30):
        return []

    ids = driver.execute_script(
        """
        const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
        const valid = s => /^S\\d{6,}$/.test(clean(s));
        let out = [];
        function pushValue(v) {
            v = clean(v);
            if (valid(v) && !out.includes(v)) out.push(v);
        }
        try {
            if (window.Ext && Ext.ComponentQuery) {
                const grids = Ext.ComponentQuery.query('grid,gridpanel');
                for (const grid of grids) {
                    const store = grid.getStore ? grid.getStore() : null;
                    if (!store) continue;
                    let columns = [];
                    try {
                        if (grid.headerCt && grid.headerCt.getGridColumns) {
                            columns = grid.headerCt.getGridColumns();
                        } else if (grid.query) {
                            columns = grid.query('gridcolumn');
                        }
                    } catch (e) {}
                    let targetCol = null;
                    for (const col of columns) {
                        const label = clean(col.text || col.header || col.tooltip || col.dataIndex || col.id || '').toLowerCase();
                        if (label === 'op. unit id' || label.includes('op. unit id') || label.includes('op unit id')) {
                            targetCol = col; break;
                        }
                    }
                    if (!targetCol) targetCol = columns.find(c => String(c.id || '').includes('gridcolumn-1021'));
                    if (!targetCol && columns.length >= 5) targetCol = columns[4];
                    if (targetCol && targetCol.dataIndex) {
                        store.each(function(rec) { pushValue(rec.get(targetCol.dataIndex)); });
                    }
                    if (out.length) return out;
                }
            }
        } catch (e) {}
        try {
            const cells = Array.from(document.querySelectorAll('.x-grid-cell-gridcolumn-1021, td.x-grid-cell-gridcolumn-1021'));
            for (const cell of cells) pushValue(cell.innerText || cell.textContent);
            if (out.length) return out;
        } catch (e) {}
        try {
            const headers = Array.from(document.querySelectorAll('.x-column-header, .x-grid3-hd, th, td'));
            let headerIndex = -1;
            for (let i = 0; i < headers.length; i++) {
                const h = clean(headers[i].innerText || headers[i].textContent).toLowerCase();
                if (h.includes('op. unit id') || h.includes('op unit id')) {
                    const parent = headers[i].parentElement;
                    if (parent) headerIndex = Array.from(parent.children).indexOf(headers[i]);
                    break;
                }
            }
            if (headerIndex < 0) headerIndex = 4;
            const rows = Array.from(document.querySelectorAll('tr.x-grid-row, tr[role="row"], .x-grid-item'));
            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll('td.x-grid-cell, td[role="gridcell"], td, .x-grid-cell-inner'));
                if (cells.length > headerIndex) pushValue(cells[headerIndex].innerText || cells[headerIndex].textContent);
            }
            if (out.length) return out;
        } catch (e) {}
        try {
            const bodyText = document.body ? document.body.innerText : '';
            const matches = bodyText.match(/S\\d{6,}/g) || [];
            for (const val of matches) pushValue(val);
        } catch (e) {}
        return out;
        """
    )
    ids = [x.strip() for x in ids if x and x.strip()]
    logging.info(f"  Op. Unit ID terbaca dari grid: {len(ids)}")
    return ids


def wait_for_op_unit_result(driver, timeout: int = 90):
    end_time = time.time() + timeout
    while time.time() < end_time:
        ids = collect_op_unit_ids_from_grid(driver)
        if ids:
            return ids
        time.sleep(1)
    return []


def get_registered_op_unit_values(driver):
    if not open_search_op_unit(driver):
        raise RuntimeError("Gagal membuka Search Op. Unit")
    click_search_op_unit_button(driver, timeout=25)
    logging.info("Menunggu hasil Search Op. Unit...")
    ids = wait_for_op_unit_result(driver, timeout=90)
    if not ids:
        save_debug_artifacts(driver, "op_unit_ids_empty")
        raise RuntimeError("Tidak ada Op. Unit ID yang berhasil diambil dari Search Op. Unit")
    joined = ",".join(ids)
    logging.info(f"Op. Unit ID berhasil diambil: {len(ids)} data")
    return joined


# ──────────────────────── EXCEL DOWNLOAD BUTTON ───────────────────────────────

def click_excel_download_button(driver, timeout: int = 20):
    """
    Klik tombol Excel D/L di Search Client Odr.
    Prioritas: ID pasti → fallback teks/title.
    """
    exact_ids = [
        "aaaa.SearchOrderCompView.excelDownBtn",   # ID aktual dikonfirmasi dari HTML
        "aaaa.SearchOrderCompView.Button2",
        "aaaa.SearchOrderCompView.ExcelDL",
        "aaaa.SearchOrderCompView.EXCEL",
    ]
    for eid in exact_ids:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.ID, eid))
            )
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.2)
                try:
                    ActionChains(driver).move_to_element(btn).pause(0.1).click(btn).perform()
                except Exception:
                    driver.execute_script(
                        "const e=arguments[0]; for(const ev of ['mousedown','mouseup','click']) "
                        "e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));",
                        btn,
                    )
                logging.info(f"  Tombol Excel D/L diklik via ID: {eid}")
                return True
        except Exception:
            continue

    # Fallback: cari berdasarkan teks/title
    try:
        btn = WebDriverWait(driver, timeout).until(lambda d: d.execute_script(
            """
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const textOf = e => [e.innerText, e.textContent, e.title, e.value,
                e.getAttribute('aria-label'), e.getAttribute('lsdata'),
                e.getAttribute('lsevents'), e.id]
                .filter(Boolean).join(' ').toLowerCase();
            const candidates = Array.from(document.querySelectorAll(
                "button,a,input[type='button'],input[type='submit'],[ct='B'],.lsButton,[role='button'],span,div"
            )).filter(visible);
            for (const el of candidates) {
                const t = textOf(el);
                if (/excel\\s*d\\/?l|excel.*download|download.*excel|exceldownload/i.test(t)) {
                    return el.closest("a,button,[role='button'],[ct='B'],input") || el;
                }
            }
            return null;
            """
        ) or False)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2)
        try:
            ActionChains(driver).move_to_element(btn).pause(0.1).click(btn).perform()
        except Exception:
            driver.execute_script(
                "const e=arguments[0]; for(const ev of ['mousedown','mouseup','click']) "
                "e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));",
                btn,
            )
        logging.info("  Tombol Excel D/L diklik via teks/title fallback")
        return True
    except Exception as e:
        logging.error(f"  Tombol Excel D/L tidak ditemukan: {e}")
        return False


# ──────────────────────────── UPLOAD ──────────────────────────────────────────

def upload_to_dashboard(file_path: Path, max_retries: int = None):
    """Upload file Excel ke Flask dashboard sebagai multipart.
    Retry dengan EXPONENTIAL BACKOFF untuk HTTP 5xx (15s -> 30s -> 60s -> 120s -> 180s).
    Retry juga untuk 429, Timeout, ConnectionError, generic Exception.
    Default max_retries=CONFIG['UPLOAD_MAX_RETRIES'] (5). Sebelumnya hanya 3.
    """
    if max_retries is None:
        max_retries = CONFIG.get('UPLOAD_MAX_RETRIES', 5)
    # FIX: ambil worker tag dari thread saat ini supaya log upload (sukses/gagal)
    # konsisten dengan log "Mengantre upload"/"Semaphore acquired" yang sudah
    # punya tag — tanpa perlu ubah signature fungsi di semua call site.
    _wid = getattr(threading.current_thread(), 'worker_id', None)
    worker_prefix = f"[W{_wid}] " if _wid else ""
    # Exponential backoff (detik). Index 0 = jeda sebelum attempt ke-2.
    # Untuk 5xx, PythonAnywhere sering butuh 1-3 menit untuk recover dari OOM/cold start.
    backoff_5xx = [15, 30, 60, 120, 180]
    backoff_429 = [30, 60, 120, 240, 300]
    backoff_other = [15, 20, 30, 60, 60]
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            file_size_kb = file_path.stat().st_size / 1024
            logging.info(f"  {worker_prefix}Local Import (SQLite): {file_path.name} ({file_size_kb:.1f} KB, attempt {attempt}/{max_retries})")
            with open(file_path, 'rb') as f:
                # FIX V25: POST lewat net_tls — kebal proxy korporat TLS-interception
                # (upload gagal berulang padahal endpoint tidak berubah; lihat
                # net_tls.py untuk penjelasan root cause & tangga fallback-nya).
                # FIX V35: sinyal SYNC untuk dashboard — kirim query param ?sync=1
                # + header X-Sync-Upload: 1, minta server memproses data LANGSUNG
                # dan mengirim respons HANYA setelah data benar-benar selesai
                # (bukan 202 {"queued": true}). Sifatnya FORWARD-COMPATIBLE:
                #   - Dashboard lama: param/header asing diabaikan -> tetap 202
                #     queued -> tetap dianggap sukses (perilaku tidak berubah).
                #   - Dashboard baru (jika fitur sync diimplementasikan): respons
                #     baru dikirim setelah commit -> kegagalan server pasti terlihat
                #     sebagai 4xx/5xx -> file aman di failed_uploads & di-requeue.
                base_url = str(CONFIG['DASHBOARD_API'])
                from _paths import assert_local_dashboard_api
                assert_local_dashboard_api(base_url)
                sep = '&' if '?' in base_url else '?'
                resp = net_tls.tls_resilient_post(
                    f"{base_url}{sep}sync=1",
                    files={'file': (file_path.name, f, upload_mime_type(file_path))},
                    headers={'X-Sync-Upload': '1'},
                    timeout=CONFIG.get('UPLOAD_HTTP_TIMEOUT_S', 600),
                )
            elapsed = time.time() - t0
            if 200 <= resp.status_code < 300:
                try:
                    result = resp.json()
                    # FIX V35b: dashboard SMRO inline (sync) mengembalikan kunci
                    # 'inserted' (bukan 'added') — fallback supaya log akurat.
                    added = result.get('added', result.get('inserted', 0))
                    updated = result.get('updated', 0)
                    msg = result.get('message', 'OK')
                    logging.info(f"  {worker_prefix}✓ Local Import berhasil HTTP {resp.status_code}: {msg} | added={added} updated={updated} | {elapsed:.1f}s")
                    if 'diagnostics' in result and 'warning' in result['diagnostics']:
                        logging.warning(f"  {worker_prefix}⚠ Warning dari server: {result['diagnostics']['warning']}")
                    # FIX V35: transparansi mode pemrosesan server — supaya user
                    # tahu apakah data sudah benar-benar selesai atau masih
                    # mengantre di sisi server saat respons diterima.
                    if result.get('queued'):
                        logging.info(
                            f"  {worker_prefix}Mode pemrosesan lokal: ASYNC (queued) — data diterima & masuk antrean "
                            "server, diproses setelah respons ini. File lokal aman dihapus."
                        )
                    else:
                        logging.info(
                            f"  {worker_prefix}Mode pemrosesan lokal: SYNC — data sudah benar-benar selesai "
                            "diproses saat respons diterima."
                        )
                except Exception:
                    logging.info(f"  {worker_prefix}✓ Upload berhasil ({elapsed:.1f}s): {resp.text[:200]}")
                return True
            # 5xx retryable dengan exponential backoff
            if resp.status_code >= 500:
                body_preview = resp.text[:300].replace('\n', ' ').replace('\r', '')
                server = resp.headers.get('Server', '?')
                if attempt < max_retries:
                    delay = backoff_5xx[min(attempt - 1, len(backoff_5xx) - 1)]
                    logging.warning(
                        f"  {worker_prefix}⚠ Upload gagal HTTP {resp.status_code} ({elapsed:.1f}s, server={server}). "
                        f"Body: {body_preview[:150]}. "
                        f"Retry dalam {delay}s... (attempt {attempt}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                logging.error(
                    f"  {worker_prefix}✗ Upload gagal HTTP {resp.status_code} setelah {max_retries}x. "
                    f"Body: {body_preview}"
                )
                return False
            # 429 Rate Limited
            if resp.status_code == 429:
                if attempt < max_retries:
                    delay = backoff_429[min(attempt - 1, len(backoff_429) - 1)]
                    retry_after = resp.headers.get('Retry-After')
                    if retry_after:
                        try: delay = max(delay, int(retry_after))
                        except Exception: pass
                    logging.warning(f"  {worker_prefix}⚠ Rate limited (429). Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                    time.sleep(delay)
                    continue
                logging.error(f"  {worker_prefix}✗ Rate limited (429) setelah {max_retries} percobaan")
                return False
            # 4xx lainnya (400/401/403/404/413/422) — tidak retry (permanent)
            logging.error(f"  {worker_prefix}✗ Upload gagal HTTP {resp.status_code} (permanent): {resp.text[:500]}")
            return False
        except requests.exceptions.Timeout:
            elapsed = time.time() - t0
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                logging.warning(f"  {worker_prefix}⚠ Upload timeout ({elapsed:.1f}s). Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  {worker_prefix}✗ Upload timeout setelah {max_retries} percobaan ({elapsed:.1f}s)")
            return False
        except requests.exceptions.ConnectionError as e:
            # FIX: ConnectionError transient — wajib retry.
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                err_short = str(e).replace('\n', ' ')[:200]
                logging.warning(f"  {worker_prefix}⚠ Upload koneksi gagal: {err_short}. Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  {worker_prefix}✗ Upload koneksi gagal setelah {max_retries} percobaan: {e}")
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                logging.warning(f"  {worker_prefix}⚠ Upload error: {e}. Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  {worker_prefix}✗ Upload error: {e}")
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

# ──────────────────────── SEARCH & DOWNLOAD ───────────────────────────────────

def _wait_vpn_reconnect(interval: int = 30, max_wait: float = None) -> bool:
    """Tunggu sampai VPN tersambung kembali. Log setiap interval detik.

    FIX V23: sebelumnya loop INFINITE — kalau VPN tidak pernah kembali,
    worker SMRO/PUR menggantung selamanya menunggu, log tetap bertambah
    tiap 30 detik (watchdog GUI tidak menyadarinya) dan Run All sequential
    MACET di step itu sampai hard timeout 1 jam — inilah akar laporan
    "process all prod id selesai tapi tidak lanjut proses berikutnya".
    Sekarang dibatasi max_wait (default env SMRO_VPN_MAX_WAIT_SECONDS,
    900 detik / 15 menit): return True kalau VPN kembali, False kalau
    menyerah — pemanggil lalu membatalkan run ini dengan terhormat supaya
    proses/jadwal berikutnya tetap jalan (jadwal interval akan retry
    otomatis saat VPN sudah normal).
    """
    if max_wait is None:
        try:
            max_wait = float(os.getenv('SMRO_VPN_MAX_WAIT_SECONDS', '900'))
        except Exception:
            max_wait = 900.0
    logging.warning("  Menunggu VPN / koneksi kembali...")
    deadline = time.time() + max_wait
    while True:
        time.sleep(interval)
        if check_vpn_connection():
            logging.info("  VPN tersambung kembali.")
            return True
        if time.time() >= deadline:
            logging.error(
                f"  VPN BELUM kembali setelah {int(max_wait)}s. Berhenti menunggu — "
                f"run ini dibatalkan supaya proses/jadwal berikutnya tetap jalan."
            )
            return False
        logging.info("  Masih menunggu VPN... (cek lagi dalam %ds)" % interval)


def _reopen_browser_and_resume(op_unit_values):
    """
    Tutup browser lama (jika ada), buka baru, login, navigasi ke SMRO,
    buka Search Client Odr. Kembalikan driver baru atau raise jika gagal.
    Op. Unit values TIDAK diambil ulang — pakai nilai yang sudah disimpan.
    """
    # FIX V13: profile_tag dedicated untuk path reopen sequential (bukan
    # worker_N) supaya tidak bentrok dengan profil lain.
    driver = setup_driver(profile_tag="sequential_reopen")
    try:
        if not login_to_portal(driver):
            safe_driver_quit(driver)  # FIX V16.2: anti spam + orphan cleanup
            raise RuntimeError("Login gagal saat reopen browser")
        close_popup_if_exists(driver)
        if not navigate_to_smro(driver):
            safe_driver_quit(driver)  # FIX V16.2
            raise RuntimeError("Navigasi SMRO gagal saat reopen browser")
        if not open_search_client_odr(driver):
            safe_driver_quit(driver)  # FIX V16.2
            raise RuntimeError("Buka Search Client Odr gagal saat reopen browser")
        # Tunggu form aktif
        time.sleep(5)
        return driver
    except Exception:
        safe_driver_quit(driver)  # FIX V16.2
        raise


def _ensure_form_ready(driver):
    """Pastikan form Search Client Odr aktif setelah context switch."""
    if not switch_to_search_client_odr_context(driver, timeout=30):
        logging.warning("  Search Client Odr tidak aktif, membuka ulang tab Search Client Odr...")
        if not open_search_client_odr(driver):
            raise RuntimeError("Halaman Search Client Odr tidak aktif")
        if not switch_to_search_client_odr_context(driver, timeout=45):
            raise RuntimeError("Halaman Search Client Odr tidak aktif")
    form_marker_xpath = (
        "//*[contains(normalize-space(.),'SO Search') or contains(@value,'SO Search') "
        "or contains(@title,'SO Search') or contains(normalize-space(.),'Excel D/L') "
        "or contains(@title,'Excel D/L')]"
    )
    try:
        wait_for_page_ready(driver, timeout=30)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, form_marker_xpath))
        )
        logging.info("  Form Search Client Odr sudah aktif")
    except Exception:
        logging.warning("  Form marker belum terdeteksi, lanjut dengan context aktif")


def search_and_download_smro(driver, op_unit_values=None, _pending_ranges=None, _uploaded_batches=None):
    """
    Search → Excel D/L → upload langsung ke dashboard, per batch.
    - Timeout download  : 600 detik (10 menit), tanpa hard stop.
    - Timeout upload    : 600 detik per percobaan, retry 5× tanpa batas waktu.
    - Jika VPN/internet putus di tengah batch: tunggu VPN, relogin, lanjut dari
      batch yang SAMA (tidak skip, tidak mulai ulang dari awal).
    - op_unit_values disimpan oleh caller sebelum masuk fungsi ini; tidak diambil
      ulang saat reconnect sehingga tidak perlu buka menu Search Op. Unit lagi.

    _pending_ranges / _uploaded_batches digunakan untuk resume dari luar
    (misalnya saat reentry setelah reconnect total dari run_automation).
    """
    # ── State yang bisa dilanjutkan ──
    if _pending_ranges is None:
        _pending_ranges = get_download_date_ranges()
        logging.info(
            f"  Range awal: {len(_pending_ranges)} batch bulanan. "
            f"Split otomatis jika hasil >= {CONFIG['SEARCH_LIMIT_ROWS']} rows: "
            "month → 2week → 1week."
        )
    else:
        logging.info(f"  Melanjutkan dari sisa {len(_pending_ranges)} batch.")

    if _uploaded_batches is None:
        _uploaded_batches = []
    # FIX V33: hitung batch yang gagal upload supaya run TIDAK dilaporkan sukses
    # kalau ada file yang tidak sampai ke endpoint.
    failed_batches = 0

    if not _pending_ranges:
        logging.info("  Tidak ada batch range untuk diproses.")
        return True, driver, _pending_ranges, _uploaded_batches

    processed = len(_uploaded_batches)

    # ── Pastikan form aktif saat pertama masuk ──
    try:
        _ensure_form_ready(driver)
    except Exception as e:
        logging.error(f"  Form tidak siap: {e}")
        save_debug_artifacts(driver, "search_no_client_odr_context")
        return False, driver, _pending_ranges, _uploaded_batches

    while _pending_ranges:
        date_from, date_to, range_level = _pending_ranges[0]   # peek, jangan pop dulu
        processed += 1
        label = f"{format_smro_date(date_from)} - {format_smro_date(date_to)}"
        logging.info(
            f"  [Batch {processed}] {label} "
            f"(level {range_level}, sisa {len(_pending_ranges)})"
        )

        # ═══════════════ RETRY LOOP PER BATCH ═══════════════
        # Setiap kegagalan karena network/browser akan di-retry di sini.
        MAX_BATCH_RETRIES = 5
        batch_attempt = 0
        while batch_attempt < MAX_BATCH_RETRIES:
            batch_attempt += 1
            if batch_attempt > 1:
                logging.warning(
                    f"  Percobaan ke-{batch_attempt} untuk batch {label}..."
                )

            try:
                # ── Pastikan form masih aktif ──
                _ensure_form_ready(driver)

                # ── Isi tanggal ──
                set_search_date_range(driver, date_from, date_to)

                # ── Isi Op. Unit ──
                if op_unit_values:
                    op_unit_field = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located(
                            (By.ID, "aaaa.SearchOrderCompView.OPR_UNT_ID")
                        )
                    )
                    set_field_value(driver, op_unit_field, op_unit_values)
                    logging.info("  Op. Unit ID diisi ke field Search Client Odr")
                else:
                    logging.warning("  Op. Unit ID kosong, fallback ke metode popup Multi")
                    if not select_all_operation_units(driver):
                        raise RuntimeError("select_all_operation_units gagal")

                # ── Klik SO Search ──
                click_sap_button_by_id(driver, "aaaa.SearchOrderCompView.Button", timeout=20)
                logging.info("  Tombol SO Search diklik")
                time.sleep(2)
                result_count = wait_for_search_result(driver, timeout=90)

            except Exception as e:
                err_msg = str(e).lower()
                logging.error(f"  Error saat setup/search batch {label}: {e}")

                # Deteksi ChromeDriver crash (cek string + status driver aktif)
                is_dead = _is_chromedriver_dead(err_msg, driver)
                if is_dead:
                    logging.error(f"  ChromeDriver mati/tidak responsif. Mencoba buka ulang browser...")
                    # FIX V16.2: safe_driver_quit — anti spam + bersihkan orphan.
                    safe_driver_quit(driver)
                    try:
                        driver = _reopen_browser_and_resume(op_unit_values)
                        logging.info("  Browser berhasil dibuka ulang, retry batch...")
                        continue
                    except Exception as reopen_err:
                        logging.error(f"  Reopen browser gagal: {reopen_err}. Batch {label} dilewati.")
                        _pending_ranges.pop(0)
                        break

                save_debug_artifacts(driver, f"batch_setup_error_{date_from.strftime('%Y%m%d')}")

                # Cek apakah VPN mati
                if not check_vpn_connection():
                    # FIX V23: VPN wait dibatasi — kalau menyerah, lewati batch
                    # ini supaya proses bisa exit dan Run All lanjut step berikutnya.
                    if not _wait_vpn_reconnect():
                        logging.error(
                            f"  VPN tidak kembali dalam batas waktu. Batch {label} dilewati "
                            f"supaya proses berikutnya tetap jalan."
                        )
                        _pending_ranges.pop(0)
                        break
                    # Reopen browser dengan op_unit yang sudah disimpan
                    logging.info("  Membuka ulang browser dan login ulang...")
                    try:
                        safe_driver_quit(driver)  # FIX V16.2: anti spam + orphan cleanup
                        driver = _reopen_browser_and_resume(op_unit_values)
                    except Exception as reopen_err:
                        logging.error(f"  Reopen browser gagal: {reopen_err}. Tunggu 30 detik lagi...")
                        time.sleep(30)
                    continue   # retry batch yang sama
                else:
                    # Network oke tapi ada error lain — tunggu sebentar lalu coba lagi
                    logging.warning("  Koneksi OK. Tunggu 20 detik lalu retry...")
                    time.sleep(20)
                    continue

            # ── Evaluasi hasil search ──
            has_rows = table_has_data_rows(driver)
            if result_count is not None:
                logging.info(f"  Hasil search: {result_count} rows")

            # Skip batch kosong
            if not has_rows and (result_count is None or result_count == 0):
                logging.warning(f"  Tidak ada data untuk batch {label}, dilewati")
                _pending_ranges.pop(0)
                break   # lanjut ke batch berikutnya

            if result_count is None:
                if has_rows:
                    row_count_estimate = driver.execute_script(
                        """
                        const tbody = document.querySelector("[id$='Table_BasicList-contentTBody']");
                        if (!tbody) return 0;
                        return Array.from(tbody.querySelectorAll("tr")).filter(r =>
                            r.hasAttribute("rr") && r.getAttribute("rr") !== "-1" &&
                            !(r.innerHTML || "").includes("EMPTYROW")
                        ).length;
                        """
                    ) or 0
                    logging.warning(
                        f"  Result count tidak terbaca untuk batch {label}, "
                        f"tapi tabel ada data (~{row_count_estimate} baris terlihat). "
                        "Lanjutkan download dengan asumsi < limit."
                    )
                    result_count = row_count_estimate
                else:
                    logging.error(
                        f"  Result count tidak terbaca dan tabel kosong untuk batch {label}. "
                        "Tunggu 20 detik lalu retry..."
                    )
                    save_debug_artifacts(driver, "result_count_not_detected")
                    time.sleep(20)
                    continue   # retry batch yang sama

            # Split jika kena limit
            if result_count >= CONFIG["SEARCH_LIMIT_ROWS"]:
                split_ranges = split_range_if_limit(date_from, date_to, range_level)
                if split_ranges:
                    _pending_ranges.pop(0)
                    _pending_ranges[0:0] = split_ranges
                    next_level = split_ranges[0][2]
                    logging.warning(
                        f"  Batch {label} mencapai limit {result_count} rows. "
                        f"Dipecah menjadi {len(split_ranges)} batch level {next_level}."
                    )
                    break   # keluar retry loop, proses sub-batch
                logging.warning(
                    f"  Batch {label} masih {result_count} rows pada level 1week. "
                    "Download tetap dilanjutkan, tapi data mungkin terpotong."
                )

            # ── Catat file & blob sebelum download ──
            before_files = list(CONFIG["DOWNLOAD_DIR"].iterdir())
            before_blobs = get_blob_iframes(driver)

            # ── Klik Excel D/L ──
            if not click_excel_download_button(driver, timeout=20):
                logging.error(f"  Tombol Excel D/L tidak ditemukan untuk batch {label}")
                save_debug_artifacts(driver, f"excel_dl_button_failed_{date_from.strftime('%Y%m%d')}")
                time.sleep(15)
                continue   # retry batch yang sama

            # ── Tunggu file (10 menit, tanpa hard stop) ──
            logging.info(f"  Menunggu file Excel batch {label} (maks 10 menit)...")
            xl_file = wait_for_new_download(
                CONFIG["DOWNLOAD_DIR"], before_files,
                timeout=600,          # 10 menit
                driver=driver,
                before_blobs=before_blobs,
            )

            if not xl_file:
                logging.error(f"  File Excel tidak muncul dalam 10 menit untuk batch {label}.")
                save_debug_artifacts(driver, f"excel_dl_timeout_{date_from.strftime('%Y%m%d')}")
                if not check_vpn_connection():
                    # FIX V23: VPN wait dibatasi — kalau menyerah, lewati batch ini.
                    if not _wait_vpn_reconnect():
                        logging.error(
                            f"  VPN tidak kembali dalam batas waktu. Batch {label} dilewati "
                            f"supaya proses berikutnya tetap jalan."
                        )
                        _pending_ranges.pop(0)
                        break
                    logging.info("  Membuka ulang browser dan login ulang setelah VPN reconnect...")
                    try:
                        safe_driver_quit(driver)  # FIX V16.2: anti spam + orphan cleanup
                        driver = _reopen_browser_and_resume(op_unit_values)
                    except Exception as reopen_err:
                        logging.error(f"  Reopen browser gagal: {reopen_err}. Tunggu 30 detik...")
                        time.sleep(30)
                else:
                    if batch_attempt >= 2:
                        logging.warning("  Koneksi OK tapi Excel tidak muncul berulang. Reopen browser sebelum retry...")
                        try:
                            safe_driver_quit(driver)  # FIX V16.2: anti spam + orphan cleanup
                            driver = _reopen_browser_and_resume(op_unit_values)
                        except Exception as reopen_err:
                            logging.error(f"  Reopen browser gagal: {reopen_err}. Tunggu 30 detik...")
                            time.sleep(30)
                    else:
                        logging.warning("  Koneksi OK tapi file tidak muncul. Buka ulang Search Client Odr lalu retry...")
                        try:
                            open_search_client_odr(driver)
                        except Exception:
                            pass
                        time.sleep(20)
                continue   # retry batch yang sama

            logging.info(
                f"  File Excel diterima: {xl_file.name} "
                f"({xl_file.stat().st_size / 1024:.1f} KB)"
            )

            # ── Rename ──
            target_name = (
                f"smro_{date_from.strftime('%Y%m%d')}_"
                f"{date_to.strftime('%Y%m%d')}_{range_level}{xl_file.suffix}"
            )
            target_path = xl_file.parent / target_name
            if target_path.exists():
                target_path = xl_file.parent / (
                    f"smro_{date_from.strftime('%Y%m%d')}_{date_to.strftime('%Y%m%d')}"
                    f"_{range_level}_{int(time.time())}{xl_file.suffix}"
                )
            xl_file.rename(target_path)
            logging.info(f"  File di-rename: {target_path.name}")

            ok = True
            if CONFIG['SAVE_LOCAL']:
                ok = save_local_copy(target_path, CONFIG['LOCAL_SAVE_DIR']) and ok
            if CONFIG['UPDATE_DASHBOARD']:
                logging.info(f"  Local Import: {target_path.name} -> SQLite via 127.0.0.1")
                upload_ok = upload_to_dashboard(target_path)
                if not upload_ok:
                    logging.warning("  Upload gagal setelah semua percobaan. Cek koneksi...")
                    # FIX V23: VPN wait dibatasi — kalau VPN tidak kembali, upload
                    # ulang terakhir dilewati (tidak menggantung proses).
                    vpn_back = check_vpn_connection() or _wait_vpn_reconnect()
                    if vpn_back:
                        logging.info("  Mencoba upload ulang 1 kali terakhir...")
                        upload_ok = upload_to_dashboard(target_path)
                    else:
                        logging.warning(
                            "  VPN belum kembali dalam batas waktu — "
                            "upload ulang terakhir dilewati."
                        )
                if upload_ok:
                    logging.info(f"  Upload dashboard selesai: {target_path.name}")
                ok = upload_ok and ok
            if not CONFIG['UPDATE_DASHBOARD']:
                logging.info("  Upload dashboard dilewati sesuai setting")

            # ── FIX V33: file kerja hanya dihapus jika upload SUKSES/skip ──
            # Dulu file dihapus SEBELUM hasil upload dicek, lalu batch gagal
            # tetap di-pop dan proses lanjut — akibatnya data hilang tanpa
            # pernah sampai ke endpoint dan run tetap dilaporkan sukses.
            if not ok:
                logging.error(
                    f"  Upload batch {label} tetap gagal. "
                    "Lanjut ke batch berikutnya agar proses tidak terhenti."
                )
                # Simpan file ke failed_uploads/ (JANGAN hapus) supaya bisa
                # diupload ulang — di run ini lewat final retry, atau di run
                # berikutnya lewat requeue otomatis.
                park_unuploaded_file(target_path)
                failed_batches += 1
                _pending_ranges.pop(0)
                break

            # Upload sukses (atau memang dilewati setting) — file kerja aman dihapus
            try:
                target_path.unlink()
                logging.info(f"  File kerja dihapus: {target_path.name}")
            except Exception as e:
                logging.warning(f"  Gagal hapus file kerja {target_path.name}: {e}")

            _pending_ranges.pop(0)
            _uploaded_batches.append(target_name)
            logging.info(f"  Batch selesai: {target_name} ({result_count} rows)")
            break   # keluar retry loop, batch ini sukses
        else:
            logging.error(f"  Batch " + label + " gagal setelah " + str(MAX_BATCH_RETRIES) + " percobaan, dilewati.")
            save_debug_artifacts(driver, f"batch_max_retries_{date_from.strftime('%Y%m%d')}")
            _pending_ranges.pop(0)
        # ═════════════════ END RETRY LOOP ════════════════════

    if not _uploaded_batches:
        logging.error("Tidak ada batch yang berhasil diupload")
        save_debug_artifacts(driver, "all_batches_failed")
        return False, driver, _pending_ranges, _uploaded_batches

    # FIX V33: ada batch yang gagal upload = run TIDAK sukses, supaya watcher/
    # jadwal mengulang proses dan file di failed_uploads/ diupload ulang.
    # Dulu kondisi ini diabaikan (return True) sehingga data hilang diam-diam.
    if failed_batches:
        logging.error(
            f"{failed_batches} batch gagal diupload ke endpoint. "
            "Proses dilaporkan GAGAL agar diulang; file gagal aman di failed_uploads/."
        )
        save_debug_artifacts(driver, "upload_incomplete_sequential")
        return False, driver, _pending_ranges, _uploaded_batches

    logging.info(f"Semua batch selesai. Total uploaded: {len(_uploaded_batches)} batch")
    return True, driver, _pending_ranges, _uploaded_batches


# ═══════════════════════════════════════════════════════════════════════════════
#  UPLOAD MANAGER — thread-safe concurrent upload dengan semaphore
# ═══════════════════════════════════════════════════════════════════════════════

class UploadManager:
    """
    Mengontrol upload concurrent + menyimpan file GAGAL ke folder failed_uploads/.
    Saat upload gagal, file Excel dipindahkan (BUKAN dihapus) ke UPLOAD_FAIL_DIR.
    File itu akan di-retry 1 kali lagi di akhir run via retry_failed().
    """
    def __init__(self, max_concurrent: int = 2):
        self.semaphore = threading.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
        self.lock = threading.Lock()
        self.total_uploads = 0
        self.failed_uploads = 0
        self.failed_files = []      # list of Path yang gagal upload
        self.succeeded_files = []   # list of filename yang sukses
        self.upload_threads = []    # list of Thread untuk upload async (NEW)
        # FIX V33: catat semua file yang disubmit supaya di akhir run bisa
        # direkonsiliasi — batch hanya boleh dianggap sukses kalau filenya
        # benar-benar sampai ke endpoint (bukan cuma selesai didownload).
        self.submitted_files = []

    def upload(self, file_path: Path, max_retries: int = None) -> bool:
        """Upload dengan concurrency control. Saat gagal, file dipindah ke failed_uploads/."""
        worker_tag = f"[W{getattr(threading.current_thread(), 'worker_id', '?')}]"
        logging.info(f"  {worker_tag} Mengantre upload: {file_path.name} (antrean semaphore, max {self.max_concurrent} concurrent)")
        with self.semaphore:
            logging.info(f"  {worker_tag} Semaphore acquired, mulai upload: {file_path.name}")
            ok = upload_to_dashboard(file_path, max_retries=max_retries)
            with self.lock:
                self.total_uploads += 1
                if ok:
                    self.succeeded_files.append(file_path.name)
                    # FIX V34: hapus file SEGERA setelah upload sukses — jangan
                    # menunggu pemanggil. Ini kunci permintaan user "file yang
                    # diupload langsung dihapus supaya disk tidak penuh".
                    try:
                        file_path.unlink()
                        logging.info(f"  {worker_tag} File kerja dihapus (upload sukses): {file_path.name}")
                    except Exception:
                        pass
                else:
                    self.failed_uploads += 1
                    self.failed_files.append(file_path)
                    # PINDAHKAN file ke failed_uploads/ (JANGAN HAPUS!)
                    # File ini akan di-retry di akhir run via retry_failed().
                    moved = _in_fail_dir(file_path)  # FIX V33: sudah di fail dir? skip move
                    try:
                        fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
                        if fail_dir and not moved:
                            fail_dir.mkdir(parents=True, exist_ok=True)
                            dest = fail_dir / file_path.name
                            if dest.exists():
                                dest = fail_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                            file_path.rename(dest)
                            logging.info(f"  {worker_tag} File gagal dipindah ke: {dest}")
                            moved = True
                    except Exception as mv_err:
                        logging.error(f"  {worker_tag} Gagal pindah file ke failed_uploads/: {mv_err}")
                    # Auto-save local backup sebagai last-resort safety net
                    if not moved and CONFIG.get('AUTO_SAVE_LOCAL_ON_FAIL'):
                        try:
                            backup_dir = Path(CONFIG['AUTO_SAVE_LOCAL_DIR'])
                            backup_dir.mkdir(parents=True, exist_ok=True)
                            backup_dest = backup_dir / file_path.name
                            if backup_dest.exists():
                                backup_dest = backup_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                            shutil.copy2(file_path, backup_dest)
                            logging.info(f"  {worker_tag} File di-backup lokal: {backup_dest}")
                        except Exception as bk_err:
                            logging.error(f"  {worker_tag} Gagal backup lokal: {bk_err}")
            return ok

    def retry_failed(self) -> int:
        """Retry semua file di failed_files. Return jumlah yang berhasil."""
        if not self.failed_files:
            return 0
        with self.lock:
            to_retry = list(self.failed_files)
            self.failed_files = []
        success_count = 0
        # Retry satu per satu (bukan concurrent) supaya tidak membebani server.
        for path in to_retry:
            if not path.exists():
                # Mungkin sudah dipindah ke UPLOAD_FAIL_DIR
                fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
                if fail_dir:
                    candidates = list(fail_dir.glob(f"{path.stem}*{path.suffix}"))
                    if candidates:
                        path = candidates[0]
                    else:
                        logging.warning(f"  File tidak ditemukan untuk retry: {path.name}")
                        continue
            logging.info(f"  Retry upload: {path.name} (final retry)")
            ok = upload_to_dashboard(path, max_retries=2)
            if ok:
                success_count += 1
                with self.lock:
                    self.failed_uploads = max(0, self.failed_uploads - 1)
                    self.succeeded_files.append(path.name)
                # Hapus file di failed_uploads/ setelah sukses
                try: path.unlink()
                except Exception: pass
            else:
                with self.lock:
                    self.failed_files.append(path)
                logging.error(f"  ✗ Retry gagal: {path.name}")
                # Tetap simpan di failed_uploads/, akan di-retry lagi di run berikutnya
        return success_count

    def upload_async(self, file_path: Path, max_retries: int = None) -> None:
        """Submit file ke upload queue. NON-BLOCKING — langsung return.
        Upload dijalankan di thread terpisah, dibatasi semaphore (max_concurrent).
        Worker TIDAK menunggu upload selesai — langsung lanjut download range berikutnya.
        """
        worker_tag = f"[W{getattr(threading.current_thread(), 'worker_id', '?')}]"
        logging.info(f"  {worker_tag} Submit upload queue: {file_path.name}")

        # FIX V33: catat file yang disubmit SEKARANG (sebelum thread jalan)
        # supaya rekonsiliasi akhir run tidak melewatkan file apapun.
        with self.lock:
            self.submitted_files.append(file_path)

        def _do_upload():
            """Inner function yang jalan di thread terpisah."""
            try:
                with self.semaphore:
                    logging.info(f"  {worker_tag} Semaphore acquired, mulai upload: {file_path.name}")
                    ok = upload_to_dashboard(file_path, max_retries=max_retries)
                    with self.lock:
                        self.total_uploads += 1
                        if ok:
                            self.succeeded_files.append(file_path.name)
                            # Hapus file kalau upload sukses
                            try: file_path.unlink()
                            except Exception: pass
                        else:
                            self.failed_uploads += 1
                            self.failed_files.append(file_path)
                            # PINDAHKAN file ke failed_uploads/ (BUKAN dihapus)
                            moved = _in_fail_dir(file_path)  # FIX V33: sudah di fail dir? skip move
                            try:
                                fail_dir = CONFIG.get('UPLOAD_FAIL_DIR')
                                if fail_dir and not moved:
                                    fail_dir.mkdir(parents=True, exist_ok=True)
                                    dest = fail_dir / file_path.name
                                    if dest.exists():
                                        dest = fail_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                                    file_path.rename(dest)
                                    logging.info(f"  {worker_tag} File gagal dipindah ke: {dest}")
                                    moved = True
                            except Exception as mv_err:
                                logging.error(f"  {worker_tag} Gagal pindah file ke failed_uploads/: {mv_err}")
                            # Auto-save local backup sebagai last-resort safety net
                            if not moved and CONFIG.get('AUTO_SAVE_LOCAL_ON_FAIL'):
                                try:
                                    backup_dir = Path(CONFIG['AUTO_SAVE_LOCAL_DIR'])
                                    backup_dir.mkdir(parents=True, exist_ok=True)
                                    backup_dest = backup_dir / file_path.name
                                    if backup_dest.exists():
                                        backup_dest = backup_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                                    shutil.copy2(file_path, backup_dest)
                                    logging.info(f"  {worker_tag} File di-backup lokal: {backup_dest}")
                                except Exception as bk_err:
                                    logging.error(f"  {worker_tag} Gagal backup lokal: {bk_err}")
            except Exception as e:
                logging.error(f"  {worker_tag} Upload thread error: {e}")
                with self.lock:
                    self.failed_uploads += 1
                    self.failed_files.append(file_path)

        # Spawn thread baru untuk upload. FIX V33: daemon=False — thread daemon
        # dibunuh begitu proses exit, sehingga upload yang masih antre/berjalan
        # HILANG tanpa jejak padahal batch sudah dianggap sukses. Dengan
        # non-daemon, proses tidak akan exit sebelum semua upload selesai
        # (kecuali di-kill paksa oleh watchdog — ditangani collect_orphan_uploads
        # di run berikutnya).
        # FIX: wariskan worker_id ke thread baru supaya log di dalam upload_to_dashboard
        # (Upload berhasil/gagal) tetap punya prefix [W1]/[W2] yang benar — tanpa ini,
        # thread baru tidak punya worker_id sehingga log upload kehilangan tag worker.
        _parent_worker_id = getattr(threading.current_thread(), 'worker_id', None)

        def _do_upload_with_tag():
            if _parent_worker_id is not None:
                threading.current_thread().worker_id = _parent_worker_id
            _do_upload()

        t = threading.Thread(target=_do_upload_with_tag, daemon=False, name=f"upload-{file_path.name}")
        with self.lock:
            self.upload_threads.append(t)
        t.start()

    def wait_all(self, timeout: float = None, progress_interval: float = 60.0) -> bool:
        """Tunggu semua upload thread selesai. Return True jika semua selesai,
        False jika timeout tercapai dan masih ada yang berjalan.

        FIX V33: versi lama memakai t.join(timeout) PER-THREAD dengan nilai
        timeout yang sama (total tunggu = N x timeout) dan hasilnya diabaikan
        caller — proses bisa lanjut exit padahal upload masih berjalan. Sekarang:
        - deadline DIHITUNG SEKALI (total, bukan per thread),
        - log progres tiap `progress_interval` detik supaya stall watchdog Run
          All (log diam 900s) dan GUI (2700s) TIDAK membunuh proses di tengah
          antrean upload yang lambat.
        """
        with self.lock:
            threads = list(self.upload_threads)
        if not threads:
            return True
        logging.info(f"Menunggu {len(threads)} upload thread selesai...")
        deadline = None if timeout is None else time.time() + timeout
        last_progress = time.time()
        while True:
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                logging.info("Semua upload thread selesai.")
                return True
            now = time.time()
            if deadline is not None and now >= deadline:
                logging.warning(
                    f"{len(alive)} upload thread masih berjalan setelah batas waktu "
                    f"({timeout:.0f}s). File yang belum terupload akan direkonsiliasi."
                )
                return False
            if now - last_progress >= progress_interval:
                stats = self.get_stats()
                logging.info(
                    f"Menunggu upload async: {stats['total']} selesai "
                    f"({stats['failed']} gagal), {len(alive)} masih berjalan..."
                )
                last_progress = now
            time.sleep(0.5)

    def pending_files(self) -> list:
        """FIX V33: file yang sudah disubmit tapi BELUM punya hasil final
        (belum sukses dan belum gagal — thread masih jalan atau mati tanpa
        hasil). Dipanggil setelah wait_all() untuk rekonsiliasi akhir run.

        Catatan tipe: succeeded_files berisi NAMA file (str), failed_files
        berisi Path — keduanya dinormalisasi ke nama di sini."""
        with self.lock:
            done_names = set(self.succeeded_files) | {
                p.name if isinstance(p, Path) else str(p) for p in self.failed_files
            }
            return [p for p in self.submitted_files
                    if (p.name if isinstance(p, Path) else str(p)) not in done_names]

    def get_stats(self) -> dict:
        with self.lock:
            return {"total": self.total_uploads, "failed": self.failed_uploads, "failed_files": len(self.failed_files)}


# ═══════════════════════════════════════════════════════════════════════════════
#  PARALLEL EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def split_ranges_into_chunks(all_ranges: list, chunk_size: int = 6) -> list:
    """
    Split list of date ranges into chunks of `chunk_size`.
    Contoh: 12 bulan → [[bln1..bln6], [bln7..bln12]]
    """
    return [all_ranges[i:i + chunk_size] for i in range(0, len(all_ranges), chunk_size)]


def worker_setup_session(driver, op_unit_values):
    """
    Pastikan driver siap di Search Client Odr context dengan Op. Unit terisi.
    """
    try:
        _ensure_form_ready(driver)
    except Exception:
        raise RuntimeError("Form Search Client Odr tidak siap")

    # Isi Op. Unit
    if op_unit_values:
        try:
            wait_for_page_ready(driver, timeout=30)
            time.sleep(2)  # tunggu form stabil setelah page ready
            op_unit_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "aaaa.SearchOrderCompView.OPR_UNT_ID"))
            )
            set_field_value(driver, op_unit_field, op_unit_values)
            logging.info(f"  Op. Unit ID diisi")
        except Exception as e:
            logging.warning(f"  Gagal isi Op. Unit field: {e}")
            raise
    return True


def worker_process_range(driver, date_from, date_to, range_level,
                         download_dir, upload_manager, worker_id,
                         op_unit_values=None):
    """
    Proses SATU range tanggal: search → download → upload.
    Return (success: bool, result_count: int, target_name: str or None)

    op_unit_values WAJIB diteruskan dari caller supaya saat reopen browser
    (path download timeout berulang) form Search Client Odr tetap terisi
    Op. Unit. Sebelumnya tidak dithread → NameError di worker_setup_session.
    """
    tag = f"[W{worker_id}]"
    label = f"{format_smro_date(date_from)} - {format_smro_date(date_to)}"
    logging.info(f"  {tag} Proses batch {label} (level {range_level})")

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            logging.warning(f"  {tag} Percobaan ke-{attempt} untuk batch {label}...")

        try:
            # ── Pastikan form aktif ──
            _ensure_form_ready(driver)

            # ── Pastikan page sudah siap ──
            try:
                wait_for_page_ready(driver, timeout=30)
            except Exception:
                pass
            time.sleep(1)

            # ── Isi tanggal ──
            set_search_date_range(driver, date_from, date_to)

            # ── Klik SO Search ──
            click_sap_button_by_id(driver, "aaaa.SearchOrderCompView.Button", timeout=20)
            logging.info(f"  {tag} Tombol SO Search diklik")
            time.sleep(3)  # tambah wait biar search tidak bentrok dengan page load
            result_count = wait_for_search_result(driver, timeout=120)  # naikkan timeout 90→120

        except Exception as e:
            err_msg = str(e).lower()
            logging.error(f"  {tag} Error setup/search batch {label}: {e}")
            # Deteksi ChromeDriver crash (cek string + status driver aktif)
            is_dead = _is_chromedriver_dead(err_msg, driver)
            if is_dead:
                logging.error(f"  {tag} ChromeDriver mati/tidak responsif. Worker akan berhenti.")
                return False, 0, None
            save_debug_artifacts(driver, f"w{worker_id}_batch_error_{date_from.strftime('%Y%m%d')}")
            time.sleep(15)
            continue

        # ── Evaluasi hasil ──
        has_rows = table_has_data_rows(driver)
        if result_count is not None:
            logging.info(f"  {tag} Hasil search: {result_count} rows")

        # Skip batch kosong
        if not has_rows and (result_count is None or result_count == 0):
            logging.warning(f"  {tag} Tidak ada data untuk batch {label}, dilewati")
            return True, 0, None

        if result_count is None:
            if has_rows:
                row_count_estimate = driver.execute_script(
                    """
                    const tbody = document.querySelector("[id$='Table_BasicList-contentTBody']");
                    if (!tbody) return 0;
                    return Array.from(tbody.querySelectorAll("tr")).filter(r =>
                        r.hasAttribute("rr") && r.getAttribute("rr") !== "-1" &&
                        !(r.innerHTML || "").includes("EMPTYROW")
                    ).length;
                    """
                ) or 0
                logging.warning(f"  {tag} Result count tidak terbaca, estimasi ~{row_count_estimate} baris")
                result_count = row_count_estimate
            else:
                logging.error(f"  {tag} Result count tidak terbaca & tabel kosong")
                save_debug_artifacts(driver, f"w{worker_id}_no_result_{date_from.strftime('%Y%m%d')}")
                time.sleep(20)
                continue

        if result_count >= CONFIG["SEARCH_LIMIT_ROWS"]:
            split_ranges = split_range_if_limit(date_from, date_to, range_level)
            if split_ranges:
                next_level = split_ranges[0][2]
                logging.warning(
                    f"  {tag} Batch {label} mencapai limit {result_count} rows. "
                    f"Skip download parent, split jadi {len(split_ranges)} sub-batch level {next_level}."
                )
                return True, result_count, None
            logging.warning(
                f"  {tag} Batch {label} masih {result_count} rows pada level 1week. "
                "Download tetap dilanjutkan, tapi data mungkin terpotong."
            )

        # ── Catat file & blob sebelum download ──
        before_files = list(download_dir.iterdir())
        before_blobs = get_blob_iframes(driver)

        # ── Klik Excel D/L ──
        if not click_excel_download_button(driver, timeout=20):
            logging.error(f"  {tag} Tombol Excel D/L tidak ditemukan")
            save_debug_artifacts(driver, f"w{worker_id}_excel_btn_{date_from.strftime('%Y%m%d')}")
            time.sleep(15)
            continue

        # ── Tunggu file ──
        logging.info(f"  {tag} Menunggu file Excel batch {label} (maks 10 menit)...")
        xl_file = wait_for_new_download(
            download_dir, before_files,
            timeout=600,
            driver=driver,
            before_blobs=before_blobs,
        )

        if not xl_file:
            logging.error(f"  {tag} File Excel tidak muncul dalam 10 menit")
            save_debug_artifacts(driver, f"w{worker_id}_dl_timeout_{date_from.strftime('%Y%m%d')}")
            # Jika download timeout berulang (attempt >= 2), reopen browser
            # agar session SAP tidak stale dan tidak terus retry di session yang sama
            if attempt >= 2:
                logging.warning(f"  {tag} Download timeout berulang (attempt {attempt}). Reopen browser untuk refresh session...")
                # FIX V16.2: safe_driver_quit — anti urllib3 spam + bersihkan
                # chrome.exe orphan by profile sebelum reopen.
                safe_driver_quit(driver, tag=tag)
                try:
                    driver = setup_driver(download_dir=download_dir, profile_tag=f"worker_{worker_id}")
                    if not login_to_portal(driver):
                        raise RuntimeError("Login gagal saat reopen browser worker")
                    close_popup_if_exists(driver)
                    if not navigate_to_smro(driver):
                        raise RuntimeError("Navigasi SMRO gagal saat reopen browser worker")
                    if not open_search_client_odr(driver):
                        raise RuntimeError("Search Client Odr gagal saat reopen browser worker")
                    worker_setup_session(driver, op_unit_values)
                    logging.info(f"  {tag} Browser worker berhasil direopen, retry batch...")
                except Exception as reopen_err:
                    logging.error(f"  {tag} Reopen browser gagal: {reopen_err}. Lanjut retry tanpa reopen...")
            time.sleep(10)
            continue

        logging.info(f"  {tag} File Excel: {xl_file.name} ({xl_file.stat().st_size / 1024:.1f} KB)")

        # ── Rename ──
        target_name = (
            f"smro_{date_from.strftime('%Y%m%d')}_"
            f"{date_to.strftime('%Y%m%d')}_{range_level}{xl_file.suffix}"
        )
        target_path = xl_file.parent / target_name
        if target_path.exists():
            target_path = xl_file.parent / (
                f"smro_{date_from.strftime('%Y%m%d')}_{date_to.strftime('%Y%m%d')}"
                f"_{range_level}_{int(time.time())}{xl_file.suffix}"
            )
        xl_file.rename(target_path)
        logging.info(f"  {tag} File: {target_path.name}")

        # ── Save local copy (optional, synchronous) ──
        if CONFIG['SAVE_LOCAL']:
            save_local_copy(target_path, CONFIG['LOCAL_SAVE_DIR'])

        # ── FIX V34 (KEPUTUSAN USER): upload BLOCKING — TUNGGU sampai selesai ──
        # Keluhan user (2026-09-22): "disk saya sering penuh ... excel menumpuk
        # di folder upload_job dashboard ... seharusnya ditunggu sampai upload
        # selesai baru lanjut upload yang lain, tidak peduli lamanya, harus
        # selesai upload dulu baru lanjut, supaya file yang diupload ini
        # langsung dihapus supaya disk tidak penuh".
        # Dulu (FIX V4): upload_async() non-blocking — worker terus mendownload
        # range berikutnya sambil file menunggu antrean upload. Akibatnya:
        #   - banyak file menumpuk lokal & di antrean server (upload_job),
        #   - kalau proses mati/di-kill, file tertinggal tanpa status jelas.
        # Sekarang: worker MENUNGGU upload batch ini selesai (retry berapa
        # kalipun lamanya), file langsung DIHAPUS begitu sukses, baru lanjut
        # download range berikutnya. Upload gagal -> file aman di
        # failed_uploads/, batch dilaporkan GAGAL (tidak mengulang download).
        if CONFIG['UPDATE_DASHBOARD']:
            logging.info(f"  {tag} Upload (blocking — tunggu sampai selesai, tidak peduli lamanya): {target_path.name}")
            upload_ok = upload_manager.upload(target_path)
            if upload_ok:
                logging.info(
                    f"  {tag} ✓ Batch selesai & TERUPLOAD: {target_name} ({result_count} rows)")
                return True, result_count, target_name
            logging.error(
                f"  {tag} ✗ Batch {target_name}: download sukses TAPI upload GAGAL "
                "setelah semua percobaan. File aman di failed_uploads/ dan akan "
                "diupload ulang otomatis di run berikutnya.")
            return False, result_count, target_name
        logging.info(f"  {tag} Upload dashboard dilewati sesuai setting")
        # Upload disabled — hapus file kerja
        try: target_path.unlink()
        except Exception: pass
        logging.info(f"  {tag} ✓ Batch download selesai: {target_name} ({result_count} rows)")
        return True, result_count, target_name

    return False, 0, None


def run_worker(assigned_ranges: list, op_unit_values: str,
               upload_manager: UploadManager, worker_id: int,
               results_dict: dict):
    """
    Satu worker thread: buka browser sendiri, login, proses range yang ditugaskan.
    """
    thread = threading.current_thread()
    thread.worker_id = worker_id
    tag = f"[W{worker_id}]"

    # Buat download directory khusus worker
    worker_download_dir = CONFIG['DOWNLOAD_DIR'] / f"worker_{worker_id}"
    worker_download_dir.mkdir(exist_ok=True)

    driver = None
    worker_results = []

    try:
        logging.info(f"{tag} ===== Worker {worker_id} dimulai ({len(assigned_ranges)} range) =====")

        # ── Setup browser ──
        # FIX V13: profile_tag stabil per worker supaya profile_dir reuseable
        # di retry (tidak bikin folder baru tiap retry -> hemat disk & lebih
        # cepat start karena profile sudah warm).
        driver = setup_driver(download_dir=worker_download_dir, profile_tag=f"worker_{worker_id}")

        # ── Login ──
        if not login_to_portal(driver):
            logging.error(f"{tag} Login gagal")
            results_dict[worker_id] = {"success": False, "results": [], "error": "Login gagal"}
            return

        close_popup_if_exists(driver)

        # ── Navigasi SMRO ──
        if not navigate_to_smro(driver):
            logging.error(f"{tag} Navigasi SMRO gagal")
            results_dict[worker_id] = {"success": False, "results": [], "error": "Navigasi SMRO gagal"}
            return

        # ── Buka Search Client Odr ──
        if not open_search_client_odr(driver):
            logging.error(f"{tag} Buka Search Client Odr gagal")
            results_dict[worker_id] = {"success": False, "results": [], "error": "Search Client Odr gagal"}
            return

        # ── Setup session (Op. Unit) ──
        try:
            worker_setup_session(driver, op_unit_values)
        except Exception as e:
            logging.error(f"{tag} Setup session gagal: {e}")
            results_dict[worker_id] = {"success": False, "results": [], "error": f"Setup session: {e}"}
            return

        # ── Proses setiap range ──
        worker_ok = True
        for date_from, date_to, range_level in assigned_ranges:
            # Cek dulu apakah perlu split
            range_ok = worker_process_one_range(
                driver, date_from, date_to, range_level,
                worker_download_dir, upload_manager, worker_id,
                worker_results, op_unit_values
            )
            worker_ok = range_ok and worker_ok

        logging.info(f"{tag} ===== Worker {worker_id} selesai ({len(worker_results)} batch) =====")
        results_dict[worker_id] = {"success": worker_ok, "results": worker_results}

    except Exception as e:
        logging.error(f"{tag} Worker gagal: {e}", exc_info=True)
        try:
            save_debug_artifacts(driver, f"worker_{worker_id}_error")
        except Exception:
            pass
        results_dict[worker_id] = {"success": False, "results": worker_results, "error": str(e)}

    finally:
        if driver:
            # FIX V16.2: safe_driver_quit — anti spam + bersihkan proses orphan.
            safe_driver_quit(driver, tag=tag)
            logging.info(f"{tag} Browser worker ditutup")


def run_queue_worker(range_queue, op_unit_values: str,
                     upload_manager: UploadManager, worker_id: int,
                     results_dict: dict, results_lock):
    """
    Worker persistent: buka satu Chrome, lalu ambil range berikutnya dari queue.
    Begitu upload range selesai, worker langsung lanjut range lain tanpa menunggu worker lain.
    """
    thread = threading.current_thread()
    thread.worker_id = worker_id
    tag = f"[W{worker_id}]"

    worker_download_dir = CONFIG['DOWNLOAD_DIR'] / f"worker_{worker_id}"
    worker_download_dir.mkdir(exist_ok=True)

    driver = None
    worker_results = []
    worker_ok = True
    # FIX: batasi retry per range supaya kegagalan permanen (bukan VPN)
    # tidak menyebabkan loop tak berkesudahan saat range terus dikembalikan ke queue.
    range_retry_count = {}
    MAX_RANGE_RETRY = 3

    try:
        logging.info(f"{tag} ===== Worker queue {worker_id} dimulai =====")

        # FIX V16.2: stagger start antar worker. Dua Chrome yang mulai LOGIN ke
        # portal di detik yang sama bikin beban CPU/RAM/network melonjak di
        # mesin kantor — salah satunya sering kena error/timeout. Worker kedua
        # diberi jeda supaya login W1 sudah setengah jalan dulu.
        if worker_id > 1:
            stagger_s = min(45, 15 * (worker_id - 1))
            logging.info(f"{tag} Jeda stagger {stagger_s}s sebelum start (hindari 2 Chrome login bersamaan)...")
            time.sleep(stagger_s)

        # FIX: initial login worker sebelumnya cuma dicoba 1x — kalau gagal
        # (mis. VPN baru connect & belum stabil saat autostart), worker langsung
        # menyerah tanpa retry sama sekali, beda dengan kegagalan saat proses
        # range yang sudah punya retry+reconnect. Sekarang initial setup juga
        # di-retry beberapa kali dengan browser baru sebelum benar-benar gagal.
        MAX_INITIAL_LOGIN_RETRY = 3
        INITIAL_LOGIN_RETRY_DELAY = 20

        last_setup_error = None
        for attempt in range(1, MAX_INITIAL_LOGIN_RETRY + 1):
            try:
                if driver:
                    # FIX V15: pakai safe_driver_quit supaya tidak spam urllib3
                    # retry kalau driver sudah mati (Chrome crash sebelumnya).
                    safe_driver_quit(driver, tag=tag)
                if attempt > 1:
                    if not check_vpn_connection():
                        logging.warning(f"{tag} VPN belum connect saat retry login #{attempt}. Menunggu VPN...")
                        # FIX V23: VPN wait dibatasi — kalau menyerah, batalkan
                        # loop login retry (worker gagal dengan jelas, range
                        # diproses worker lain / dianggap gagal, TIDAK menggantung).
                        if not _wait_vpn_reconnect():
                            logging.error(f"{tag} VPN tidak kembali dalam batas waktu. Login worker dibatalkan.")
                            break
                    logging.info(f"{tag} Retry login #{attempt}/{MAX_INITIAL_LOGIN_RETRY} setelah jeda {INITIAL_LOGIN_RETRY_DELAY}s...")
                    time.sleep(INITIAL_LOGIN_RETRY_DELAY)
                driver = setup_driver(download_dir=worker_download_dir, profile_tag=f"worker_{worker_id}")
                if not login_to_portal(driver):
                    raise RuntimeError("Login gagal")
                close_popup_if_exists(driver)
                if not navigate_to_smro(driver):
                    raise RuntimeError("Navigasi SMRO gagal")
                if not open_search_client_odr(driver):
                    raise RuntimeError("Search Client Odr gagal")
                worker_setup_session(driver, op_unit_values)
                last_setup_error = None
                break
            except Exception as setup_err:
                last_setup_error = setup_err
                # FIX V15: kalau ChromeDriver process sudah mati (Chrome crash),
                # jangan waste 30+ detik di urllib3 retry dengan save_screenshot
                # pada session yang sudah closed. Pre-flight check is_driver_alive
                # di save_debug_artifacts sudah skip cleanly.
                err_str = str(setup_err).lower()
                driver_dead = (
                    'refused' in err_str or 'target machine actively refused' in err_str
                    or 'connection refused' in err_str or 'invalid session' in err_str
                    or 'session not created' in err_str
                    or (driver is not None and not is_driver_alive(driver))
                )
                if driver_dead:
                    logging.error(
                        f"{tag} Setup awal gagal (percobaan {attempt}/{MAX_INITIAL_LOGIN_RETRY}): "
                        f"ChromeDriver session closed/crashed — skip save_debug_artifacts (akan retry dengan Chrome baru)"
                    )
                else:
                    logging.error(f"{tag} Setup awal gagal (percobaan {attempt}/{MAX_INITIAL_LOGIN_RETRY}): {setup_err}")
                    try:
                        save_debug_artifacts(driver, f"worker_{worker_id}_initial_setup_error_{attempt}")
                    except Exception:
                        pass

        if last_setup_error is not None:
            raise RuntimeError(f"Login/setup awal gagal setelah {MAX_INITIAL_LOGIN_RETRY}x percobaan: {last_setup_error}")

        while True:
            try:
                date_from, date_to, range_level = range_queue.get_nowait()
            except queue.Empty:
                break

            try:
                logging.info(
                    f"{tag} Ambil range berikutnya: {format_smro_date(date_from)} - "
                    f"{format_smro_date(date_to)} (sisa queue {range_queue.qsize()})"
                )
                range_ok = worker_process_one_range(
                    driver, date_from, date_to, range_level,
                    worker_download_dir, upload_manager, worker_id,
                    worker_results, op_unit_values,
                )
                worker_ok = range_ok and worker_ok
                range_queue.task_done()
            except Exception as e:
                logging.error(f"{tag} Range gagal: {e}", exc_info=True)
                try:
                    save_debug_artifacts(driver, f"worker_{worker_id}_range_error")
                except Exception:
                    pass

                # FIX: kalau penyebabnya VPN/koneksi putus, jangan langsung
                # menyerah pada range ini — tunggu VPN tersambung lagi, buka
                # ulang browser, lalu coba range yang sama sekali lagi
                # (dikembalikan ke queue), alih-alih dianggap gagal permanen.
                range_key = (date_from, date_to, range_level)
                if not check_vpn_connection():
                    logging.warning(
                        f"{tag} Koneksi/VPN terputus saat memproses "
                        f"{format_smro_date(date_from)} - {format_smro_date(date_to)}. "
                        f"Menunggu VPN tersambung kembali..."
                    )
                    # FIX V23: VPN wait dibatasi — kalau menyerah, range ini
                    # dianggap gagal (tidak di-retry) supaya worker TIDAK
                    # menggantung menunggu VPN selamanya.
                    if not _wait_vpn_reconnect():
                        logging.error(
                            f"{tag} VPN tidak kembali dalam batas waktu. Range "
                            f"{format_smro_date(date_from)} - {format_smro_date(date_to)} "
                            f"dianggap gagal dan tidak di-retry."
                        )
                        worker_ok = False
                        range_queue.task_done()
                        continue
                    logging.info(f"{tag} VPN sudah tersambung lagi. Membuka ulang browser worker...")
                    try:
                        # FIX V16.2: safe_driver_quit — quit() biasa pada driver
                        # mati memicu urllib3 spam; ini juga force-kill sisa
                        # chrome.exe orphan by profile.
                        safe_driver_quit(driver, tag=tag)
                        driver = setup_driver(download_dir=worker_download_dir, profile_tag=f"worker_{worker_id}")
                        if not login_to_portal(driver):
                            raise RuntimeError("Login gagal saat reconnect")
                        close_popup_if_exists(driver)
                        if not navigate_to_smro(driver):
                            raise RuntimeError("Navigasi SMRO gagal saat reconnect")
                        if not open_search_client_odr(driver):
                            raise RuntimeError("Search Client Odr gagal saat reconnect")
                        worker_setup_session(driver, op_unit_values)
                        logging.info(f"{tag} Browser berhasil dibuka ulang. Range dikembalikan ke antrean.")
                        # Kembalikan range ke queue supaya dicoba ulang, bukan dilewati
                        # (dibatasi MAX_RANGE_RETRY supaya tidak loop selamanya)
                        retry_n = range_retry_count.get(range_key, 0)
                        if retry_n < MAX_RANGE_RETRY:
                            range_retry_count[range_key] = retry_n + 1
                            range_queue.put(range_key)
                        else:
                            logging.error(f"{tag} Range {format_smro_date(date_from)} - {format_smro_date(date_to)} sudah {MAX_RANGE_RETRY}x gagal, dilewati.")
                            worker_ok = False
                        range_queue.task_done()
                        continue
                    except Exception as reopen_err:
                        logging.error(
                            f"{tag} Gagal membuka ulang browser setelah VPN reconnect: {reopen_err}. "
                            f"Range dikembalikan ke antrean, akan dicoba lagi nanti."
                        )
                        retry_n = range_retry_count.get(range_key, 0)
                        if retry_n < MAX_RANGE_RETRY:
                            range_retry_count[range_key] = retry_n + 1
                            range_queue.put(range_key)
                        else:
                            logging.error(f"{tag} Range {format_smro_date(date_from)} - {format_smro_date(date_to)} sudah {MAX_RANGE_RETRY}x gagal, dilewati.")
                            worker_ok = False
                        range_queue.task_done()
                        time.sleep(15)
                        continue

                # Bukan masalah VPN — anggap gagal permanen untuk range ini, lanjut ke range berikutnya
                worker_ok = False
                range_queue.task_done()

        logging.info(f"{tag} ===== Worker queue {worker_id} selesai ({len(worker_results)} batch) =====")
        with results_lock:
            results_dict[worker_id] = {"success": worker_ok, "results": worker_results}

    except Exception as e:
        logging.error(f"{tag} Worker queue gagal: {e}", exc_info=True)
        try:
            save_debug_artifacts(driver, f"worker_{worker_id}_error")
        except Exception:
            pass
        with results_lock:
            results_dict[worker_id] = {"success": False, "results": worker_results, "error": str(e)}

    finally:
        if driver:
            # FIX V16.2: safe_driver_quit (bukan driver.quit() langsung) —
            # driver.quit() pada chromedriver yang sudah mati memicu urllib3
            # Retry spam DELETE /session/... 3x (terlihat di log user
            # 09:09:59-09:10:07) DAN meninggalkan chrome.exe orphan.
            safe_driver_quit(driver, tag=tag)
            logging.info(f"{tag} Browser worker {worker_id} ditutup")


def worker_process_one_range(driver, date_from, date_to, range_level,
                              download_dir, upload_manager, worker_id,
                              worker_results, op_unit_values=None):
    """
    Proses satu range tanggal dengan splitting logic:
    Jika result >= SEARCH_LIMIT_ROWS, split → sub-range → recursive.

    op_unit_values diteruskan ke worker_process_range supaya path reopen
    browser (saat download timeout berulang) bisa re-init Op. Unit di form
    Search Client Odr. Sebelumnya tidak dithread → NameError.
    """
    tag = f"[W{worker_id}]"

    # ── Proses range ini ──
    ok, result_count, target_name = worker_process_range(
        driver, date_from, date_to, range_level,
        download_dir, upload_manager, worker_id, op_unit_values
    )

    if target_name is not None or not ok:
        worker_results.append({
            "range": f"{format_smro_date(date_from)} - {format_smro_date(date_to)}",
            "level": range_level,
            "rows": result_count,
            "file": target_name or "-",
            "success": ok
        })

    # ── Jika kena limit dan bisa split lebih lanjut ──
    if ok and result_count is not None and result_count >= CONFIG["SEARCH_LIMIT_ROWS"]:
        split_ranges = split_range_if_limit(date_from, date_to, range_level)
        if split_ranges:
            next_level = split_ranges[0][2]
            logging.warning(
                f"  {tag} Batch mencapai limit {result_count} rows. "
                f"Split jadi {len(split_ranges)} sub-batch level {next_level}."
            )
            sub_ok = True
            for sub_from, sub_to, sub_level in split_ranges:
                sub_ok = worker_process_one_range(
                    driver, sub_from, sub_to, sub_level,
                    download_dir, upload_manager, worker_id,
                    worker_results, op_unit_values
                ) and sub_ok
            return sub_ok
        else:
            logging.warning(
                f"  {tag} Batch masih {result_count} rows pada level 1week. "
                "Data mungkin terpotong."
            )
    return ok


def run_parallel_automation():
    """
    Orchestrator parallel: split bulan → chunk (6) → spawn workers per chunk.
    """
    logging.info("=" * 70)
    logging.info("SMRO PARALLEL AUTOMATION DIMULAI")
    logging.info(f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Parallel workers: {CONFIG['PARALLEL_WORKERS']} Chrome per batch")
    logging.info(f"Max concurrent uploads: {CONFIG['MAX_CONCURRENT_UPLOADS']}")
    logging.info("=" * 70)

    # FIX V33: file gagal upload dari run sebelumnya di-requeue, lalu dipulihkan
    # juga file Excel yang tertinggal di folder worker (run sebelumnya mati
    # sebelum upload selesai). Dilakukan SEKARANG karena titik ini BEBAS race —
    # belum ada thread upload yang membaca file manapun.
    cleanup_old_failed_uploads()
    cleanup_old_backup_files()  # FIX V34: backup lama ikut dibersihkan
    collect_orphan_uploads()

    upload_manager = UploadManager(max_concurrent=CONFIG['MAX_CONCURRENT_UPLOADS'])
    op_unit_values = None
    main_driver = None
    success = False

    try:
        # ── Step 1: VPN ──
        if not check_vpn_connection():
            msg = "VPN tidak terhubung!\n\nSilakan connect ke AxGate VPN terlebih dahulu."
            logging.error(msg)
            show_popup("VPN Required", msg, success=False)
            return False

        # FIX V33: requeue file gagal upload SETELAH VPN dipastikan connect —
        # kalau di-requeue saat VPN mati, thread upload akan habiskan seluruh
        # retry percuma dan menahan proses exit tanpa hasil.
        requeue_failed_uploads(upload_manager)

        # ── Step 2: Dapatkan semua date ranges ──
        all_ranges = get_download_date_ranges()
        total_months = len(all_ranges)
        logging.info(f"Total bulan: {total_months}")

        # ── Step 3: Siapkan queue range untuk worker persistent ──
        worker_count = min(CONFIG['PARALLEL_WORKERS'], len(all_ranges))
        range_queue = queue.Queue()
        for item in all_ranges:
            range_queue.put(item)
        logging.info(f"Worker pool: {worker_count} Chrome persistent untuk {len(all_ranges)} range")

        # ── Step 4: Ambil Op. Unit ID (sekali, untuk semua worker) ──
        logging.info("\n[Setup] Mengambil Op. Unit ID...")
        # FIX V13: profile_tag dedicated untuk orchestrator browser (bukan
        # worker_1/worker_2) supaya tidak bentrok dengan worker yang akan
        # start sesaat setelah ini.
        main_driver = setup_driver(profile_tag="orchestrator")
        try:
            if not login_to_portal(main_driver):
                show_popup("Login Failed", "Gagal login ke portal", success=False)
                return False
            close_popup_if_exists(main_driver)
            if not navigate_to_smro(main_driver):
                show_popup("Navigation Failed", "Gagal navigasi SMRO", success=False)
                return False
            op_unit_values = get_registered_op_unit_values(main_driver)
            logging.info(f"  Op. Unit ID: {len(op_unit_values.split(',')) if op_unit_values else 0} data")
        except Exception as e:
            logging.warning(f"  Gagal ambil Op. Unit: {e}. Lanjut tanpa filter.")
            op_unit_values = None
        finally:
            if main_driver:
                # FIX V16.2: safe quit — anti spam + orphan cleanup.
                safe_driver_quit(main_driver)
                main_driver = None
                logging.info("  Browser setup ditutup")

        # ── Step 5: Jalankan worker pool ──
        logging.info("\n" + "=" * 60)
        logging.info(f"Membuka {worker_count} Chrome parallel persistent...")
        logging.info("Worker yang selesai upload akan langsung ambil range berikutnya.")
        logging.info("=" * 60)

        results_dict = {}
        results_lock = threading.Lock()
        threads = []
        for worker_id in range(1, worker_count + 1):
            t = threading.Thread(
                target=run_queue_worker,
                args=(range_queue, op_unit_values, upload_manager, worker_id, results_dict, results_lock),
                name=f"Worker-{worker_id}",
            )
            t.daemon = True
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        total_uploaded = 0
        total_failed = 0
        worker_ok = 0
        worker_fail = 0
        for result in results_dict.values():
            if result.get("success"):
                worker_ok += 1
            else:
                worker_fail += 1
            for r in result.get("results", []):
                if r.get("success"):
                    total_uploaded += 1
                else:
                    total_failed += 1

        logging.info(f"Worker selesai: {worker_ok} sukses" + (f", {worker_fail} gagal" if worker_fail else ""))
        logging.info(f"Batch selesai: {total_uploaded} sukses" + (f", {total_failed} gagal" if total_failed else ""))

        # ── Tunggu semua upload async selesai ──
        # Worker sudah selesai download semua range, tapi upload threads mungkin masih jalan.
        # FIX V33: timeout kini TOTAL (bukan per-thread) dan HASILNYA DIPAKAI —
        # sebelumnya wait_all(1800) diabaikan sehingga proses bisa exit sambil
        # upload masih berjalan (thread dibunuh, batch tetap dianggap sukses).
        # Progres dilog tiap 60s supaya stall watchdog (Run All 900s / GUI 2700s)
        # tidak salah mengira proses hang lalu membunuhnya di tengah antrean upload.
        logging.info("=" * 60)
        logging.info("Menunggu semua upload async selesai sebelum final retry...")
        logging.info("=" * 60)
        uploads_done = upload_manager.wait_all(
            timeout=CONFIG['UPLOAD_FINISH_WAIT_SECONDS'])

        # ── Retry semua upload yang gagal (NEW) ──
        # File-file di failed_uploads/ akan di-retry satu per satu di akhir run.
        # Ini memberi kesempatan kedua kalau PythonAnywhere sebelumnya down.
        stats_before_retry = upload_manager.get_stats()
        if stats_before_retry.get('failed_files', 0) > 0:
            logging.info("=" * 60)
            logging.info(f"Final retry: {stats_before_retry['failed_files']} file yang gagal upload")
            logging.info("=" * 60)
            retried_ok = upload_manager.retry_failed()
            logging.info(f"Final retry selesai: {retried_ok} file berhasil diupload ulang")
        else:
            logging.info("Tidak ada file yang gagal upload. Final retry dilewati.")

        # ── Final report ──
        upload_stats = upload_manager.get_stats()

        # ── FIX V33: REKONSILIASI UPLOAD ──
        # Dulu: batch langsung dianggap sukses begitu Excel-nya selesai
        # didownload (upload cuma disubmit async, hasilnya tidak pernah dicek).
        # Sekarang: setiap file yang disubmit WAJIB punya hasil final. File
        # tanpa hasil (thread mati/timeout) dipindah ke failed_uploads/ — BUKAN
        # dihapus — dan run dilaporkan GAGAL supaya watcher/Run All mengulang.
        pending = upload_manager.pending_files()
        for p in pending:
            parked = park_unuploaded_file(p)
            logging.error(
                f"✗ Upload tidak pernah selesai untuk file: {p.name}"
                + (" (disimpan di failed_uploads/ untuk run berikutnya)" if parked else "")
            )
        remaining_failed = list(upload_manager.failed_files)
        uploads_ok = (
            uploads_done
            and not pending
            and not remaining_failed
            and upload_stats['failed'] == 0
        )
        if not uploads_ok:
            logging.error(
                f"✗ Rekap upload TIDAK lengkap: "
                f"selesai={upload_stats['total']}, gagal={upload_stats['failed']}, "
                f"tanpa hasil={len(pending)}, tersisa gagal={len(remaining_failed)}. "
                "File gagal dipertahankan di failed_uploads/ dan akan diupload ulang di run berikutnya."
            )

        # FIX: sebelumnya success di-hardcode True tanpa cek hasil worker sama
        # sekali, jadi meski semua worker gagal login (0 sukses, 0 upload),
        # tetap dilaporkan "Success" dan watcher/Run All tidak pernah retry.
        # Sekarang: gagal total kalau tidak ada satupun worker yang berhasil.
        # FIX V33: upload yang tidak lengkap juga membuat run dianggap GAGAL —
        # bukan lagi sukses hanya karena download-nya sukses.
        success = worker_ok > 0
        if not success:
            message = (
                f"❌ SMRO Parallel Automation Gagal!\n\n"
                f"Semua worker ({worker_fail}) gagal login/setup.\n"
                f"Kemungkinan VPN belum stabil atau portal tidak bisa diakses.\n"
                f"⏰ Waktu: {datetime.now().strftime('%H:%M:%S')}"
            )
            logging.info("\n" + "=" * 70)
            logging.error(f"✗ PARALLEL AUTOMATION GAGAL TOTAL: {worker_fail} worker gagal, 0 sukses")
            logging.info("=" * 70)
            show_popup("Automation Error", message, success=False)
            cleanup_worker_dirs()
            return False

        if not uploads_ok:
            # FIX V33: download sukses tapi upload tidak lengkap — JANGAN
            # laporkan sukses (dulu: "✅ SELESAI" padahal data tidak sampai ke
            # endpoint). Return False supaya watcher/jadwal mengulang proses;
            # file yang belum terupload sudah aman di failed_uploads/ dan akan
            # di-requeue otomatis di awal run berikutnya.
            upload_issue_count = upload_stats['failed'] + len(pending) + len(remaining_failed)
            message = (
                f"⚠ SMRO Parallel Automation selesai dengan ERROR upload!\n\n"
                f"📥 Download batch: {total_uploaded} sukses\n"
                f"📤 Upload selesai: {upload_stats['total']} file\n"
                f"❌ Upload gagal/belum selesai: {upload_issue_count} file\n\n"
                f"File gagal disimpan di failed_uploads/ dan akan diupload ulang otomatis.\n"
                f"⏰ Waktu: {datetime.now().strftime('%H:%M:%S')}"
            )
            logging.info("\n" + "=" * 70)
            logging.error("✗ PARALLEL AUTOMATION SELESAI DENGAN ERROR UPLOAD")
            logging.info("=" * 70)
            show_popup("Upload Incomplete", message, success=False)
            cleanup_worker_dirs()
            return False

        message = (
            f"✅ SMRO Parallel Automation Selesai!\n\n"
            f"📤 Upload: {upload_stats['total']} file\n"
            f"❌ Gagal: {upload_stats['failed']}\n"
            f"⏰ Waktu: {datetime.now().strftime('%H:%M:%S')}"
        )
        logging.info("\n" + "=" * 70)
        logging.info(f"✅ PARALLEL AUTOMATION SELESAI")
        logging.info(f"   Total upload: {upload_stats['total']}, Gagal: {upload_stats['failed']}")
        logging.info("=" * 70)
        show_popup("Success", message, success=True)

        # Cleanup worker download directories
        cleanup_worker_dirs()
        return True

    except Exception as e:
        logging.error(f"\n✗ Unexpected error: {e}", exc_info=True)
        show_popup("Automation Error", f"Terjadi error:\n{e}", success=False)
        return False

    finally:
        if main_driver:
            safe_driver_quit(main_driver)  # FIX V16.2: anti spam + orphan cleanup


def cleanup_worker_dirs():
    """Bersihkan folder download worker.

    FIX V33: folder worker di-sweep dulu oleh collect_orphan_uploads() — file
    Excel yang masih ada di sini berarti BELUM TERUPLOAD (run mati di tengah
    jalan) dan TIDAK BOLEH dihapus; dipindah ke failed_uploads/ untuk diupload
    ulang di run berikutnya. Yang dihapus hanya file sementara
    (.crdownload/.tmp/.htm/.html)."""
    collect_orphan_uploads()
    exts = {".crdownload", ".tmp", ".htm", ".html"}
    for fp in CONFIG["DOWNLOAD_DIR"].iterdir():
        if fp.is_dir() and fp.name.startswith("worker_"):
            try:
                for f in fp.iterdir():
                    try:
                        if f.is_file() and f.suffix.lower() in exts:
                            f.unlink()
                    except Exception:
                        pass
                fp.rmdir()
            except Exception:
                pass


# ──────────────────────────── MAIN AUTOMATION ─────────────────────────────────

def run_automation():
    """
    Jalankan full automation secara SEQUENTIAL (1 Chrome).
    - Proses per bulan, satu per satu, tanpa parallel.
    - Jika koneksi putus di tengah, browser dibuka ulang dan proses
      dilanjutkan dari batch terakhir.
    """
    logging.info("=" * 70)
    logging.info("SMRO SEQUENTIAL AUTOMATION DIMULAI")
    logging.info(f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 70)

    cleanup_old_failed_uploads()
    cleanup_old_backup_files()  # FIX V34: backup lama ikut dibersihkan
    # FIX V33: pulihkan file Excel yang tertinggal (run sebelumnya mati sebelum
    # upload selesai) lalu upload ulang file gagal/belum terupload SEBELUM
    # memproses range baru — supaya data lama tidak menumpuk tanpa pernah
    # sampai ke endpoint.
    collect_orphan_uploads()
    requeue_pending_uploads_sync()
    driver = None
    success = False
    op_unit_values = None

    try:
        # ── VPN ──
        if not check_vpn_connection():
            msg = "VPN tidak terhubung!\n\nSilakan connect ke AxGate VPN terlebih dahulu."
            logging.error(msg)
            show_popup("VPN Required", msg, success=False)
            return False

        # ── Setup browser ──
        logging.info("\n[Setup] Membuka browser...")
        cleanup_downloaded_excel_files(include_completed=False)
        # FIX V13: profile_tag dedicated untuk sequential automation (bukan
        # worker_N) supaya tidak bentrok dengan profil lain.
        driver = setup_driver(profile_tag="sequential")

        # ── Login ──
        logging.info("\n[Login]")
        if not login_to_portal(driver):
            show_popup("Login Failed", "Gagal login ke portal", success=False)
            return False
        close_popup_if_exists(driver)

        # ── Navigasi SMRO ──
        logging.info("\n[Navigasi]")
        if not navigate_to_smro(driver):
            show_popup("Navigation Failed", "Gagal navigasi SMRO", success=False)
            return False

        # ── Buka Search Client Odr ──
        logging.info("\n[Search Client Odr]")
        if not open_search_client_odr(driver):
            show_popup("Search Client Odr Failed", "Gagal buka Search Client Odr", success=False)
            return False

        # ── Ambil Op. Unit ID ──
        logging.info("\n[Op. Unit ID]")
        op_unit_values = get_registered_op_unit_values(driver)
        logging.info(f"  Op. Unit ID berhasil diambil")

        # ── Proses semua range SEQUENTIAL ──
        logging.info("\n[Download & Upload]")
        if not open_search_client_odr(driver):
            show_popup("Search Client Odr Failed", "Gagal kembali ke Search Client Odr", success=False)
            return False
        success, driver, _, _ = search_and_download_smro(driver, op_unit_values=op_unit_values)

    except Exception as e:
        logging.error(f"\n✗ Unexpected error: {e}", exc_info=True)
        show_popup("Automation Error", f"Terjadi error:\n{e}", success=False)
        success = False

    finally:
        if driver:
            safe_driver_quit(driver)  # FIX V16.2: anti spam + orphan cleanup

    # ── Popup hasil ──
    if success:
        logging.info("\n" + "=" * 70)
        logging.info("✅ SEQUENTIAL AUTOMATION SELESAI")
        logging.info("=" * 70)
        show_popup("Success", "✅ SMRO Sequential Automation Selesai!", success=True)
    else:
        logging.warning("\n" + "=" * 70)
        logging.warning("⚠ Sequential automation selesai dengan error")
        logging.warning("=" * 70)

    return success
def parse_run_times(value: str):
    raw_parts = [p.strip() for p in re.split(r"[,;\s]+", value or "") if p.strip()]
    parts = raw_parts or ["15:00"]
    parsed = []
    for part in parts:
        try:
            parsed.append(datetime.strptime(part, "%H:%M").time())
        except ValueError:
            logging.warning(f"Format SMRO_AFTERNOON_RUN_TIME tidak valid: {part}. Dilewati.")
    if not parsed:
        logging.warning("Tidak ada jadwal valid. Pakai default 15:00.")
        parsed = [datetime.strptime("15:00", "%H:%M").time()]
    return sorted(set(parsed))

def run_smro_download_process() -> bool:
    if CONFIG['PARALLEL_WORKERS'] > 1:
        return run_parallel_automation()
    return run_automation()


def wait_for_vpn_and_run():
    os.environ.setdefault('SMRO_SHOW_POPUP', 'false')
    run_times = parse_run_times(CONFIG['AFTERNOON_RUN_TIME'])
    run_time_labels = [t.strftime("%H:%M") for t in run_times]

    logging.info("\n" + "=" * 70)
    logging.info("VPN WATCH MODE AKTIF")
    logging.info(f"Cek VPN tiap {CONFIG['VPN_CHECK_SECONDS']} detik")
    logging.info(f"Retry automation tiap {CONFIG['RETRY_SECONDS']} detik kalau gagal")
    logging.info(f"Run update harian: {', '.join(run_time_labels)}")
    logging.info("Press Ctrl+C untuk berhenti")
    logging.info("=" * 70 + "\n")

    vpn_connected = False
    first_run_date = None
    schedule_done = {}
    pending_run = None
    pending_schedule_label = None
    attempt = 0

    while True:
        now = datetime.now()
        today = now.date()
        connected = check_vpn_connection()

        if not connected:
            if vpn_connected:
                logging.info("VPN disconnect. Automation standby sampai VPN connect lagi.")
            vpn_connected = False
            pending_run = None
            pending_schedule_label = None
            attempt = 0
            time.sleep(CONFIG['VPN_CHECK_SECONDS'])
            continue

        if not vpn_connected:
            logging.info("VPN terdeteksi connect.")
            vpn_connected = True
            if first_run_date != today:
                pending_run = "initial"

        if pending_run is None:
            done_today = schedule_done.setdefault(today, set())
            due_times = [t for t in run_times if now.time() >= t and t.strftime("%H:%M") not in done_today]
            if due_times:
                pending_run = "schedule"
                pending_schedule_label = due_times[0].strftime("%H:%M")

        if pending_run is None:
            time.sleep(CONFIG['VPN_CHECK_SECONDS'])
            continue

        attempt += 1
        label = "run awal setelah VPN connect" if pending_run == "initial" else f"run schedule {pending_schedule_label}"
        logging.info(f"Percobaan {label} #{attempt}")

        # FIX V38: watcher boleh hidup permanen, tetapi hanya boleh memulai
        # run saat tidak ada automation lain yang sedang aktif. Presence mutex
        # dipakai sebagai gate; idle watcher tidak memblokir siapa pun.
        try:
            import run_presence as _rp
            if _rp.is_held():
                logging.info("Automation lain sedang berjalan. VPN watcher SMRO menunggu giliran.")
                time.sleep(CONFIG['VPN_CHECK_SECONDS'])
                continue
            _rp.acquire()
        except Exception:
            _rp = None
        try:
            _run_ok = run_smro_download_process()
        finally:
            if _rp:
                _rp.release()
        if _run_ok:
            completed = pending_run
            pending_run = None
            attempt = 0
            if completed == "initial":
                first_run_date = today
                done_today = schedule_done.setdefault(today, set())
                for t in run_times:
                    if datetime.now().time() >= t:
                        done_today.add(t.strftime("%H:%M"))
                if done_today:
                    logging.info("Run awal berhasil setelah jadwal lewat, jadwal yang sudah lewat hari ini dianggap selesai.")
                else:
                    logging.info("Run awal berhasil. Menunggu jadwal update harian.")
            else:
                if pending_schedule_label:
                    schedule_done.setdefault(today, set()).add(pending_schedule_label)
                logging.info("Run schedule berhasil. Menunggu jadwal berikutnya.")
            pending_schedule_label = None
            time.sleep(CONFIG['VPN_CHECK_SECONDS'])
        else:
            logging.warning("Automation gagal.")
            if not check_vpn_connection():
                logging.warning("VPN tidak accessible setelah gagal. Menunggu VPN connect lagi.")
                vpn_connected = False
                while not check_vpn_connection():
                    time.sleep(CONFIG['VPN_CHECK_SECONDS'])
                vpn_connected = True
                logging.info("VPN connect lagi. Mengulang automation.")
                time.sleep(CONFIG['VPN_CHECK_SECONDS'])
            else:
                logging.warning(f"Retry dalam {CONFIG['RETRY_SECONDS']} detik.")
                time.sleep(CONFIG['RETRY_SECONDS'])


# ──────────────────────────── ENTRY POINT ─────────────────────────────────────

def _arg_value(args, name, default=None):
    if name not in args:
        return default
    pos = args.index(name)
    if pos + 1 >= len(args):
        return default
    return args[pos + 1]

def run_selected_process(process: str, watch: bool = False) -> bool:
    process = (process or "smro").strip().lower().replace("_", "-")
    if process in ("all", "run-all", "all-processes"):
        if watch:
            if not acquire_single_instance_lock("AllProcessAutomationWatcher"):
                return True
            wait_for_vpn_and_run_all()
            return True
        return run_all_processes_sequential()
    if process in ("smro", "search-client-odr", "client-odr"):
        if watch:
            if not acquire_single_instance_lock("SMROAutomationWatcher"):
                return True
            wait_for_vpn_and_run()
            return True
        return run_smro_download_process()
    if process in ("serp", "all-product-id", "product-id"):
        import automation_serp as serp_process
        if watch:
            if not serp_process.acquire_single_instance_lock():
                return True
            serp_process.wait_for_vpn_and_run()
            return True
        return serp_process.run_automation()
    if process in ("pur", "pur-info-reg", "process-pur-info-reg", "item-registration"):
        import automation_pur_info_reg as pur_process
        if watch:
            if not pur_process.smro.acquire_single_instance_lock("PurInfoRegAutomationWatcher"):
                return True
            pur_process.wait_for_vpn_and_run()
            return True
        return pur_process.run_automation()
    raise ValueError(f"Process tidak dikenal: {process}")

# ═════ FIX V23: SUPERVISI STEP RUN ALL (anti-macet berlapis) ═══════════════════

_STEP_LOG_NAMES = {
    "serp": "serp_automation.log",
    "pur-info-reg": "pur_info_reg_automation.log",
    "smro": "smro_automation.log",
}


def _RUN_ALL_STEP_STALL_SECONDS():
    """FIX V23: batas waktu stall watchdog per-step Run All — step dianggap
    MACET kalau log-nya tidak bertambah sama sekali selama N detik
    (default 900s / 15 menit, env SMRO_RUN_ALL_STEP_STALL_SECONDS)."""
    try:
        return max(60, int(os.getenv('SMRO_RUN_ALL_STEP_STALL_SECONDS', '900')))
    except Exception:
        return 900


def _kill_step_process_tree(proc):
    """FIX V23: bunuh step subprocess + SELURUH anak-cucunya.

    Kelemahan lama: subprocess.run(timeout=...) di Windows hanya memanggil
    TerminateProcess pada anak LANGSUNG — cucu (chrome.exe, chromedriver,
    SAP GUI, EXCEL.EXE yang dibuka SAP) jadi yatim: Excel hasil download
    tetap terbuka & mengunci file, SAP popup bisa mengganggu step
    berikutnya. Solusi: taskkill /T /F (Windows) + fallback psutil
    (bunuh anak-cucu dulu, baru induk)."""
    if not proc:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        pass
    pid = proc.pid
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
    else:
        try:
            proc.terminate()
        except Exception:
            pass
    # Fallback lintas-OS: psutil — anak-cucu dulu supaya tidak sempat
    # spawn ulang, lalu induknya.
    try:
        import psutil
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for ch in children:
                try:
                    ch.terminate()
                except Exception:
                    pass
            try:
                _, alive = psutil.wait_procs(children, timeout=5)
            except Exception:
                alive = []
            for ch in alive:
                try:
                    ch.kill()
                except Exception:
                    pass
            if parent.is_running():
                try:
                    parent.kill()
                except Exception:
                    pass
        except psutil.NoSuchProcess:
            pass
    except Exception:
        pass
    # Pastikan induk benar-benar mati supaya poll() bersih.
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _supervised_step_wait(proc, log_path, hard_timeout, stall_timeout,
                          poll_interval=2.0):
    """FIX V23: tunggu subprocess step Run All dengan DUA pengawas sekaligus.

    1. Hard timeout (RUN_ALL_STEP_TIMEOUT_SECONDS) — umur maksimum step.
    2. Stall watchdog (SMRO_RUN_ALL_STEP_STALL_SECONDS) — kalau LOG FILE
       step tidak bertambah sama sekali selama N detik = step macet diam
       (hang COM/UIA/SAP yang tidak melempar exception dan tidak menulis
       log apa pun) -> bunuh pohon prosesnya.

    Return (returncode, reason):
      (rc, None)         -> step selesai normal (rc bisa != 0).
      (None, "timeout")  -> hard timeout; proses SUDAH di-kill tree.
      (None, "stall")    -> log diam terlalu lama; proses SUDAH di-kill tree.

    Catatan: VPN-wait loop yang menulis log tiap 30s TIDAK terdeteksi oleh
    stall watchdog (log terus bertambah) — kelas itu ditangani oleh batas
    max_wait di _wait_vpn_reconnect/_wait_vpn_reconnect_serp/_wait_vpn_
    reconnect_pur supaya worker-nya tetap bisa exit sendiri.
    """
    started = time.time()
    last_growth = started
    try:
        last_size = log_path.stat().st_size if log_path.exists() else 0
    except Exception:
        last_size = 0
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc, None
        now = time.time()
        if now - started >= hard_timeout:
            _kill_step_process_tree(proc)
            return None, "timeout"
        try:
            size = log_path.stat().st_size if log_path.exists() else 0
            if size > last_size:
                last_size = size
                last_growth = now
        except Exception:
            pass
        if now - last_growth >= stall_timeout:
            _kill_step_process_tree(proc)
            return None, "stall"
        time.sleep(poll_interval)


def _run_all_cleanup_sap_excel():
    """FIX V23: pembersihan antar-step Run All setelah step SERP (All
    Product ID): tutup paksa EXCEL.EXE + proses SAP GUI yang tertinggal.

    Kenapa selalu dijalankan (walau step sukses): SERP punya beberapa jalur
    keluar dini (mis. 'Download Gagal') yang TIDAK melewati cleanup
    internalnya — SAP/Excel bisa menggantung terbuka (keluhan user: "excel
    hasil download all prod id juga masih terbuka"), file Excel tertinggal
    di folder download, dan SAP popup bisa mengganggu step berikutnya.
    Step PUR/SMRO tidak memakai Excel/SAP sama sekali, jadi pembersihan
    ini 100% aman bagi mereka."""
    names = {"excel.exe", "saplogon.exe", "sapgui.exe", "saplgpad.exe",
             "sapmsas.exe"}
    closed = 0
    try:
        import psutil
        targets = []
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if (p.info["name"] or "").lower() in names:
                    targets.append(p)
            except Exception:
                continue
        for p in targets:
            try:
                logging.info(f"  [Run All cleanup] Tutup {p.info['name']} (PID {p.info['pid']})")
                p.terminate()
                closed += 1
            except Exception:
                continue
        if targets:
            time.sleep(1.0)
            for p in psutil.process_iter(["name", "pid"]):
                try:
                    if (p.info["name"] or "").lower() in names:
                        p.kill()
                except Exception:
                    continue
    except Exception as e:
        logging.debug(f"  [Run All cleanup] Excel/SAP cleanup dilewati: {e}")
    if closed:
        logging.info(
            f"  [Run All cleanup] {closed} proses Excel/SAP ditutup supaya "
            f"step berikutnya jalan bersih (tanpa file terkunci)."
        )


def run_all_processes_sequential() -> bool:
    """Run all automations one by one: SERP -> Prod Reg Status -> SMRO.

    SERP tetap harus jalan duluan supaya cleanup SAP Excel tidak mengganggu
    download SMRO. Urutan Prod Reg Status -> SMRO mengikuti urutan tombol di UI
    (kiri->kanan: All Product ID -> Prod Reg Status -> Search Client Odr).

    FIX V11: Respect toggle SMRO_AUTOMATION_ENABLED / SERP_AUTOMATION_ENABLED /
    PUR_INFO_REG_AUTOMATION_ENABLED. Kalau disabled, skip proses tersebut.
    """
    # FIX V11: baca toggle dari env vars (set oleh GUI sebelum spawn worker)
    serp_enabled = os.getenv('SERP_AUTOMATION_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')
    smro_enabled = os.getenv('SMRO_AUTOMATION_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')
    pur_enabled = os.getenv('PUR_INFO_REG_AUTOMATION_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')

    order = []
    # FIX: urutan eksekusi Run All disamakan dengan urutan tombol di UI
    # (kiri->kanan: All Product ID -> Prod Reg Status -> Search Client Odr).
    # PUR tidak menyentuh SAP GUI/file .xls sama sekali (murni Selenium web),
    # jadi aman ditaruh sebelum SMRO tanpa mengganggu alasan urutan lama
    # (SERP harus duluan supaya cleanup SAP Excel tidak mengganggu download SMRO).
    if serp_enabled: order.append(("serp", "All Product ID / SERP"))
    # FIX V28 (permintaan user: "Pur Info Reg dan Process Pur Info Reg (di
    # setting) seharusnya semua diganti jadi Prod Reg Status"): label stage
    # ini diganti "Prod Reg Status". Label muncul di log "Mulai proses:" /
    # "Selesai sukses:" / "Selesai gagal:" / popup ringkasan Run All, dan
    # HARUS tetap sinkron dengan _run_all_stage_label_map di app.py. Arg
    # --process, env var PUR_INFO_REG_*, dan nama log file TIDAK diubah
    # (tetap "pur-info-reg" / PUR_INFO_REG_* / pur_info_reg_automation.log)
    # supaya kompatibel mundur.
    if pur_enabled: order.append(("pur-info-reg", "Prod Reg Status"))
    if smro_enabled: order.append(("smro", "Search Client Odr"))

    if not order:
        logging.warning("RUN ALL: Tidak ada proses yang enabled. Skip semua.")
        return True
    env = os.environ.copy()
    env["SMRO_SHOW_POPUP"] = "false"
    ok_all = True
    logging.info("=" * 70)
    logging.info("RUN ALL SEQUENTIAL DIMULAI: SERP -> PROD REG STATUS -> SMRO")
    logging.info("=" * 70)
    if not check_vpn_connection():
        logging.error("RUN ALL dibatalkan: VPN tidak terhubung atau DNS portal tidak resolve.")
        logging.info("=" * 70)
        logging.info("RUN ALL SEQUENTIAL SELESAI DENGAN ERROR")
        logging.info("=" * 70)
        return False
    step_results = []  # FIX: dipakai untuk popup ringkasan di akhir Run All
    for process_name, label in order:
        logging.info(f"Mulai proses: {label}")
        # FIX: sebelumnya saat frozen, tiap step (SERP/SMRO/PUR) di-import dan
        # dipanggil IN-PROCESS di proses Python yang sama. Masalahnya: SERP
        # pakai otomasi SAP GUI lewat COM/pywinauto yang rawan HANG (bukan
        # exception) atau crash fatal kalau SAP GUI client belum siap saat
        # baru boot+VPN connect — try/except Python tidak bisa menangkap hang,
        # dan crash fatal bisa mematikan seluruh proses induk ini, sehingga
        # step berikutnya (SMRO, PUR) tidak pernah kebagian giliran.
        #
        # Sekarang setiap step SELALU dijalankan sebagai subprocess terpisah
        # dengan timeout (RUN_ALL_STEP_TIMEOUT_SECONDS). Kalau satu step hang,
        # subprocess-nya dipaksa mati setelah timeout dan Run All tetap lanjut
        # ke step berikutnya. Kalau satu step crash fatal, itu cuma mematikan
        # subprocess-nya, bukan proses Run All ini.
        #
        # FIX V23 (laporan user: "process all prod id selesai tapi tidak lanjut
        # proses berikutnya" + "excel hasil download all prod id masih
        # terbuka"): dua kelemahan lama ditutup —
        #   (a) subprocess.run(timeout=...) hanya membunuh ANAK LANGSUNG di
        #       Windows; cucu (chrome.exe, chromedriver, SAP GUI, EXCEL.EXE)
        #       jadi yatim, Excel hasil download tetap menggantung terbuka dan
        #       bisa mengganggu step berikutnya. Sekarang: kill PROSES POHON
        #       penuh (taskkill /T /F).
        #   (b) step yang hang TANPA log (COM/UIA/SAP block) tetap ditunggu
        #       sampai 1 jam penuh; step yang menunggu VPN dalam loop INFINITE
        #       (log bertambah tiap 30s — watchdog GUI tidak menyadarinya)
        #       bahkan menggantung sampai hard timeout. Sekarang: stall
        #       watchdog per-step (log diam SMRO_RUN_ALL_STEP_STALL_SECONDS,
        #       default 900s) + VPN wait dibatasi max_wait (lihat
        #       _wait_vpn_reconnect) + cleanup Excel/SAP setelah step SERP.
        script = str(Path(sys.executable if _is_frozen() else __file__).resolve())
        cmd = [sys.executable] if _is_frozen() else [sys.executable, script]
        cmd += ["--process", process_name]
        step_timeout = CONFIG['RUN_ALL_STEP_TIMEOUT_SECONDS']
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(Path(__file__).resolve().parent),
                env=env,
            )
            step_log_path = _safe_log_path(
                _STEP_LOG_NAMES.get(process_name, "smro_automation.log"))
            logging.info(
                f"  Menunggu step selesai (hard timeout {step_timeout}s, "
                f"stall watchdog {_RUN_ALL_STEP_STALL_SECONDS()}s)..."
            )
            result_code, stop_reason = _supervised_step_wait(
                proc, step_log_path, step_timeout, _RUN_ALL_STEP_STALL_SECONDS())
            if stop_reason == "timeout":
                ok_all = False
                step_results.append((label, False))
                logging.error(
                    f"Selesai gagal: {label} (timeout {step_timeout}s tercapai — "
                    f"proses + seluruh anaknya [Chrome/SAP/Excel] dipaksa mati "
                    f"supaya step berikutnya tetap jalan)"
                )
            elif stop_reason == "stall":
                ok_all = False
                step_results.append((label, False))
                logging.error(
                    f"Selesai gagal: {label} (macet total: tidak ada aktivitas "
                    f"log selama {_RUN_ALL_STEP_STALL_SECONDS()}s — kemungkinan "
                    f"hang SAP GUI/Windows; proses dipaksa mati supaya step "
                    f"berikutnya tetap jalan)"
                )
            elif result_code == 0:
                # FIX V13: bahkan kalau subprocess exit rc=0, cek log file untuk
                # marker kegagalan eksplisit (mis. 'session not created: Chrome
                # failed to start: crashed.', 'PARALLEL AUTOMATION GAGAL TOTAL',
                # '✗ Unexpected error:'). Kadang-kadang subprocess lapor rc=0
                # walau automation jelas gagal (mis. setup_driver crash ditangkap
                # oleh except-handler yang tidak re-raise -> return False, tapi
                # ada code path yang akhirnya exit 0 walau ada error). Defense-
                # in-depth: kalau marker kegagalan terdeteksi di log file, treat
                # sebagai gagal meskipun rc=0.
                # Cek log file untuk marker kegagalan yang ditulis subprocess.
                log_path = _safe_log_path("smro_automation.log")
                step_failed_marker = False
                try:
                    if log_path.exists():
                        # Baca 50 KB terakhir supaya cepat (cukup untuk lihat
                        # error dari step yang baru saja selesai).
                        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
                            try:
                                f.seek(0, 2)  # end
                                size = f.tell()
                                f.seek(max(0, size - 51200))
                                tail = f.read()
                            except Exception:
                                f.seek(0)
                                tail = f.read()
                        # Hanya cek marker yang muncul SETELAH "Mulai proses: <label>"
                        # step ini dimulai — bukan marker dari step sebelumnya.
                        # Pakai simple heuristic: cari posisi "Mulai proses: <label>"
                        # di tail, kalau ada, slice dari situ ke depan.
                        start_marker = f"Mulai proses: {label}"
                        idx = tail.rfind(start_marker)
                        segment = tail[idx:] if idx >= 0 else tail
                        failure_markers = (
                            "session not created: Chrome failed to start",
                            "PARALLEL AUTOMATION GAGAL TOTAL",
                            "Sequential automation selesai dengan error",
                            "RuntimeError: Login gagal",
                            "RuntimeError: Login gagal saat reconnect",
                            "✗ Unexpected error:",
                            "Process gagal:",
                        )
                        for marker in failure_markers:
                            if marker in segment:
                                step_failed_marker = True
                                logging.error(
                                    f"Step '{label}' exit rc=0 tapi log mendeteksi marker "
                                    f"kegagalan: '{marker}'. Tetap dihitung sebagai gagal."
                                )
                                break
                except Exception as log_err:
                    logging.debug(f"Cek marker kegagalan di log dilewati: {log_err}")
                if step_failed_marker:
                    ok_all = False
                    step_results.append((label, False))
                    logging.error(f"Selesai gagal: {label} (log marker mendeteksi kegagalan walau exit rc=0)")
                else:
                    logging.info(f"Selesai sukses: {label}")
                    step_results.append((label, True))
                    continue
            else:
                ok_all = False
                step_results.append((label, False))
                logging.error(f"Selesai gagal: {label} (exit code {result_code})")
        except Exception as e:
            ok_all = False
            step_results.append((label, False))
            logging.error(f"Selesai gagal: {label} (error: {e})")
        finally:
            # FIX V23: setelah step SERP (All Product ID), SELALU bersihkan
            # Excel + SAP GUI yang tertinggal — baik step sukses (cleanup
            # internal sudah jalan; ini redundan tapi cepat & aman) maupun
            # gagal/timeout/stall (SAP/Excel bisa menggantung terbuka, itulah
            # akar keluhan "excel hasil download all prod id masih terbuka").
            # Step berikutnya (PUR/SMRO) tidak butuh Excel/SAP sama sekali.
            if process_name == "serp":
                _run_all_cleanup_sap_excel()
    logging.info("=" * 70)
    logging.info("RUN ALL SEQUENTIAL SELESAI" if ok_all else "RUN ALL SEQUENTIAL SELESAI DENGAN ERROR")
    logging.info("=" * 70)

    # FIX: sebelumnya notifikasi popup tiap step (SERP/PUR/SMRO) sengaja
    # di-disable selama Run All (SMRO_SHOW_POPUP=false di `env` yang dipakai
    # untuk subprocess step) supaya tidak muncul 3 popup beruntun mengganggu.
    # Tapi akibatnya TIDAK ADA popup sama sekali di akhir Run All — user
    # merasa notifikasi "proses selesai" hilang. Sekarang ditambah SATU popup
    # ringkasan di akhir, dan popup ini pakai SMRO_SHOW_POPUP dari environ
    # proses Run All ITU SENDIRI (bukan `env` yang di-override utk child) —
    # jadi tetap respect toggle "Show popup" di GUI untuk klik manual Run All,
    # dan tetap otomatis disuppress saat autostart/watch mode (karena
    # wait_for_vpn_and_run_all() sudah set default SMRO_SHOW_POPUP=false
    # sebelum manggil fungsi ini).
    summary_enabled = os.getenv('SMRO_SHOW_POPUP', 'true').lower() in ('1', 'true', 'yes')
    detail_lines = "\n".join(
        f"{'✅' if ok else '❌'} {lbl}" for lbl, ok in step_results
    )
    summary_message = (
        f"Run All {'Selesai!' if ok_all else 'Selesai dengan error!'}\n\n"
        f"{detail_lines}\n\n"
        f"⏰ Waktu: {datetime.now().strftime('%H:%M:%S')}"
    )
    show_popup("Run All Selesai" if ok_all else "Run All Error", summary_message,
                success=ok_all, enabled_override=summary_enabled)

    return ok_all


def wait_for_vpn_and_run_all():
    """
    Watch loop untuk --process all --wait-vpn:
    - Begitu VPN pertama kali terhubung (mis. baru nyalakan laptop & connect VPN),
      otomatis jalankan Run All (SERP -> Prod Reg Status -> SMRO) sekali.
    - Setelah itu, jadwal harian (SMRO_AFTERNOON_RUN_TIME) tetap dipakai untuk
      auto-rerun Run All setiap hari, sama seperti behaviour SMRO watch mode.
    - Dipakai untuk autorun saat Windows startup / VPN connect pertama kali.
    """
    os.environ.setdefault('SMRO_SHOW_POPUP', 'false')
    run_times = parse_run_times(CONFIG['AFTERNOON_RUN_TIME'])
    run_time_labels = [t.strftime("%H:%M") for t in run_times]

    logging.info("\n" + "=" * 70)
    logging.info("RUN ALL — VPN WATCH MODE AKTIF")
    logging.info(f"Cek VPN tiap {CONFIG['VPN_CHECK_SECONDS']} detik")
    logging.info(f"Retry Run All tiap {CONFIG['RETRY_SECONDS']} detik kalau gagal")
    logging.info(f"Run All terjadwal: {', '.join(run_time_labels)}")
    logging.info("Run All pertama otomatis dijalankan begitu VPN pertama kali terhubung.")
    logging.info("Press Ctrl+C untuk berhenti")
    logging.info("=" * 70 + "\n")

    vpn_connected = False
    first_run_date = None
    schedule_done = {}
    pending_run = None
    pending_schedule_label = None
    attempt = 0

    while True:
        now = datetime.now()
        today = now.date()
        connected = check_vpn_connection()

        if not connected:
            if vpn_connected:
                logging.info("VPN disconnect. Run All standby sampai VPN connect lagi.")
            vpn_connected = False
            pending_run = None
            pending_schedule_label = None
            attempt = 0
            time.sleep(CONFIG['VPN_CHECK_SECONDS'])
            continue

        if not vpn_connected:
            logging.info("VPN terdeteksi connect.")
            vpn_connected = True
            if first_run_date != today:
                pending_run = "initial"

        if pending_run is None:
            done_today = schedule_done.setdefault(today, set())
            due_times = [t for t in run_times if now.time() >= t and t.strftime("%H:%M") not in done_today]
            if due_times:
                pending_run = "schedule"
                pending_schedule_label = due_times[0].strftime("%H:%M")

        if pending_run is None:
            time.sleep(CONFIG['VPN_CHECK_SECONDS'])
            continue

        attempt += 1
        label = "Run All awal setelah VPN connect" if pending_run == "initial" else f"Run All schedule {pending_schedule_label}"
        logging.info(f"Percobaan {label} #{attempt}")

        # FIX V38: service VPN Watch boleh tetap hidup, tetapi Run All aktual
        # hanya dimulai kalau tidak ada automation lain. Ini mencegah duplicate
        # run dengan Task Scheduler / scheduler internal pada jam yang sama.
        try:
            import run_presence as _rp
            if _rp.is_held():
                logging.info("Automation lain sedang berjalan. VPN Watch Run All menunggu giliran.")
                time.sleep(CONFIG['VPN_CHECK_SECONDS'])
                continue
            _rp.acquire()
        except Exception:
            _rp = None
        try:
            _run_ok = run_all_processes_sequential()
        finally:
            if _rp:
                _rp.release()
        if _run_ok:
            completed = pending_run
            pending_run = None
            attempt = 0
            if completed == "initial":
                first_run_date = today
                done_today = schedule_done.setdefault(today, set())
                for t in run_times:
                    if datetime.now().time() >= t:
                        done_today.add(t.strftime("%H:%M"))
                if done_today:
                    logging.info("Run All awal berhasil setelah jadwal lewat, jadwal yang sudah lewat hari ini dianggap selesai.")
                else:
                    logging.info("Run All awal berhasil. Menunggu jadwal Run All harian berikutnya.")
            else:
                if pending_schedule_label:
                    schedule_done.setdefault(today, set()).add(pending_schedule_label)
                logging.info("Run All schedule berhasil. Menunggu jadwal berikutnya.")
            pending_schedule_label = None
            time.sleep(CONFIG['VPN_CHECK_SECONDS'])
        else:
            logging.warning("Run All gagal.")
            if not check_vpn_connection():
                logging.warning("VPN tidak accessible setelah gagal. Menunggu VPN connect lagi.")
                vpn_connected = False
                while not check_vpn_connection():
                    time.sleep(CONFIG['VPN_CHECK_SECONDS'])
                vpn_connected = True
                logging.info("VPN connect lagi. Mengulang Run All.")
                time.sleep(CONFIG['VPN_CHECK_SECONDS'])
            else:
                logging.warning(f"Retry Run All dalam {CONFIG['RETRY_SECONDS']} detik.")
                time.sleep(CONFIG['RETRY_SECONDS'])

if __name__ == "__main__":
    args = sys.argv[1:]
    process_name = _arg_value(args, "--process", "smro")
    watch_mode = any(a in args for a in ("--wait-vpn", "--watch", "--schedule"))
    try:
        ok = run_selected_process(process_name, watch=watch_mode)
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        logging.info("\n\nWatcher dihentikan oleh user")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Process gagal: {e}", exc_info=True)
        sys.exit(1)
