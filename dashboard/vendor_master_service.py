# -*- coding: utf-8 -*-
"""
Local SQLite Vendor Master service (zero Google Sheets dependency).
Provides:
  - SQLite vendor_master table + one-time seed from legacy snapshot / SOData
  - Excel Template download, Excel Upload (upsert), Excel Export
  - Multi-term universal search & multi-select Vendor / Brand / Location filters
  - Internet lookup for Company Address (Kota, Provinsi) and Brand Handling
"""
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from sqlalchemy import text

# Comprehensive Indonesian City/Regency -> Province mapping (strictly "Kota, Provinsi")
_ID_CITY_PROVINCE = [
    # DKI Jakarta
    ('jakarta pusat', 'Jakarta Pusat, DKI Jakarta'),
    ('jakarta selatan', 'Jakarta Selatan, DKI Jakarta'),
    ('jakarta barat', 'Jakarta Barat, DKI Jakarta'),
    ('jakarta timur', 'Jakarta Timur, DKI Jakarta'),
    ('jakarta utara', 'Jakarta Utara, DKI Jakarta'),
    ('central jakarta', 'Jakarta Pusat, DKI Jakarta'),
    ('south jakarta', 'Jakarta Selatan, DKI Jakarta'),
    ('west jakarta', 'Jakarta Barat, DKI Jakarta'),
    ('east jakarta', 'Jakarta Timur, DKI Jakarta'),
    ('north jakarta', 'Jakarta Utara, DKI Jakarta'),
    ('dki jakarta', 'Jakarta, DKI Jakarta'),
    ('jakarta', 'Jakarta, DKI Jakarta'),
    # Banten
    ('tangerang selatan', 'Tangerang Selatan, Banten'),
    ('south tangerang', 'Tangerang Selatan, Banten'),
    ('bsd city', 'Tangerang Selatan, Banten'),
    ('serpong', 'Tangerang Selatan, Banten'),
    ('tangerang', 'Tangerang, Banten'),
    ('cilegon', 'Cilegon, Banten'),
    ('serang', 'Serang, Banten'),
    ('cikupa', 'Tangerang, Banten'),
    ('balaraja', 'Tangerang, Banten'),
    ('banten', 'Tangerang, Banten'),
    # Jawa Barat
    ('cikarang', 'Bekasi, Jawa Barat'),
    ('jababeka', 'Bekasi, Jawa Barat'),
    ('mm2100', 'Bekasi, Jawa Barat'),
    ('lippo cikarang', 'Bekasi, Jawa Barat'),
    ('deltamas', 'Bekasi, Jawa Barat'),
    ('bekasi', 'Bekasi, Jawa Barat'),
    ('karawang', 'Karawang, Jawa Barat'),
    ('kiic', 'Karawang, Jawa Barat'),
    ('purwakarta', 'Purwakarta, Jawa Barat'),
    ('depok', 'Depok, Jawa Barat'),
    ('cibinong', 'Bogor, Jawa Barat'),
    ('citeureup', 'Bogor, Jawa Barat'),
    ('gunung putri', 'Bogor, Jawa Barat'),
    ('bogor', 'Bogor, Jawa Barat'),
    ('cimahi', 'Cimahi, Jawa Barat'),
    ('bandung', 'Bandung, Jawa Barat'),
    ('cirebon', 'Cirebon, Jawa Barat'),
    ('sukabumi', 'Sukabumi, Jawa Barat'),
    ('tasikmalaya', 'Tasikmalaya, Jawa Barat'),
    ('subang', 'Subang, Jawa Barat'),
    ('majalengka', 'Majalengka, Jawa Barat'),
    ('sumedang', 'Sumedang, Jawa Barat'),
    # Jawa Tengah & DIY
    ('semarang', 'Semarang, Jawa Tengah'),
    ('ungaran', 'Semarang, Jawa Tengah'),
    ('kendal', 'Kendal, Jawa Tengah'),
    ('surakarta', 'Surakarta, Jawa Tengah'),
    ('solo', 'Surakarta, Jawa Tengah'),
    ('kudus', 'Kudus, Jawa Tengah'),
    ('pekalongan', 'Pekalongan, Jawa Tengah'),
    ('tegal', 'Tegal, Jawa Tengah'),
    ('cilacap', 'Cilacap, Jawa Tengah'),
    ('magelang', 'Magelang, Jawa Tengah'),
    ('purwokerto', 'Banyumas, Jawa Tengah'),
    ('salatiga', 'Salatiga, Jawa Tengah'),
    ('yogyakarta', 'Yogyakarta, DI Yogyakarta'),
    ('sleman', 'Sleman, DI Yogyakarta'),
    ('bantul', 'Bantul, DI Yogyakarta'),
    # Jawa Timur
    ('surabaya', 'Surabaya, Jawa Timur'),
    ('sidoarjo', 'Sidoarjo, Jawa Timur'),
    ('waru', 'Sidoarjo, Jawa Timur'),
    ('gresik', 'Gresik, Jawa Timur'),
    ('pasuruan', 'Pasuruan, Jawa Timur'),
    ('pier', 'Pasuruan, Jawa Timur'),
    ('pandaan', 'Pasuruan, Jawa Timur'),
    ('mojokerto', 'Mojokerto, Jawa Timur'),
    ('ngoro', 'Mojokerto, Jawa Timur'),
    ('malang', 'Malang, Jawa Timur'),
    ('kediri', 'Kediri, Jawa Timur'),
    ('probolinggo', 'Probolinggo, Jawa Timur'),
    ('jember', 'Jember, Jawa Timur'),
    ('banyuwangi', 'Banyuwangi, Jawa Timur'),
    ('madiun', 'Madiun, Jawa Timur'),
    ('tuban', 'Tuban, Jawa Timur'),
    ('lamongan', 'Lamongan, Jawa Timur'),
    # Sumatera
    ('batam', 'Batam, Kepulauan Riau'),
    ('tanjung pinang', 'Tanjung Pinang, Kepulauan Riau'),
    ('bintan', 'Bintan, Kepulauan Riau'),
    ('medan', 'Medan, Sumatera Utara'),
    ('deli serdang', 'Deli Serdang, Sumatera Utara'),
    ('pekanbaru', 'Pekanbaru, Riau'),
    ('dumai', 'Dumai, Riau'),
    ('palembang', 'Palembang, Sumatera Selatan'),
    ('bandar lampung', 'Bandar Lampung, Lampung'),
    ('lampung', 'Bandar Lampung, Lampung'),
    ('padang', 'Padang, Sumatera Barat'),
    ('jambi', 'Jambi, Jambi'),
    ('bengkulu', 'Bengkulu, Bengkulu'),
    ('banda aceh', 'Banda Aceh, Aceh'),
    # Kalimantan, Sulawesi, Bali & others
    ('balikpapan', 'Balikpapan, Kalimantan Timur'),
    ('samarinda', 'Samarinda, Kalimantan Timur'),
    ('bontang', 'Bontang, Kalimantan Timur'),
    ('banjarmasin', 'Banjarmasin, Kalimantan Selatan'),
    ('pontianak', 'Pontianak, Kalimantan Barat'),
    ('makassar', 'Makassar, Sulawesi Selatan'),
    ('manado', 'Manado, Sulawesi Utara'),
    ('denpasar', 'Denpasar, Bali'),
    ('badung', 'Badung, Bali'),
    ('mataram', 'Mataram, Nusa Tenggara Barat'),
    ('kupang', 'Kupang, Nusa Tenggara Timur'),
    ('jayapura', 'Jayapura, Papua'),
    # Common overseas supplier hubs
    ('singapore', 'Singapore, Singapore'),
    ('seoul', 'Seoul, South Korea'),
    ('busan', 'Busan, South Korea'),
    ('incheon', 'Incheon, South Korea'),
    ('tokyo', 'Tokyo, Japan'),
    ('osaka', 'Osaka, Japan'),
    ('shanghai', 'Shanghai, China'),
    ('shenzhen', 'Shenzhen, China'),
    ('guangzhou', 'Guangzhou, China'),
    ('suzhou', 'Suzhou, China'),
    ('taipei', 'Taipei, Taiwan'),
    ('kuala lumpur', 'Kuala Lumpur, Malaysia'),
    ('penang', 'Penang, Malaysia'),
    ('bangkok', 'Bangkok, Thailand'),
]

_CURATED_MRO_BRANDS = [
    '3M', 'ABB', 'Airtac', 'Allen-Bradley', 'Ansell', 'Atlas Copco', 'Autonics',
    'Balluff', 'Banner', 'Beckhoff', 'Bosch', 'Brady', 'Bussmann', 'CKD',
    'Cognex', 'Continental', 'Delta', 'DeWalt', 'Donaldson', 'Drager', 'Eaton',
    'Endress+Hauser', 'FAG', 'Festo', 'Fluke', 'Fuji Electric', 'Gates', 'Grundfos',
    'Hilti', 'Hioki', 'Hitachi', 'Honeywell', 'IFM', 'IKO', 'INA', 'Ingersoll Rand',
    'Karcher', 'Keyence', 'King Safety', 'Kito', 'Koganei', 'Krisbow', 'KSB',
    'Legris', 'Lenze', 'Lapp', 'Loctite', 'Makita', 'Mean Well', 'Mitutoyo',
    'Mitsubishi', 'MSA', 'Nachi', 'NBK', 'Norgren', 'NSK', 'NTN', 'Omron',
    'Oriental Motor', 'Osram', 'Panasonic', 'Parker', 'Pepperl+Fuchs', 'Philips',
    'Phoenix Contact', 'Pisco', 'Rittal', 'Rockwell', 'Sandvik', 'Schneider',
    'SEW', 'Sick', 'Siemens', 'SKF', 'SMC', 'Stanley', 'Swagelok', 'Tekiro',
    'THK', 'Timken', 'Tsubaki', 'Turck', 'Uvex', 'Vega', 'Wago', 'Weidmuller',
    'Wika', 'Yaskawa', 'Yokogawa',
]

_GENERIC_BRAND_STOPWORDS = {
    'pt', 'cv', 'ud', 'tbk', 'ltd', 'inc', 'corp', 'co', 'indo', 'indonesia',
    'trading', 'tech', 'technology', 'tools', 'tool', 'general', 'local',
    'lokal', 'other', 'others', 'unknown', 'none', 'null', 'na', 'n/a',
    'no brand', 'nobrand', 'non brand', 'standard', 'std', 'custom', 'oem',
    'generic', 'china', 'japan', 'korea', 'taiwan', 'germany', 'usa',
    'service', 'services', 'jasa', 'material', 'safety', 'electric', 'electrical',
    'industrial', 'supply', 'supplier', 'distributor', 'store', 'shop', 'online',
    'jakarta', 'bekasi', 'surabaya', 'bandung', 'tangerang', 'semarang',
}


def _clean(val):
    if val is None:
        return ''
    s = str(val).strip()
    return '' if s.lower() in ('nan', 'none', 'null') else s


def _parse_web_brands(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        s = str(raw).strip()
        if s.startswith('['):
            try:
                items = json.loads(s)
            except Exception:
                items = [x.strip() for x in re.split(r'[,;\n]+', s)]
        else:
            items = [x.strip() for x in re.split(r'[,;\n]+', s)]
    seen = set()
    out = []
    for item in items:
        c = _clean(item)
        if not c:
            continue
        k = c.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def ensure_vendor_master_table(db):
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS vendor_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT NOT NULL,
            city_province TEXT DEFAULT '',
            vendor_id TEXT DEFAULT '',
            password TEXT DEFAULT '',
            web_brands TEXT DEFAULT '[]',
            source_url TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_vendor_master_name
        ON vendor_master (vendor_name)
    """))
    db.session.commit()


def seed_vendor_master_if_empty(db, legacy_rows_loader=None, sodata_model=None):
    ensure_vendor_master_table(db)
    count = db.session.execute(text("SELECT COUNT(*) FROM vendor_master")).scalar() or 0
    if count > 0:
        return count

    now_iso = datetime.utcnow().isoformat() + 'Z'
    seed_items = []

    if callable(legacy_rows_loader):
        try:
            legacy_rows, _ = legacy_rows_loader()
            for r in (legacy_rows or []):
                vn = _clean(r.get('vendor_name'))
                if not vn:
                    continue
                seed_items.append({
                    'vendor_name': vn,
                    'city_province': _clean(r.get('city_province')),
                    'vendor_id': _clean(r.get('vendor_id')),
                    'password': _clean(r.get('password')),
                    'web_brands': json.dumps(_parse_web_brands(r.get('web_brands')), ensure_ascii=False),
                    'source_url': _clean(r.get('source_url')),
                    'updated_at': now_iso,
                })
        except Exception as exc:
            print(f"[vendor_master] legacy seed skipped: {exc}", flush=True)

    if not seed_items and sodata_model is not None:
        try:
            distinct_vendors = db.session.query(sodata_model.vendor_name).filter(
                sodata_model.vendor_name.isnot(None),
                sodata_model.vendor_name != ''
            ).distinct().all()
            for (vname,) in distinct_vendors:
                vn = _clean(vname)
                if vn and not re.fullmatch(r'\d+(?:\.0+)?', vn):
                    seed_items.append({
                        'vendor_name': vn,
                        'city_province': '',
                        'vendor_id': '',
                        'password': '',
                        'web_brands': '[]',
                        'source_url': '',
                        'updated_at': now_iso,
                    })
        except Exception as exc:
            print(f"[vendor_master] SOData seed failed: {exc}", flush=True)

    if seed_items:
        seen = set()
        deduped = []
        for item in seed_items:
            key = (item['vendor_name'].lower(), item['vendor_id'].lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        db.session.execute(text("""
            INSERT INTO vendor_master
            (vendor_name, city_province, vendor_id, password, web_brands, source_url, updated_at)
            VALUES (:vendor_name, :city_province, :vendor_id, :password, :web_brands, :source_url, :updated_at)
        """), deduped)
        db.session.commit()
        print(f"[vendor_master] seeded {len(deduped)} vendors into local SQLite", flush=True)
        return len(deduped)
    return 0


def load_vendor_master_rows(db, inject_brand_fn=None, legacy_rows_loader=None, sodata_model=None):
    seed_vendor_master_if_empty(db, legacy_rows_loader=legacy_rows_loader, sodata_model=sodata_model)
    result = db.session.execute(text("""
        SELECT id, vendor_name, city_province, vendor_id, password, web_brands, source_url, updated_at
        FROM vendor_master
        ORDER BY LOWER(vendor_name), LOWER(vendor_id)
    """)).fetchall()

    rows = []
    latest_updated = None
    for r in result:
        upd = _clean(r[7])
        if upd and (latest_updated is None or upd > latest_updated):
            latest_updated = upd
        web_brands = _parse_web_brands(r[5])
        rows.append({
            'id': r[0],
            'row_key': str(r[0]),
            'vendor_name': _clean(r[1]),
            'city_province': _clean(r[2]),
            'vendor_id': _clean(r[3]),
            'password': _clean(r[4]),
            'web_brands': web_brands,
            'source_url': _clean(r[6]),
            'updated_at': upd,
        })

    if callable(inject_brand_fn) and rows:
        try:
            inject_brand_fn(rows)
        except Exception as exc:
            print(f"[vendor_master] brand inject warning: {exc}", flush=True)

    for row in rows:
        bh = row.get('brand_handling') or {'cheapest': [], 'all': [], 'single': []}
        so_all = list(bh.get('all') or [])
        so_lower = {b.lower() for b in so_all}
        web_only = [b for b in (row.get('web_brands') or []) if b.lower() not in so_lower]
        row['brand_handling'] = {
            'cheapest': list(bh.get('cheapest') or []),
            'all': so_all,
            'single': list(bh.get('single') or []),
            'web': web_only,
        }
    return rows, latest_updated


def split_multi_terms(raw_text):
    if not raw_text:
        return []
    parts = re.split(r'[\n,;]+', str(raw_text))
    return [p.strip().lower() for p in parts if p and p.strip()]


def filter_vendor_rows(rows, search_raw='', vendors=None, brands=None, locations=None):
    vendors = [v.strip().lower() for v in (vendors or []) if v and v.strip()]
    brands = [b.strip().lower() for b in (brands or []) if b and b.strip()]
    locations = [loc.strip().lower() for loc in (locations or []) if loc and loc.strip()]
    search_terms = split_multi_terms(search_raw)

    out = []
    for row in rows:
        vn_l = (row.get('vendor_name') or '').lower()
        vid_l = (row.get('vendor_id') or '').lower()
        loc_l = (row.get('city_province') or '').lower()
        bh = row.get('brand_handling') or {}
        row_brands = list(bh.get('all') or []) + list(bh.get('web') or [])
        row_brands_l = [b.lower() for b in row_brands]

        if vendors:
            if not any(v in vn_l or v in vid_l for v in vendors):
                continue

        if brands:
            if not any(any(b in rb for rb in row_brands_l) for b in brands):
                continue

        if locations:
            if not any(loc in loc_l for loc in locations):
                continue

        if search_terms:
            haystack = ' | '.join([vn_l, loc_l, vid_l, ' '.join(row_brands_l)])
            if not any(term in haystack for term in search_terms):
                continue

        out.append(row)
    return out


def build_vendor_template_xlsx(rows=None):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Vendor Template'

    headers = [
        'Vendor Name',
        'Alamat (Kota, Provinsi)',
        'Vendor ID',
        'Password',
        'Brand Handling (Internet)',
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color='1D4ED8', end_color='1D4ED8', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')

    if rows:
        for row in rows:
            bh = row.get('brand_handling') or {}
            web_brands = ', '.join(bh.get('web') or row.get('web_brands') or [])
            ws.append([
                row.get('vendor_name') or '',
                row.get('city_province') or '',
                row.get('vendor_id') or '',
                row.get('password') or '',
                web_brands,
            ])
    else:
        ws.append([
            'PT CONTOH SUPPLIER INDONESIA',
            'Bekasi, Jawa Barat',
            'VND001',
            'Pass123!',
            'Omron, SMC, Schneider',
        ])
        ws.append([
            'PT CONTOH TEKNIK MANDIRI',
            'Surabaya, Jawa Timur',
            'VND002',
            'Pass456!',
            'SKF, NSK, Bosch',
        ])

    widths = [36, 28, 18, 18, 36]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_vendor_excel(rows):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Vendor List'

    headers = [
        'No',
        'Vendor Name',
        'Alamat (Kota, Provinsi)',
        'Vendor ID',
        'Password',
        'Brand Handling (SO Data)',
        'Brand Handling (Internet)',
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color='1D4ED8', end_color='1D4ED8', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for idx, row in enumerate(rows, start=1):
        bh = row.get('brand_handling') or {}
        so_brands = ', '.join(bh.get('all') or [])
        web_brands = ', '.join(bh.get('web') or row.get('web_brands') or [])
        ws.append([
            idx,
            row.get('vendor_name') or '',
            row.get('city_province') or '',
            row.get('vendor_id') or '',
            row.get('password') or '',
            so_brands,
            web_brands,
        ])

    widths = [8, 36, 28, 18, 18, 42, 34]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _norm_hdr(h):
    return re.sub(r'[^a-z0-9]+', '', str(h or '').lower())


def import_vendor_excel(db, file_bytes):
    import openpyxl

    ensure_vendor_master_table(db)
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    rows_iter = list(ws.iter_rows(values_only=True))
    if not rows_iter:
        return {'inserted': 0, 'updated': 0, 'skipped': 0}

    # Detect header row within first 10 rows
    header_idx = 0
    col_map = {}
    for idx, raw_row in enumerate(rows_iter[:10]):
        norm_cols = {_norm_hdr(c): i for i, c in enumerate(raw_row or []) if _clean(c)}
        vn_col = (
            norm_cols.get('vendorname')
            or norm_cols.get('vendornm')
            or norm_cols.get('vendor')
            or norm_cols.get('suppliername')
            or norm_cols.get('supplier')
            or norm_cols.get('namavendor')
        )
        if vn_col is not None:
            header_idx = idx
            col_map = {
                'vendor_name': vn_col,
                'city_province': next(
                    (norm_cols[k] for k in norm_cols if any(x in k for x in ('alamat', 'kotaprovinsi', 'cityprovince', 'kota', 'address', 'lokasi', 'location'))),
                    None,
                ),
                'vendor_id': next(
                    (norm_cols[k] for k in ('vendorid', 'userid', 'id') if k in norm_cols),
                    None,
                ),
                'password': next(
                    (norm_cols[k] for k in ('password', 'pass', 'pwd') if k in norm_cols),
                    None,
                ),
                'web_brands': next(
                    (norm_cols[k] for k in norm_cols if 'brand' in k and 'sodata' not in k),
                    None,
                ),
            }
            break

    if col_map.get('vendor_name') is None:
        raise ValueError('Kolom "Vendor Name" tidak ditemukan pada file Excel. Gunakan Template Excel yang disediakan.')

    existing_rows = db.session.execute(text(
        "SELECT id, vendor_name, city_province, vendor_id, password, web_brands FROM vendor_master"
    )).fetchall()
    existing_by_name = {(_clean(r[1]).lower()): r for r in existing_rows}

    now_iso = datetime.utcnow().isoformat() + 'Z'
    inserted = 0
    updated = 0
    skipped = 0

    def _cell(row_vals, key):
        ci = col_map.get(key)
        if ci is None or ci >= len(row_vals):
            return ''
        return _clean(row_vals[ci])

    for raw in rows_iter[header_idx + 1:]:
        if not raw:
            continue
        vn = _cell(raw, 'vendor_name')
        if not vn or re.fullmatch(r'\d+(?:\.0+)?', vn):
            skipped += 1
            continue
        cp = _cell(raw, 'city_province')
        vid = _cell(raw, 'vendor_id')
        pwd = _cell(raw, 'password')
        wb_raw = _cell(raw, 'web_brands')
        wb_list = _parse_web_brands(wb_raw)

        key = vn.lower()
        if key in existing_by_name:
            ex = existing_by_name[key]
            ex_id, _ex_vn, ex_cp, ex_vid, ex_pwd, ex_wb = ex
            merged_wb = _parse_web_brands(ex_wb)
            existing_wb_l = {b.lower() for b in merged_wb}
            for b in wb_list:
                if b.lower() not in existing_wb_l:
                    merged_wb.append(b)
                    existing_wb_l.add(b.lower())
            db.session.execute(text("""
                UPDATE vendor_master
                SET city_province = :cp,
                    vendor_id = :vid,
                    password = :pwd,
                    web_brands = :wb,
                    updated_at = :upd
                WHERE id = :id
            """), {
                'cp': cp or _clean(ex_cp),
                'vid': vid or _clean(ex_vid),
                'pwd': pwd or _clean(ex_pwd),
                'wb': json.dumps(merged_wb, ensure_ascii=False),
                'upd': now_iso,
                'id': ex_id,
            })
            updated += 1
        else:
            db.session.execute(text("""
                INSERT INTO vendor_master
                (vendor_name, city_province, vendor_id, password, web_brands, source_url, updated_at)
                VALUES (:vn, :cp, :vid, :pwd, :wb, '', :upd)
            """), {
                'vn': vn,
                'cp': cp,
                'vid': vid,
                'pwd': pwd,
                'wb': json.dumps(wb_list, ensure_ascii=False),
                'upd': now_iso,
            })
            existing_by_name[key] = (None, vn, cp, vid, pwd, json.dumps(wb_list))
            inserted += 1

    db.session.commit()
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped}


def _extract_city_province(text_blob):
    low = (text_blob or '').lower()
    if not low:
        return ''
    for needle, formatted in _ID_CITY_PROVINCE:
        if re.search(r'\b' + re.escape(needle) + r'\b', low):
            return formatted
    return ''


def _extract_brands_from_text(text_blob, known_brands=None):
    if not text_blob:
        return []
    candidates = list(_CURATED_MRO_BRANDS)
    seen_c = {b.lower() for b in candidates}
    for b in (known_brands or []):
        c = _clean(b)
        if len(c) < 3:
            continue
        cl = c.lower()
        if cl in _GENERIC_BRAND_STOPWORDS or cl in seen_c:
            continue
        if re.fullmatch(r'[\d\W]+', c):
            continue
        seen_c.add(cl)
        candidates.append(c)

    found = []
    seen_f = set()
    for brand in candidates:
        if len(brand) < 2:
            continue
        pat = r'(?<![A-Za-z0-9])' + re.escape(brand) + r'(?![A-Za-z0-9])'
        if re.search(pat, text_blob, re.IGNORECASE):
            bl = brand.lower()
            if bl not in seen_f and bl not in _GENERIC_BRAND_STOPWORDS:
                seen_f.add(bl)
                found.append(brand)
        if len(found) >= 12:
            break
    return found


def _ddg_search_snippets(query, timeout=8):
    url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote_plus(query)
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
        },
    )
    try:
        from desktop_network_guard import permit_vendor_lookup_network
    except Exception:
        from contextlib import nullcontext as permit_vendor_lookup_network
    with permit_vendor_lookup_network():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_text = resp.read().decode('utf-8', errors='ignore')

    # Strip tags cleanly to extract titles, snippets, and first result URL
    urls = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html_text)
    first_url = ''
    for u in urls:
        if 'uddg=' in u:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            if qs.get('uddg'):
                first_url = qs['uddg'][0]
                break
        elif u.startswith('http'):
            first_url = u
            break

    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', html_text, re.DOTALL | re.IGNORECASE)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html_text, re.DOTALL | re.IGNORECASE)
    raw_chunks = titles[:8] + snippets[:8]
    clean_chunks = [re.sub(r'<[^>]+>', ' ', c) for c in raw_chunks]
    clean_chunks = [re.sub(r'\s+', ' ', c).strip() for c in clean_chunks if c.strip()]
    return ' '.join(clean_chunks), first_url


def lookup_vendor_internet(vendor_name, known_brands=None):
    vn = _clean(vendor_name)
    if not vn:
        return {'city_province': '', 'web_brands': [], 'source_url': '', 'error': 'Nama vendor kosong'}

    # Clean up trailing legal/branch tokens for better search hit rate
    q_name = re.sub(r'\s+', ' ', vn).strip()
    combined_text = ''
    source_url = ''
    errors = []

    for q in (
        f'"{q_name}" Indonesia alamat kota',
        f'"{q_name}" distributor produk brand',
    ):
        try:
            txt, url = _ddg_search_snippets(q, timeout=8)
            if txt:
                combined_text += ' ' + txt
            if url and not source_url:
                source_url = url
        except Exception as exc:
            errors.append(str(exc))

    city_province = _extract_city_province(combined_text)
    web_brands = _extract_brands_from_text(combined_text, known_brands=known_brands)

    return {
        'city_province': city_province,
        'web_brands': web_brands,
        'source_url': source_url,
        'found': bool(city_province or web_brands),
        'warning': '; '.join(errors) if (errors and not combined_text) else '',
    }
