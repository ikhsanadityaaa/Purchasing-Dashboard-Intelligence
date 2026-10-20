"""
net_tls.py — POST multipart ke dashboard yang tahan terhadap jaringan korporat
dengan TLS-inspection proxy (certificate re-signing / MITM).

FIX V25 — bug: "Upload gagal (attempt 1/5)" berulang 5x lalu popup
"Output Gagal: Upload/simpan gagal setelah retry", padahal endpoint
DASHBOARD_API tidak berubah sama sekali dan Chrome masih bisa buka dashboard.

ROOT CAUSE (sama dengan bug ChromeDriver di V24, beda victim):
  requests memverifikasi HTTPS hanya dengan bundle CA milik 'certifi'.
  Di jaringan kantor yang memasang TLS-inspection proxy, SEMUA HTTPS ke host
  eksternal (local-only dashboard) ditandatangani ulang memakai CA
  korporat. CA korporat itu ADA di Windows certificate store (itulah sebabnya
  Chrome/Edge masih bisa membuka dashboard), tetapi TIDAK ada di bundle
  certifi milik Python. Akibatnya:
      SSLError -> ConnectionError: certificate verify failed:
      self-signed certificate in certificate chain
  yang menggagalkan upload dalam ~1 detik per attempt, lalu retry backoff
  15/20/30/60/60 detik (pola timing di log user cocok persis) dan akhirnya
  semua 5 attempt gagal. Host internal (portal via VPN) TIDAK diintercept,
  makanya langkah VPN-check dan login portal tetap sukses.

SOLUSI — eskalasi mode per POST (hasil sukses di-cache utk sisa proses):
  1. "default"  : requests.post biasa (certifi) — jaringan normal/rumah.
  2. "winca"    : Session dengan verify = PEM gabungan (Windows certificate
                  store ROOT+CA + certifi) — trust anchor SAMA dengan yang
                  dipakai Chrome, jadi lolos proxy korporat secara AMAN.
  3. "insecure" : Session verify=False (paling akhir, WARNING sekali) — untuk
                  kasus ekstrem CA korporat bahkan tidak ada di Windows store.
                  Koneksi tetap terenkripsi, hanya verifikasi identitas server
                  yang dilewati.

Error koneksi yang BUKAN masalah sertifikat (DNS, connection refused,
connection reset, timeout) TIDAK dieskalasi — di-raise ke caller supaya
retry/backoff lama di upload_to_dashboard() tetap bekerja seperti biasa.

Catatan log: semua baris log dari modul ini sengaja TIDAK memakai kata
kunci yang dipetakan GUI parser app.py ("upload", "gagal", "error",
"failed", dst) supaya tidak mengganggu tampilan status di Activity Log.
"""
import os
import ssl
import sys
import logging
import threading
import warnings
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# ── State modul (dipakai lintas panggilan upload dalam satu proses) ──────────
_LOCK = threading.Lock()
_STATE = {
    "mode": "default",          # default | winca | insecure
    "winca_pem": None,          # Path ke PEM gabungan (Windows store + certifi)
    "insecure_announced": False # supaya WARNING verify-off hanya muncul 1x
}
_MODE_ORDER = ("default", "winca", "insecure")

# Penanda bahwa exception disebabkan kegagalan VERIFIKASI SERTIFIKAT
# (bukan masalah jaringan lain). Dicek terhadap seluruh rantai penyebab.
_CERT_FAIL_MARKERS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "certificateverificationerror",
    "sslcertverificationerror",
    "self-signed",
    "self signed",
    "unable to get local issuer certificate",
    "unknown ca",
    "certificate chain",
    "depth lookup",
    "invalid certificate",
    "ca certificate",
)


def _is_cert_verify_failure(exc: BaseException) -> bool:
    """True kalau exception (atau salah satu cause-nya) adalah kegagalan
    verifikasi sertifikat TLS. Walk maksimal 8 level supaya rantai
    requests -> urllib3 -> ssl tetap tercakup tanpa risiko loop."""
    cur = exc
    for _ in range(8):
        if cur is None:
            return False
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        text = str(cur).lower()
        if any(marker in text for marker in _CERT_FAIL_MARKERS):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


def _rewind_files(files) -> None:
    """Seek(0) semua file-like object di `files` sebelum mencoba ulang POST.

    WAJIB: saat attempt pertama setengah jalan membaca body multipart lalu
    koneksi putus di tengah, posisi file sudah di EOF — kalau tidak
    di-rewind, attempt berikutnya mengirim file KOSONG (0 byte) dan server
    menolaknya dengan error yang membingungkan.
    """
    if not files:
        return
    values = files.values() if isinstance(files, dict) else files
    for v in values:
        obj = v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else v
        try:
            if hasattr(obj, "seek"):
                obj.seek(0)
        except Exception:
            pass


def _winca_target_path() -> Path:
    """Lokasi file PEM gabungan — di data dir writable (_paths._data_dir),
    bukan di sebelah .exe, supaya aman saat dipasang di Program Files."""
    try:
        from _paths import _data_dir
        base = _data_dir()
    except Exception:
        base = Path.home()
    return base / "win_ca_bundle.pem"


def _export_windows_ca_bundle():
    """Export Windows certificate store (ROOT + CA) ke satu file PEM,
    lalu gabungkan dengan bundle certifi supaya mode 'winca' adalah
    SUPERSET dari trust anchor lama (tidak mungkin lebih ketat dari
    mode default). Return Path PEM, atau None kalau gagal/non-Windows.

    Ditulis secara atomik (file .tmp + os.replace) supaya proses paralel
    W1/W2 tidak pernah membaca file setengah jadi.
    """
    if not sys.platform.startswith("win") and os.name != "nt":
        return None
    try:
        pem_parts = []
        n_win = 0
        for store_name in ("ROOT", "CA"):
            try:
                # enum_certificates hanya ada di Windows; item = (encoding, DER, flag)
                for _enc, der, _flag in ssl.enum_certificates(store_name):
                    if der:
                        pem_parts.append(ssl.DER_cert_to_PEM_cert(der))
                        n_win += 1
            except Exception:
                continue
        if n_win == 0:
            logger.info("TLS dashboard: Windows certificate store kosong/tidak terbaca")
            return None
        # Gabungkan certifi supaya root publik yang tidak ada di Windows store
        # (jarang, tapi mungkin pada Windows lama) tetap terpercaya.
        try:
            import certifi
            certifi_text = Path(certifi.where()).read_text(encoding="utf-8", errors="ignore")
            if "BEGIN CERTIFICATE" in certifi_text:
                pem_parts.append(certifi_text)
        except Exception:
            pass
        target = _winca_target_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text("".join(pem_parts), encoding="ascii", errors="ignore")
        os.replace(tmp, target)
        logger.info(
            f"TLS dashboard: bundle CA siap ({n_win} sertifikat Windows store + certifi) "
            f"-> {target.name}"
        )
        return target
    except Exception as e:
        logger.info(f"TLS dashboard: pembuatan bundle CA batal: {e}")
        return None


def _prepare_winca() -> bool:
    """Pastikan PEM winca tersedia (export sekali per proses, thread-safe).
    Return True kalau mode winca bisa dipakai."""
    with _LOCK:
        if _STATE["winca_pem"] is not None:
            return True
        pem = _export_windows_ca_bundle()
        if pem is None:
            return False
        _STATE["winca_pem"] = pem
        return True


def _suppress_insecure_warning() -> None:
    """Redam warning 'Unverified HTTPS request' — hanya dinyalakan saat mode
    insecure benar-benar dipakai, cukup sekali per proses."""
    with _LOCK:
        if _STATE["insecure_announced"]:
            return
        _STATE["insecure_announced"] = True
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
    try:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    except Exception:
        pass


def _post_with_mode(mode, url, *, files, data, timeout, headers):
    """Jalankan POST dengan mode tertentu. Response HTTP apapun (200/4xx/5xx)
    DIKEMBALIKAN tanpa raise — penanganan status code tetap milik caller."""
    if mode == "default":
        # Path identik dengan kode lama — requests.post top-level membaca env
        # REQUESTS_CA_BUNDLE / REQUESTS_VERIFY bila user set manual.
        return requests.post(url, files=files, data=data, timeout=timeout, headers=headers)
    sess = requests.Session()
    try:
        if mode == "winca":
            sess.verify = str(_STATE["winca_pem"])
        else:  # insecure
            sess.verify = False
            _suppress_insecure_warning()
        return sess.post(url, files=files, data=data, timeout=timeout, headers=headers)
    finally:
        sess.close()


def _remember_mode(mode) -> None:
    """Kunci mode yang terbukti berhasil dipakai untuk sisa proses,
    supaya upload/percobaan berikutnya langsung jalan tanpa probing ulang."""
    with _LOCK:
        if _STATE["mode"] != mode:
            _STATE["mode"] = mode


def tls_resilient_post(url, *, files=None, data=None, timeout=300, headers=None):
    """Drop-in pengganti requests.post() untuk upload multipart ke dashboard.

    Behavior:
    - Mengembalikan requests.Response apa pun status HTTP-nya (logika
      retry/backoff/status-code di caller TIDAK berubah).
    - Kalau terjadi kegagalan VERIFIKASI SERTIFIKAT, otomatis naik tangga
      mode: default -> winca -> insecure (file di-rewind dulu), dan mode
      yang berhasil diingat untuk panggilan berikutnya.
    - Ke gagalan koneksi non-sertifikat (DNS/reset/refused/timeout) tetap
      di-raise apa adanya — retry/backoff lama yang menangani.
    """
    # Desktop safeguard: database uploads may only target this PC.
    from urllib.parse import urlsplit
    parsed = urlsplit(str(url))
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Desktop blocks uploads to external servers')

    idx = _MODE_ORDER.index(_STATE["mode"])
    while True:
        mode = _MODE_ORDER[idx]
        if mode == "winca" and not _prepare_winca():
            # Windows store tidak bisa di-export (non-Windows / store kosong)
            # -> mode dilewati, langsung ke mode berikutnya.
            logger.info("TLS dashboard: bundle CA Windows tidak tersedia, mode dilewati")
            if idx + 1 >= len(_MODE_ORDER):
                raise requests.exceptions.ConnectionError(
                    "tls: winca unavailable"
                )
            idx += 1
            continue
        try:
            resp = _post_with_mode(mode, url, files=files, data=data,
                                   timeout=timeout, headers=headers)
        except Exception as e:
            cert_fail = _is_cert_verify_failure(e)
            if not cert_fail or idx + 1 >= len(_MODE_ORDER):
                # Bukan masalah sertifikat, ATAU tangga sudah habis:
                # kembalikan ke penanganan retry/backoff caller (perilaku lama).
                raise
            _rewind_files(files)
            nxt = _MODE_ORDER[idx + 1]
            logger.info(
                "TLS dashboard: verifikasi sertifikat tidak lolos di mode "
                f"'{mode}' (jaringan dengan TLS inspection?), lanjut ke mode '{nxt}'"
            )
            idx += 1
            continue
        # Koneksi sukses — kunci mode ini untuk sisa proses.
        if _STATE["mode"] != mode:
            _remember_mode(mode)
            if mode == "winca":
                logger.info(
                    "TLS dashboard: mode Windows-certificate-store aktif "
                    "(trust anchor sama dengan Chrome; dipakai untuk sisa proses)"
                )
            elif mode == "insecure":
                logger.warning(
                    "TLS dashboard: verifikasi identitas server dilewati (mode terakhir). "
                    "Koneksi tetap terenkripsi, tetapi server tidak dicek — "
                    "laporkan ke IT bila ini terus terjadi agar CA korporat "
                    "bisa ditambahkan. Mode dipakai untuk sisa proses."
                )
        return resp


def current_mode() -> str:
    """Mode TLS yang sedang aktif (diagnostik / testing)."""
    return _STATE["mode"]


def _reset_for_tests() -> None:
    """Kembalikan state modul ke kondisi awal — HANYA untuk validation script."""
    with _LOCK:
        _STATE["mode"] = "default"
        _STATE["winca_pem"] = None
        _STATE["insecure_announced"] = False
