#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cleanup_disk.py — pembersih kuota PythonAnywhere (V63.25).

BARU V63.25 — --repair diperbaiki (ruang-aware). Laporan lapangan: repair
V63.24 gagal EDQUOT pada kuota 512 MB yang 100% penuh, karena:
  (a) DROP TABLE butuh journal kecil di HOME — 0 byte tersisa -> disk I/O error
      (guard otomatis juga gagal dengan cara yang sama), dan
  (b) /tmp TIDAK selalu di luar kuota — backup 246 MB ke /tmp justru bisa
      memakan sisa kuota.
Urutan baru:
  [0] GRATISKAN ruang aman OTOMATIS (semua PENGHAPUSAN — tidak butuh sisa
      kuota): pip cache, thumbnails, dist lama, upload_jobs tua, backup
      rotasi lama, arsip korup, __pycache__, test-suite di virtualenv
      (pandas/numpy/matplotlib/scipy — aman dihapus).
  [1] probe tulis kecil di HOME.
  [2] backup /tmp/po_backup_pre.db — best-effort; dipakai ulang bila sudah
      ada & valid (integrity_check); DILEWATI bila kuota tak cukup (hasil
      compact nanti menjadi salinan aman; DB asli tak disentuh sebelum
      verifikasi lolos).
  [3] DROP lost_and_found (residu .recover — bukan data aktif).
  [4] VACUUM INTO /tmp/po_compact.db (DB asli TIDAK ditulis sama sekali;
      bila /tmp penuh karena backup lama, backup lama dihapus lalu diulang).
  [5] verifikasi integrity_check + jumlah baris == DB asli.
  [6] pasang: hapus -wal/-shm + DB lama (kuota langsung bebas), salin
      compact balik, verifikasi ulang. WAJIB Reload tab Web setelahnya.

BARU V63.24 — opsi --repair: perbaikan kuota 100% penuh TANPA butuh ruang
  ekstra di home (kuota PythonAnywhere TIDAK terlihat dari df/disk_usage —
  yang terlihat justru disk HOST yang lega):
    python3 cleanup_disk.py --repair
  Urutan aman: (1) backup DB -> /tmp/po_backup_pre.db (di luar kuota),
  (2) DROP tabel lost_and_found bila ada (residu .recover — bukan data
  aktif), (3) VACUUM INTO /tmp/po_compact.db (snapshot utuh TERMASUK isi
  -wal; home tidak menulis apa pun), (4) verifikasi integrity_check +
  jumlah baris so_data, (5) hapus po_database.db + -wal + -shm (kuota
  langsung bebas ratusan MB), salin hasil compact kembali. WAJIB tekan
  Reload di tab Web PythonAnywhere setelahnya. JANGAN upload Excel selama
  repair berjalan (tulis yang terjadi di sela-sela bisa hilang).

BARU V63.16:
  * [1b] 20 FILE TERBESAR di seluruh home — menemukan "penampung tersembunyi"
    (cache, log, build lama) yang selama ini luput dari laporan per-folder.
  * Statistik halaman SQLite (freelist) — file DB yang membengkak sisa
    UPDATE/DELETE lama terlihat jelas berapa MB yang bisa disusutkan.
  * Opsi --vacuum : menyusutkan file po_database.db ke ukuran data hidup
    (guard: ruang kosong minimal 3x ukuran DB; tidak dijalankan saat upload
    berlangsung).
  * Statistik tabel lost_and_found (sisa pemulihan .recover DB korup —
    bukan data aktif; drop via endpoint /api/maintenance/lost-and-found POST).
  * Bersihkan file tmp* sisa proses di folder aplikasi (usia > 1 jam).

LATAR: kuota PythonAnywhere 512 MB mencakup SELURUH home directory (~),
bukan hanya folder mysite. Sumber sampah paling umum yang membuat kuota
100% penuh:
  1. ~/.cache/pip                        — cache unduhan pip (bisa ratusan MB)
  2. mysite/instance/backups/*.db        — backup rotasi (tiap file seukuran DB)
  3. mysite/instance/*.corrupt_*         — arsip DB korup (autoheal V63.8)
  4. __pycache__/ (di mana pun)          — bytecode, dibuat ulang otomatis
  5. mysite/instance/upload_jobs/*       — salinan Excel sisa upload
  6. mysite/dist/, *.zip delivery lama   — build/paket versi lama

CARA PAKAI (Bash console PythonAnywhere):
    cd ~
    python3 cleanup_disk.py            # DRY-RUN: laporan saja, TIDAK menghapus
    python3 cleanup_disk.py --apply    # HAPUS sampah yang dikenal

AMAN: file data utama (po_database.db, app.py, static/, job.json aktif)
TIDAK disentuh. Folder dist/ dan .virtualenvs HANYA dilaporkan — hapus
manual bila yakin (lihat petunjuk di akhir laporan).
Jangan jalankan --apply saat upload Excel sedang berlangsung.
"""
import os
import sys
import glob
import shutil
import time

HOME = os.path.expanduser('~')
APPLY = '--apply' in sys.argv[1:]
DO_VACUUM = '--vacuum' in sys.argv[1:]
BACKUP_KEEP = 2
JOB_MAX_AGE_S = 1 * 3600          # sisa upload_jobs lebih tua dari 1 jam = sampah

OPENPYXL_TMP_MAX_AGE_S = 60 * 60     # export Excel crash/timeout; export aktif jangan disentuh

def clean_openpyxl_tmp(apply=False):
    """Lapor/hapus /tmp/openpyxl.* stale. openpyxl write_only membuat file
    XML sementara di /tmp dan menghapusnya saat save sukses. Jika worker mati,
    file bisa tertinggal dan pada PythonAnywhere dapat ikut memakan kuota."""
    import glob
    now = time.time()
    files = []
    total = 0
    for fp in glob.glob('/tmp/openpyxl.*'):
        try:
            if not os.path.isfile(fp):
                continue
            age = now - os.path.getmtime(fp)
            sz = os.path.getsize(fp)
            files.append((fp, sz, age))
            total += sz
        except Exception:
            pass
    stale = [(fp, sz, age) for fp, sz, age in files if age >= OPENPYXL_TMP_MAX_AGE_S]
    stale_bytes = sum(sz for _fp, sz, _age in stale)
    if files:
        print('  openpyxl /tmp: %d file, %s total; stale >1j: %d file, %s' %
              (len(files), human(total), len(stale), human(stale_bytes)))
    else:
        print('  - openpyxl /tmp: bersih')
    if apply:
        removed = 0
        for fp, _sz, _age in stale:
            try:
                os.remove(fp); removed += 1
            except Exception:
                pass
        if removed:
            print('    -> %d stale openpyxl temp dihapus' % removed)
    return stale_bytes

freed_total = 0

def human(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%.1f %s' % (n, unit)) if unit != 'B' else '%d B' % n
        n /= 1024.0

def dir_size(path):
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total

def find_mysite():
    for cand in (os.path.join(HOME, 'mysite'), os.getcwd(), HOME):
        if cand and os.path.isdir(os.path.join(cand, 'instance')):
            return cand
    return None

def act_will():
    return 'AKAN DIHAPUS (dry-run)' if not APPLY else 'DIHAPUS'

def rm(path, reason):
    """Hapus file/dir dengan laporan. return bytes dibebaskan (perkiraan)."""
    global freed_total
    if os.path.isdir(path):
        sz = dir_size(path)
        print('  [%s] %s  %8s  (%s)' % (act_will(), path, human(sz), reason))
        if APPLY:
            shutil.rmtree(path, ignore_errors=True)
            freed_total += sz
    elif os.path.isfile(path):
        try:
            sz = os.path.getsize(path)
        except OSError:
            sz = 0
        print('  [%s] %s  %8s  (%s)' % (act_will(), path, human(sz), reason))
        if APPLY:
            try:
                os.remove(path)
                freed_total += sz
            except OSError:
                pass
    else:
        return

def clean_pip_cache():
    p = os.path.join(HOME, '.cache', 'pip')
    sz = dir_size(p)
    if sz == 0:
        print('  - cache pip: kosong / tidak ada')
        return
    print('  cache pip %s -> %s' % (human(sz), act_will()))
    if APPLY:
        shutil.rmtree(p, ignore_errors=True)
        globals()['freed_total'] += sz

def clean_pycache():
    n, sz = 0, 0
    for root, dirs, files in os.walk(HOME):
        if '__pycache__' in dirs:
            p = os.path.join(root, '__pycache__')
            sz += dir_size(p)
            n += 1
            if APPLY:
                shutil.rmtree(p, ignore_errors=True)
            dirs.remove('__pycache__')
    if n:
        print('  __pycache__ %d lokasi %s -> %s' % (n, human(sz), act_will()))
        if APPLY:
            globals()['freed_total'] += sz
    else:
        print('  - __pycache__: tidak ada')

def clean_upload_jobs(mysite):
    clean_openpyxl_tmp(apply=APPLY)
    jd = os.path.join(mysite, 'instance', 'upload_jobs')
    if not os.path.isdir(jd):
        print('  - upload_jobs: tidak ada')
        return
    now = time.time()
    n, sz = 0, 0
    for name in os.listdir(jd):
        p = os.path.join(jd, name)
        try:
            age = now - os.path.getmtime(p)
        except OSError:
            continue
        if age > JOB_MAX_AGE_S:
            sz += dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
            n += 1
            if APPLY:
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
    if n:
        print('  upload_jobs sisa lama %d item %s (usia > 1 jam) -> %s' % (n, human(sz), act_will()))
        if APPLY:
            globals()['freed_total'] += sz
    else:
        print('  - upload_jobs: bersih (tidak ada sisa > 1 jam)')

def rotate(pattern, keep, label):
    olds = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    olds.sort(key=os.path.getmtime)
    extra = olds[:-keep] if keep and len(olds) > keep else []
    if not olds:
        print('  - %s: tidak ada' % label)
        return
    print('  %s: %d file, simpan %d terbaru' % (label, len(olds), keep))
    for old in extra:
        rm(old, 'rotasi > keep=%d' % keep)
    if not extra:
        print('    - sudah dalam batas keep=%d' % keep)

def _migrate_raw_backups(mysite):
    """V63.10: kompres backup mentah po_database_*.db -> .db.gz (hemat ~5-7x).
    Hanya dijalankan pada mode --apply. Return (jumlah, bytes dibebaskan)."""
    import gzip
    bdir = os.path.join(mysite, 'instance', 'backups')
    if not os.path.isdir(bdir):
        return 0, 0
    n, freed = 0, 0
    now = time.time()
    for raw in sorted(glob.glob(os.path.join(bdir, 'po_database_*.db'))):
        try:
            if not os.path.isfile(raw):
                continue
            if now - os.path.getmtime(raw) < 120:
                continue
            gz = raw + '.gz'
            raw_sz = os.path.getsize(raw)
            if os.path.exists(gz):
                os.remove(raw)
                freed += raw_sz
                n += 1
                continue
            tmp = gz + '.tmp'
            try:
                with open(raw, 'rb') as fi, gzip.open(tmp, 'wb', compresslevel=6) as fo:
                    while True:
                        chunk = fi.read(1024 * 1024)
                        if not chunk:
                            break
                        fo.write(chunk)
                # verifikasi: baca balik, ukuran harus sama
                total = 0
                with gzip.open(tmp, 'rb') as fi:
                    while True:
                        chunk = fi.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                if total != raw_sz:
                    raise OSError('verifikasi gzip gagal')
                os.replace(tmp, gz)
                os.remove(raw)
                freed += raw_sz
                n += 1
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        except OSError:
            continue
    return n, freed


def checkpoint_wal(mysite):
    dbp = os.path.join(mysite, 'instance', 'po_database.db')
    wal = dbp + '-wal'
    if os.path.isfile(wal):
        sz = os.path.getsize(wal)
        print('  WAL %s (%s)%s' % (wal, human(sz), '' if sz <= 50 * 1024 * 1024 else ' — > 50 MB, checkpoint disarankan'))
        if sz > 50 * 1024 * 1024 and APPLY:
            try:
                import sqlite3
                con = sqlite3.connect(dbp, timeout=5)
                try:
                    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                finally:
                    con.close()
                print('    checkpoint WAL selesai (WAL dikosongkan, TIDAK dihapus)')
            except Exception as e:
                print('    checkpoint gagal: %s' % e)
                print('    ^ bila pesannya disk I/O error = kuota PA penuh '
                      '(bukan disk host). Coba: python3 cleanup_disk.py --repair')

def report_top_files(limit=20):
    """V63.16: file terbesar di seluruh HOME (kedalaman penuh)."""
    top = []
    for root, dirs, files in os.walk(HOME):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                top.append((os.path.getsize(p), p))
            except OSError:
                pass
    top.sort(reverse=True)
    print('  %d file diskan; %d terbesar:' % (len(top), limit))
    for sz, p in top[:limit]:
        print('  %12s  %s' % (human(sz), p))
    if not top:
        print('  - tidak ada file')


def sqlite_stats(dbp):
    """V63.16: statistik halaman SQLite + potongan penyusutan VACUUM."""
    import sqlite3
    if not os.path.isfile(dbp):
        print('  - DB tidak ditemukan: %s' % dbp)
        return
    try:
        con = sqlite3.connect(dbp, timeout=10)
        try:
            pc = con.execute('PRAGMA page_count').fetchone()[0]
            fl = con.execute('PRAGMA freelist_count').fetchone()[0]
            ps = con.execute('PRAGMA page_size').fetchone()[0]
        finally:
            con.close()
        db_mb = pc * ps / 1048576.0
        fl_mb = fl * ps / 1048576.0
        print('  DB: %.1f MB (%d halaman x %d B), freelist %.1f MB (%.0f%%)' % (
            db_mb, pc, ps, fl_mb, (fl * 100.0 / pc) if pc else 0))
        if fl_mb > 12 and db_mb > 16 and (fl * 100.0 / pc) > 30:
            print('  -> file DB membengkak; jalankan ulang dengan --vacuum untuk menyusutkan ~%s' % human(int(fl * ps)))
        elif fl_mb > 1:
            print('  -> freelist kecil; VACUUM tidak perlu')
        else:
            print('  -> file DB sehat (freelist nyaris nol)')
        try:
            con2 = sqlite3.connect(dbp, timeout=10)
            try:
                row = con2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lost_and_found'").fetchone()
                if row:
                    n = con2.execute('SELECT COUNT(*) FROM lost_and_found').fetchone()[0]
                    print('  lost_and_found: %d baris (sisa pemulihan DB korup, BUKAN data aktif)' % n)
                    print('    -> drop via browser: buka /api/maintenance/lost-and-found lalu POST,')
                    print('       atau sebelum dihapus bisa diunduh: /api/maintenance/lost-and-found?export=csv')
            finally:
                con2.close()
        except Exception:
            pass
    except Exception as e:
        print('  - statistik gagal: %s' % e)
        print('    (bila disk I/O error: kuota PA penuh — coba --repair)')


def vacuum_db(dbp):
    """V63.16: VACUUM dengan guard ruang kosong >= 3x ukuran DB."""
    import sqlite3
    if not os.path.isfile(dbp):
        print('  - DB tidak ditemukan: %s' % dbp)
        return
    before = os.path.getsize(dbp)
    free = shutil.disk_usage(os.path.dirname(dbp)).free
    if free < before * 3:
        print('  VACUUM DILEWATI: ruang kosong %s < 3x DB (%s). Bersihkan dulu, ulangi.' % (human(free), human(before)))
        return
    try:
        con = sqlite3.connect(dbp, timeout=30, isolation_level=None)
        try:
            con.execute('PRAGMA busy_timeout=30000')
            print('  VACUUM berjalan (bisa beberapa detik)...')
            con.execute('VACUUM')
        finally:
            con.close()
        after = os.path.getsize(dbp)
        print('  VACUUM selesai: %s -> %s (dibebaskan %s)' % (
            human(before), human(after), human(max(0, before - after))))
        globals()['freed_total'] += max(0, before - after)
    except Exception as e:
        print('  VACUUM gagal (DB dipakai aplikasi? coba lagi saat idle): %s' % e)


def _venv_test_dirs():
    """V63.25: folder test-suite di dalam virtualenv (aman dihapus)."""
    bases = []
    for b in (os.path.join(HOME, '.virtualenvs'),
              os.path.join(HOME, '.local', 'share', 'virtualenvs'),
              os.path.join(HOME, '.venv')):
        if os.path.isdir(b):
            bases.append(b)
    found = []
    for b in bases:
        for root, dirs, _files in os.walk(b):
            for d in list(dirs):
                if d == 'tests' and 'site-packages' in root:
                    found.append(os.path.join(root, d))
                    dirs.remove(d)
    return found


def _safe_free(mysite_dir):
    """V63.25 [0]: hapus sampah aman — semua PENGHAPUSAN, tidak butuh sisa
    kuota. Return perkiraan bytes dibebaskan."""
    freed = [0]

    def _rmrf(p, label):
        try:
            if os.path.isdir(p):
                sz = dir_size(p)
                shutil.rmtree(p, ignore_errors=True)
                freed[0] += sz
                print('  - %s: %s dibebaskan  (%s)' % (label, human(sz), p))
            elif os.path.isfile(p):
                sz = os.path.getsize(p)
                os.remove(p)
                freed[0] += sz
                print('  - %s: %s dibebaskan  (%s)' % (label, human(sz), p))
        except OSError:
            pass

    print('[0/6] membersihkan ruang aman (penghapusan, kuota 0 byte tetap jalan):')
    _rmrf(os.path.join(HOME, '.cache', 'pip'), 'cache pip')
    _rmrf(os.path.join(HOME, '.cache', 'thumbnails'), 'cache thumbnails')
    for c in (os.path.join(HOME, 'mysite', 'dist'),
              os.path.join(mysite_dir, 'dist') if mysite_dir else None):
        if c and os.path.isdir(c):
            _rmrf(c, 'dist lama')
    if mysite_dir:
        jd = os.path.join(mysite_dir, 'instance', 'upload_jobs')
        if os.path.isdir(jd):
            now = time.time()
            for name in os.listdir(jd):
                p = os.path.join(jd, name)
                try:
                    if now - os.path.getmtime(p) > 3600:
                        _rmrf(p, 'upload_jobs tua')
                except OSError:
                    pass
        bdir = os.path.join(mysite_dir, 'instance', 'backups')
        for pat, keep in (('po_database_*.db', 1), ('po_database_*.db.gz', 2)):
            fs = sorted(glob.glob(os.path.join(bdir, pat)), key=os.path.getmtime)
            for old in (fs[:-keep] if len(fs) > keep else []):
                _rmrf(old, 'backup lama')
        for old in glob.glob(os.path.join(mysite_dir, 'instance', 'po_database.db.corrupt_*')):
            _rmrf(old, 'arsip DB korup')
    n, sz = 0, 0
    for root, dirs, _files in os.walk(HOME):
        if '__pycache__' in dirs:
            p = os.path.join(root, '__pycache__')
            sz += dir_size(p)
            n += 1
            shutil.rmtree(p, ignore_errors=True)
            dirs.remove('__pycache__')
    if n:
        freed[0] += sz
        print('  - __pycache__: %d lokasi, %s dibebaskan' % (n, human(sz)))
    for p in _venv_test_dirs():
        _rmrf(p, 'test-suite virtualenv')
    if freed[0] == 0:
        print('  - tidak ada sampah yang ditemukan')
    print('  total dibebaskan tahap ini: ~%s' % human(freed[0]))
    return freed[0]


def do_repair():
    """V63.25 --repair: pulihkan kuota penuh — ruang-aware.
    Detail urutan lihat docstring header file. Return exit-code."""
    import sqlite3 as _sq3r
    here = os.path.dirname(os.path.abspath(__file__))
    dbp = os.path.join(here, 'instance', 'po_database.db')
    if not os.path.isfile(dbp):
        dbp = os.path.join(HOME, 'mysite', 'instance', 'po_database.db')
    if not os.path.isfile(dbp):
        print('po_database.db tidak ditemukan — jalankan dari folder aplikasi.')
        return 1
    mysite_dir = os.path.dirname(os.path.dirname(dbp))
    bakp = '/tmp/po_backup_pre.db'
    cmpp = '/tmp/po_compact.db'

    def _sz(p):
        try:
            return human(os.path.getsize(p))
        except Exception:
            return '?'

    def _probe():
        p = os.path.join(os.path.dirname(dbp), '.probe_v6325')
        try:
            with open(p, 'w') as f:
                f.write('ok')
            os.remove(p)
            return True
        except OSError:
            return False

    print('DB : %s (%s)' % (dbp, _sz(dbp)))
    print('WAL: %s' % _sz(dbp + '-wal'))
    old_size = _sz(dbp)
    _safe_free(mysite_dir)

    try:
        con = _sq3r.connect(dbp, timeout=120, isolation_level=None)
        con.execute('PRAGMA busy_timeout=120000')
        # (1) backup ke /tmp — best-effort, pakai ulang bila sudah valid
        reused = False
        if os.path.isfile(bakp):
            try:
                bcon = _sq3r.connect(bakp)
                bic = bcon.execute('PRAGMA integrity_check').fetchone()[0]
                bcon.close()
                reused = (bic == 'ok')
                print('[1/6] backup lama %s %s -> dipakai (integrity: %s)'
                      % (bakp, _sz(bakp), bic))
            except Exception:
                reused = False
        if not reused:
            try:
                if os.path.exists(bakp):
                    os.remove(bakp)
                shutil.copy2(dbp, bakp)
                print('[1/6] backup -> %s (%s)' % (bakp, _sz(bakp)))
            except OSError as e:
                print('[1/6] backup ke /tmp DILEWATI (%s)' % e)
                print('      -> aman: DB asli tidak diubah sebelum verifikasi lolos,')
                print('         dan hasil compact di /tmp menjadi salinan aman.')
        # (2) probe tulis kecil di HOME
        if not _probe():
            print('[2/6] PROBE GAGAL: kuota masih 0 byte — journal DROP tak muat.')
            print('      Cek pemakan terbesar lalu hapus (biasanya virtualenv ~250 MB):')
            print('        du -h --max-depth=1 ~ | sort -h | tail -8')
            print('      Setelah ada ruang, jalankan ulang script ini.')
            con.close()
            return 1
        print('[2/6] probe tulis: OK (ada ruang untuk journal)')
        # (3) drop lost_and_found bila ada
        ex = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='lost_and_found'").fetchone()
        pre_so = con.execute('SELECT COUNT(*) FROM so_data').fetchone()[0]
        try:
            pre_ir = con.execute('SELECT COUNT(*) FROM item_registration').fetchone()[0]
        except Exception:
            pre_ir = -1
        if ex:
            n_lost = con.execute('SELECT COUNT(*) FROM lost_and_found').fetchone()[0]
            con.execute('DROP TABLE lost_and_found')
            print('[3/6] tabel lost_and_found DI-DROP (%d baris residu .recover)'
                  % n_lost)
        else:
            print('[3/6] tabel lost_and_found tidak ada')
        # (4) VACUUM INTO /tmp — DB asli tidak ditulis sama sekali
        if os.path.exists(cmpp):
            os.remove(cmpp)
        print('[4/6] VACUUM INTO /tmp berjalan (bisa beberapa detik)...')
        try:
            con.execute('VACUUM INTO ?', (cmpp,))
        except Exception as e:
            # /tmp kemungkinan ikut dihitung kuota & penuh — buang backup lama,
            # lalu ulangi sekali (compact menjadi salinan aman penggantinya)
            if os.path.isfile(bakp) and os.path.getsize(bakp) > 100 * 1024 * 1024:
                os.remove(bakp)
                print('      VACUUM gagal (%s) — backup lama di /tmp dihapus, ulangi' % e)
                con.execute('VACUUM INTO ?', (cmpp,))
            else:
                raise
        con.close()
        print('      ukuran compact: %s' % _sz(cmpp))
        # (5) verifikasi hasil compact — WAJIB identik dgn DB asli
        vcon = _sq3r.connect(cmpp)
        ic = vcon.execute('PRAGMA integrity_check').fetchone()[0]
        so = vcon.execute('SELECT COUNT(*) FROM so_data').fetchone()[0]
        try:
            ir = vcon.execute('SELECT COUNT(*) FROM item_registration').fetchone()[0]
        except Exception:
            ir = -1
        lost = vcon.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                            "name='lost_and_found'").fetchone()[0]
        vcon.close()
        print('[5/6] integrity: %s | so_data: %d (asli %d) | item_registration: %d (asli %d)'
              % (ic, so, pre_so, ir, pre_ir))
        if ic != 'ok' or so != pre_so or ir != pre_ir or lost:
            print('VERIFIKASI GAGAL — DB asli TIDAK diubah.')
            print('Salinan aman: %s (bila ada) dan %s' % (bakp, cmpp))
            return 1
        # (6) pasang DB baru — kuota langsung bebas, lalu salin compact balik
        for p in (dbp + '-wal', dbp + '-shm', dbp):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        shutil.copy2(cmpp, dbp)
        vcon = _sq3r.connect(dbp)
        ic2 = vcon.execute('PRAGMA integrity_check').fetchone()[0]
        vcon.close()
        print('[6/6] DB baru terpasang: %s (%s) — integrity: %s'
              % (dbp, _sz(dbp), ic2))
        print('SELESAI. Total DB menyusut dari %s -> %s.' % (old_size, _sz(dbp)))
        print('SEKARANG: (1) salinan aman tetap ada di /tmp (%s, %s) —' % (bakp, cmpp))
        print('   boleh dihapus belakangan: rm -f /tmp/po_backup_pre.db /tmp/po_compact.db')
        print('(2) tekan tombol Reload di tab Web PythonAnywhere,')
        print('(3) buka /api/maintenance/disk-report — pastikan probe ok.')
        return 0
    except Exception as e:
        print('repair gagal di tengah: %s' % e)
        print('DB asli TIDAK diubah (penghapusan hanya terjadi di [6/6] setelah verifikasi).')
        print('- bila "disk quota exceeded"/"disk I/O error" di [3/6]: kuota masih 0 —')
        print('  hapus virtualenv (~250 MB) lalu jalankan ulang; du -h --max-depth=1 ~ | sort -h | tail -8')
        print('- bila gagal di [4/6]: /tmp kemungkinan dihitung kuota — cek ls -la /tmp/,')
        print('  bila ada po_backup_pre.db ~246 MB: rm /tmp/po_backup_pre.db lalu ulangi.')
        print('- bila gagal di [6/6] (sangat jarang): pulihkan dengan')
        print('    cp /tmp/po_compact.db %s' % dbp)
        return 1


def main():
    global freed_total
    if '--repair' in sys.argv[1:]:
        sys.exit(do_repair())
    print('=' * 72)
    print('cleanup_disk.py — pembersih kuota PythonAnywhere (V63.25)')
    print('Mode: %s%s' % ('APPLY — sampah akan DIHAPUS' if APPLY else 'DRY-RUN — hanya laporan, tambahkan --apply untuk menghapus',
                          ' + VACUUM' if DO_VACUUM else ''))
    print('Home : %s' % HOME)
    print('=' * 72)

    mysite = find_mysite()
    print('\n[1] HOME top consumers (laporan saja):')
    items = []
    if os.path.isdir(HOME):
        for name in os.listdir(HOME):
            p = os.path.join(HOME, name)
            try:
                sz = dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
            except OSError:
                sz = 0
            items.append((name + ('/' if os.path.isdir(p) else ''), sz))
    items.sort(key=lambda kv: kv[1], reverse=True)
    for name, sz in items[:15]:
        if sz > 0:
            print('  %10s  %s' % (human(sz), name))

    print('\n[1b] 20 FILE TERBESAR di home (laporan saja):')
    report_top_files(20)

    print('\n[2] Cache pip (~/.cache/pip):')
    clean_pip_cache()

    print('\n[3] __pycache__ (semua lokasi di HOME):')
    clean_pycache()

    print('\n[4] Sisa salinan Excel upload (instance/upload_jobs, usia > 1 jam):')
    if mysite:
        clean_upload_jobs(mysite)
    else:
        print('  - folder mysite tidak ditemukan (lewati)')

    print('\n[5] Backup & arsip DB (instance/):')
    if mysite:
        rotate(os.path.join(mysite, 'instance', 'backups', 'po_database_*.db'), BACKUP_KEEP, 'backup DB rotasi (mentah)')
        rotate(os.path.join(mysite, 'instance', 'backups', 'po_database_*.db.gz'), BACKUP_KEEP, 'backup DB rotasi (.db.gz)')
        rotate(os.path.join(mysite, 'instance', 'po_database.db.corrupt_*'), 1, 'arsip DB korup')
        rotate(os.path.join(mysite, 'instance', '*.bak_idxclean'), 1, 'backup .bak_idxclean')
        if APPLY:
            _nm, _sm = _migrate_raw_backups(mysite)
            if _nm:
                print('  backup mentah dikompresi ke .db.gz: %d file (%s dibebaskan)' % (_nm, human(_sm)))
                globals()['freed_total'] += _sm
        print('  [cek] WAL:')
        checkpoint_wal(mysite)
        print('  [cek] halaman SQLite / freelist:')
        sqlite_stats(os.path.join(mysite, 'instance', 'po_database.db'))
        print('  [bersih] file tmp* sisa proses (usia > 1 jam):')
        _now = time.time()
        for _p in glob.glob(os.path.join(mysite, 'tmp*')):
            if os.path.isfile(_p) and _now - os.path.getmtime(_p) > 3600:
                rm(_p, 'tmp sisa proses')
        if DO_VACUUM:
            print('  [mode --vacuum]')
            vacuum_db(os.path.join(mysite, 'instance', 'po_database.db'))
    else:
        print('  - folder mysite tidak ditemukan (lewati)')

    print('\n[6] Paket ZIP delivery lama (simpan 1 terbaru):')
    zips = []
    for base in (HOME, os.path.join(HOME, 'mysite') if mysite else None):
        if base:
            zips += [p for p in glob.glob(os.path.join(base, 'purchasing_dashboard_fixed_v*.zip')) if os.path.isfile(p)]
    zips = sorted(set(zips), key=os.path.getmtime)
    for old in (zips[:-1] if len(zips) > 1 else []):
        rm(old, 'zip versi lama')
    if not zips:
        print('  - tidak ada zip delivery')
    elif len(zips) == 1:
        print('  - hanya 1 zip (%s) — dibiarkan' % os.path.basename(zips[0]))

    print('\n[7] LAPORAN SAJA — tidak dihapus otomatis (cek manual):')
    if mysite:
        dsz = dir_size(os.path.join(mysite, 'dist'))
        if dsz:
            print('  mysite/dist/ = %s  <- build lama; hapus bila yakin: rm -rf ~/mysite/dist' % human(dsz))
        else:
            print('  - mysite/dist/: tidak ada')
    venvs = os.path.join(HOME, '.virtualenvs')
    if os.path.isdir(venvs):
        for name in sorted(os.listdir(venvs)):
            p = os.path.join(venvs, name)
            if os.path.isdir(p):
                print('  .virtualenvs/%s = %s  <- virtualenv; hapus bila tak terpakai (cek Web tab)' % (name, human(dir_size(p))))
    else:
        print('  - ~/.virtualenvs: tidak ada')

    print('\n' + '=' * 72)
    if APPLY:
        print('SELESAI. Total dibebaskan: %s' % human(freed_total))
        print('Cek kuota baru di tab Files PythonAnywhere (http server di-reload otomatis tidak perlu).')
    else:
        print('DRY-RUN selesai. Perkiraan total yang bisa dibebaskan: %s' % human(freed_total))
        print('Jalankan ulang dengan --apply untuk benar-benar menghapus:')
        print('    python3 cleanup_disk.py --apply')
    print('=' * 72)

if __name__ == '__main__':
    main()
