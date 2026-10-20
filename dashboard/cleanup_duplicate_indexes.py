#!/usr/bin/env python3
"""
cleanup_duplicate_indexes.py — Bersihkan index duplikat di po_database.db.

Latar belakang:
  Kolom model yang dideklarasikan `index=True` (mis. SOData.so_number) membuat
  index SQLAlchemy bernama ix_<tabel>_<kolom> (mis. ix_so_data_so_number).
  Blok _ensure_performance_indexes() di app.py dulu juga membuat index manual
  dengan nama idx_* pada kolom yang sama (mis. idx_so_number) sehingga ada dua
  index identik (boros disk & memperlambat write). Yang dipakai/dipertahankan
  adalah index ORM (ix_...); duplikat manual (idx_...) dihapus.

Cara pakai (dari folder aplikasi, sejajar app.py):
  python3 cleanup_duplicate_indexes.py            # mode inspeksi (read-only, tidak mengubah apa pun)
  python3 cleanup_duplicate_indexes.py --apply    # backup -> hapus duplikat -> VACUUM -> laporan
  python3 cleanup_duplicate_indexes.py --db /path/po_database.db   # pakai file DB lain

Aman secara data: yang dihapus hanya SALINAN INDEX, tidak ada row tabel yang disentuh.
"""
import os
import re
import sys
import shutil
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, 'instance', 'po_database.db')
KEEP_RE = re.compile(r'^ix_', re.I)  # index buatan SQLAlchemy (dideklarasikan di model)


def parse_db_path(argv):
    if '--db' in argv:
        i = argv.index('--db')
        return argv[i + 1]
    return DEFAULT_DB


def parse_cols(sql):
    """Ekstrak daftar kolom dari DDL 'CREATE [UNIQUE] INDEX x ON tbl (a, b)'."""
    m = re.search(r'\bON\b\s+[`"\[]?[\w.]+[`"\]]?\s*\((.+)\)\s*$', sql or '', re.S | re.I)
    if not m:
        return None
    cols = []
    for part in m.group(1).split(','):
        part = re.sub(r'\s+(ASC|DESC|COLLATE\s+\w+)\s*$', '', part.strip(), flags=re.I)
        cols.append(part.strip('`"[] ').lower())
    return tuple(cols)


def is_unique(sql):
    return bool(re.match(r'\s*CREATE\s+UNIQUE\s+INDEX', sql or '', re.I))


def collect(con):
    """Kembalikan (semua_index, grup_duplikat). Grup: (tabel, unik, kolom) -> [nama]."""
    rows = con.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type='index' AND sql IS NOT NULL ORDER BY name"
    ).fetchall()
    groups = {}
    for name, tbl, sql in rows:
        if name.startswith('sqlite_autoindex'):
            continue  # index implisit constraint UNIQUE/PK — tidak bisa & tidak boleh dihapus
        cols = parse_cols(sql)
        if not cols:
            continue
        groups.setdefault((tbl.lower(), is_unique(sql), cols), []).append(name)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    return rows, dups


def index_sizes(con):
    try:
        return dict(con.execute(
            "SELECT name, SUM(pgsize) FROM dbstat WHERE name IN "
            "(SELECT name FROM sqlite_master WHERE type='index') GROUP BY name"
        ).fetchall())
    except Exception:
        return {}


def fmt_mb(n):
    return f'{n / 1024 / 1024:.2f} MB' if n else '?'


def choose_keeper(names, tbl, cols):
    """Prioritas keeper: index ORM (ix_...). Bila tidak ada, buat ix_<tabel>_<kolom> (kolom tunggal)."""
    for n in names:
        if KEEP_RE.match(n):
            return n, False
    if len(cols) == 1:
        return f'ix_{tbl}_{cols[0]}', True  # perlu dibuat dulu (nama konvensi SQLAlchemy)
    return sorted(names)[0], False


def main():
    apply = '--apply' in sys.argv
    db = parse_db_path(sys.argv)
    if not os.path.exists(db):
        print(f'ERROR: DB tidak ditemukan: {db}')
        return 1
    print(f'DB: {db}')
    print(f'Mode: {"APPLY (mengubah DB)" if apply else "INSPEKSI (read-only)"}')

    con = sqlite3.connect(db, isolation_level=None)  # autocommit; VACUUM butuh ini
    size_before = os.path.getsize(db)
    rows, dups = collect(con)
    sizes = index_sizes(con)

    print(f'\nTotal index eksplisit: {len(rows)}')
    if not dups:
        print('Tidak ada index duplikat. Tidak ada yang perlu dilakukan.')
        con.close()
        return 0

    print(f'\nDuplikat ditemukan: {len(dups)} grup')
    to_drop, to_create = [], []
    for (tbl, uniq, cols), names in sorted(dups.items()):
        keeper, need_create = choose_keeper(names, tbl, cols)
        extras = [n for n in names if n != keeper]
        if need_create:
            to_create.append((keeper, tbl, uniq, cols))
        print(f'\n  Tabel {tbl} | kolom {", ".join(cols)} | {"UNIQUE" if uniq else "non-unique"}')
        print(f'    KEEP  : {keeper}' + ('  (akan dibuat, konvensi ORM)' if need_create else '  (index ORM/model)'))
        for n in extras:
            print(f'    DROP  : {n}  ({fmt_mb(sizes.get(n))})')
            to_drop.append(n)

    if not apply:
        print('\nIni hanya inspeksi. Jalankan dengan --apply untuk membersihkan.')
        con.close()
        return 0

    # --- APPLY ---
    con.execute('PRAGMA wal_checkpoint(TRUNCATE);')
    bak = db + '.bak_idxclean'
    if os.path.exists(bak):
        bak = f'{db}.bak_idxclean_{int(time.time())}'
    shutil.copy2(db, bak)
    print(f'\nBackup: {bak}')

    for name, tbl, uniq, cols in to_create:
        u = 'UNIQUE ' if uniq else ''
        collist = ', '.join(cols)
        stmt = f'CREATE {u}INDEX IF NOT EXISTS "{name}" ON "{tbl}" ({collist})'
        con.execute(stmt)
        print(f'CREATE: {name}')

    for name in to_drop:
        con.execute(f'DROP INDEX IF EXISTS "{name}"')
        print(f'DROP  : {name}')

    print('VACUUM (membebaskan ruang file)...')
    con.execute('VACUUM;')
    ic = con.execute('PRAGMA integrity_check;').fetchone()[0]
    size_after = os.path.getsize(db)

    print(f'\nintegrity_check : {ic}')
    print(f'Ukuran DB       : {fmt_mb(size_before)} -> {fmt_mb(size_after)} '
          f'(hemat {fmt_mb(max(0, size_before - size_after))})')
    _, dups2 = collect(con)
    print(f'Sisa grup duplikat: {len(dups2)}')
    print('SELESAI.' if not dups2 else 'MASIH ADA DUPLIKAT — periksa manual.')
    con.close()
    return 0 if not dups2 and ic == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
