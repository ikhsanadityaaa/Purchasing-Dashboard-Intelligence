#!/usr/bin/env python3
"""V63.10 — Deteksi, pemulihan, dan backup database SQLite (backup .db.gz terkompresi).

Latar: di produksi (PythonAnywhere) pernah terjadi "database disk image is
malformed" saat aplikasi membaca so_data. Modul ini dipakai app.py saat
startup (auto-heal) dan bisa dijalankan manual:

    python db_recovery.py                       # pakai instance/po_database.db
    python db_recovery.py /path/ke/db.sqlite    # path eksplisit

Strategi pemulihan (urut prioritas):
  1. sqlite3 CLI .recover (jika CLI tersedia) — resmi, paling menyeluruh.
  2. Salvage murni Python: salin tabel satu per satu, toleran halaman rusak
     (pindai per-chunk rowid, lalu per-baris pada chunk yang gagal).
  3. Restore backup sehat terbaru dari instance/backups/ (dibuat otomatis
     setelah tiap update data besar — lihat backup_db()).

File korup tidak pernah langsung dibuang: diarsipkan sebagai
<db>.corrupt_<timestamp> agar data selalu bisa dianalisis ulang.
"""
import glob
import os
import shutil
import sqlite3
import subprocess
import time
import traceback

QUICK_CHECK_LIMIT = 30


def _open_ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)


def quick_check(path, limit=QUICK_CHECK_LIMIT):
    """Return (healthy: bool, detail: str) untuk file DB."""
    try:
        con = _open_ro(path)
        try:
            rows = con.execute(f'PRAGMA quick_check({limit})').fetchall()
        finally:
            con.close()
        msgs = [str(r[0]) for r in rows]
        return (msgs == ['ok'], '; '.join(msgs)[:400])
    except Exception as e:
        return (False, f'gagal buka/check: {e}')


def _unique_path(base):
    """Hindari bentrokan nama file (dua recovery/backup di detik yang sama)."""
    if not os.path.exists(base):
        return base
    i = 1
    while os.path.exists(f'{base}.{i}'):
        i += 1
    return f'{base}.{i}'


def _quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def _salvage_python(src, dst):
    """Fallback tanpa sqlite3 CLI: salin isi tabel sebanyak mungkin."""
    scon = _open_ro(src)
    dcon = sqlite3.connect(dst, timeout=30)
    dcon.execute('PRAGMA journal_mode=OFF')
    dcon.execute('PRAGMA synchronous=OFF')
    copied = {}
    skipped = {}
    try:
        master = scon.execute(
            "SELECT name, type, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
        ).fetchall()
        for name, typ, sql in master:
            if typ == 'table' and name == 'lost_and_found':
                # V63.27: junk hasil .recover — bukan data dashboard, jangan
                # dibawa ke DB pulihan (pernah 740.821 baris / ratusan MB).
                skipped[name] = 'lost_and_found skip'
                continue
            try:
                if typ == 'table':
                    dcon.execute(sql)
                elif sql:
                    try:
                        dcon.execute(sql)
                        continue
                    except Exception:
                        # index/view/trigger bermasalah: lewati, bukan data
                        skipped[name] = 'non-table skip'
                        continue
            except Exception as e:
                skipped[name] = f'create gagal: {e}'
                continue
            # ── salin baris ──
            ncols = len(scon.execute(f'SELECT * FROM {_quote(name)} LIMIT 0').description)
            ph = ','.join('?' * ncols)
            ins = f'INSERT INTO {_quote(name)} VALUES ({ph})'

            def _insert_rows(rows):
                """Insert satu chunk; jika gagal massal (mis. constraint), per-baris."""
                try:
                    dcon.executemany(ins, rows)
                    return len(rows)
                except Exception:
                    got = 0
                    for row in rows:
                        try:
                            dcon.execute(ins, row)
                            got += 1
                        except Exception:
                            continue
                    return got

            def _copy_range(a, b):
                """Salin rowid [a, b]; DatabaseError -> per-baris. Return jumlah masuk."""
                try:
                    rows = scon.execute(
                        f'SELECT * FROM {_quote(name)} WHERE rowid BETWEEN ? AND ? ORDER BY rowid',
                        (a, b)).fetchall()
                    return _insert_rows(rows) if rows else 0
                except sqlite3.DatabaseError:
                    got = 0
                    for rid in range(a, b + 1):
                        try:
                            row = scon.execute(
                                f'SELECT * FROM {_quote(name)} WHERE rowid = ?', (rid,)).fetchone()
                            if row is not None:
                                dcon.execute(ins, row)
                                got += 1
                        except Exception:
                            continue
                    return got

            # maxid: >=0 -> chunk scan normal; -1 -> btree korup (probe scan);
            # None -> tabel WITHOUT ROWID (full scan sekali)
            maxid = None
            try:
                maxid = scon.execute(
                    f'SELECT COALESCE(MAX(rowid), 0) FROM {_quote(name)}').fetchone()[0]
            except sqlite3.Error as e:
                maxid = None if 'no such column' in str(e).lower() else -1
            n = 0
            if maxid is None:
                try:
                    rows = scon.execute(f'SELECT * FROM {_quote(name)}').fetchall()
                    n = _insert_rows(rows)
                except Exception as e:
                    skipped[name] = f'full scan gagal: {e}'
            elif maxid == -1:
                # halaman interior korup: probes range naik sampai 3 range kosong
                # berturut-turut (tak bergantung MAX(rowid))
                step, a, empty_streak, LIMIT = 500, 1, 0, 50_000_000
                while a <= LIMIT and empty_streak < 3:
                    b = a + step - 1
                    try:
                        rows = scon.execute(
                            f'SELECT * FROM {_quote(name)} WHERE rowid BETWEEN ? AND ? ORDER BY rowid',
                            (a, b)).fetchall()
                        if rows:
                            empty_streak = 0
                            n += _insert_rows(rows)
                        else:
                            empty_streak += 1
                    except sqlite3.DatabaseError:
                        n += _copy_range(a, b)  # per-baris di dalam range
                    a = b + 1
            else:
                step = 500
                for a in range(1, int(maxid) + 1, step):
                    b = a + step - 1
                    try:
                        rows = scon.execute(
                            f'SELECT * FROM {_quote(name)} WHERE rowid BETWEEN ? AND ? ORDER BY rowid',
                            (a, b)).fetchall()
                        n += _insert_rows(rows) if rows else 0
                    except sqlite3.DatabaseError:
                        n += _copy_range(a, b)
                    except Exception as e:
                        skipped[name] = f'chunk {a}-{b}: {e}'
                        continue
            copied[name] = n
        dcon.commit()
    finally:
        try:
            dcon.close()
        except Exception:
            pass
        try:
            scon.close()
        except Exception:
            pass
    return copied, skipped


def recover(src):
    """Pulihkan `src` ke file baru. Return (ok, dst, method, info)."""
    if not os.path.isfile(src):
        return (False, None, 'none', f'file tidak ada: {src}')
    ts = time.strftime('%Y%m%d_%H%M%S')
    dst = _unique_path(f'{src}.recovered_{ts}.db')
    # ── strategi 1: sqlite3 CLI .recover ──
    if shutil.which('sqlite3'):
        dump = dst + '.sql'
        try:
            with open(dump, 'w') as fo:
                r1 = subprocess.run(['sqlite3', src, '.recover'], stdout=fo,
                                    stderr=subprocess.PIPE, timeout=1800)
            with open(dump, 'r') as fi:
                r2 = subprocess.run(['sqlite3', dst], stdin=fi,
                                    stderr=subprocess.PIPE, timeout=1800)
            os.remove(dump)
            # V63.27: buang tabel lost_and_found hasil .recover — tabel ini
            # hanya wadah baris yatim (orphaned pages) dari korupsi lama;
            # tidak ada data dashboard yang memakainya dan ukurannya bisa
            # ratusan MB (pernah 740.821 baris). Dibuang SEBELUM quick_check
            # agar tidak pernah lagi ikut menjadi DB aktif.
            try:
                _con_v6327 = sqlite3.connect(dst, timeout=60)
                try:
                    _con_v6327.execute('DROP TABLE IF EXISTS lost_and_found')
                    _con_v6327.commit()
                finally:
                    _con_v6327.close()
            except Exception as _e_v6327:
                print(f'[db-recovery] drop lost_and_found gagal (dilewati): '
                      f'{_e_v6327}', flush=True)
            ok, detail = quick_check(dst)
            if r1.returncode == 0 and r2.returncode == 0 and ok:
                return (True, dst, 'sqlite3 .recover', detail or 'ok')
        except Exception as e:
            try:
                if os.path.isfile(dump):
                    os.remove(dump)
            except Exception:
                pass
            print(f'[db-recovery] .recover CLI gagal, lanjut salvage Python: {e}', flush=True)
        # dst mungkin setengah jadi — buang sebelum fallback
        try:
            if os.path.isfile(dst):
                os.remove(dst)
        except Exception:
            pass
    # ── strategi 2: salvage murni Python ──
    try:
        copied, skipped = _salvage_python(src, dst)
    except Exception as e:
        traceback.print_exc()
        try:
            if os.path.isfile(dst):
                os.remove(dst)
        except Exception:
            pass
        return (False, dst, 'python-salvage', f'salvage exception: {e}')
    ok, detail = quick_check(dst)
    if ok:
        total = sum(copied.values())
        return (True, dst, 'python-salvage',
                f'{total} baris dari {len(copied)} tabel; dilewati: {skipped or "-"}')
    try:
        if os.path.isfile(dst):
            os.remove(dst)
    except Exception:
        pass
    return (False, dst, 'python-salvage', f'hasil tetap tidak sehat: {detail}; copied={copied}')


def swap_in(db_path, recovered_path):
    """Ganti db_path dengan file pulihan (hapus -wal/-shm lama terlebih dahulu)."""
    for suffix in ('-wal', '-shm'):
        try:
            if os.path.isfile(db_path + suffix):
                os.remove(db_path + suffix)
        except Exception:
            pass
    os.replace(recovered_path, db_path)
    ok, detail = quick_check(db_path)
    if not ok:
        raise RuntimeError(f'setelah swap tetap tidak sehat: {detail}')
    return True


# ── V63.10: backup TERKOMPRESI (.db.gz) ──────────────────────────────────────
# Latar: setelah recovery, DB bisa ~90 MB; backup mentah keep=2 memakan
# ~180 MB dari kuota 512 MB PythonAnywhere -> disk 100% -> SEMUA commit
# SQLite gagal (KPI Import 0, label update macet, upload gagal). Backup
# SQLite berisi teks -> gzip memampatkan ~5-7x (90 MB -> ~15 MB).

def _free_bytes(path):
    """Ruang bebas (byte) pada filesystem yang menampung `path`, atau None."""
    try:
        import shutil as _sh
        return _sh.disk_usage(path).free
    except Exception:
        return None


def _gzip_file(src, dst_gz, level=6):
    """Kompres `src` -> `dst_gz` (streaming 1 MB). Return (uk_gz, uk_raw)."""
    import gzip as _gz
    raw = os.path.getsize(src)
    with open(src, 'rb') as fi, _gz.open(dst_gz, 'wb', compresslevel=level) as fo:
        while True:
            chunk = fi.read(1024 * 1024)
            if not chunk:
                break
            fo.write(chunk)
    return os.path.getsize(dst_gz), raw


def _gz_verify_size(gz_path):
    """Baca balik seluruh gz (memvalidasi CRC). Return ukuran terdekompresi, -1 bila rusak."""
    import gzip as _gz
    try:
        total = 0
        with _gz.open(gz_path, 'rb') as fi:
            while True:
                chunk = fi.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
        return total
    except Exception:
        return -1


def _backup_candidates(bdir):
    """Semua file backup (mentah *.db + terkompresi *.db.gz), terlama -> terbaru."""
    cands = []
    for pat in ('po_database_*.db', 'po_database_*.db.gz'):
        cands += glob.glob(os.path.join(bdir, pat))
    return sorted({c for c in cands if os.path.isfile(c)}, key=os.path.getmtime)


def _rotate_backups_dir(bdir, keep=2):
    """Buang backup tertua melebihi `keep` (campuran .db/.db.gz). Return jumlah dihapus."""
    removed = 0
    olds = _backup_candidates(bdir)
    if keep and len(olds) > keep:
        for old in olds[:-keep]:
            try:
                os.remove(old)
                removed += 1
            except Exception:
                pass
    return removed


def migrate_backups_to_gz(bdir, min_age_s=120):
    """V63.10: kompres backup mentah po_database_*.db -> .db.gz (sekali jalan).
    File mentah dihapus HANYA setelah gz terverifikasi (CRC + ukuran sama).
    Return (jumlah_dikonversi, bytes_dibebaskan)."""
    if not bdir or not os.path.isdir(bdir):
        return 0, 0
    converted, freed = 0, 0
    now = time.time()
    for raw in sorted(glob.glob(os.path.join(bdir, 'po_database_*.db'))):
        try:
            if not os.path.isfile(raw):
                continue
            if now - os.path.getmtime(raw) < min_age_s:
                continue  # sangat baru — mungkin sedang ditulis proses lain
            gz = raw + '.gz'
            raw_sz = os.path.getsize(raw)
            if os.path.exists(gz):
                # sudah pernah dikonversi -> buang mentahnya bila gz sehat
                if _gz_verify_size(gz) == raw_sz:
                    try:
                        os.remove(raw)
                        freed += raw_sz
                        converted += 1
                    except Exception:
                        pass
                continue
            free = _free_bytes(bdir)
            if free is not None and free < raw_sz + (8 * 1024 * 1024):
                continue  # ruang tidak cukup utk menulis gz — coba lain kali
            src = raw + '.tmp_gzsrc'
            try:
                os.rename(raw, src)  # amankan dari rotasi bersamaan
            except Exception:
                continue
            try:
                sz, expected = _gzip_file(src, gz)
                if sz <= 0 or _gz_verify_size(gz) != expected:
                    raise RuntimeError('verifikasi gzip gagal')
                os.remove(src)
                converted += 1
                freed += raw_sz
                print(f'[db-recovery] backup dikompresi: {os.path.basename(gz)} '
                      f'({raw_sz / 1048576.0:.1f} MB -> {sz / 1048576.0:.1f} MB)', flush=True)
            except Exception:
                try:
                    if os.path.isfile(gz):
                        os.remove(gz)
                except Exception:
                    pass
                try:
                    os.rename(src, raw)  # kembalikan file mentah
                except Exception:
                    pass
        except Exception:
            continue
    return converted, freed


def backup_db(db_path, keep=2):  # V63.10: TERKOMPRESI .db.gz + guard ruang (V63.9: keep 5→2)
    """Snapshot backup TERKOMPRESI (*.db.gz). Panggil SETELAH wal_checkpoint(TRUNCATE).
    - DB pulih bisa ~90 MB; backup mentah 2x memakan ~180 MB kuota -> kini ~15 MB x2.
    - Guard ruang: bila ruang tidak cukup, rotasi agresif dulu; masih kurang -> LEWATI
      (tidak pernah memenuhi disk — disk penuh = semua commit SQLite gagal).
    - File setengah jadi SELALU dibuang (bug lama: partial copy tertinggal).
    Return path backup baru atau None jika gagal/ruang kurang."""
    if not os.path.isfile(db_path):
        return None
    bdir = os.path.join(os.path.dirname(db_path), 'backups')
    try:
        os.makedirs(bdir, exist_ok=True)
    except Exception:
        return None
    ts = time.strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(bdir, f'po_database_{ts}.db.gz')
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(bdir, f'po_database_{ts}_k{i}.db.gz')
        i += 1
    raw_sz = os.path.getsize(db_path)
    # gz teks biasanya ~15% raw; 0.5x + 8 MB margin = longgar & aman
    need = int(raw_sz * 0.5) + 8 * 1024 * 1024
    free = _free_bytes(bdir)
    if free is not None and free < need:
        try:
            _rotate_backups_dir(bdir, keep=max(0, keep - 1))
        except Exception:
            pass
        free = _free_bytes(bdir)
        if free is not None and free < need:
            print(f'[db-recovery] backup DILEWATI: ruang kurang '
                  f'(butuh ~{need / 1048576.0:.0f} MB, bebas {free / 1048576.0:.0f} MB) '
                  '— data utama TIDAK terpengaruh', flush=True)
            return None
    tmp = dst + '.tmp'
    try:
        sz, _expected = _gzip_file(db_path, tmp)
        if _gz_verify_size(tmp) != raw_sz:
            raise RuntimeError('verifikasi gzip gagal (ukuran/CRC)')
        os.replace(tmp, dst)
    except Exception as exc:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        print(f'[db-recovery] backup gagal, file setengah jadi dibuang: {exc}', flush=True)
        return None
    try:
        _rotate_backups_dir(bdir, keep=keep)
    except Exception:
        pass
    return dst


def _archive_corrupt(db_path, ts):
    for suffix in ('', '-wal', '-shm'):
        f = db_path + suffix
        if os.path.isfile(f):
            try:
                shutil.copy2(f, f'{db_path}.corrupt_{ts}{suffix}')
            except Exception:
                pass


def _rotate_corrupt(db_path, keep=1):  # V63.9: 3→1
    olds = sorted(glob.glob(db_path + '.corrupt_*'))
    for old in olds[:-keep] if keep and len(olds) > keep else []:
        try:
            os.remove(old)
        except Exception:
            pass


def autoheal(db_path, keep_backups=2):  # V63.9: 5→2
    """Jalankan saat startup aplikasi. Return dict laporan.
    action: 'none' (sehat/belum ada) | 'recovered_from_current' | 'restored_from_backup' | 'failed'."""
    if not os.path.isfile(db_path):
        # boot pertama / deploy segar — file DB dibuat create_all nanti
        return {'action': 'none', 'detail': 'file DB belum ada (boot pertama)'}
    healthy, detail = quick_check(db_path)
    if healthy:
        return {'action': 'none', 'detail': detail}
    ts = time.strftime('%Y%m%d_%H%M%S')
    print(f'[db-recovery] DB TIDAK SEHAT: {detail}', flush=True)
    # kunci lintas-proses agar dua worker tidak memulihkan bersamaan
    lock_path = db_path + '.recovery.lock'
    try:
        import fcntl
        _lk = open(lock_path, 'w')
        fcntl.flock(_lk, fcntl.LOCK_EX)
    except Exception:
        _lk = None
    try:
        # cek ulang di dalam lock (proses lain mungkin baru selesai memulihkan)
        healthy2, detail2 = quick_check(db_path)
        if healthy2:
            return {'action': 'none', 'detail': detail2 + ' (setelah lock)'}
        _archive_corrupt(db_path, ts)
        ok, dst, method, info = recover(db_path)
        if ok:
            swap_in(db_path, dst)
            _rotate_corrupt(db_path)
            print(f'[db-recovery] PULIH dari file korup via {method}: {info}', flush=True)
            return {'action': 'recovered_from_current', 'method': method, 'info': info}
        print(f'[db-recovery] recovery file korup gagal ({method}): {info}', flush=True)
        # ── strategi 3: backup sehat terbaru (mentah .db atau .db.gz V63.10) ──
        bdir = os.path.join(os.path.dirname(db_path), 'backups')
        for cand in sorted(_backup_candidates(bdir), key=os.path.getmtime, reverse=True):
            tmp_restore = None
            try:
                use_path = cand
                if cand.endswith('.gz'):
                    tmp_restore = db_path + f'.restore_{os.getpid()}.tmp'
                    import gzip as _gzr
                    with _gzr.open(cand, 'rb') as fi, open(tmp_restore, 'wb') as fo:
                        while True:
                            chunk = fi.read(1024 * 1024)
                            if not chunk:
                                break
                            fo.write(chunk)
                    use_path = tmp_restore
                okc, _ = quick_check(use_path)
                if not okc:
                    continue
                for suffix in ('-wal', '-shm'):
                    try:
                        if os.path.isfile(db_path + suffix):
                            os.remove(db_path + suffix)
                    except Exception:
                        pass
                shutil.copy2(use_path, db_path)
                print(f'[db-recovery] RESTORE dari backup: {cand} '
                      f'(data sejak backup hilang — upload ulang bila perlu)', flush=True)
                return {'action': 'restored_from_backup', 'backup': os.path.basename(cand)}
            except Exception:
                continue
            finally:
                if tmp_restore:
                    try:
                        os.remove(tmp_restore)
                    except Exception:
                        pass
        print('[db-recovery] GAGAL memulihkan: tidak ada file pulihan maupun backup sehat. '
              'Aplikasi tetap berjalan tapi query DB akan error sampai dipulihkan manual.', flush=True)
        return {'action': 'failed', 'detail': detail}
    finally:
        if _lk is not None:
            try:
                fcntl.flock(_lk, fcntl.LOCK_UN)
                _lk.close()
            except Exception:
                pass


if __name__ == '__main__':
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, 'instance', 'po_database.db')
    print(f'[db-recovery] target: {target}')
    report = autoheal(target)
    healthy, detail = quick_check(target)
    print(f'[db-recovery] laporan : {report}')
    print(f'[db-recovery] status  : {"SEHAT" if healthy else "TIDAK SEHAT"} ({detail})')
    raise SystemExit(0 if healthy else 1)
