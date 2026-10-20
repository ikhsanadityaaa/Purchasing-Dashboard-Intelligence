# Serveone Dashboard Desktop V0.1.14 — Mulai di sini

## Apa yang diperbaiki

**Pending Settlement hanya dirender di React Summary.** Kartu/grafik/tabel kosong muncul langsung tanpa menunggu API; data diisi otomatis ketika selesai dimuat. Widget JavaScript lama yang menyebabkan section tersisip sebentar di page lain telah dihapus dari bundle. Filter dan perpindahan page membatalkan/mengabaikan respons usang.

## Cara memasang update (PC build Windows yang disetujui IT)

1. Jangan hapus database lokal atau `settings.json`, dan jangan uninstall sambil memilih opsi yang menghapus data pengguna. Data lama disimpan di `%LOCALAPPDATA%\ServeoneDashboardDesktop\instance\po_database.db`. Backup dahulu menggunakan backup SQLite yang konsisten, bukan copy file saat app aktif.
2. Ekstrak ZIP source ini di PC build Windows. Ini **bukan installer siap pakai**. Installer Windows harus dibangun dengan `build_windows.bat` (Python 3.11 x64 dan Inno Setup 6 diperlukan hanya pada PC build). Jika Inno belum ada, jalankan `build_installer_windows.bat` setelah PyInstaller selesai. Baca `README_DESKTOP_ID.md` untuk prasyarat lengkap termasuk WebView2.
3. Installer akan dibuat sebagai `installer_output\ServeoneDashboard-Desktop-Setup-V0.1.14.exe` di PC build. Uji dulu di PC QA perusahaan dengan database dummy/snapshot internal yang diizinkan.
4. Jalankan aplikasi terbarukan, buka **Summary → Import → Vendor → Settlement → Summary** dan pastikan Pending Settlement selalu berada di Summary tanpa flash di page lain; data tidak perlu ditunggu untuk melihat template kosong. Uji pergantian date/client/PIC filter cepat dan buka detail heatmap.
5. Jika data masih lambat setelah UI stabil, catat nama page, filter, jumlah row dan waktu load. Sisi query SQLite perlu diprofiling secara terpisah dengan dataset internal, karena paket source ini tidak memiliki database produksi.

## Batas pengujian

77 tes Python + Node React/nav + sintaks JS lulus di lingkungan non-Windows. Build `.exe`/WebView2, perilaku Windows nyata, dan latensi API/database produksi **belum diverifikasi**. Paket ini tidak menyertakan data/kredensial. Tetap gunakan kanal dan perangkat yang disetujui IT.
