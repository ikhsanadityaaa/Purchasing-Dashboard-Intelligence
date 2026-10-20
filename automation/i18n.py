"""Lightweight bilingual localization for ServeOne Automation.

UI language is selected with SMRO_LANGUAGE=id|en. Technical names such as
Headless Chrome, SAP GUI, SERP, SMRO, API, VPN, Windows Task Scheduler and
product/module names are intentionally kept unchanged.
"""
from __future__ import annotations

import logging
import os
import re

_LANGUAGE = (os.getenv("SMRO_LANGUAGE", "id") or "id").strip().lower()
if _LANGUAGE not in {"id", "en"}:
    _LANGUAGE = "id"


def set_language(code: str) -> str:
    global _LANGUAGE
    code = (code or "id").strip().lower()
    _LANGUAGE = code if code in {"id", "en"} else "id"
    os.environ["SMRO_LANGUAGE"] = _LANGUAGE
    return _LANGUAGE


def get_language() -> str:
    return _LANGUAGE


# Exact UI strings. Keys may be either legacy Indonesian or English strings;
# both resolve to the currently selected language.
_PAIRS = [
    ("Ready", "Siap", "Ready"),
    ("Loading", "Memuat", "Loading"),
    ("Loading...", "Memuat...", "Loading..."),
    ("Settings", "Pengaturan", "Settings"),
    ("Save Settings", "Simpan Pengaturan", "Save Settings"),
    ("Saved Settings", "Pengaturan Tersimpan", "Saved Settings"),
    ("Show v", "Tampilkan ▾", "Show ▾"),
    ("Hide ^", "Sembunyikan ▴", "Hide ▴"),
    ("Activity Log", "Log Aktivitas", "Activity Log"),
    ("Clear", "Bersihkan", "Clear"),
    ("Run Auto Download Once", "Jalankan Sekali", "Run Once"),
    ("Auto Run All Process", "Jalankan Semua Proses", "Run All Processes"),
    ("Run All Process", "Jalankan Semua Proses", "Run All Processes"),
    ("Stop All", "Hentikan Semua", "Stop All"),
    ("Enable", "Aktif", "Enabled"),
    ("No process running", "Tidak ada proses berjalan", "No process running"),
    ("No Process Enabled", "Tidak ada proses yang aktif", "No Process Enabled"),
    ("Process", "Proses", "Process"),
    ("Range / Time", "Rentang / Waktu", "Range / Time"),
    ("Headless", "Headless", "Headless"),
    ("Dashboard", "Dashboard", "Dashboard"),
    ("Local", "Lokal", "Local"),
    ("Configuration", "Konfigurasi", "Configuration"),
    ("Date Range", "Rentang Tanggal", "Date Range"),
    ("Start Date", "Tanggal Mulai", "Start Date"),
    ("End Date", "Tanggal Akhir", "End Date"),
    ("Daily Run Time", "Waktu Jalan Harian", "Daily Run Time"),
    ("Headless Mode", "Mode Headless", "Headless Mode"),
    ("Headless Mode (Chrome hidden)", "Headless Chrome (Chrome tersembunyi)", "Headless Chrome (Chrome hidden)"),
    ("Local Save Folder", "Folder Simpan Lokal", "Local Save Folder"),
    ("Browse", "Pilih", "Browse"),
    ("Chrome Parallel Tabs", "Tab Chrome Paralel", "Chrome Parallel Tabs"),
    ("ServeOne Account", "Akun ServeOne", "ServeOne Account"),
    ("User ID", "User ID", "User ID"),
    ("Password", "Password", "Password"),
    ("Schedule", "Jadwal", "Schedule"),
    ("Jadwal Proses Tertentu", "Jadwal Proses Tertentu", "Process Schedules"),
    ("Proses:", "Proses:", "Process:"),
    ("1 kali sehari", "1 kali sehari", "Once a day"),
    ("Setiap 1 jam", "Setiap 1 jam", "Every 1 hour"),
    ("Setiap 2 jam", "Setiap 2 jam", "Every 2 hours"),
    ("Setiap 3 jam", "Setiap 3 jam", "Every 3 hours"),
    ("Setiap 4 jam", "Setiap 4 jam", "Every 4 hours"),
    ("Custom hari dan Jam", "Custom", "Custom"),
    ("Custom", "Custom", "Custom"),
    ("Hari", "Hari", "Days"),
    ("Jam", "Jam", "Time"),
    ("Jam:", "Jam:", "Time:"),
    ("Senin", "Senin", "Monday"),
    ("Selasa", "Selasa", "Tuesday"),
    ("Rabu", "Rabu", "Wednesday"),
    ("Kamis", "Kamis", "Thursday"),
    ("Jumat", "Jumat", "Friday"),
    ("Sabtu", "Sabtu", "Saturday"),
    ("Minggu", "Minggu", "Sunday"),
    ("Only update in working hour (07:00-17:00)", "Hanya update pada jam kerja (07:00-17:00)", "Only update during working hours (07:00-17:00)"),
    ("Berlaku hanya untuk jadwal interval Setiap 1-4 jam.", "Hanya berlaku untuk jadwal repetitif Setiap 1-4 jam.", "Available only for repetitive Every 1-4 hours schedules."),
    ("Range saat schedule:", "Rentang saat jadwal:", "Schedule range:"),
    ("Ikuti Range Proses", "Ikuti Rentang Proses", "Use Process Range"),
    ("Custom Range", "Rentang Custom", "Custom Range"),
    ("Start (YYYY atau YYYY/MM/DD)", "Mulai (YYYY atau YYYY/MM/DD)", "Start (YYYY or YYYY/MM/DD)"),
    ("End (kosong = today)", "Akhir (kosong = hari ini)", "End (blank = today)"),
    ("Mengikuti Date Range proses yang dipilih di Settings.", "Mengikuti Rentang Tanggal proses yang dipilih di Pengaturan.", "Uses the process Date Range selected in Settings."),
    ("Range otomatis dihitung mundur dari hari run (end = today).", "Rentang otomatis dihitung mundur dari hari proses dijalankan (akhir = hari ini).", "The range is calculated backward from the run date (end = today)."),
    ("Range ini hanya dipakai saat schedule. Run Once tetap memakai Date Range proses di atas.", "Rentang ini hanya dipakai saat jadwal berjalan. Jalankan Sekali tetap memakai Rentang Tanggal proses di atas.", "This range is used only for scheduled runs. Run Once still uses the process Date Range above."),
    ("Simpan Jadwal Proses Ini", "Simpan Jadwal Proses Ini", "Save This Process Schedule"),
    ("Menyimpan ke daftar di bawah. Klik Save Settings untuk menerapkan semua jadwal ke Windows Task Scheduler.", "Menyimpan jadwal ke daftar di bawah. Klik Simpan Pengaturan untuk menerapkan seluruh daftar ke Windows Task Scheduler.", "Saves the schedule to the list below. Click Save Settings to apply the full list to Windows Task Scheduler."),
    ("Hapus Semua Schedule", "Hapus Semua Jadwal", "Delete All Schedules"),
    ("VPN Reconnect Interval (sec)", "Interval Sambung Ulang VPN (detik)", "VPN Reconnect Interval (sec)"),
    ("Retry Interval (sec)", "Interval Coba Ulang (detik)", "Retry Interval (sec)"),
    ("Preferences", "Preferensi", "Preferences"),
    ("Show Result Popup", "Tampilkan Popup Hasil", "Show Result Popup"),
    ("System", "Sistem", "System"),
    ("Start with Windows (Autostart)", "Mulai bersama Windows (Autostart)", "Start with Windows (Autostart)"),
    ("Jika aktif, aplikasi otomatis terbuka (minimized ke tray) saat Windows login.\nVPN hanya dicek ketika Run Once / Run All / jadwal akan berjalan.", "Jika aktif, aplikasi otomatis terbuka dan diminimalkan ke tray saat login Windows.\nVPN hanya dicek ketika Jalankan Sekali, Jalankan Semua Proses, atau jadwal akan berjalan.", "When enabled, the app opens minimized to the tray at Windows sign-in.\nVPN is checked only when Run Once, Run All, or a schedule is about to run."),
    ("Bisa lebih dari 1 jam, pisahkan koma. Contoh: 06:00, 14:00", "Bisa lebih dari 1 jam, pisahkan dengan koma. Contoh: 06:00, 14:00", "You can enter multiple times separated by commas. Example: 06:00, 14:00"),
    ("Belum ada jadwal proses tertentu — centang opsi di atas lalu Simpan.", "Belum ada jadwal proses tertentu. Atur opsi di atas lalu klik Simpan Jadwal Proses Ini.", "No process schedule yet. Configure the options above, then click Save This Process Schedule."),
    ("Jadwal belum dipilih", "Jadwal belum dipilih", "No schedule selected"),
    ("Jadwal Belum Valid", "Jadwal belum valid", "Invalid schedule"),
    ("Invalid", "Tidak valid", "Invalid"),
    ("Sedang Berjalan", "Sedang berjalan", "Process running"),
    ("Gagal Menyimpan Settings", "Gagal menyimpan pengaturan", "Failed to Save Settings"),
    ("Gagal Menyimpan Jadwal", "Gagal menyimpan jadwal", "Failed to Save Schedule"),
    ("Schedule Dikosongkan", "Jadwal dikosongkan", "Schedules Cleared"),
    ("Folder lokal belum dipilih", "Folder lokal belum dipilih", "Local folder not selected"),
    ("File Missing", "File tidak ditemukan", "File Missing"),
    ("Python Missing", "Python tidak ditemukan", "Python Missing"),
    ("Launch Error", "Gagal menjalankan", "Launch Error"),
    ("Running", "Berjalan", "Running"),
    ("Autostart", "Autostart", "Autostart"),
    ("Exit", "Keluar", "Exit"),
    ("Start", "Mulai", "Start"),
    ("Stop", "Hentikan", "Stop"),
    ("Run Once", "Jalankan Sekali", "Run Once"),
    ("Test", "Tes", "Test"),
    ("Language", "Bahasa", "Language"),
    ("Bahasa / Language", "Bahasa / Language", "Language / Bahasa"),
    ("Indonesian", "Indonesia", "Indonesian"),
    ("English", "Inggris", "English"),
    ("Tampilan", "Tampilan", "Appearance"),
    ("Antarmuka", "Antarmuka", "Interface"),
    ("Daily Run Times - Run All (HH:MM, pisahkan koma/spasi)", "Waktu Harian - Jalankan Semua Proses (HH:MM, pisahkan koma/spasi)", "Daily Run Times - Run All (HH:MM, separate with comma/space)"),
    ("Checking...", "Memeriksa...", "Checking..."),
    ("All Product ID Automation Control", "Kontrol Automation All Product ID", "All Product ID Automation Control"),
    ("Prod Reg Status Automation Control", "Kontrol Automation Prod Reg Status", "Prod Reg Status Automation Control"),
    ("Automation Control", "Kontrol Automation", "Automation Control"),
    ("1 Week", "1 Minggu", "1 Week"),
    ("1 Month", "1 Bulan", "1 Month"),
    ("3 Months", "3 Bulan", "3 Months"),
    ("6 Months", "6 Bulan", "6 Months"),
    ("This Year", "Tahun Ini", "This Year"),
    ("Range", "Rentang", "Range"),
    ("Time", "Waktu", "Time"),
    ("Start Date", "Tanggal Mulai", "Start Date"),
    ("End Date", "Tanggal Akhir", "End Date"),
    ("Split Limit", "Batas Pemisahan", "Split Limit"),
    ("Daily Run Time", "Waktu Jalan Harian", "Daily Run Time"),
    ("SAP GUI Minimize", "Minimalkan SAP GUI", "Minimize SAP GUI"),
    ("Author", "Pembuat", "Author"),
    ("(Hanya tersedia di Windows)", "(Hanya tersedia di Windows)", "(Available on Windows only)"),
    ("Folder lokal belum dipilih", "Folder lokal belum dipilih", "Local folder has not been selected"),
    ("Settings saved & scheduler updated", "Pengaturan disimpan & scheduler diperbarui", "Settings saved & scheduler updated"),
    ("Settings saved; scheduler sync failed", "Pengaturan disimpan; sinkronisasi scheduler gagal", "Settings saved; scheduler sync failed"),
    ("VPN Connected", "VPN Terhubung", "VPN Connected"),
    ("VPN Disconnected", "VPN Terputus", "VPN Disconnected"),
    ("VPN disconnected", "VPN terputus", "VPN disconnected"),
    ("SMRO Auto running", "SMRO Auto berjalan", "SMRO Auto running"),
    ("SMRO Manual run started", "SMRO manual mulai berjalan", "SMRO manual run started"),
    ("SERP Auto running", "SERP Auto berjalan", "SERP Auto running"),
    ("SERP Manual run started", "SERP manual mulai berjalan", "SERP manual run started"),
    ("Prod Reg Status Auto running", "Prod Reg Status Auto berjalan", "Prod Reg Status Auto running"),
    ("Prod Reg Status Manual run started", "Prod Reg Status manual mulai berjalan", "Prod Reg Status manual run started"),
    ("Stopped - standby", "Dihentikan - standby", "Stopped - standby"),
    ("Stopped all - standby", "Semua dihentikan - standby", "Stopped all - standby"),
    ("No running process - standby", "Tidak ada proses berjalan - standby", "No running process - standby"),
    ("Ready - All processes disabled", "Siap - semua proses nonaktif", "Ready - all processes disabled"),
    ("Running: ", "Berjalan: ", "Running: "),
    ("Show ServeOne Automation", "Tampilkan ServeOne Automation", "Show ServeOne Automation"),
    ("▷ Run All Process", "▷ Jalankan Semua Proses", "▷ Run All Processes"),
    ("Pilih folder lokal di Settings sebelum mengaktifkan Save Local.", "Pilih folder lokal di Pengaturan sebelum mengaktifkan Simpan Lokal.", "Select a local folder in Settings before enabling Save Local."),
    ("Process masih berjalan", "Proses masih berjalan", "Process is still running"),
    ("SERP Process masih berjalan", "Proses SERP masih berjalan", "SERP process is still running"),
    ("Masih ada process berjalan. Stop dulu sebelum Run All.", "Masih ada proses berjalan. Hentikan proses tersebut sebelum Jalankan Semua Proses.", "Another process is still running. Stop it before Run All."),
    ("Semua proses dinonaktifkan. Aktifkan minimal 1 proses di toggle.", "Semua proses dinonaktifkan. Aktifkan minimal 1 proses.", "All processes are disabled. Enable at least one process."),
    ("VPN belum connect", "VPN belum terhubung", "VPN is not connected"),
    ("Silakan connect VPN terlebih dahulu, lalu klik Start atau Run Once lagi.", "Silakan hubungkan VPN terlebih dahulu, lalu klik Mulai atau Jalankan Sekali lagi.", "Connect the VPN first, then click Start or Run Once again."),
    ("Task Scheduler SEBAGIAN gagal", "Sebagian Windows Task Scheduler gagal", "Windows Task Scheduler partially failed"),
    ("Jadwal internal (selama aplikasi terbuka) tetap aktif.", "Jadwal internal (selama aplikasi terbuka) tetap aktif.", "The internal scheduler remains active while the app is open."),
    ("Klik Save Settings", "Klik Simpan Pengaturan", "Click Save Settings"),
    ("Update Dashboard", "Update Dashboard", "Update Dashboard"),
    ("Save Local", "Simpan Lokal", "Save Local"),
    ("Run All", "Jalankan Semua", "Run All"),
    ("Run Once", "Jalankan Sekali", "Run Once"),
]

_EXACT = {"id": {}, "en": {}}
for source, id_text, en_text in _PAIRS:
    for alias in {source, id_text, en_text}:
        _EXACT["id"][alias] = id_text
        _EXACT["en"][alias] = en_text


def tr(text):
    if text is None:
        return text
    s = str(text)
    return _EXACT.get(_LANGUAGE, {}).get(s, s)


# Phrase substitutions used for dynamic status strings and logs. Longest first.
_ID_TO_EN = {
    "Tidak ada proses berjalan": "No process running",
    "Semua schedule": "All schedules",
    "Semua jadwal": "All schedules",
    "Jadwal internal": "Internal schedule",
    "Jadwal ": "Schedule ",
    "jadwal ": "schedule ",
    "disimpan ke daftar": "saved to the list",
    "berhasil disimpan": "saved successfully",
    "gagal disimpan": "failed to save",
    "berhasil": "successful",
    "gagal": "failed",
    "Gagal": "Failed",
    "Selesai": "Completed",
    "selesai": "completed",
    "Mulai proses": "Starting process",
    "Selesai sukses": "Completed successfully",
    "Selesai gagal": "Completed with failure",
    "VPN terhubung": "VPN connected",
    "VPN belum terhubung": "VPN is not connected",
    "VPN tidak terhubung": "VPN is not connected",
    "proses lain masih berjalan": "another process is still running",
    "automation lain masih berjalan": "another automation is still running",
    "menunggu": "waiting",
    "Menunggu": "Waiting",
    "dicoba ulang": "will be retried",
    "Cek ulang": "Check again",
    "DIBATALKAN": "CANCELLED",
    "DITUNDA": "DEFERRED",
    "dihapus": "deleted",
    "hapus": "delete",
    "dibuat": "created",
    "menjalankan": "running",
    "Menjalankan": "Running",
    "dijalankan": "started",
    "pengaturan": "settings",
    "Pengaturan": "Settings",
    "Rentang": "Range",
    "rentang": "range",
    "hari ini": "today",
    "jam kerja": "working hours",
    "tiap ": "every ",
    " kali sehari": " times a day",
    "1x/hari": "once/day",
    "Error - ": "Error - ",
    "Ada Error": "Error",
    "Siap": "Ready",
    "Aktif": "Enabled",
    "Tidak valid": "Invalid",
    "Klik Simpan Pengaturan": "Click Save Settings",
    "Windows Task Scheduler": "Windows Task Scheduler",
    "Berlaku khusus untuk proses": "Applies only to process",
    "proses lain punya range-nya sendiri": "other processes have their own range",
    "Sumber data:": "Data source:",
    "download 1 range penuh": "download one full range",
    "jika angka di kiri bawah": "if the number at the lower left is",
    "range otomatis dipecah": "the range is automatically split",
    "Tanggal manual khusus": "Manual dates for",
    "Hanya berlaku untuk": "Available only for",
    "Hanya update pada jam kerja": "Only update during working hours",
    "Pilih hari": "Select days",
    "Pilih satu jenis jadwal": "Select one schedule type",
    "Simpan satu jenis jadwal per entri": "Save one schedule type per entry",
    "Simpan jadwal ini dulu, lalu tambahkan jadwal berikutnya": "Save this schedule first, then add the next schedule",
    "pilih minimal 1 hari": "select at least 1 day",
    "isi minimal 1 jam": "enter at least 1 time",
    "tidak valid": "is invalid",
    "format HH:MM, pisahkan koma": "use HH:MM format, separated by commas",
    "Rentang Custom": "Custom Range",
    "tanggal mulai wajib diisi": "start date is required",
    "Tanggal mulai": "Start date",
    "Tanggal akhir": "End date",
    "tidak boleh lebih besar dari": "must not be later than",
    "berisi tanggal yang tidak valid": "contains an invalid date",
    "Jadwal yang sama sudah ada di daftar": "The same schedule already exists in the list",
    "Belum ada jadwal proses tertentu": "No process schedule yet",
    "Semua jadwal sudah dihapus dari daftar": "All schedules have been removed from the list",
    "Semua jadwal dihapus dari daftar": "All schedules removed from the list",
    "untuk menerapkan": "to apply",
    "seluruh daftar": "the full list",
    "menghapus task lama": "delete old tasks",
    "perubahan": "changes",
    "bahasa": "language",
    "Bahasa": "Language",
    "karena masih ada proses automation yang benar-benar berjalan": "because another automation process is still running",
    "menit ke depan": "more minutes",
    "tombol kembali standby": "buttons returned to standby",
    "Semua proses dihentikan oleh user": "All processes stopped by the user",
    "dihentikan oleh user": "stopped by the user",
    "VPN belum connect": "VPN is not connected",
    "Silakan connect VPN terlebih dahulu": "Connect the VPN first",
    "sudah berjalan": "is already running",
    "masih berjalan": "is still running",
    "memulai auto download": "starting auto download",
    "auto mode berjalan": "auto mode is running",
    "dihentikan oleh user": "stopped by user",
    "tidak ada proses yang sedang berjalan": "no process is currently running",
    "tidak ada proses berjalan": "no process running",
    "dipaksa berhenti": "forced to stop",
    "tidak ada aktivitas selama": "no activity for",
    "kemungkinan hang": "possibly hung",
    "Pilih folder lokal": "Select a local folder",
    "pilih folder lokal": "select a local folder",
    "sebelum mengaktifkan": "before enabling",
    "harus angka": "must be a number",
    "wajib diisi": "is required",
    "pilih minimal": "select at least",
    "tidak bisa menulis ke file pengaturan": "cannot write to the settings file",
    "Tidak bisa menulis ke file pengaturan": "Cannot write to the settings file",
    "Kemungkinan penyebab": "Possible causes",
    "sedang dipakai proses lain": "is being used by another process",
    "memblokir penulisan file": "is blocking file writes",
    "tidak punya izin tulis": "does not have write permission",
    "Terjadi error tak terduga": "An unexpected error occurred",
    "Gagal membuat task jadwal": "Failed to create schedule task",
    "Gagal update Task Scheduler": "Failed to update Task Scheduler",
    "terdaftar": "registered",
    "run berikutnya": "next run",
    "dijalankan manual": "started manually",
    "Test jadwal": "Schedule test",
    "menjalankan task": "starting task",
    "lebih dari": "for more than",
    "sudah ditangani oleh": "already handled by",
    "duplicate run dilewati": "duplicate run skipped",
    "run jadwal lain sedang berjalan": "another scheduled run is active",
    "proses automation yang benar-benar berjalan": "an automation process is actually running",
    "dicoba ulang otomatis sampai": "will be retried automatically for up to",
    "dicek ulang hanya selama jadwal ini pending": "rechecked only while this schedule is pending",
    "VPN siap": "VPN ready",
    "pukul": "at",
    "selesai, tapi ada error di salah satu tahap": "completed, but one stage had an error",
    "Cek detail log di atas": "Check the log details above",
    "berhenti dengan error": "stopped with an error",
    "exit code": "exit code",
    "Jadwal interval": "Interval schedule",
    "dilewati": "skipped",
    "Custom schedule yang berhasil hari ini": "A successful Custom schedule today",
    "sudah mencakup rentang update": "already covers the update range",
    "masih berada dalam interval yang sama": "is still within the same interval window",
    "syarat siap": "prerequisites ready",
    "tidak ada automation lain": "no other automation is running",
    "Memulai process": "Starting process",
    "Process tidak dikenal": "Unknown process",
    "Tombol kembali standby": "Buttons returned to standby",
    "Semua proses dihentikan": "All processes stopped",
    "Tidak ada proses yang sedang berjalan": "No process is currently running",
    "diaktifkan": "enabled",
    "dimatikan": "disabled",
    "Aplikasi akan terbuka otomatis": "The app will open automatically",
    "saat Windows login": "at Windows sign-in",
    "Gagal mengubah pengaturan autostart di Registry": "Failed to change the autostart setting in the Registry",
    "Run time harus format": "Run time must use format",
    "Untuk banyak waktu": "For multiple times",
    "pisahkan dengan koma/spasi": "separate them with comma/space",
    "harus format": "must use format",
    "tidak boleh lebih besar dari": "must not be later than",
    "pilih minimal Update Dashboard atau Save Local": "select at least Update Dashboard or Save Local",
    "pilih folder lokal dulu sebelum Save Local aktif": "select a local folder before Save Local is enabled",
    "Menyimpan ke daftar di bawah": "Saves to the list below",
    "menerapkan semua jadwal": "apply all schedules",
    "Semua jadwal dihapus dari daftar": "All schedules were removed from the list",
    "Semua jadwal sudah dihapus dari daftar": "All schedules have been removed from the list",
    "menerapkan penghapusan": "apply the deletion",
    "jadwal proses tertentu": "process schedules",
    "centang opsi di atas lalu Simpan": "configure an option above and save it",
    "Jadwal yang sama sudah ada di daftar": "The same schedule already exists in the list",
    "disimpan ke daftar": "saved to the list",
    "menerapkan seluruh daftar": "apply the full list",
    "menghapus seluruh task jadwal ServeOne lama": "delete all old ServeOne schedule tasks",
}

_EN_TO_ID = {
    "No process running": "Tidak ada proses berjalan",
    "Internal schedule": "Jadwal internal",
    "Schedule ": "Jadwal ",
    "schedule ": "jadwal ",
    "saved to the list": "disimpan ke daftar",
    "saved successfully": "berhasil disimpan",
    "failed to save": "gagal disimpan",
    "successful": "berhasil",
    "Completed successfully": "Selesai sukses",
    "Completed with failure": "Selesai gagal",
    "Starting process": "Mulai proses",
    "Completed": "Selesai",
    "VPN connected": "VPN terhubung",
    "VPN is not connected": "VPN belum terhubung",
    "another process is still running": "proses lain masih berjalan",
    "another automation is still running": "automation lain masih berjalan",
    "waiting": "menunggu",
    "Waiting": "Menunggu",
    "will be retried": "akan dicoba ulang",
    "Check again": "Cek ulang",
    "CANCELLED": "DIBATALKAN",
    "DEFERRED": "DITUNDA",
    "deleted": "dihapus",
    "created": "dibuat",
    "running": "berjalan",
    "Running": "Berjalan",
    "started": "dijalankan",
    "Settings": "Pengaturan",
    "settings": "pengaturan",
    "Range": "Rentang",
    "range": "rentang",
    "today": "hari ini",
    "working hours": "jam kerja",
    "every ": "setiap ",
    "once/day": "1x/hari",
    "Ready": "Siap",
    "Enabled": "Aktif",
    "Invalid": "Tidak valid",
    "Click Save Settings": "Klik Simpan Pengaturan",
    "Applies only to process": "Berlaku khusus untuk proses",
    "other processes have their own range": "proses lain memiliki rentang sendiri",
    "Data source:": "Sumber data:",
    "Select days": "Pilih hari",
    "Select one schedule type": "Pilih satu jenis jadwal",
    "Save one schedule type per entry": "Simpan satu jenis jadwal per entri",
    "The same schedule already exists in the list": "Jadwal yang sama sudah ada di daftar",
    "No process schedule yet": "Belum ada jadwal proses tertentu",
    "All schedules have been removed from the list": "Semua jadwal sudah dihapus dari daftar",
    "All schedules removed from the list": "Semua jadwal dihapus dari daftar",
}


_WORD_GLOSSARY_EN = {
    "karena": "because", "masih": "still", "ada": "there is", "oleh": "by",
    "user": "user", "tombol": "button", "kembali": "back", "dan": "and",
    "atau": "or", "untuk": "for", "dengan": "with", "tanpa": "without",
    "selama": "during", "setelah": "after", "sebelum": "before", "sampai": "until",
    "menit": "minutes", "detik": "seconds", "jam": "hours", "hari": "days",
    "proses": "process", "jadwal": "schedule", "rentang": "range", "tanggal": "date",
    "mulai": "start", "akhir": "end", "berjalan": "running", "berikutnya": "next",
    "dilewati": "skipped", "dibatalkan": "cancelled", "ditunda": "deferred",
    "dihapus": "deleted", "dibuat": "created", "dibuka": "opened", "ditutup": "closed",
    "berhasil": "successful", "gagal": "failed", "kesalahan": "error", "error": "error",
    "memulai": "starting", "menunggu": "waiting", "membuka": "opening", "menghapus": "deleting",
    "menyimpan": "saving", "menggunakan": "using", "mencoba": "trying", "ulang": "again",
    "terhubung": "connected", "terputus": "disconnected", "siap": "ready",
    "tidak": "not", "belum": "not yet", "sudah": "already", "semua": "all",
    "hanya": "only", "lebih": "more", "dari": "from", "ke": "to", "pada": "on",
    "saat": "when", "ini": "this", "yang": "that", "lain": "other", "aktif": "active",
    "pengaturan": "settings", "folder": "folder", "lokal": "local", "file": "file",
    "dipilih": "selected", "pilih": "select", "kosong": "blank", "sekarang": "now",
}

_WORD_GLOSSARY_ID = {
    "because": "karena", "waiting": "menunggu", "opening": "membuka",
    "failed": "gagal", "successful": "berhasil", "cancelled": "dibatalkan",
    "deferred": "ditunda", "connected": "terhubung", "disconnected": "terputus",
    "selected": "dipilih", "select": "pilih", "blank": "kosong",
}

def _translate_common_words(s: str, target: str) -> str:
    glossary = _WORD_GLOSSARY_EN if target == "en" else _WORD_GLOSSARY_ID
    # Apply only whole words so API URLs, process names and technical identifiers remain intact.
    for src in sorted(glossary, key=len, reverse=True):
        s = re.sub(r"(?i)(?<![A-Za-z0-9_])" + re.escape(src) + r"(?![A-Za-z0-9_])", glossary[src], s)
    return s

def translate_text(text, lang=None):
    if text is None:
        return text
    target = (lang or _LANGUAGE).lower()
    s = str(text)
    # Exact UI mapping first.
    exact = _EXACT.get(target, {}).get(s)
    if exact is not None:
        return exact
    repl = _ID_TO_EN if target == "en" else _EN_TO_ID
    # Long phrase substitutions first, then a conservative whole-word glossary
    # for dynamic log lines that contain variable values.
    for src in sorted(repl, key=len, reverse=True):
        s = s.replace(src, repl[src])
    return _translate_common_words(s, target)


class LocalizedFormatter(logging.Formatter):
    def format(self, record):
        original_msg, original_args, original_level = record.msg, record.args, record.levelname
        try:
            record.msg = translate_text(record.getMessage())
            record.args = None
            if get_language() == "id":
                record.levelname = {"WARNING": "PERINGATAN", "CRITICAL": "KRITIS"}.get(record.levelname, record.levelname)
            return super().format(record)
        finally:
            record.msg, record.args, record.levelname = original_msg, original_args, original_level


def apply_localized_logging(root_logger=None):
    logger = root_logger or logging.getLogger()
    fmt = LocalizedFormatter('%(asctime)s - %(levelname)s - %(message)s')
    for handler in logger.handlers:
        try:
            handler.setFormatter(fmt)
        except Exception:
            pass
