# Serveone Dashboard Desktop V0.1.14 — React Summary & Navigation Performance (source preview)

**Changes:**
- Manual Upload authentication adjusted for Windows WebView2 loopback calls that omit Origin/Referer. Uses a per-launch, unguessable UI capability; explicitly cross-origin requests remain denied. The real Windows Manual Upload still requires user verification.
- Ctrl + mouse wheel, Ctrl +/- and Ctrl + 0 zoom; floating zoom controls removed.
- Run All Process uses **the individual date ranges currently selected in Processes** (not Scheduler), executes Search Client Odr, All Product ID, Prod Reg Status in sequence, marks all as queued/loading from the start, and shows a separate green Completed or red Failed for every process. A failed process does not cancel the others.
- Existing SQLite Import page can read locally saved rows without refreshing external Google Sheets. Vendor uses a Google Sheets sync on first visit in each app session and stores the complete snapshot offline in a **Windows-DPAPI-encrypted file** (including vendor login fields). Both pages remain readable offline afterward; navigating between pages does not start another sync.
- This is a **single-PC desktop source preview**. It does not yet implement Host/Client connectivity, centralized user/device Access Monitor, or shared offline cache. Do not mistake these for completed features.

**V0.1.12 fix:** Windows build previously stopped during the Stop All mock test because the mock worker has no PID. The cancellation code now safely handles a test worker and a real Windows process tree, with Windows-specific regression tests. Python unittest tests passed in a non-Windows environment, but a full EXE/installer build has not been verified on Windows.

**Build:** on an approved Windows 10/11 x64 computer, extract to a new folder and run `build_windows.bat`. Inno Setup 6 is required on the build PC to create `installer_output\ServeoneDashboard-Desktop-Setup-V0.1.14.exe`. The installer includes WebView2 only after the Windows build process obtains and verifies the signed Microsoft redistributable. The development package does not contain that binary.

**Data:** existing database and preferences in `%LOCALAPPDATA%\ServeoneDashboardDesktop` are **not included or deleted**. Do not remove that directory. Google service account credentials, employee data, and customer/PO data must not be bundled into this installer. Deploy and test only in the company's approved environment.

**Tests:** local synthetic source tests pass, including individual Run All results and separate date-range environment variables. Real `.exe`, actual SO file import, network/VPN, SAP applications and any proprietary DB have not been validated on Windows.

---

# Historical setup notes (V0.1.4)

Basis sumber: **Dashboard V63.27.73 optimized bundle (13 Oktober 2026)** + **Automation FIX V43**.

**Patch V0.1.4 (14 Oktober 2026):** perbaikan sesi **Manual Upload** menggunakan header upload khusus yang hanya dimasukkan ke HTML lokal setelah sesi desktop terverifikasi; hanya diterima di tiga endpoint upload dan dari same-origin. Ini menambah jalur autentikasi untuk XHR WebView2 tanpa membebaskan API dari proteksi. **Proses utama terpisah dari Scheduler dan Activity Log** dalam tiga tab. SMRO ID/password disimpan di `%LOCALAPPDATA%\ServeoneDashboardDesktop\smro_credentials.dpapi` dengan Windows DPAPI current-user; tidak ditulis ke `settings.json`. SMRO account dipakai pada ketiga worker V43. Tiap proses dapat memakai 1, 3, 6 bulan, This Year, atau Custom Date; custom End Date kosong berarti Today. Jadwal yang sudah ada dimigrasikan secara kompatibel. Activity Log menggunakan tab tersendiri dengan area lebih tinggi dan mengikuti baris terbaru. Kartu jadwal putih dengan ikon jam di kiri. **24 tes pada mesin pengembangan Linux lulus; installer Windows, SAP/VPN, dan database asli tetap belum diuji langsung.**

**PENTING untuk upgrade:** ekstrak sumber V0.1.4 ke folder build BARU; jangan hapus `%LOCALAPPDATA%\ServeoneDashboardDesktop` karena di sana tersimpan SQLite, jadwal, dan log. Password baru harus diisi langsung dari menu Auto Update > Processes > SMRO Account (jangan kirim ke chat). DPAPI terikat akun Windows yang menyimpan password. Bila masuk menggunakan akun Windows lain, masukkan ulang kredensial.

**Patch V0.1.3 (14 Oktober 2026):** installer Windows kini memeriksa WebView2 Runtime dan memasang **paket resmi Microsoft yang sudah dibundel** jika belum tersedia; tanpa unduhan, Python, maupun `pip` pada laptop karyawan. Proses build pada laptop pembuat installer menyiapkan WebView2 x64 Evergreen Standalone dari Microsoft dan **memverifikasi tanda tangan Microsoft** sebelum dibundel. Saat aplikasi dibuka, ada pemeriksaan ulang: jika runtime hilang setelah instalasi, popup *Install Required Components* dapat memperbaiki dari paket offline `support/`. Pada Windows tanpa .NET Framework yang kompatibel, aplikasi memberi pesan yang jelas dan tidak melakukan unduhan eksternal otomatis. Tidak ada perubahan pada upsert, database, API, atau tampilan dashboard. Build `.exe`/Inno Setup dan pengujian Windows nyata **belum dilakukan** dalam lingkungan penyusunan ini.

**PENTING:** Patch ini menangani *installer/runtime requirements* untuk mode **desktop lokal**. Belum menambahkan kemampuan Host–Client multiuser V0.2, cache offline terpusat, atau Access Monitor multiuser. Tidak ada jaminan fitur tersebut sudah berjalan pada V0.1.4.

**Patch V0.1.2 (13 Oktober 2026):** memperbaiki 428 `Device registration required` pada API Summary/Settlement: Access Monitor web tidak memaksa registrasi browser di desktop localhost. Desktop session guard sekarang dipasang pertama sebelum semua hook Flask bawaan; akses ke host lain dan web hosting tetap dibatasi. Jika SQLite lokal masih kosong, muncul pemberitahuan yang menjelaskan mengapa Summary/Settlement tidak berisi data. **Patch tidak mengisi database dengan data perusahaan**.

**Patch V0.1.1 (13 Oktober 2026):** memperbaiki kegagalan tes packaging Windows pada locale cp1252 dengan `read_text(encoding="utf-8")` untuk HTML dan Python. Kelima unittest lulus pada lingkungan pengembangan. Belum diuji build Windows.

**Status:** source project dan skrip build tersedia. **Source untuk membangun installer `.exe` tersedia; installer `.exe` belum dibangun/diuji di Windows/SAP nyata.** Jangan menganggapnya siap produksi sebelum pengujian lokal dan izin IT.

## Yang sudah diimplementasikan

- Flask + React V63.27.73 dipertahankan sebagai UI dan backend, dengan jendela Windows melalui **pywebview**.
- Database SQLite dipindahkan dari folder kode ke `%LOCALAPPDATA%\ServeoneDashboardDesktop\instance\po_database.db` supaya tidak hilang ketika installer diganti.
- Panel **Auto Update** di sebelah tombol Manual Update menggantikan link download Google Drive. Konfigurasi disimpan di `%LOCALAPPDATA%\ServeoneDashboardDesktop\settings.json`.
- Pengaturan jadwal jam, hari custom, interval, beberapa jadwal sekaligus, rentang tanggal, run individual / Run All, dan log lokal.
- Modul unduhan V43 (SMRO, SERP, PUR Info Reg) dikemas sebagai worker anak proses. Endpoint upload **dipaksa** ke `127.0.0.1` pada proses worker, bukan cloud.
- Update data lewat endpoint Flask yang ada sehingga memanfaatkan logika impor/tabel lama. **Belum disamakan/performance-tested pada data produksi.**
- Pembatas outbound network (pengecualian terbatas untuk thread Google Sheets yang diminta), CSP frontend, sesi lokal sementara; Automation worker tetap membutuhkan koneksi ke portal internal perusahaan yang disetujui.
- Opsi Windows Task Scheduler (off by default) agar bisa menjalankan schedule walaupun jendela UI ditutup. Diperlukan pengujian pada Windows corporate.
- Pengimpor snapshot SQLite menggunakan SQLite Backup API, menolak overwrite DB yang sudah ada.
- Tidak membawa data asli, password, file `.env`, atau salinan database dalam paket ini.

## Yang belum selesai/teruji

1. **Build Windows dan QA:** lingkungan penyusun paket adalah Linux dengan dependencies Flask/pywebview belum tersedia sehingga **tidak ada .exe yang diuji**; Windows PyInstaller/installer harus dilakukan pada PC Windows yang disetujui.
2. **Database produksi:** tidak disertakan. Sebelum mengimpor, buat snapshot konsisten dari SQLite di perangkat internal, lalu QA KPI (Delivery, Settlement, Cost Reduction, Item Registration, Import, RFQ), transaksi dan import.
3. **Otomasi SSO/SAP, VPN, ChromeDriver, Task Scheduler:** perlu pengujian di jaringan kantor serta persetujuan IT. Scheduled Task mungkin memerlukan login interaktif/VPN yang tidak tersedia ketika pengguna logout.
4. **Google Sheets:** koneksi hanya diizinkan saat sync Import/Vendor diminta (baca saja; tidak ada writeback ke Google Sheets); semua lalu lintas outbound lainnya tetap diblokir. Kurs FX online tetap belum diaktifkan.
5. **Keamanan & compliance:** source code perusahaan milik perusahaan dan harus berada di perangkat/penyimpanan yang disetujui. **Jangan unggah paket ini ke GitHub personal, layanan AI, hosting eksternal, atau distribusikan sebagai produk.**
6. **Pekerjaan produksi:** migrasi rilis terbaru dari PythonAnywhere dan fallback database harus diverifikasi jika ternyata V63.27.73 belum merupakan rilis yang aktif di server.

## Build di PC Windows (diizinkan IT) — V0.1.14

> **Tidak bisa membuat executable Windows di lingkungan Linux ini.** File ZIP ini adalah **source project**, bukan installer yang siap dijalankan. Keberhasilan compile Inno Setup, Pythonnet, WebView2, dan aplikasi sebenarnya masih harus diverifikasi pada Windows.

1. Di **PC build** (bukan PC karyawan), siapkan Python 3.11 **x64** dan Inno Setup 6 (disarankan 6.3+). Jangan masukkan database asli, kredensial, atau `.env` ke direktori source/build.
2. Ekstrak source ZIP ke folder internal yang disetujui. Jalankan **`build_windows.bat`**. Ia memasang dependensi ke `.venv` PC build, menjalankan unittest, membangun PyInstaller, dan jika Inno Setup tersedia, otomatis membuat installer.
3. `prepare_webview2_runtime.ps1` secara otomatis mengambil **Microsoft x64 WebView2 Evergreen Standalone** ke `vendor/` **di PC build**. Skrip akan gagal jika tanda tangan Microsoft tidak valid. Jika akses unduhan dibatasi, salin terlebih dahulu installer x64 resmi yang telah disetujui IT ke `vendor\MicrosoftEdgeWebView2RuntimeInstallerX64.exe`, lalu jalankan ulang. Sumber resmi: https://developer.microsoft.com/en-us/microsoft-edge/webview2/
4. Jika Inno Setup belum ada, hasil PyInstaller tersedia di `dist\ServeoneDashboard\ServeoneDashboard.exe` tetapi **installer Setup belum dibuat**. Setelah Inno Setup terpasang, jalankan `build_installer_windows.bat`.
5. Paket install yang selesai berada di **`installer_output\ServeoneDashboard-Desktop-Setup-V0.1.14.exe`**. Ukurannya lebih besar dari ZIP source karena membawa runtime WebView2 offline (dapat ratusan MB).
6. Bagikan **hanya installer yang sudah teruji** melalui kanal internal yang disetujui IT. Pada PC Client, cukup jalankan Setup: **tidak perlu memasang Python, pip, Flask, SQLite, atau WebView2 secara manual**. Bila WebView2 belum tersedia, Setup meminta persetujuan dan memasangnya secara otomatis dari file yang sudah dibundel.
7. Jika WebView2 hilang setelah Setup, aplikasi menampilkan popup **Install Required Components**; klik untuk menginstal ulang paket Microsoft dari `support/`. Tidak ada unduhan dari internet pada PC karyawan. Jika setup tidak menyertakan file tersebut atau sertifikat tidak valid, aplikasi tidak menjalankan file dan menampilkan pesan.

**Batasan:** Ini mendukung Windows 10 x64 build 17763+ dan Windows 11 x64. Windows/.NET Framework yang tidak didukung atau yang dikelola dengan kebijakan pemasangan khusus tetap memerlukan penanganan resmi IT. Dukungan `.NET Framework` tidak mengunduh komponen dari internet secara otomatis. Komponen SAP, SSO, driver browser, dan VPN milik perusahaan harus diverifikasi oleh IT untuk laptop Host, bukan dipasang diam-diam ke semua laptop.

**Pemeriksaan praktis setelah build:** uji di Windows Sandbox atau VM internal yang disetujui, dengan dan tanpa WebView2. Pastikan dialog install hanya muncul jika diperlukan, lalu cek tampilan Summary/Settlement, restart, uninstall/reinstall, data `%LOCALAPPDATA%\ServeoneDashboardDesktop`, dan log instalasi. Jangan menghapus WebView2 dari laptop kerja utama hanya untuk menguji.

## Cara memasukkan database lokal

**Jangan menyalin file SQLite yang masih aktif secara sembarangan**. Gunakan fitur backup SQLite dari sumber, simpan pada media/internal PC yang disetujui. Alternatif melalui executable, **hanya ketika belum pernah membuka app** (supaya DB tujuan belum dibuat):

```bat
"dist\ServeoneDashboard\ServeoneDashboard.exe" --import-db "D:\BackupInternal\po_database.db"
```

Proses ini tidak menghapus sumber dan tidak akan menimpa DB lokal yang sudah ada. Jika sudah pernah menjalankan app, minta prosedur penggantian DB yang terkontrol; jangan menghapus SQLite aktif begitu saja.

## Pengaturan automation

- Tekan **Auto Update** di sebelah Manual Update.
- Centang **Enable Automation**.
- Tambah satu atau beberapa schedule (proses, hari/jam, tanggal mulai, tanggal akhir). Klik **Save Settings**.
- Klik **Run** / **Run All Process** untuk menjalankan manual, lihat Activity Log.
- Untuk run walau UI tertutup, centang opsi **Windows Task Scheduler**, simpan; jika Windows/IT menolak, aplikasi menampilkan error dan konfigurasi Task Scheduler tidak boleh dianggap aktif.
- Masukkan **SMRO ID** dan **password** di tab **Processes** > **SMRO Account**, kemudian klik **Save Account**. Password disimpan terenkripsi memakai Windows DPAPI; tidak perlu lagi mengisi file `.env` untuk kedua nilai ini. Jangan pernah mengirim password ke percakapan atau repo.
- Pilih date range terpisah untuk Search Client Odr, All Product ID, dan Prod Reg Status. Rentang custom dengan End Date kosong memakai tanggal hari ini.
- Tab **Scheduler** khusus untuk membuat jadwal. Tab **Activity Log** mengikuti pembaruan log otomatis.

## Pengujian yang benar-benar dijalankan pada paket ini

- Python `compileall` semua modul sumber: lulus.
- `node --check` untuk `auto_update.js`: lulus.
- `unittest` untuk validasi jadwal lama dan baru, autentikasi upload, DPAPI mock, preset tanggal per proses, serta import SQLite sintetis: **24/24 lulus**.
- **Belum** uji startup Flask+React penuh, Windows pywebview, build installer, SAP/VPN, maupun database produksi.

## Catatan arsitektur

- Aplikasi Dashboard hanya listen pada `127.0.0.1` port acak. Autentikasi sesi sementara dipakai untuk HTTP localhost.
- Otomasi V43 berjalan di proses terpisah dan tidak boleh mengirim database ke host eksternal. Satu proses otomasi aktif pada satu waktu.
- Schedule internal berjalan selama app dibuka; tugas Windows (bila berhasil didaftarkan) akan menjalankan backend headless + worker saat waktunya tiba.
- Pada V0.1.12, sync Google Sheets khusus Import/Vendor dapat diaktifkan dengan tautan Google Sheets yang boleh dibaca atau Google OAuth read-only; koneksi kurs FX online tetap tidak diaktifkan. Vendor login membaca snapshot terenkripsi lokal.

**Kebijakan Head Office 20 Oktober 2026:** meskipun source proyek ini bisa dibangun lokal, migrasi dari PythonAnywhere dan penghapusan asset eksternal harus dilakukan sesuai instruksi serta persetujuan IT perusahaan.

## Troubleshooting V0.1.2 — Summary/Settlement tidak muncul

- Jika sebelumnya mendapat `428 Device registration required`, bangun ulang aplikasi dari ZIP V0.1.2 dan jalankan **exe baru** (bukan exe V0.1.1 di folder lama). Registrasi perangkat hanya dimatikan untuk mode Desktop localhost; server web tetap memerlukan registrasi.
- Jika sudah tidak ada `428` tetapi Summary atau Settlement kosong, lihat pemberitahuan kuning di kanan bawah: source code tidak membawa database nyata. File DB diharapkan di `%LOCALAPPDATA%\ServeoneDashboardDesktop\instance\po_database.db`.
- Jangan mengirim SQLite produksi ke ChatGPT atau repositori eksternal. Impor snapshot yang sah sesuai persetujuan IT dan prosedur SQLite backup dari folder internal. Pastikan aplikasi ditutup saat memigrasikan database.
- CLI `--import-db` pada build V0.1.2 masih **menolak overwrite** database lokal yang sudah ada. Aplikasi yang sempat dibuka dapat membuat database skema kosong; jangan hapus berkas DB lama sebelum mengecek isinya dan mendapat konfirmasi prosedur pemulihan.
- Pemberitahuan kuning bukan bukti Settlement pasti benar: setelah DB terpasang, lakukan QA endpoint dan validasi hitungan dengan data kantor di PC yang disetujui.


## V0.1.5

Run `build_windows.bat` on Windows. `build_installer_windows.bat` only rebuilds Setup after a previous successful EXE build. Windows EXE/installer icon now blue S. Manual upload processing uses localhost-only Flask and upserts local SQLite; no server upload. In Auto Update > Processes, `Check Local SO Data` displays aggregate counts when Summary shows no recent rows. Dashboard starts fitted to laptop viewport; bottom-right +/-/Fit and Ctrl +/-/0 adjust zoom. Existing LocalAppData database is left unchanged.


## Pembaruan V0.1.7 (14 Oktober 2026)

Lihat `CHANGELOG_V0_1_7.md`. Setelah upsert berhasil, aplikasi mendeteksi penanda perubahan data lokal setiap 2,5 detik saat jendela terlihat. KPI, grafik dan tabel aktif diminta ulang menggunakan callback frontend yang sudah ada; tidak ada reload seluruh jendela, filter/pagination dipertahankan. Memo KPI Settlement 5 menit juga di-invalidate. Tombol Manual Update dan Auto Update sekarang sejajar dengan tinggi sama, dan ikon Auto Update diperbarui. Data/kredensial tetap berada di laptop, bukan di dalam installer. **Belum termasuk** Host–Client multiuser; verifikasi dengan Excel sintetis di Windows sebelum memakai database produksi.


## Pembaruan V0.1.8 — Google Sheets First-Open Sync (14 Oktober 2026)

1. Masuk halaman Import **pertama kali dalam satu sesi aplikasi** ⇒ sync Google Sheets berjalan di belakang layar menggunakan logika V63 asli; baris Excel/SQLite lokal tetap ditampilkan saat proses berlangsung. Hal yang sama (independen) berlaku untuk Vendor. Kembali ke halaman yang sama **tidak** memulai sync baru.
2. Klik **Sync Now** pada status kecil di pojok bawah halaman untuk memperbarui lagi kapan saja. Tombol manual lama yang memakai `?refresh=1` juga memulai sync di background; membaca halaman biasa tidak pernah menyentuh Google.
3. Sheet yang memang bisa dibaca lewat tautan tidak perlu login. Untuk **Sheet privat** gunakan **Google Login** dengan konfigurasi **OAuth Desktop client JSON** dari Google Cloud (bukan service-account private key); buka browser dan login memakai akun Google yang sudah memiliki hak baca Sheet tersebut. Token OAuth disimpan terenkripsi dengan Windows DPAPI. Jangan kirim file kredensial, token, atau data Vendor ke ZIP/repository/chat.
4. Import menggunakan upsert SQLite asli dan melindungi kolom user-edit; sumber parsial/gagal tidak dapat memicu penghapusan offline. Vendor menyimpan snapshot terenkripsi dan membacanya tanpa jaringan, termasuk fitur login vendor dari snapshot.
5. Setelah berhasil, penanda Live Analytics berubah dan grafik/KPI halaman aktif diperbarui otomatis. Tidak ada koneksi Google 24 jam dan tidak ada reload jendela setiap kali halaman dibuka.
6. Sinkronisasi desktop menggunakan hak **baca saja**. Edit Import/Vendor hanya disimpan lokal; Google Sheets **tidak pernah ditulis**. Sheet Vendor berisi informasi sensitif, jangan mengubah aksesnya menjadi publik hanya demi sync. Koneksi memerlukan internet yang diizinkan perusahaan.

**Batas validasi:** tes sintetis otomatis lulus di lingkungan non-Windows. UI diuji dengan simulasi navigasi SPA. Sync sebenarnya ke Google Sheets perusahaan, pembacaan DPAPI Windows, installer, dan lisensi akses sheet belum diuji end-to-end.



## V0.1.14 — React Summary Pending Settlement dan navigasi tanpa flash

- **Pending Settlement** kini komponen React di dalam render tree Summary, persis **sebelum Pending Delivery Aging**. Widget DOM legacy V63.23 (60 KB, MutationObserver, interval 3 dtk, findAnchorContainer, dan prefetch di page lain) dihapus dari bundle. Jadi tidak mungkin disisipkan lagi ke halaman Import/Settlement/Vendor saat React berpindah route.
- Template React KPI (5 card), grafik bulanan, donut Settlement Not Complete, Top 10 PO per Op Unit, dan heatmap ditampilkan langsung sebelum request API selesai. Data masuk lewat state React tanpa melepas/menyisipkan section.
- Data `/api/data/pending-settlement` hanya diambil **saat Summary aktif**; pembatalan request, scope URL filter, dan pengecekan `alive` mencegah response lama mengganti data terbaru setelah pindah halaman atau ganti filter. Cache sesi disimpan per kombinasi filter, refresh 60 detik, dan `serveone:data-changed` me-refresh tanpa full reload.
- Komponen menjaga 5 KPI dapat diklik untuk memfilter Pending Delivery, pilihan Qty/Sales Amount, search/clear Op Unit, zebra/sticky heatmap, popup detail dengan pagination 50 dan Download Excel.
- Loading terpusat tidak dipicu oleh API yang sudah mempunyai template/loading React sendiri. **Tidak ada perubahan** pada data SQLite lokal, script SAP worker, Google OAuth, schema API, atau theme dashboard lain.
- **Pengujian:** 77 tes Python, uji React state via Node (template instan, fetch, stale response, cleanup, navigasi), parsing sintaks bundle JS — lulus. Build Windows/QA visual di WebView2 nyata tetap perlu dilakukan.

## V0.1.13 — OAuth form & Windows SAP runtime

- All Product ID (SERP) previously failed with `ModuleNotFoundError: No module named psutil` because automation dependencies were missing from `requirements-windows.txt`. The build now installs psutil, pyautogui, pygetwindow, pyperclip, pywinauto, and pywin32; PyInstaller explicitly includes critical modules. An import check fails the build early with an actionable message rather than releasing a broken app. This **requires a rebuild** of the Windows EXE.
- Google Login on Import/Vendor now opens an in-app form requesting an OAuth **Desktop** Client ID and Client Secret. **No JSON upload** and **no Google service-account private key**. The form links to Google Cloud setup. After saving encrypted locally with Windows DPAPI, it opens the external browser for consent to **Sheets read-only**.
- Steps: Cloud Console > Google Auth Platform > Branding/Audience > Clients > Create Client (Desktop app); enable Google Sheets API, configure test user if necessary, and copy the ID & secret into the popup. Google user MUST already have access to private sheets.
- Existing saved OAuth Desktop clients remain usable through 'Login dengan pengaturan tersimpan'; the form never displays stored credentials.
- Data remains on local SQLite/snapshot as before, including sync once per page per app session, background sync, and read-only behavior. No Google Sheets write API.
- Legacy Google service-account private key shared in chat should be revoked in the original Google Cloud project.
-  Tests validate code and synthetic flows only. Actual SAP GUI, Google OAuth external consent and Windows installer remain unverified and require user testing on an approved computer.
