"""FIX V36: penanda kehadiran lintas-proses "ada automation yang sedang
berjalan" — memakai Windows global mutex (Global\\ServeOneAutomationRun).

Latar belakang (keluhan user): log GUI menampilkan "Jadwal internal 08:00
(Prod Reg Status): DILEWATI karena masih ada proses lain yang berjalan",
TAPI proses Prod Reg Status tetap berjalan. Penyebab: worker jadwal dari
Windows Task Scheduler (`app.py --process <proc> --scheduled`) hanya
dedup terhadap worker jadwal LAIN (lock file jadwal) — ia tidak mendeteksi
run dari jalur lain (Run Once manual, Run All, atau run yang dipicu auto
mode/watcher), sehingga task Windows tetap menjalankan proses meski ada
proses lain aktif.

Kenapa mutex, bukan file lock: objek kernel Windows dirusak otomatis begitu
semua handle tertutup / prosesnya mati — TIDAK ADA lock basi (stale lock)
walau proses crash atau di-kill.

Cara pakai:
- Proses yang MENJALANKAN satu automation run (worker `app.py --process ...`
  tanpa --wait-vpn, dan SATU run di dalam loop watcher) memanggil acquire()
  di awal run dan release() di akhir (jangan sampai lupa di finally).
- Worker jadwal (`--scheduled`) memanggil is_held() SEBELUM mulai: kalau
  True berarti ada automation lain yang sedang berjalan -> run jadwal
  DILEWATI (log + exit 0).

Catatan: di non-Windows (mode dev Linux) semua fungsi no-op dan is_held()
selalu False — tidak memblokir apa pun.
"""

import os
import ctypes

_MUTEX_NAME = "Global\\ServeOneAutomationRun"
_handle = None  # handle mutex milik proses ini (dibiarkan terbuka selama run)


def is_held() -> bool:
    """True kalau ADA proses lain yang sedang menjalankan automation
    (handle mutex sedang terbuka di proses lain)."""
    if os.name != "nt":
        return False
    kernel32 = ctypes.windll.kernel32
    h = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not h:
        return False  # gagal buka -> anggap kosong (fail-open, jangan blokir)
    exists = (kernel32.GetLastError() == 183)  # 183 = ERROR_ALREADY_EXISTS
    kernel32.CloseHandle(h)
    return exists


def acquire() -> bool:
    """Tandai "automation sedang berjalan" untuk proses ini: buka handle
    mutex dan biarkan terbuka sampai release() dipanggil (atau proses mati).
    Selalu fail-open: kegagalan mutex tidak boleh menggagalkan automation."""
    global _handle
    if os.name != "nt":
        return True
    if _handle:
        return True
    kernel32 = ctypes.windll.kernel32
    h = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not h:
        return True  # fail-open
    # Bisa jadi GetLastError()==183 (sudah ada pemegang lain) — tidak masalah:
    # ini presence flag, bukan exclusi. Handle tetap disimpan sampai release.
    _handle = h
    return True


def release():
    """Lepas penanda run ini. Aman dipanggil berkali-kali / tanpa acquire.

    FIX V39: ``ctypes`` harus tersedia di scope fungsi ini. Pada V38, ctypes
    hanya di-import lokal di acquire()/is_held(), sehingga release() terkena
    NameError yang tertelan oleh except. Akibatnya handle mutex Windows tidak
    pernah benar-benar ditutup dan scheduler mengira automation masih running
    sampai proses watcher exit.
    """
    global _handle
    h = _handle
    _handle = None
    if not h or os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass
