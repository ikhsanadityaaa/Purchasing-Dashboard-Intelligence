"""
Prod Reg Status automation (Item Registration).
(FIX V28: label proses diganti dari "Process Pur Info Reg" ke "Prod Reg
Status" — nama file modul, env var PUR_INFO_REG_*, dan --process
pur-info-reg TETAP supaya kompatibel mundur.)

FIX V26: sumber data DIGANTI ke menu **Prod. Reg. Status** (PCD page
MG100_026, komponen ProcessInfoCheckCompView) — sebelumnya menu
Process Pur. Info. Reg. (MG100_006). Flow:
1. Login portal ServeOne, buka S-MRO.
2. Klik menu Prod. Reg. Status (Folder_23 tree: Prod. Mgmt -> Prod. Reg.
   Status).
3. Menu ini TIDAK punya field PIC/purchaser — tidak perlu pengosongan
   (langkah kosongkan purchaser dari menu lama DIHAPUS).
4. Isi tanggal awal (aaaa.ProcessInfoCheckCompView.InputField11) dan
   tanggal akhir (aaaa.ProcessInfoCheckCompView.InputField12), format
   yyyy/MM/dd.
5. Search (aaaa.ProcessInfoCheckCompView.Button), baca jumlah hasil di
   kiri bawah, download Excel (img src xlsDownload.png), upload ke
   dashboard.
6. Limit download menu ini = 5000 baris. Jika angka di kiri bawah
   mencapai 5000, range tanggal dipecah jadi SETENGAH berulang sampai
   hasil < 5000 (FIX V22 — logika adaptive, mirip Search Client Odr;
   FIX V26 — limit turun 10.000 -> 5.000 sesuai menu baru).

FIX V27: anti download-GANDA (laporan user: "di menu ini excel
_terdownload 2 kali"). Tombol download kini diklik SEKALI sampai
 terverifikasi memicu aktivitas download — verifikasi dilakukan SETELAH
 setiap varian klik (bukan menembakkan 3 klik berturut-turut), loading
 mask dicek LINTAS FRAME (mask di frame lain = server sedang memroses,
 bukan klik gagal), setiap strategi klik didahului pre-check "download
 sudah jalan?", tombol Allow popup tidak diklik ulang dalam 10 detik,
 dan sisa file kembar di folder download dibersihkan otomatis per batch.
"""

import logging
from i18n import set_language, apply_localized_logging

# FIX V11: path resolution saat frozen (.exe)
from _paths import _app_dir, _data_dir, _env_file_path, _safe_log_path, _safe_mkdir, _safe_download_dir, _safe_fail_dir, _is_frozen
import os
import sys
import time
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import net_tls
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import main as smro
set_language(os.getenv("SMRO_LANGUAGE", "id"))


DOWNLOAD_DIR = _safe_download_dir('downloads_pur_info_reg')  # FIX V11: sudah auto-mkdir
LOG_PATH = _safe_log_path("pur_info_reg_automation.log")

# FIX V11: wrap FileHandler in try/except supaya tidak crash kalau path tidak writable
try:
    _file_handler = logging.FileHandler(str(LOG_PATH), encoding="utf-8")
except (PermissionError, OSError):
    _file_handler = logging.FileHandler(str(_data_dir() / "pur_info_reg_automation.log"), encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[_file_handler, logging.StreamHandler()],
    force=True,
)
apply_localized_logging(logging.getLogger())


def default_start_date(today: date | None = None) -> date:
    today = today or datetime.now().date()
    return date(today.year - 1, 1, 1)


def resolve_date_range_from_preset(today: date | None = None):
    """Preset date range PER-PROSES (Prod Reg Status — dulu
    "Process Pur Info Reg" — punya range sendiri, tidak dipaksa sama
    dengan proses lain).

    Baca PUR_INFO_REG_DATE_RANGE_PRESET; kalau kosong/tidak dikenal ->
    'custom' (pakai PUR_INFO_REG_START_DATE/PUR_INFO_REG_END_DATE).
    Tidak ada fallback ke DATE_RANGE_PRESET global: preset SMRO tidak
    boleh menular ke proses ini.

    Pilihan preset (semua mundur dari hari ini, end = hari ini):
      - '1week'     : 7 hari terakhir
      - '1month'    : 30 hari terakhir
      - '3month'    : 90 hari terakhir
      - '6month'    : 180 hari terakhir
      - 'this_year' : 1 Januari tahun ini s/d hari ini

    Return (start, end, preset_code) kalau preset aktif, atau
    (None, None, preset_code) kalau 'custom' / tidak dikenal
    (→ pemanggil pakai PUR_INFO_REG_START_DATE/END_DATE seperti biasa).
    """
    today = today or datetime.now().date()
    preset = (os.getenv("PUR_INFO_REG_DATE_RANGE_PRESET") or "custom").strip().lower()
    if preset == "1week":
        return today - timedelta(days=7), today, preset
    if preset == "1month":
        return today - timedelta(days=30), today, preset
    if preset == "3month":
        return today - timedelta(days=90), today, preset
    if preset == "6month":
        return today - timedelta(days=180), today, preset
    if preset == "this_year":
        return date(today.year, 1, 1), today, preset
    return None, None, preset


# FIX V26: URL default menu Prod. Reg. Status (PCD page MG100_026, dari
# HTML portal user: iframe tab_ifrm_MG100_026 src pcd!3aportal_content!
# 2f...!2frole.localsd!2fFolder!2fFolder!2fpage.MG100_026).
_PROD_REG_STATUS_URL = (
    "http://svobpp.serveone.co.kr/irj/servlet/prt/portal/prtroot/"
    "pcd!3aportal_content!2fmdcs!2frole!2fgsi!2findonesia!2frole.localsd!"
    "2fFolder!2fFolder!2fpage.MG100_026"
)

CONFIG = {
    # FIX V26: sumber data ganti menu -> Prod. Reg. Status.
    # Env SMRO_PUR_INFO_REG_URL tetap bisa override (tapi URL menu lama
    # MG100_006 otomatis dimigrasi — lihat blok migrasi di bawah CONFIG).
    "PUR_INFO_REG_URL": os.getenv("SMRO_PUR_INFO_REG_URL", _PROD_REG_STATUS_URL),
    "DASHBOARD_API": os.getenv(
        "PUR_INFO_REG_DASHBOARD_API",
        "http://127.0.0.1:5001/api/upload/item-registration",
    ),
    "UPDATE_DASHBOARD": os.getenv("PUR_INFO_REG_UPDATE_DASHBOARD", "true").lower() in ("1", "true", "yes"),
    "SAVE_LOCAL": os.getenv("PUR_INFO_REG_SAVE_LOCAL", "false").lower() in ("1", "true", "yes"),
    "LOCAL_SAVE_DIR": os.getenv("PUR_INFO_REG_LOCAL_SAVE_DIR", ""),
    "START_DATE": os.getenv("PUR_INFO_REG_START_DATE", ""),
    "END_DATE": os.getenv("PUR_INFO_REG_END_DATE", ""),
    "FILENAME_PREFIX": os.getenv("PUR_INFO_REG_FILENAME_PREFIX", "ItemRegistration"),
    # FIX V26: limit download menu Prod. Reg. Status = 5000 baris (bukan
    # lagi 10.000 seperti menu lama). "Jika di kiri bawah angkanya 5000,
    # maka perpendek range" -> gate >= SEARCH_LIMIT_ROWS memecah range
    # jadi setengah berulang (pola Search Client Odr).
    "SEARCH_LIMIT_ROWS": int(os.getenv("PUR_INFO_REG_SEARCH_LIMIT_ROWS", "5000")),
    "VPN_CHECK_SECONDS": int(os.getenv("SMRO_VPN_CHECK_SECONDS", "60")),
    "RETRY_SECONDS": int(os.getenv("SMRO_RETRY_SECONDS", "300")),
    # Upload failure recovery (NEW)
    "UPLOAD_FAIL_DIR": _safe_fail_dir("failed_uploads_pur"),
    "UPLOAD_MAX_RETRIES": int(os.getenv("PUR_UPLOAD_MAX_RETRIES", "5")),
}

# FIX V26: MIGRASI URL menu lama -> menu baru. .env / scheduled task /
# environment user lama masih memuat URL Process Pur. Info. Reg.
# (page.MG100_006); tanpa migrasi, proses akan membuka MENU YANG SALAH
# (form lama Rgtdt/Purmdid sudah tidak dipakai lagi). URL lama di-swap ke
# menu Prod. Reg. Status + os.environ diperbarui supaya seluruh proses
# (termasuk reopen browser saat VPN reconnect) memakai URL yang benar.
if "page.MG100_006" in CONFIG["PUR_INFO_REG_URL"]:
    logging.warning(
        "  FIX V26: URL menu lama (MG100_006 Process Pur. Info. Reg.) terdeteksi "
        "di env/.env — sumber data Item Registration sudah pindah ke menu "
        "Prod. Reg. Status (MG100_026). URL otomatis dimigrasi."
    )
    CONFIG["PUR_INFO_REG_URL"] = _PROD_REG_STATUS_URL
    os.environ["SMRO_PUR_INFO_REG_URL"] = _PROD_REG_STATUS_URL

smro.CONFIG.update({
    "LOGIN_START_URL": os.getenv("SMRO_LOGIN_START_URL", smro.CONFIG["LOGIN_START_URL"]),
    "USERNAME": os.getenv("SMRO_USERNAME", smro.CONFIG["USERNAME"]),
    "PASSWORD": os.getenv("SMRO_PASSWORD", smro.CONFIG["PASSWORD"]),
    "DOWNLOAD_DIR": DOWNLOAD_DIR,
    "HEADLESS": os.getenv("PUR_INFO_REG_HEADLESS", "true").lower() in ("1", "true", "yes"),
    "KEEP_BROWSER_OPEN_ON_ERROR": os.getenv("SMRO_KEEP_BROWSER_OPEN_ON_ERROR", "false").lower() in ("1", "true", "yes"),
})


def parse_date(value: str, fallback: date | None = None) -> date:
    value = (value or "").strip()
    if not value:
        if fallback is None:
            raise ValueError("Tanggal kosong dan fallback tidak ada")
        return fallback
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Format tanggal tidak valid: {value}. Gunakan YYYY/MM/DD.")


def format_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def get_initial_ranges():
    today = datetime.now().date()
    # FIX V18: Date Range PER-PROSES. Kalau preset selain 'custom' dipilih
    # khusus untuk Prod Reg Status, range mengikuti preset itu saja
    # (dihitung mundur dari hari ini) dan PUR_INFO_REG_START_DATE/END_DATE
    # diabaikan. Kalau 'custom', perilaku lama: tanggal manual dari .env atau
    # default (1 Januari tahun lalu s/d hari ini).
    # FIX V22: REVERT V20 — range TIDAK lagi dipecah per bulan kalender di
    # awal (permintaan user: "kembalikan seperti sebelumnya saja, jangan
    # pecah per bulan"). Selalu SATU batch 'full'; pecah hanya terjadi
    # REAKTIF ketika hasil search >= SEARCH_LIMIT_ROWS (10.000) → range
    # dipecah jadi SETENGAH berulang (mirip logika Search Client Odr).
    preset_start, preset_end, preset_code = resolve_date_range_from_preset(today)
    if preset_start is not None:
        logging.info(
            f"  Date Range preset Prod Reg Status '{preset_code}' (dari Settings, khusus proses ini): "
            f"{format_date(preset_start)} - {format_date(preset_end)}"
        )
        return [(preset_start, preset_end, "full")]
    start = parse_date(CONFIG["START_DATE"], default_start_date(today))
    end = parse_date(CONFIG["END_DATE"], today)
    if start > end:
        raise ValueError("PUR_INFO_REG_START_DATE tidak boleh lebih besar dari PUR_INFO_REG_END_DATE/hari ini")
    return [(start, end, "full")]


def split_range_if_limit(date_from: date, date_to: date, level: str):
    """FIX V22: saat hasil search >= SEARCH_LIMIT_ROWS (default 10.000),
    range tanggal dipecah jadi DUA SETENGAH — bukan per bulan kalender
    (perilaku V20, direvert atas permintaan user) dan bukan tangga tetap
    6/3/1 bulan (perilaku lama). Pola adaptive persis Search Client Odr:
    setengah pertama date_from s/d titik tengah, setengah kedua sisanya;
    kalau setengahnya masih >= limit, ia dipecah lagi jadi setengah
    (berulang lewat antrian batch di process_ranges). Range 1 hari =
    terminal (return None) → download tetap diteruskan dengan warning
    (persis perilaku terminal 1week di Search Client Odr).

    Argumen 'level' dipertahankan demi kompatibilitas pemanggil, tapi
    keputusan pecah kini murni berbasis PANJANG RANGE (jumlah hari)."""
    total_days = (date_to - date_from).days + 1
    if total_days <= 1:
        return None
    mid = date_from + timedelta(days=total_days // 2 - 1)
    return [
        (date_from, mid, "half"),
        (mid + timedelta(days=1), date_to, "half"),
    ]


def visible_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", element)
    time.sleep(0.2)
    try:
        ActionChains(driver).move_to_element(element).pause(0.1).click(element).perform()
    except Exception:
        driver.execute_script(
            "const e=arguments[0]; e.focus?.(); for(const ev of ['mouseover','mousedown','mouseup','click']) "
            "e.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));",
            element,
        )


def context_has_pur_form(driver) -> bool:
    """FIX V26: deteksi form halaman sumber data baru — Prod. Reg. Status
    (komponen ProcessInfoCheckCompView). Elemen kunci dari HTML user:
      - aaaa.ProcessInfoCheckCompView.InputField11  (tanggal awal)
      - aaaa.ProcessInfoCheckCompView.InputField12  (tanggal akhir)
      - aaaa.ProcessInfoCheckCompView.Button        (tombol Search)
    """
    try:
        return bool(driver.execute_script(
            """
            return !!document.getElementById('aaaa.ProcessInfoCheckCompView.InputField11') ||
                   !!document.getElementById('aaaa.ProcessInfoCheckCompView.InputField12') ||
                   !!document.getElementById('aaaa.ProcessInfoCheckCompView.Button');
            """
        ))
    except Exception:
        return False


def switch_to_pur_info_reg_context(driver, timeout: int = 40) -> bool:
    """Switch ke frame halaman Prod. Reg. Status (FIX V26).

    FIX V26: traversal iframe sekarang REKURSIF (depth 3) — struktur frame
    halaman baru (tab_ifrm_MG100_026 -> page frame -> kemungkinan frame
    dalam) tidak lagi dianggap depth-1 seperti menu lama. Kalau frame hasil
    gagal ditemukan, kembali ke default content supaya state driver tidak
    'nyangkut' di frame salah.
    """
    if not smro.switch_to_window_with_url(driver, "svobpp.serveone.co.kr", timeout=timeout):
        return False
    end_time = time.time() + timeout
    last_error = None

    def _walk(depth: int) -> bool:
        try:
            if context_has_pur_form(driver):
                return True
        except Exception as e:
            nonlocal last_error
            last_error = e
        if depth <= 0:
            return False
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(frame)
            except Exception:
                continue
            if _walk(depth - 1):
                return True
            try:
                driver.switch_to.parent_frame()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
        return False

    while time.time() < end_time:
        try:
            driver.switch_to.default_content()
            if _walk(3):
                return True
            driver.switch_to.default_content()
        except Exception as e:
            last_error = e
        time.sleep(0.5)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    logging.error(f"Gagal masuk context Prod. Reg. Status: {last_error}")
    return False


def click_process_pur_menu(driver) -> bool:
    """FIX V26: klik menu **Prod. Reg. Status** di tree menu kiri
    (leaf node di bawah folder Prod. Mgmt)."""
    menu_text = "Prod. Reg. Status"
    try:
        clicked = driver.execute_script(
            """
            const text = arguments[0];
            const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
            try {
                if (window.Ext && Ext.ComponentQuery) {
                    const trees = Ext.ComponentQuery.query('treepanel,treeview');
                    for (const tree of trees) {
                        const store = tree.getStore ? tree.getStore() : (tree.up && tree.up('treepanel')?.getStore?.());
                        const view = tree.getView ? tree.getView() : tree;
                        if (!store || !view) continue;
                        let found = null;
                        store.each(function(rec) {
                            const t = clean(rec.get('text') || rec.get('name') || rec.get('title'));
                            if (t === text) found = rec;
                        });
                        if (!found) continue;
                        try { view.expand?.(found.parentNode); } catch (e) {}
                        const node = view.getNode ? view.getNode(found) : null;
                        if (node) {
                            node.scrollIntoView({block:'center'});
                            node.click();
                            try { view.fireEvent('itemclick', view, found, node, 0, new MouseEvent('click')); } catch (e) {}
                            return true;
                        }
                    }
                }
            } catch (e) {}
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const nodes = Array.from(document.querySelectorAll('span,div,td,tr,a,[role="row"]')).filter(visible);
            for (const el of nodes) {
                if (clean(el.innerText || el.textContent) === text) {
                    const target = el.closest('tr,[role="row"],a,div') || el;
                    target.scrollIntoView({block:'center'});
                    target.click();
                    return true;
                }
            }
            return false;
            """,
            menu_text,
        )
        return bool(clicked)
    except Exception:
        return False


def search_process_pur_menu(driver) -> bool:
    """Use SMRO left Menu Search to reveal Prod. Reg. Status (FIX V26)."""
    menu_text = "Prod. Reg. Status"
    try:
        search_input = driver.execute_script(
            """
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const inputs = Array.from(document.querySelectorAll('input[type="text"],input:not([type])')).filter(visible);
            return inputs.find(e => /menu\\s*search|search/i.test([e.placeholder,e.title,e.id,e.name].filter(Boolean).join(' '))) || null;
            """
        )
        if not search_input:
            return False
        smro.set_field_value(driver, search_input, menu_text)
        time.sleep(0.5)
        clicked = driver.execute_script(
            """
            const input = arguments[0];
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const ir = input.getBoundingClientRect();
            const buttons = Array.from(document.querySelectorAll('button,img,a,div,span,[role="button"]')).filter(visible)
                .map(e => ({el:e, r:e.getBoundingClientRect(), t:[e.id,e.title,e.alt,e.src,e.className,e.innerText,e.textContent].filter(Boolean).join(' ').toLowerCase()}))
                .filter(x => Math.abs((x.r.top+x.r.bottom)/2 - (ir.top+ir.bottom)/2) < 35 && x.r.left > ir.right - 10);
            const btn = buttons.find(x => /search|magnifier|btn|icon/i.test(x.t)) || buttons[0];
            if (btn) {
                btn.el.click();
                return true;
            }
            input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', bubbles:true}));
            return true;
            """,
            search_input,
        )
        if clicked:
            logging.info("  Menu Search Prod. Reg. Status dijalankan")
            time.sleep(2)
            return True
    except Exception as e:
        logging.debug(f"  Menu Search gagal: {e}")
    return False


def expand_pur_info_folder(driver) -> bool:
    """FIX V26: expand folder parent **Prod. Mgmt** di tree menu kiri
    supaya leaf 'Prod. Reg. Status' kelihatan (struktur tree dari HTML
    portal user: Prod. Mgmt -> Request Prod. Reg. / Request Batch Reg. of
    Prod. / Prod. Reg. Status / ...)."""
    try:
        expanded = driver.execute_script(
            """
            const wanted = ['prod. mgmt', 'prod mgmt', 'product mgmt',
                            'product management', 'prod management'];
            const clean = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const compact = s => clean(s).replace(/[.\\s_-]/g, '');
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const match = txt => wanted.some(w => clean(txt).includes(w) || compact(txt).includes(compact(w)));
            const nodes = Array.from(document.querySelectorAll('span,div,td,tr,a,[role="row"]')).filter(visible);
            for (const el of nodes) {
                const txt = clean(el.innerText || el.textContent);
                if (!txt || !match(txt)) continue;
                const row = el.closest('tr,[role="row"],li,div') || el;
                const icons = Array.from(row.querySelectorAll('img,span,div')).filter(visible);
                const expander = icons.find(i => /plus|expand|collapsed|x-tree-elbow-plus|x-tree-expander/i.test([i.className,i.src,i.id].filter(Boolean).join(' '))) || icons[0];
                (expander || row).scrollIntoView({block:'center'});
                (expander || row).click();
                return true;
            }
            return false;
            """
        )
        if expanded:
            logging.info("  Folder Prod. Mgmt diexpand")
            time.sleep(1.5)
        return bool(expanded)
    except Exception as e:
        logging.debug(f"  Expand folder Prod. Mgmt gagal: {e}")
        return False


def open_process_pur_info_reg(driver) -> bool:
    try:
        # FIX V26: sumber data = menu Prod. Reg. Status (PCD page MG100_026,
        # judul tab 'Prod. Reg. Status' — dari HTML portal user:
        # <div id="MG100_026" ...><iframe id="tab_ifrm_MG100_026" ...>).
        logging.info("Membuka menu Prod. Reg. Status...")
        if not smro.switch_to_window_with_url(driver, "svobpp.serveone.co.kr", timeout=30):
            return False

        target_url = CONFIG["PUR_INFO_REG_URL"]
        opened = False
        try:
            opened = driver.execute_script(
                """
                const url = arguments[0];
                if (typeof openTab === 'function') {
                    openTab(url, 'Prod. Reg. Status', 'MG100_026', window.mainUrl || '', 'doRefresh');
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
            logging.info(f"  Direct URL Prod. Reg. Status dibuka: {target_url}")
        else:
            logging.info("  Prod. Reg. Status dibuka via framework tab")

        if switch_to_pur_info_reg_context(driver, timeout=60):
            logging.info("Prod. Reg. Status siap")
            return True

        logging.warning("  Direct open belum menampilkan form. Coba fallback Menu Search.")

        deadline = time.time() + 60
        while time.time() < deadline:
            driver.switch_to.default_content()
            search_process_pur_menu(driver)
            if click_process_pur_menu(driver):
                logging.info("  Menu Prod. Reg. Status diklik")
                break
            expand_pur_info_folder(driver)
            if click_process_pur_menu(driver):
                logging.info("  Menu Prod. Reg. Status diklik setelah expand")
                break
            clicked = False
            for frame in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(frame)
                    search_process_pur_menu(driver)
                    if click_process_pur_menu(driver):
                        logging.info("  Menu Prod. Reg. Status diklik dari iframe")
                        clicked = True
                        break
                    expand_pur_info_folder(driver)
                    if click_process_pur_menu(driver):
                        logging.info("  Menu Prod. Reg. Status diklik dari iframe setelah expand")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
            time.sleep(1)
        else:
            smro.save_debug_artifacts(driver, "prod_reg_status_menu_not_found")
            return False

        if not switch_to_pur_info_reg_context(driver, timeout=60):
            smro.save_debug_artifacts(driver, "prod_reg_status_context_failed")
            return False
        logging.info("Prod. Reg. Status siap")
        return True
    except Exception as e:
        logging.error(f"Gagal membuka Prod. Reg. Status: {e}")
        smro.save_debug_artifacts(driver, "open_prod_reg_status_failed")
        return False


def set_input_by_id(driver, element_id: str, value: str, allow_readonly: bool = False) -> bool:
    """
    Isi field SAP UI5 berdasarkan element ID, tahan terhadap stale element.

    Root cause yang diatasi:
      Implementasi lama memegang `WebElement` di sisi Python via
      `WebDriverWait(...).until(EC.presence_of_element_located(...))`,
      lalu meneruskan reference itu ke `smro.set_field_value`. Jika SAP UI5
      sempat re-render antara `find_element` dan `click` / `execute_script`,
      reference itu jadi stale → `click()` throws StaleElementReferenceException,
      dan fallback JS (`arguments[0].focus()`) juga throw karena masih memakai
      reference yang sama. Akibatnya: log "Field ... gagal diisi: stale element
      reference" padahal field-nya sebenarnya ada.

    Strategi baru (V14):
      1. Pure-JS setter (immune stale): re-locate element di DALAM script via
         `document.getElementById(id)`. Tidak ada Python-side reference yang
         melintasi DOM re-render, jadi stale element tidak mungkin terjadi.
         Dispatch event input/change/blur supaya SAP UI5 binding pick up
         perubahan nilai.
      2. Fallback Selenium (defensive): jika pure-JS gagal (mis. element belum
         ada di DOM), re-find element tiap attempt dengan WebDriverWait, dan
         panggil `smro.set_field_value` dengan element yang baru. Sampai 3x.
      3. Logging warning HOANYA setelah semua upaya gagal — sehingga warning
         transient (yang akan auto-recover di attempt berikutnya) tidak
         muncul di log user.
    """
    # ── Strategy 1: Pure-JS setter (immune dari stale element) ───────────
    # Element di-locate di dalam script, jadi tidak ada jendela waktu antara
    # "find" dan "use" yang bisa disisipi DOM re-render.
    try:
        ok = driver.execute_script(
            """
            const id = arguments[0], val = arguments[1], allow_ro = arguments[2];
            const el = document.getElementById(id);
            if (!el) return false;
            if (allow_ro) {
                el.removeAttribute('readonly');
                try { el.tabIndex = 0; } catch(e) {}
            }
            try { el.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
            try { el.focus(); } catch(e) {}
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            if (setter) setter.call(el, val); else el.value = val;
            el.setAttribute('value', val);
            for (const ev of ['input','change','blur']) {
                try { el.dispatchEvent(new Event(ev, {bubbles:true})); } catch(e) {}
            }
            // verify: el.value bisa di-format ulang oleh SAP, jadi cek setelah
            // strip karakter non-data.
            const norm = s => (s || '').replace(/[^0-9A-Za-z./-]/g, '');
            return norm(el.value) === norm(val);
            """,
            element_id, value, allow_readonly,
        )
        if ok:
            return True
    except Exception as e:
        # Bisa terjadi kalau driver sedang disconnect, atau script timeout.
        # Lanjut ke fallback Selenium.
        logging.info(f"  Field {element_id}: pure-JS setter gagal ({e}); coba Selenium fallback...")

    # ── Strategy 2: Selenium dengan re-find + retry (max 3) ──────────────
    # Setiap attempt re-find element via WebDriverWait supaya selalu fresh.
    # Stale element di attempt ke-N akan di-retry di attempt ke-N+1.
    last_err = None
    for attempt in range(1, 4):
        try:
            el = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.ID, element_id))
            )
            if allow_readonly:
                driver.execute_script(
                    "arguments[0].removeAttribute('readonly'); arguments[0].tabIndex=0;",
                    el,
                )
            smro.set_field_value(driver, el, value)
            return True
        except Exception as e:
            last_err = e
            # stale element reference akan muncul di sini → jeda singkat lalu
            # retry akan re-find element yang baru.
            time.sleep(0.5)

    logging.warning(f"  Field {element_id} gagal diisi setelah 3 percobaan: {last_err}")
    return False


def force_input_value_by_id(driver, element_id: str, value: str) -> bool:
    """Set SAP input value without firing Change submit."""
    try:
        return bool(driver.execute_script(
            """
            const id = arguments[0], val = arguments[1];
            const el = document.getElementById(id);
            if (!el) return false;
            el.removeAttribute('readonly');
            el.readOnly = false;
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            if (setter) setter.call(el, val); else el.value = val;
            el.setAttribute('value', val);
            return el.value === val;
            """,
            element_id,
            value,
        ))
    except Exception as e:
        logging.warning(f"  Field {element_id} gagal diset silent: {e}")
        return False


def wait_for_pur_form_idle(driver, timeout: int = 12):
    deadline = time.time() + timeout
    last_signature = None
    stable_since = None
    while time.time() < deadline:
        if not switch_to_pur_info_reg_context(driver, timeout=5):
            time.sleep(0.3)
            continue
        try:
            loading = driver.execute_script(
                """
                const loaders = Array.from(document.querySelectorAll('#ur-loading,.urLoad,.lsBusyIndicator,.x-mask,.x-mask-msg'));
                const visible = e => {
                    const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
                };
                return loaders.some(visible);
                """
            )
            signature = driver.execute_script(
                """
                const ids = ['aaaa.ProcessInfoCheckCompView.InputField11',
                             'aaaa.ProcessInfoCheckCompView.InputField12',
                             'aaaa.ProcessInfoCheckCompView.Button'];
                return ids.map(id => {
                    const e = document.getElementById(id);
                    return e ? `${id}:${e.value || e.tagName}` : `${id}:missing`;
                }).join('|');
                """
            )
            now = time.time()
            if not loading and signature == last_signature:
                if stable_since is None:
                    stable_since = now
                if now - stable_since >= 1.0:
                    return True
            else:
                stable_since = None
                last_signature = signature
        except Exception:
            pass
        time.sleep(0.3)
    return False


def set_processing_date_range(driver, date_from: date, date_to: date):
    """FIX V26: isi tanggal awal/akhir di form Prod. Reg. Status.

    Dari HTML user (komponen ProcessInfoCheckCompView):
      - tanggal awal : aaaa.ProcessInfoCheckCompView.InputField11
                       (lsdata format "yyyy/MM/dd", value '2026/08/01')
      - tanggal akhir: aaaa.ProcessInfoCheckCompView.InputField12
                       (lsdata format "yyyy/MM/dd", value '2026/09/14')
    Struktur retry + fallback-by-label dari versi lama dipertahankan
    (kebal stale element saat form re-render).
    """
    if not switch_to_pur_info_reg_context(driver, timeout=20):
        raise RuntimeError("Form Prod. Reg. Status tidak aktif")
    # FIX: tunggu form benar-benar idle SEBELUM mulai isi tanggal (sebelumnya
    # cuma ditunggu SESUDAH isi). Form SAPUI5 sering masih re-render sesaat
    # setelah context aktif, jadi elemen yang baru ditemukan langsung jadi
    # "stale" begitu mau dipakai -> log "Gagal ... stale element reference"
    # padahal field-nya sebenarnya ada, cuma timing-nya kepagian.
    logging.info("  Menunggu form siap...")
    if not wait_for_pur_form_idle(driver, timeout=10):
        logging.info("  Form belum sepenuhnya idle, tetap lanjut (ada retry otomatis kalau gagal)")
    from_text = format_date(date_from)
    to_text = format_date(date_to)
    start_field_id = "aaaa.ProcessInfoCheckCompView.InputField11"
    end_field_id = "aaaa.ProcessInfoCheckCompView.InputField12"
    if not set_input_by_id(driver, start_field_id, from_text):
        logging.info("  Menunggu form stabil, mencoba ulang field tanggal awal...")
        wait_for_pur_form_idle(driver, timeout=5)
        if not set_input_by_id(driver, start_field_id, from_text):
            # Fallback: cari field by label. Ini berisiko stale element karena
            # find_input_by_label me-return WebElement yang bisa jadi stale
            # saat smro.set_field_value memakainya. Bungkus dalam retry loop.
            from_field_set = False
            for _attempt in range(3):
                try:
                    field = smro.find_input_by_label(
                        driver,
                        ["Date From", "Start Date", "Reg. Date From", "Register Date From",
                         "From Date", "Registration Date", "Reg. Date"],
                        timeout=8,
                    )
                    smro.set_field_value(driver, field, from_text)
                    from_field_set = True
                    break
                except Exception:
                    # Stale element reference di sini → re-find di attempt berikutnya.
                    time.sleep(0.5)
            if not from_field_set:
                logging.warning("  Field tanggal awal tidak dapat diisi via fallback (stale/missing). Lanjut dengan field end date...")

    end_set = False
    for element_id in (end_field_id,):
        ok = set_input_by_id(driver, element_id, to_text)
        if not ok:
            # FIX: kalau gagal karena form sempat re-render (stale element),
            # tunggu sampai idle lagi lalu coba SEKALI lagi di ID yang sama.
            logging.info(f"  Menunggu form stabil, mencoba ulang field tanggal akhir ({element_id})...")
            wait_for_pur_form_idle(driver, timeout=5)
            ok = set_input_by_id(driver, element_id, to_text)
        if ok:
            end_set = True
            break
    if not end_set:
        try:
            end_field = driver.execute_script(
                """
                const start = document.getElementById('aaaa.ProcessInfoCheckCompView.InputField11');
                if (!start) return null;
                const inputs = Array.from(document.querySelectorAll('input[type="text"],input:not([type])'))
                    .filter(e => e !== start);
                // 1) ID kandidat langsung (pola komponen: InputField12)
                const byId = inputs.find(e => /InputField12/i.test(e.id || ''));
                if (byId) return byId;
                // 2) input di BARIS YANG SAMA dengan tanggal awal, di kanannya
                // (layout user: InputField11 & InputField12 sebaris dalam grid).
                const sr = start.getBoundingClientRect();
                const sameRow = inputs.filter(e => {
                    const r = e.getBoundingClientRect();
                    return Math.abs((r.top + r.bottom) / 2 - (sr.top + sr.bottom) / 2) < 25
                           && r.left >= sr.right - 10;
                });
                sameRow.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                if (sameRow.length) return sameRow[0];
                // 3) input terdekat secara vertikal (fallback terakhir)
                inputs.sort((a, b) => Math.abs(a.getBoundingClientRect().top - sr.top)
                                  - Math.abs(b.getBoundingClientRect().top - sr.top));
                return inputs[0] || null;
                """
            )
            if end_field:
                # Fallback path juga berisiko stale element. Bungkus dalam retry.
                for _attempt in range(3):
                    try:
                        smro.set_field_value(driver, end_field, to_text)
                        end_set = True
                        break
                    except Exception:
                        # Stale element reference → re-find di attempt berikutnya.
                        time.sleep(0.5)
                        try:
                            end_field = driver.execute_script(
                                """
                                const start = document.getElementById('aaaa.ProcessInfoCheckCompView.InputField11');
                                if (!start) return null;
                                const inputs = Array.from(document.querySelectorAll('input[type="text"],input:not([type])'))
                                    .filter(e => e !== start);
                                const byId = inputs.find(e => /InputField12/i.test(e.id || ''));
                                if (byId) return byId;
                                const sr = start.getBoundingClientRect();
                                const sameRow = inputs.filter(e => {
                                    const r = e.getBoundingClientRect();
                                    return Math.abs((r.top + r.bottom) / 2 - (sr.top + sr.bottom) / 2) < 25
                                           && r.left >= sr.right - 10;
                                });
                                sameRow.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                                if (sameRow.length) return sameRow[0];
                                inputs.sort((a, b) => Math.abs(a.getBoundingClientRect().top - sr.top)
                                                  - Math.abs(b.getBoundingClientRect().top - sr.top));
                                return inputs[0] || null;
                                """
                            )
                            if not end_field:
                                break
                        except Exception:
                            break
        except Exception:
            pass
    if end_set:
        logging.info(f"  Processing date: {from_text} - {to_text}")
    else:
        logging.info(f"  Processing date awal: {from_text} (field end date tidak ditemukan)")
    wait_for_pur_form_idle(driver, timeout=15)


def click_search(driver):
    """FIX V26: klik tombol Search di Prod. Reg. Status
    (aaaa.ProcessInfoCheckCompView.Button — caption 'Search').

    Menu ini TIDAK punya field PIC/purchaser (user: "menu ini tidak perlu
    hapus PIC karena tidak ada") — seluruh langkah kosongkan purchaser
    dari menu lama DIHAPUS dari sini."""
    if not switch_to_pur_info_reg_context(driver, timeout=20):
        raise RuntimeError("Form Prod. Reg. Status tidak aktif")
    wait_for_pur_form_idle(driver, timeout=10)
    smro.click_sap_button_by_id(driver, "aaaa.ProcessInfoCheckCompView.Button", timeout=20)
    logging.info("  Tombol Search diklik")


# FIX V26: JS pembaca kandidat teks jumlah data di halaman hasil Prod.
# Reg. Status. Berjalan LINTAS FRAME (same-origin, depth 3) supaya kebal
# struktur frame yang berbeda dari menu lama. Mengumpulkan:
#   1. angka MURNI di elemen leaf area KIRI BAWAH (user: "jika di kiri
#      bawah angkanya 5000") — counter jumlah data khas halaman list
#      ServeOne;
#   2. teks toolbar paging / status bar (pola ExtJS "Displaying items
#      1 - 100 of 5,000", "Total: 5,000", dll).
_PROD_REG_COUNT_TEXTS_JS = """
function collect(doc, depth) {
    const out = [];
    try {
        const visible = e => {
            const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
            return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
        };
        const vh = window.innerHeight || 800, vw = window.innerWidth || 1280;
        const els = Array.from(doc.querySelectorAll('span,div,td,a')).filter(visible);
        for (const e of els) {
            if (e.childElementCount > 0) continue;
            const t = (e.innerText || e.textContent || '').trim();
            if (!t || t.length > 24) continue;
            // angka murni (boleh pemisah ribuan koma/titik) di kiri bawah
            if (/^[0-9][0-9,.]{0,11}$/.test(t)) {
                const r = e.getBoundingClientRect();
                if (r.top > vh * 0.75 && r.left < vw * 0.5) out.push(t);
            }
        }
        const tl = Array.from(doc.querySelectorAll(
            '.x-toolbar-text, .x-paging-info, .x-statusbar, .x-banner-text, [class*="paging"]')).filter(visible);
        for (const e of tl) {
            const t = (e.innerText || e.textContent || '').trim();
            if (t && t.length < 120) out.push(t);
        }
    } catch (err) {}
    if (depth <= 0) return out;
    for (const f of Array.from(doc.querySelectorAll('iframe'))) {
        try {
            const d = f.contentDocument;
            if (d) out.push(...collect(d, depth - 1));
        } catch (err) { /* cross-origin, skip */ }
    }
    return out;
}
return collect(document, 3);
"""


def _parse_count_from_texts(candidates):
    """Parse daftar teks kandidat (angka murni + teks toolbar) jadi int.

    Prioritas (paling spesifik -> paling longgar):
      1. "Displaying ... 1 - 100 of 5,000"  → angka setelah 'of'
      2. "Total: 5,000" / "Total 5.000"
      3. angka murni terbesar di kiri bawah (counter total >= nomor halaman)
    """
    if not candidates:
        return None
    display_of = None
    total = None
    bare_numbers = []
    for text in candidates:
        t = (str(text) or "").strip()
        if not t:
            continue
        m = re.search(
            r"displaying[^0-9]{0,40}[0-9][0-9,.]*\s*[-\u2013~]\s*[0-9][0-9,.]*\s+(?:of|/|out\s+of)\s+([0-9][0-9,.]*)",
            t, flags=re.IGNORECASE,
        )
        if m and display_of is None:
            display_of = m.group(1)
        m = re.search(r"total\s*[:\s]?\s*([0-9][0-9,.]*)", t, flags=re.IGNORECASE)
        if m and total is None:
            total = m.group(1)
        if re.fullmatch(r"[0-9][0-9,.]{0,11}", t):
            bare_numbers.append(t)
    for picked in (display_of, total):
        if picked:
            try:
                return int(picked.replace(",", "").replace(".", ""))
            except ValueError:
                pass
    if bare_numbers:
        try:
            # counter total = angka terbesar (nomor halaman/paging selalu
            # lebih kecil dari total baris)
            return max(int(n.replace(",", "").replace(".", "")) for n in bare_numbers)
        except ValueError:
            pass
    return None


def get_result_count(driver):
    """FIX V26: baca jumlah hasil search halaman Prod. Reg. Status.

    Urutan:
      1. Message bar SAP (pola lama "N items searched" — tetap didukung).
      2. Body text context aktif (pola lama).
      3. BARU: teks counter di KIRI BAWAH + paging toolbar, dibaca LINTAS
         FRAME (same-origin) — limit download menu ini 5000 baris, dan
         angka total itulah yang dipakai gate split range (mirip Search
         Client Odr).
    Return int atau None.
    """
    count = smro.get_search_result_count(driver)
    if count is not None:
        return count
    try:
        text = driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        ) or ""
    except Exception:
        text = ""
    if text:
        for pattern in (
            r"([0-9][0-9,.]*)\s*item\(s\)\s*searched",
            r"([0-9][0-9,.]*)\s+items?\s+searched",
            r"([0-9][0-9,.]*)\s+rows?\s+searched",
            r"([0-9][0-9,.]*)\s+items?\s+found",
            r"([0-9][0-9,.]*)\s+rows?\s+found",
        ):
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            if matches:
                return int(matches[-1].replace(",", "").replace(".", ""))
    # ── FIX V26: kiri bawah + paging toolbar (lintas frame) ──
    try:
        candidates = driver.execute_script(_PROD_REG_COUNT_TEXTS_JS) or []
    except Exception:
        candidates = []
    return _parse_count_from_texts(candidates)


# FIX V26: marker konteks hasil — generik LINTAS FRAME (halaman baru tidak
# diketahui struktur iframe-nya; menu lama memakai aaaa.MainList_1View.
# IFrame + mm.pdk.mg100.mg100_006 yang sudah tidak berlaku).
# Marker kuat (kalau salah satu ada → frame ini frame hasil):
#   1. img dengan src xlsDownload.png (toolbar hasil search)
#   2. tabel Web Dynpro [id$='Table_BasicList-contentTBody'] dengan baris rr
#   3. baris grid ExtJS NON-TREE (tree menu portal juga pakai tr[role=row],
#      jadi baris yang ancestor-nya x-tree-view/treeview-* TIDAK dihitung)
_PUR_RESULT_MARKER_JS = """
try {
    const imgs = document.querySelectorAll('img');
    for (const e of imgs) {
        if (/xlsdownload/i.test(e.getAttribute('src') || '')) return true;
    }
    const tb = document.querySelector("[id$='Table_BasicList-contentTBody']");
    if (tb && tb.querySelectorAll('tr[rr]').length > 0) return true;
    const rows = document.querySelectorAll('tr.x-grid-row, .x-grid-data-row, .x-grid-item');
    if (rows.length > 0) {
        let inTree = false;
        for (const r of rows) {
            if (r.closest && r.closest('.x-tree-view, [id^="treeview-"]')) { inTree = true; break; }
        }
        if (!inTree) return true;
    }
} catch (e) {}
return false;
"""


def switch_to_pur_result_context(driver, timeout: int = 90) -> bool:
    """FIX V26: switch ke frame hasil search — generik + REKURSIF.

    Menu lama: hasil ada di iframe tetap (aaaa.MainList_1View.IFrame).
    Menu baru (Prod. Reg. Status): struktur frame belum terdokumentasi,
    jadi frame hasil dicari lewat MARKER konten (tombol xlsDownload /
    grid baris / tabel Web Dynpro) dengan walk rekursif depth 3 dari
    default content. Kalau tidak ketemu, driver dikembalikan ke frame
    form supaya state tidak nyangkut.
    """
    deadline = time.time() + timeout
    last_error = None

    def _context_has_result(d) -> bool:
        try:
            return bool(d.execute_script(_PUR_RESULT_MARKER_JS))
        except Exception as e:
            nonlocal last_error
            last_error = e
            return False

    def _walk(depth: int) -> bool:
        if _context_has_result(driver):
            return True
        if depth <= 0:
            return False
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(frame)
            except Exception:
                continue
            if _walk(depth - 1):
                return True
            try:
                driver.switch_to.parent_frame()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
        return False

    while time.time() < deadline:
        try:
            if not switch_to_pur_info_reg_context(driver, timeout=10):
                time.sleep(0.5)
                continue
            if _walk(3):
                return True
            # kembali ke frame form sambil menunggu hasil muncul
            switch_to_pur_info_reg_context(driver, timeout=5)
        except Exception as e:
            last_error = e
        time.sleep(0.5)
    logging.warning(f"  Frame hasil Prod. Reg. Status belum siap: {last_error}")
    return False


def pur_result_has_rows(driver) -> bool:
    """FIX V26: cek baris data di context aktif — mendukung grid ExtJS
    (non-tree) DAN tabel Web Dynpro (tr[rr]) supaya kebal perbedaan
    komponen list antar menu."""
    try:
        return bool(driver.execute_script(
            """
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            // Web Dynpro table (pola Search Client Odr)
            const tb = document.querySelector("[id$='Table_BasicList-contentTBody']");
            if (tb) {
                const wrows = Array.from(tb.querySelectorAll('tr')).filter(r =>
                    r.hasAttribute('rr') && r.getAttribute('rr') !== '-1' &&
                    !(r.innerHTML || '').includes('EMPTYROW'));
                if (wrows.some(row => ((row.innerText || row.textContent || '').replace(/\\s+/g, '').length > 0))) {
                    return true;
                }
            }
            // ExtJS grid rows (non-tree — tree menu portal juga tr[role=row])
            const rows = Array.from(document.querySelectorAll('tr.x-grid-row, .x-grid-item, tr.x-grid-data-row')).filter(visible);
            return rows.some(row => {
                if (row.closest && row.closest('.x-tree-view, [id^="treeview-"]')) return false;
                return /\\d{4,}/.test(row.innerText || row.textContent || '');
            });
            """
        ))
    except Exception:
        return False


def wait_for_result(driver, timeout: int = 90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not switch_to_pur_result_context(driver, timeout=5):
            time.sleep(1)
            continue
        count = get_result_count(driver)
        if count is not None:
            return count
        if pur_result_has_rows(driver):
            return None
        time.sleep(1)
    return None


def _pur_download_started(driver, download_dir: Path, before_names: set,
                          before_blobs: set, timeout: float = 12.0,
                          before_handles: frozenset = None) -> bool:
    """Cek apakah klik tombol download benar-benar memicu aktivitas download.

    FIX V16.2: indikator yang dicek (polling ~1 detik):
      - file BARU muncul di folder download (termasuk parsial .crdownload/.tmp)
      - blob: iframe BARU (pola download SAP Web Dynpro)
      - dialog JS confirm/alert yang menghalangi → ditutup otomatis

    FIX V18: indikator tambahan (klik "berhasil" tapi file belum keluar):
      - tab/window BARU terbuka (download via window.open)
      - ExtJS loading mask aktif (.x-mask-msg / .x-mask) → server sedang
        memproses generate Excel — klik TIDAK sia-sia, lanjut tunggu file
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            current = {p.name for p in download_dir.iterdir() if p.is_file()}
            if current - before_names:
                return True
        except Exception:
            pass
        try:
            if set(smro.get_blob_iframes(driver)) - before_blobs:
                return True
        except Exception:
            pass
        # Tab/window baru = download dimulai via window.open
        try:
            if before_handles and set(driver.window_handles) - set(before_handles):
                logging.info("  Tab/window baru terdeteksi (download via window.open)")
                return True
        except Exception:
            pass
        # ExtJS loading mask = server sedang generate file.
        # FIX V27: dicek LINTAS FRAME (depth 3). Struktur iframe menu
        # Prod. Reg. Status bertingkat — mask "Loading..." yang dirender
        # di frame lain (mis. shell portal) tidak terlihat dari frame
        # aktif, sehingga klik dianggap gagal dan tombol diklik LAGI
        # (akibatnya Excel terdownload 2x).
        try:
            mask = driver.execute_script(
                """
                function maskIn(doc) {
                    const m = doc.querySelector('.x-mask-msg, .x-mask-loading, .x-mask');
                    if (!m) return false;
                    const s = window.getComputedStyle(m), r = m.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0;
                }
                function walk(doc, depth) {
                    try { if (maskIn(doc)) return true; } catch (e) {}
                    if (depth <= 0) return false;
                    for (const f of Array.from(doc.querySelectorAll('iframe'))) {
                        try {
                            const d = f.contentDocument;
                            if (d && walk(d, depth - 1)) return true;
                        } catch (e) { /* cross-origin, skip */ }
                    }
                    return false;
                }
                return walk(document, 3);
                """
            )
            if mask:
                logging.info("  Loading mask aktif (lintas frame) — server sedang memproses Excel")
                return True
        except Exception:
            pass
        # Kalau ada dialog konfirmasi JS yang memblokir aksi, tutup otomatis.
        try:
            alert = driver.switch_to.alert
            alert.accept()
            logging.info("  Dialog konfirmasi download ditutup otomatis")
        except Exception:
            pass
        # FIX V20: popup 'Allow' IN-PAGE (dialog ExtJS dengan tombol
        # Allow/Izinkan) juga diklik — pola popup baru yang muncul tepat
        # sebelum download excel dimulai.
        try:
            smro.click_allow_button_in_page(driver)
        except Exception:
            pass
        time.sleep(1.0)
    return False


# FIX V18: selector presisi tombol Excel dari HTML asli yang dilaporkan user:
#   <img class="x-img x-box-item x-toolbar-item x-img-default" id="image-1014"
#        src="/com.sap.portal.resourcerepository/repo/resource.serveone/images/button/xlsDownload.png">
# Atribut paling stabil = SRC (path resource xlsDownload.png) — ID ExtJS
# (image-1014) auto-generated dan bisa berubah tiap render.
_PUR_EXCEL_BUTTON_FINDER_JS = """
const visible = e => {
    const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
};
const attr = e => [e.id, e.className, e.getAttribute('src'), e.alt, e.title,
    e.getAttribute('iconCls'), e.getAttribute('data-qtip')]
    .filter(Boolean).join(' ');
// Prioritas 1 (PALING PRESISI): img dengan src mengandung 'xlsdownload'
// — pola /images/button/xlsDownload.png (dari HTML yang dilaporkan user).
let imgs = Array.from(document.querySelectorAll('img'));
let el = imgs.find(e => /xlsdownload/i.test(e.getAttribute('src') || ''));
// Prioritas 2: img dengan alt/title/iconCls excel-ish
if (!el) el = imgs.find(e => visible(e) && /excel|xls/i.test((e.alt||'') + ' ' + (e.title||'')));
// Prioritas 3: elemen ber-ID mengandung excelDown/xlsdownload (pola ID
// tombol excel di portal ServeOne, cf. aaaa.SearchOrderCompView.excelDownBtn)
if (!el) {
    const byId = Array.from(document.querySelectorAll(
        "[id*='excelDown' i], [id*='xlsdownload' i]")).filter(visible);
    if (byId.length) el = byId[0].closest("a,button,[role='button'],[ct='B'],input,img") || byId[0];
}
if (!el) return null;
return {el: el, desc: attr(el).slice(0, 120)};
"""


def _js_click_variants(driver, el, desc: str, verify=None) -> bool:
    """FIX V18: klik elemen dgn 3 metode JS berbeda — ExtJS kadang tidak
    bereaksi terhadap .click() biasa tapi bereaksi terhadap rangkaian event
    mouse lengkap, atau handler-nya nempel di parent (toolbar item).

    Metode (berurutan):
      1. el.click() native
      2. focus + dispatch mouseover/mousedown/mouseup/click (bubbles)
      3. klik parent element (wrapper toolbar ExtJS)

    FIX V27: SETIAP metode kini diverifikasi SEBELUM metode berikutnya
    dijalankan (parameter `verify` -- callable yang return True kalau ada
    aktivitas download). Versi lama menembakkan 3 klik berturut-turut
    TANPA verifikasi di antaranya; di menu Prod. Reg. Status klik pertama
    yang sudah memicu download (yang lambat memunculkan indikatornya)
    ditumpuk 2 klik lagi sehingga Excel terdownload 2-3 KALI (laporan
    user). Return True begitu verify melaporkan aktivitas download;
    False kalau semua metode sudah dicoba tanpa aktivitas (verify=None =
    perilaku lama: semua metode ditembakkan, return False).
    """
    def _ok() -> bool:
        if verify is None:
            return False
        try:
            return bool(verify())
        except Exception:
            return False

    try:
        driver.execute_script(
            "try{arguments[0].scrollIntoView({block:'center',inline:'center'});}catch(e){}"
            "arguments[0].click();", el)
        logging.info(f"  Klik JS native ({desc})")
    except Exception as e:
        logging.debug(f"  Klik JS native gagal: {e}")
    if _ok():
        logging.info("  Aktivitas download terdeteksi setelah klik JS native — varian klik berikutnya dibatalkan (FIX V27)")
        return True
    try:
        driver.execute_script(
            """
            const e = arguments[0];
            try { e.scrollIntoView({block:'center'}); } catch(err) {}
            try { e.focus && e.focus(); } catch(err) {}
            for (const ev of ['mouseover','mousedown','mouseup','click']) {
                e.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
            }
            """, el)
        logging.info(f"  Klik JS full mouse events ({desc})")
    except Exception as e:
        logging.debug(f"  Klik JS mouse events gagal: {e}")
    if _ok():
        logging.info("  Aktivitas download terdeteksi setelah klik JS mouse events — varian klik berikutnya dibatalkan (FIX V27)")
        return True
    try:
        driver.execute_script(
            """
            const e = arguments[0];
            const p = e.parentElement || e.closest('.x-toolbar-item, .x-box-item');
            if (p) {
                try { p.scrollIntoView({block:'center'}); } catch(err) {}
                p.click();
                for (const ev of ['mouseover','mousedown','mouseup','click']) {
                    p.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window}));
                }
            }
            """, el)
        logging.info(f"  Klik parent toolbar item ({desc})")
    except Exception as e:
        logging.debug(f"  Klik parent gagal: {e}")
    if _ok():
        logging.info("  Aktivitas download terdeteksi setelah klik parent — varian klik berikutnya dibatalkan (FIX V27)")
        return True
    return False


def _crossframe_find_and_click_excel(driver) -> str:
    """FIX V18: cari tombol Excel di SEMUA frame (top + iframe same-origin,
    rekursif 3 level) langsung dari default content, lalu klik via JS di
    frame tempat tombol berada.

    Kenapa: tombol download bisa saja TIDAK berada di iframe hasil search
    (aaaa.MainList_1View.IFrame) yang dipakai switch_to_pur_result_context —
    strategi lama hanya mencari di context itu, jadi kalau tombol ada di
    frame lain (mis. frame form utama), tidak pernah ketemu → "tombol
    download tidak terklik". Semua iframe portal ini same-origin
    (svobpp.serveone.co.kr) jadi DOM-nya bisa diakses lintas frame via JS.

    Return deskripsi frame+elemen kalau tombol ditemukan (dan sudah diklik),
    atau string kosong kalau tidak ketemu.

    FIX V27: tombol hanya diklik SEKALI (el.click() tunggal). Versi lama
    menembakkan el.click() + rangkaian dispatchEvent + parent.click()
    sekaligus dalam satu script — di menu Prod. Reg. Status yang sudah
    bereaksi terhadap klik pertama, pemicu sisanya membuat Excel
    terdownload ULANG (file kembar — laporan user "excel terdownload
    2 kali"). Eskalasi metode klik tetap tersedia di strategi
    click_download berikutnya kalau klik tunggal ini tidak memicu apa pun.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        result = driver.execute_script(
            """
            function findIn(doc) {
                const imgs = Array.from(doc.querySelectorAll('img'));
                let el = imgs.find(e => /xlsdownload/i.test(e.getAttribute('src') || ''));
                if (!el) el = imgs.find(e => /excel|xls/i.test((e.alt||'') + ' ' + (e.title||'')));
                if (!el) {
                    const byId = doc.querySelector("[id*='excelDown' i], [id*='xlsdownload' i]");
                    if (byId) el = byId.closest("a,button,[role='button'],img") || byId;
                }
                return el || null;
            }
            function walk(doc, depth, path) {
                try {
                    const el = findIn(doc);
                    if (el) return {el: el, path: path};
                } catch (e) {}
                if (depth <= 0) return null;
                for (const f of Array.from(doc.querySelectorAll('iframe'))) {
                    try {
                        const d = f.contentDocument;
                        if (!d) continue;
                        const r = walk(d, depth - 1, path + '/' + (f.id || f.name || f.src.slice(-30) || '?'));
                        if (r) return r;
                    } catch (e) {}
                }
                return null;
            }
            const r = walk(document, 3, 'top');
            if (!r) return '';
            const el = r.el;
            try { el.scrollIntoView({block:'center'}); } catch (e) {}
            try { el.focus && el.focus(); } catch (e) {}
            // FIX V27: SATU klik saja — tanpa rangkaian event mouse
            // tambahan dan tanpa klik parent (pemicu ganda = file kembar).
            try { el.click(); } catch (e) {}
            return r.path + ' | id=' + (el.id || '-') + ' | src=' + (el.getAttribute('src') || '-');
            """
        )
        return (result or "").strip()
    except Exception as e:
        logging.debug(f"  Scan lintas-frame gagal: {e}")
        return ""


def click_download(driver, timeout: int = 25, download_dir: Path = None,
                   before_files=None, before_blobs=None) -> bool:
    """Klik tombol Excel download di hasil search Prod Reg Status.

    FIX V18 — tombol download (HTML asli dari user):
      <img class="x-img x-box-item x-toolbar-item x-img-default" id="image-1014"
           src="/com.sap.portal.resourcerepository/.../button/xlsDownload.png">

    Strategi (tiap klik DIVERIFIKASI — kalau tidak ada aktivitas download
    dalam ~12 detik, eskalasi metode klik / strategi berikutnya):
      1. Selector presisi src='xlsDownload' (JS _PUR_EXCEL_BUTTON_FINDER_JS —
         paling stabil; ID ExtJS image-1014 auto-generated bisa berubah
         tiap render).
         Metode klik eskalatif: ActionChains nyata → JS native click →
         JS full mouse events → klik parent toolbar item.
      2. ID kandidat (image-1014) + verifikasi atribut (V16.2) + eskalasi.
      3. Scan LINTAS FRAME: cari tombol di semua iframe same-origin lalu
         klik via JS di frame tsb — menutup celah tombol tidak berada di
         iframe hasil search.
      4. ExtJS ComponentQuery (handler di level komponen).
      5. DOM scan presise + fallback luas (V16.2).
    """
    # Konteks verifikasi aktivitas download (opsional untuk backward-compat).
    verify_ctx = None
    if download_dir is not None and before_files is not None:
        try:
            verify_ctx = (
                Path(download_dir),
                {Path(p).name for p in before_files},
                set(before_blobs or []),
            )
        except Exception:
            verify_ctx = None

    def _verify(timeout_s: float = 12.0) -> bool:
        """True kalau ada aktivitas download (file/blob/mask/tab baru).
        Guard exception: driver bermasalah / transient error dianggap 'belum
        mulai' (False) supaya eskalasi metode klik & strategi berikutnya
        tetap jalan — bukan melempar exception keluar strategi."""
        if verify_ctx is None:
            return True
        ddir, before_names, before_blobs_set = verify_ctx
        try:
            handles = frozenset(driver.window_handles)
        except Exception:
            handles = None
        try:
            return _pur_download_started(
                driver, ddir, before_names, before_blobs_set,
                timeout=timeout_s, before_handles=handles,
            )
        except TypeError:
            # kompatibilitas signature lama (tanpa before_handles)
            try:
                return _pur_download_started(
                    driver, ddir, before_names, before_blobs_set,
                    timeout=timeout_s,
                )
            except Exception:
                return False
        except Exception:
            return False

    def _click_and_verify(el, strategy_desc):
        """Klik dgn eskalasi metode; verifikasi download SETELAH tiap metode.
        Return True begitu salah satu metode memicu aktivitas download.

        FIX V27 (anti file Excel kembar / 'terdownload 2 kali'):
        pre-check 'sudah ada aktivitas?' SEBELUM klik (strategi sebelumnya
        bisa sudah memicu download yang lambat memunculkan indikatornya)
        + verify diteruskan ke _js_click_variants supaya berhenti di
        varian pertama yang berhasil.
        """
        if _verify(timeout_s=2.0):  # FIX V27: download sudah jalan? jangan klik lagi
            logging.info(f"  Aktivitas download sudah terdeteksi SEBELUM klik ({strategy_desc}) — klik dibatalkan (FIX V27)")
            return True
        # ── Metode 1: klik nyata (ActionChains) — event mouse OS-level ──
        try:
            visible_click(driver, el)
            logging.info(f"  Tombol download diklik — {strategy_desc} [klik nyata]")
        except Exception as e:
            logging.warning(f"  Klik nyata gagal ({strategy_desc}): {e}")
        if _verify():
            return True
        logging.warning(f"  Klik nyata via {strategy_desc} tidak memicu download — eskalasi ke klik JS...")

        # ── Metode 2-4: variasi klik JS — FIX V27: verify diteruskan supaya
        # berhenti di varian pertama yang memicu aktivitas download ──
        if _js_click_variants(driver, el, strategy_desc, verify=lambda: _verify(8.0)):
            return True
        if _verify():
            return True
        logging.warning(f"  Klik via {strategy_desc} tidak memicu download — coba strategi lain...")
        return False

    if not switch_to_pur_result_context(driver, timeout=30):
        return False

    # ── Strategi 1: selector presisi src='xlsDownload' (FIX V18) ──
    try:
        found = driver.execute_script(_PUR_EXCEL_BUTTON_FINDER_JS)
        if found and found.get("el"):
            desc = found.get("desc") or "img xlsDownload"
            logging.info(f"  Tombol Excel ditemukan (selector presisi): {desc}")
            if _click_and_verify(found["el"], f"selector presisi src xlsDownload"):
                return True
        else:
            logging.info("  Tombol dgn src xlsDownload tidak ada di context hasil search")
    except Exception as e:
        logging.debug(f"  Strategi selector presisi gagal: {e}")

    # ── Strategi 2: ID kandidat + VERIFIKASI ATRIBUT (FIX V16.2) ──
    # FIX V26: ID tombol excel di halaman Prod. Reg. Status = image-1013
    # (dari HTML user); image-1014 = ID halaman menu lama — keduanya tetap
    # dicoba karena ID ExtJS auto-generated, atribut diverifikasi dulu.
    for element_id in ("image-1013", "image-1014", "aaaa.MainCompView.image-1013"):
        try:
            if not switch_to_pur_result_context(driver, timeout=10):
                return False
            el = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.ID, element_id)))
            if not el.is_displayed():
                continue
            # ID ExtJS auto-generated bisa menempel ke gambar mana pun —
            # pastikan elemen ini BENAR-BENAR tombol excel sebelum diklik.
            try:
                attrs = (driver.execute_script(
                    "const e=arguments[0]; return [e.id||'', e.className||'', e.src||'', "
                    "e.alt||'', e.title||'', e.getAttribute('iconCls')||''].join('|');",
                    el,
                ) or "").lower()
            except Exception:
                attrs = ""
            if attrs and re.search(r"xls|excel|download", attrs):
                if _click_and_verify(el, f"ID {element_id} — atribut cocok: {attrs[:80]}"):
                    return True
            else:
                logging.info(
                    f"  Elemen {element_id} ada tapi bukan tombol download "
                    f"(atribut: {attrs[:80] or 'tidak terbaca'}) — dilewati"
                )
        except Exception:
            continue

    # ── Strategi 3: scan LINTAS FRAME + klik JS (FIX V18) ──
    # FIX V27: pre-check — kalau strategi 1/2 sudah memicu download yang
    # indikatornya baru terlihat sekarang, JANGAN klik tombol lagi
    # (klik kedua di atas download yang berjalan = file Excel kembar).
    if _verify(timeout_s=2.0):
        logging.info("  Aktivitas download sudah terdeteksi — scan lintas frame dilewati (FIX V27)")
        return True
    frame_desc = _crossframe_find_and_click_excel(driver)
    if frame_desc:
        logging.info(f"  Tombol ditemukan di frame lain: {frame_desc}")
        if _verify(timeout_s=15.0):
            logging.info(f"  Tombol download diklik (lintas frame: {frame_desc})")
            return True
        logging.warning("  Klik lintas-frame tidak memicu download — coba strategi lain...")

    # ── Strategi 4: ExtJS ComponentQuery (handler di level komponen) ──
    try:
        if not switch_to_pur_result_context(driver, timeout=10):
            return False
        el = WebDriverWait(driver, 8).until(lambda d: d.execute_script(
            """
            try {
                if (!(window.Ext && Ext.ComponentQuery)) return null;
                const comps = Ext.ComponentQuery.query('image, button, tool, component');
                for (const c of comps) {
                    const cfg = c.initialConfig || {};
                    const s = [c.iconCls||'', c.src||'', c.altText||'', c.tooltip||'',
                               c.text||'', c.itemId||'', cfg.iconCls||'', cfg.altText||'',
                               cfg.title||'', cfg.tooltip||'']
                        .filter(Boolean).join(' ').toLowerCase();
                    if (/xls|excel|download/.test(s)) {
                        const dom = c.el && c.el.dom;
                        if (dom) return dom;
                    }
                }
            } catch(e) {}
            return null;
            """
        ) or False)
        if _click_and_verify(el, "ExtJS ComponentQuery"):
            return True
    except Exception:
        pass

    # ── Strategi 5: DOM scan presisi (img + id pattern) ──
    try:
        if not switch_to_pur_result_context(driver, timeout=10):
            return False
        el = WebDriverWait(driver, timeout).until(lambda d: d.execute_script(
            """
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            const attr = e => [e.id, e.className, e.getAttribute('src'), e.alt, e.title,
                e.getAttribute('iconCls'), e.getAttribute('data-qtip')]
                .filter(Boolean).join(' ');
            // Prioritas 1: img dengan src/alt/class excel-ish (ikon tombol excel).
            const imgs = Array.from(document.querySelectorAll('img')).filter(visible)
                .filter(e => /xls|excel|download/i.test(attr(e)));
            if (imgs.length) return imgs[0];
            // Prioritas 2: elemen ber-ID mengandung excelDown/xlsdownload (pola ID
            // tombol excel di portal ServeOne, cf. aaaa.SearchOrderCompView.excelDownBtn).
            const byId = Array.from(document.querySelectorAll(
                "[id*='excelDown' i], [id*='xlsdownload' i], [id*='exceldown' i]"))
                .filter(visible);
            if (byId.length) return byId[0].closest("a,button,[role='button'],[ct='B'],input") || byId[0];
            // Prioritas 3: button/anchor dengan teks/iconCls excel-ish.
            const els = Array.from(document.querySelectorAll(
                "button,a,[role='button'],[ct='B'],span.x-btn-icon,div.x-btn"))
                .filter(visible).filter(e => /xlsdownload|excel/i.test(attr(e)));
            return els[0] || null;
            """
        ) or False)
        if _click_and_verify(el, "DOM scan presisi"):
            return True
    except Exception:
        pass

    # ── Strategi 6: fallback lama (regex longgar) — paling akhir ──
    try:
        if not switch_to_pur_result_context(driver, timeout=10):
            return False
        el = WebDriverWait(driver, timeout).until(lambda d: d.execute_script(
            """
            const visible = e => {
                const s = window.getComputedStyle(e), r = e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            };
            return Array.from(document.querySelectorAll('img,button,a,[role="button"],[ct="B"],div,span'))
                .find(e => visible(e) && /xlsdownload|excel|download/i.test([e.id,e.src,e.title,e.alt,e.innerText,e.textContent].filter(Boolean).join(' '))) || null;
            """
        ) or False)
        if _click_and_verify(el, "fallback luas"):
            return True
    except Exception as e:
        logging.error(f"  Tombol download tidak ditemukan: {e}")
        return False

    logging.error("  Tombol download tidak merespons klik dari semua strategi")
    return False


def upload_to_dashboard(file_path: Path, max_retries: int = None) -> bool:
    """Upload Excel ke dashboard dengan exponential backoff.
    Saat gagal, file dipindah ke failed_uploads_pur/ untuk retry di run berikutnya.
    Log messages mengandung keyword "upload berhasil" / "upload gagal" / "upload http"
    supaya GUI parser di app.py menampilkan status upload ke user.
    """
    if max_retries is None:
        max_retries = CONFIG.get("UPLOAD_MAX_RETRIES", 5)
    backoff_5xx = [15, 30, 60, 120, 180]
    backoff_429 = [30, 60, 120, 240, 300]
    backoff_other = [15, 20, 30, 60, 60]
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            logging.info(f"  Local Import Item Registration: {file_path.name} (attempt {attempt}/{max_retries})")
            with file_path.open("rb") as f:
                # FIX V25: POST lewat net_tls — kebal proxy korporat TLS-interception
                # (upload gagal berulang padahal endpoint tidak berubah).
                # FIX V35: sinyal sync (?sync=1 + header X-Sync-Upload: 1) —
                # forward-compatible; dashboard lama mengabaikan (tetap 202 queued),
                # dashboard baru memproses langsung sebelum membalas respons.
                _base = str(CONFIG["DASHBOARD_API"])
                from _paths import assert_local_dashboard_api
                assert_local_dashboard_api(_base)
                _sep = "&" if "?" in _base else "?"
                resp = net_tls.tls_resilient_post(
                    f"{_base}{_sep}sync=1",
                    files={"file": (file_path.name, f, smro.upload_mime_type(file_path))},
                    headers={"X-Sync-Upload": "1"},
                    timeout=CONFIG.get("UPLOAD_HTTP_TIMEOUT_S", 600),
                )
            elapsed = time.time() - t0
            if 200 <= resp.status_code < 300:
                logging.info(f"  Upload berhasil HTTP {resp.status_code} ({elapsed:.1f}s)")
                return True
            # 5xx retryable
            if resp.status_code >= 500:
                body_preview = resp.text[:300].replace("\n", " ").replace("\r", "")
                server = resp.headers.get("Server", "?")
                if attempt < max_retries:
                    delay = backoff_5xx[min(attempt - 1, len(backoff_5xx) - 1)]
                    logging.warning(
                        f"  Upload gagal HTTP {resp.status_code} ({elapsed:.1f}s, server={server}). "
                        f"Body: {body_preview[:150]}. "
                        f"Retry dalam {delay}s... (attempt {attempt}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                logging.error(
                    f"  Upload gagal HTTP {resp.status_code} setelah {max_retries}x. "
                    f"Body: {body_preview}"
                )
                # PINDAHKAN file ke failed_uploads_pur/ (BUKAN dihapus)
                try:
                    fail_dir = CONFIG.get("UPLOAD_FAIL_DIR")
                    if fail_dir:
                        fail_dir.mkdir(parents=True, exist_ok=True)
                        dest = fail_dir / file_path.name
                        if dest.exists():
                            dest = fail_dir / f"{file_path.stem}_{int(time.time())}{file_path.suffix}"
                        file_path.rename(dest)
                        logging.info(f"  File gagal upload dipindah ke: {dest}")
                except Exception as mv_err:
                    logging.error(f"  Gagal pindah file ke failed_uploads_pur/: {mv_err}")
                return False
            # 429 rate limited
            if resp.status_code == 429:
                if attempt < max_retries:
                    delay = backoff_429[min(attempt - 1, len(backoff_429) - 1)]
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try: delay = max(delay, int(retry_after))
                        except Exception: pass
                    logging.warning(f"  Upload gagal HTTP 429 (rate limited), retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                    time.sleep(delay)
                    continue
                logging.error(f"  Upload gagal HTTP 429 setelah {max_retries} percobaan")
                return False
            # 4xx lainnya (permanent)
            logging.error(f"  Upload gagal HTTP {resp.status_code} (permanent): {resp.text[:300]}")
            return False
        except requests.exceptions.Timeout:
            elapsed = time.time() - t0
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                logging.warning(f"  Upload timeout ({elapsed:.1f}s). Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  Upload timeout setelah {max_retries} percobaan ({elapsed:.1f}s)")
            return False
        except requests.exceptions.ConnectionError as e:
            if attempt < max_retries:
                delay = backoff_other[min(attempt - 1, len(backoff_other) - 1)]
                err_short = str(e).replace("\n", " ")[:200]
                logging.warning(f"  Upload gagal koneksi: {err_short}. Retry dalam {delay}s... (attempt {attempt}/{max_retries})")
                time.sleep(delay)
                continue
            logging.error(f"  Upload gagal koneksi setelah {max_retries}x: {e}")
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

def _wait_vpn_reconnect_pur(interval: int = 30, max_wait: float = None) -> bool:
    """Tunggu sampai VPN tersambung kembali, cek tiap interval detik.

    FIX V23: sebelumnya loop INFINITE — VPN yang tidak pernah kembali
    membuat worker PUR menggantung selamanya (log tetap bertambah tiap
    30s sehingga watchdog GUI tidak menyadarinya) dan Run All sequential
    MACET di step PUR sampai hard timeout 1 jam. Sekarang dibatasi
    max_wait (default env PUR_INFO_REG_VPN_MAX_WAIT_SECONDS, 900s /
    15 menit): return True kalau VPN kembali, False kalau menyerah."""
    if max_wait is None:
        try:
            max_wait = float(os.getenv('PUR_INFO_REG_VPN_MAX_WAIT_SECONDS', '900'))
        except Exception:
            max_wait = 900.0
    logging.info(f"  Menunggu VPN tersambung kembali (cek tiap {interval}s, maks {int(max_wait)}s)...")
    deadline = time.time() + max_wait
    while True:
        time.sleep(interval)
        if smro.check_vpn_connection():
            logging.info("  VPN terhubung kembali.")
            return True
        if time.time() >= deadline:
            logging.error(
                f"  VPN BELUM kembali setelah {int(max_wait)}s. Berhenti menunggu — "
                f"batch ini dilewati supaya proses berikutnya tetap jalan."
            )
            return False
        logging.info(f"  Masih menunggu VPN... (cek lagi dalam {interval}s)")


def _reopen_browser_pur() -> object:
    """Buka ulang browser dan navigasi kembali ke halaman Prod Reg Status.
    Return driver baru kalau berhasil, None kalau gagal."""
    try:
        driver = smro.setup_driver(download_dir=DOWNLOAD_DIR)
        if not smro.login_to_portal(driver):
            smro.safe_driver_quit(driver)  # FIX V16.2: anti spam + orphan cleanup
            return None
        smro.close_popup_if_exists(driver)
        if not smro.navigate_to_smro(driver):
            smro.safe_driver_quit(driver)  # FIX V16.2
            return None
        if not open_process_pur_info_reg(driver):
            smro.safe_driver_quit(driver)  # FIX V16.2
            return None
        logging.info("  Browser berhasil dibuka ulang, siap lanjut dari range terakhir.")
        return driver
    except Exception as e:
        logging.error(f"  Gagal reopen browser PUR: {e}")
        return None


def _sweep_orphan_downloads(download_dir: Path, run_start_names: set) -> int:
    """FIX V27: hapus sisa file download yang TIDAK terpakai dari folder kerja.

    Kenapa ada: laporan user "di menu ini excel terdownload 2 kali" — file
    kembar bisa tertinggal di folder download dari (a) tombol download
    yang terpicu lebih dari satu kali (sebelum FIX V27), atau (b) download
    via blob:iframe yang di-fetch manual pada saat Chrome juga menyimpan
    file yang sama secara native. File batch yang SAH selalu sudah
    di-rename -> upload -> dihapus, atau dipindahkan ke failed_uploads_pur/
    jadi file Excel/parsial APA PUN yang muncul selama run dan masih
    tersisa di folder ini adalah kembaran yang tidak terpakai.

    File yang sudah ada SEBELUM run dimulai (run_start_names) TIDAK
    disentuh — itu milik user/run sebelumnya.
    Return jumlah file yang dihapus.
    """
    removed = 0
    try:
        for p in list(download_dir.iterdir()):
            try:
                if not p.is_file() or p.name in run_start_names:
                    continue
                if p.suffix.lower() not in {".xlsx", ".xls", ".csv",
                                            ".crdownload", ".tmp", ".part"}:
                    continue
                p.unlink()
                removed += 1
                logging.warning(f"  FIX V27: sisa download duplikat dihapus: {p.name}")
            except Exception as e:
                # file sedang ditulis Chrome (locked di Windows) — di-cache,
                # dicoba lagi pada sweep berikutnya (per batch / akhir run)
                logging.debug(f"  Gagal hapus sisa download {p.name}: {e}")
    except Exception:
        pass
    return removed


def process_ranges(driver) -> bool:
    pending = get_initial_ranges()
    uploaded = []
    # FIX V27: snapshot nama file saat run dimulai — dasar sweep sisa
    # download duplikat (file yang muncul SELAMA run dan tidak terpakai).
    try:
        run_start_names = {p.name for p in DOWNLOAD_DIR.iterdir() if p.is_file()}
    except Exception:
        run_start_names = set()
    logging.info(
        f"  FIX V22/V26: {len(pending)} batch awal "
        f"({format_date(pending[0][0])} - {format_date(pending[-1][1])}) dari menu "
        "Prod. Reg. Status. Range TIDAK dipecah per bulan (revert V20) — "
        "download SATU range penuh. Split otomatis jika hasil (angka di kiri "
        f"bawah) >= {CONFIG['SEARCH_LIMIT_ROWS']} rows: "
        "range dipecah jadi SETENGAH berulang (mirip Search Client Odr)."
    )
    MAX_VPN_RETRIES = 3  # maks berapa kali reconnect VPN per sesi

    while pending:
        # FIX V27: bersihkan sisa download kembar dari batch sebelumnya
        # SEBELUM batch ini diproses — mencegah file kembar tersisa salah
        # dikenali sebagai hasil batch ini dan menjaga folder kerja bersih
        # (user melihat 1 file Excel per batch, bukan 2).
        _sweep_orphan_downloads(DOWNLOAD_DIR, run_start_names)
        date_from, date_to, level = pending[0]
        label = f"{format_date(date_from)} - {format_date(date_to)}"
        logging.info(f"  Processing date: {format_date(date_from)} - {format_date(date_to)}")

        try:
            set_processing_date_range(driver, date_from, date_to)
            # FIX V26: menu Prod. Reg. Status TIDAK punya field PIC —
            # clear_purchaser_fields() dari menu lama tidak lagi dipanggil.
            click_search(driver)
            time.sleep(2)
            result_count = wait_for_result(driver, timeout=120)
            has_rows = pur_result_has_rows(driver)

            if result_count is not None:
                logging.info(f"  Hasil search: {result_count} rows")

            if not has_rows and (result_count is None or result_count == 0):
                logging.warning(f"  Tidak ada data untuk batch {label}, dilewati")
                pending.pop(0)
                continue

            if result_count is None:
                result_count = 0
                logging.warning("  Result count tidak terbaca, tabel ada data. Lanjut download.")

            if result_count >= CONFIG["SEARCH_LIMIT_ROWS"]:
                split_ranges = split_range_if_limit(date_from, date_to, level)
                if split_ranges:
                    pending.pop(0)
                    pending[0:0] = split_ranges
                    h1 = f"{format_date(split_ranges[0][0])} - {format_date(split_ranges[0][1])}"
                    h2 = f"{format_date(split_ranges[1][0])} - {format_date(split_ranges[1][1])}"
                    logging.warning(
                        f"  Batch {label} mencapai limit {result_count} rows. "
                        f"Dipecah jadi setengah: [{h1}] dan [{h2}]."
                    )
                    continue
                logging.warning("  Range 1 hari masih >= limit. Download tetap dilanjutkan.")

            before_files = list(DOWNLOAD_DIR.iterdir())
            before_blobs = smro.get_blob_iframes(driver)
            # FIX V16.2: klik multi-strategy + verifikasi download benar-benar
            # dimulai (file/blob baru). Kalau tombol salah klik, langsung
            # dicoba strategi lain — tidak lagi menunggu 600 detik sia-sia.
            if not click_download(driver, timeout=25, download_dir=DOWNLOAD_DIR,
                                  before_files=before_files, before_blobs=before_blobs):
                smro.save_debug_artifacts(driver, f"pur_download_button_failed_{date_from.strftime('%Y%m%d')}")
                pending.pop(0)
                continue

            logging.info(f"  Menunggu file Excel batch {label}...")
            xl_file = smro.wait_for_new_download(DOWNLOAD_DIR, before_files, timeout=600, driver=driver, before_blobs=before_blobs)
            if not xl_file:
                logging.error(f"  File Excel tidak muncul untuk batch {label}")
                smro.save_debug_artifacts(driver, f"pur_download_timeout_{date_from.strftime('%Y%m%d')}")
                pending.pop(0)
                continue

            # FIX V27: visibilitas diagnosis — kalau ada lebih dari 1 file
            # baru setelah klik download, itu tanda download terpicu 2x;
            # kembarannya dibersihkan oleh _sweep_orphan_downloads.
            try:
                before_names_batch = {Path(b).name for b in before_files}
                new_now = [p.name for p in DOWNLOAD_DIR.iterdir()
                           if p.is_file() and p.name not in before_names_batch]
                if len(new_now) > 1:
                    logging.warning(
                        f"  Terdeteksi {len(new_now)} file baru setelah download "
                        f"(kemungkinan download ganda): {', '.join(new_now[:4])}"
                    )
            except Exception:
                pass

            target_name = f"{CONFIG['FILENAME_PREFIX']}_{date_from.strftime('%Y%m%d')}_{date_to.strftime('%Y%m%d')}_{level}{xl_file.suffix}"
            target_path = xl_file.parent / target_name
            if target_path.exists():
                target_path = xl_file.parent / f"{target_path.stem}_{int(time.time())}{target_path.suffix}"
            xl_file.rename(target_path)
            logging.info(f"  File diterima: {target_path.name}")

            ok = True
            if CONFIG["SAVE_LOCAL"]:
                ok = save_local_copy(target_path, CONFIG["LOCAL_SAVE_DIR"]) and ok
            if CONFIG["UPDATE_DASHBOARD"]:
                # Upload tetap LANGSUNG per batch setelah filenya diterima
                # (bukan ditumpuk di akhir) — perilaku per-batch ini tetap
                # dipertahankan dari V20; yang direvert hanya pecah per bulan.
                logging.info(f"  Upload dashboard: {target_name}...")
                ok = upload_to_dashboard(target_path) and ok
            else:
                logging.info("  Upload dashboard dilewati sesuai setting")
            try:
                target_path.unlink()
                logging.info(f"  File kerja dihapus: {target_path.name}")
            except Exception as e:
                logging.warning(f"  Gagal hapus file kerja {target_path.name}: {e}")

            pending.pop(0)
            if ok:
                uploaded.append(target_name)
                logging.info(f"  Batch selesai: {target_name} ({result_count} rows)")
            else:
                logging.error(f"  Output gagal untuk batch {label}, lanjut batch berikutnya")

        except Exception as e:
            logging.error(f"  Batch {label} gagal: {e}")

            # FIX: cek apakah kegagalan disebabkan VPN/koneksi putus
            if not smro.check_vpn_connection():
                vpn_retry_count = getattr(process_ranges, '_vpn_retry_count', 0) + 1
                process_ranges._vpn_retry_count = vpn_retry_count

                if vpn_retry_count > MAX_VPN_RETRIES:
                    logging.error(
                        f"  VPN sudah reconnect {MAX_VPN_RETRIES}x tapi proses masih gagal. "
                        f"Menyerah pada batch {label}."
                    )
                    pending.pop(0)
                    continue

                logging.warning(
                    f"  VPN/koneksi terputus saat batch {label} "
                    f"(reconnect ke-{vpn_retry_count}/{MAX_VPN_RETRIES}). "
                    f"Menunggu VPN kembali lalu lanjut dari range ini..."
                )
                # FIX V23: VPN wait dibatasi max_wait — kalau menyerah, batch
                # ini dilewati supaya worker PUR tetap bisa selesai/exit dan
                # Run All lanjut ke proses berikutnya.
                if not _wait_vpn_reconnect_pur():
                    logging.error(
                        f"  VPN tidak kembali dalam batas waktu. Batch {label} dilewati "
                        f"supaya proses berikutnya tetap jalan."
                    )
                    pending.pop(0)
                    continue

                # Tutup driver lama yang sudah tidak valid
                # FIX V16.2: safe_driver_quit — anti urllib3 spam + orphan cleanup
                smro.safe_driver_quit(driver)

                # Buka ulang browser dan navigasi kembali ke form PUR
                new_driver = _reopen_browser_pur()
                if new_driver is None:
                    logging.error(
                        f"  Gagal reopen browser setelah VPN reconnect. "
                        f"Range {label} dilewati."
                    )
                    pending.pop(0)
                else:
                    driver = new_driver
                    logging.info(f"  Lanjut dari range {label} tanpa restart dari awal.")
                    # TIDAK pop pending — range yang sama akan dicoba ulang di iterasi berikutnya
                continue
            else:
                # Bukan masalah VPN — skip batch ini dan lanjut ke berikutnya
                smro.save_debug_artifacts(driver, f"pur_batch_failed_{date_from.strftime('%Y%m%d')}")
                pending.pop(0)

    # Reset counter setelah selesai
    process_ranges._vpn_retry_count = 0

    # FIX V27: sweep terakhir — kembaran yang selesai terdownload SETELAH
    # sweep per-batch (download lambat / Chrome baru menyelesaikan parsial)
    # tetap dibersihkan sebelum proses dinyatakan selesai.
    _sweep_orphan_downloads(DOWNLOAD_DIR, run_start_names)

    if not uploaded:
        logging.error("Tidak ada batch Prod Reg Status yang berhasil diupload")
        return False
    logging.info(f"Semua batch Prod Reg Status selesai. Total uploaded: {len(uploaded)}")
    return True


def run_automation() -> bool:
    logging.info("=" * 70)
    logging.info("PROD REG STATUS AUTOMATION DIMULAI")
    logging.info(f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 70)
    driver = None
    success = False
    try:
        if not smro.check_vpn_connection():
            msg = "VPN tidak terhubung!\n\nSilakan connect ke AxGate VPN terlebih dahulu."
            logging.error(msg)
            smro.show_popup("VPN Required", msg, success=False)
            return False

        logging.info("\n[Setup] Membuka browser...")
        driver = smro.setup_driver(download_dir=DOWNLOAD_DIR)

        logging.info("\n[Login]")
        if not smro.login_to_portal(driver):
            smro.show_popup("Login Failed", "Gagal login ke portal", success=False)
            return False
        smro.close_popup_if_exists(driver)

        logging.info("\n[Navigasi SMRO]")
        if not smro.navigate_to_smro(driver):
            smro.show_popup("Navigation Failed", "Gagal navigasi SMRO", success=False)
            return False

        logging.info("\n[Prod. Reg. Status — sumber data Item Registration]")
        if not open_process_pur_info_reg(driver):
            smro.show_popup("Prod. Reg. Status Failed", "Gagal buka menu Prod. Reg. Status", success=False)
            return False

        logging.info("\n[Search, Download, Upload]")
        # FIX: process_ranges sekarang mengelola driver-nya sendiri saat VPN reconnect
        # (membuat driver baru kalau perlu) — driver lama sudah di-quit di dalam fungsinya
        success = process_ranges(driver)
        driver = None  # driver sudah dikelola/di-quit oleh process_ranges

    except Exception as e:
        logging.error(f"\nUnexpected error: {e}", exc_info=True)
        smro.show_popup("Automation Error", f"Terjadi error:\n{e}", success=False)
        success = False
    finally:
        if driver:
            # FIX V16.2: safe_driver_quit — anti spam + bersihkan proses orphan.
            smro.safe_driver_quit(driver)

    if success:
        logging.info("\n" + "=" * 70)
        logging.info("PROD REG STATUS SELESAI SUKSES")
        logging.info("=" * 70)
        smro.show_popup("Success", "Prod Reg Status Automation Selesai!", success=True)
    else:
        logging.warning("\n" + "=" * 70)
        logging.warning("Prod Reg Status selesai dengan error")
        logging.warning("=" * 70)
    return success


def wait_for_vpn_and_run():
    os.environ.setdefault("SMRO_SHOW_POPUP", "false")
    logging.info("\n" + "=" * 70)
    logging.info("VPN WATCH MODE - Prod Reg Status")
    logging.info(f"Cek VPN tiap {CONFIG['VPN_CHECK_SECONDS']} detik")
    logging.info(f"Retry automation tiap {CONFIG['RETRY_SECONDS']} detik kalau gagal")
    logging.info("=" * 70 + "\n")

    vpn_connected = False
    run_done_for_session = False
    while True:
        try:
            connected = smro.check_vpn_connection()
            if not connected:
                vpn_connected = False
                run_done_for_session = False
                time.sleep(CONFIG["VPN_CHECK_SECONDS"])
                continue
            if not vpn_connected:
                logging.info("VPN terdeteksi connect. Mulai automation.")
                vpn_connected = True
                run_done_for_session = False
            if run_done_for_session:
                time.sleep(CONFIG["VPN_CHECK_SECONDS"])
                continue
            # FIX V38: watcher long-lived hanya service standby. Sebelum run,
            # hormati automation lain yang sedang memegang presence.
            try:
                import run_presence as _rp
                if _rp.is_held():
                    logging.info("Automation lain sedang berjalan. VPN watcher Prod Reg Status menunggu giliran.")
                    time.sleep(CONFIG["VPN_CHECK_SECONDS"])
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
                logging.info("Sukses. Menunggu VPN reconnect.")
            else:
                logging.warning(f"Gagal. Retry dalam {CONFIG['RETRY_SECONDS']} detik.")
                time.sleep(CONFIG["RETRY_SECONDS"])
        except KeyboardInterrupt:
            logging.info("Watcher dihentikan oleh user")
            break
        except Exception as e:
            logging.error(f"VPN watch error: {e}", exc_info=True)
            time.sleep(CONFIG["VPN_CHECK_SECONDS"])


if __name__ == "__main__":
    if "--wait-vpn" in sys.argv or "--watch" in sys.argv:
        if not smro.acquire_single_instance_lock("PurInfoRegAutomationWatcher"):
            sys.exit(0)
        wait_for_vpn_and_run()
    else:
        run_automation()
