# Serveone Dashboard Desktop V0.1.12 — Windows Build Regression Fix

- Fixed Windows build gate: `test_stop_all_terminates_worker_and_skips_queue` could crash with `AttributeError: CancellableWorker has no attribute pid` in the Windows-only taskkill branch.
- Added safe fallback to `.terminate()` when the worker has no real integer PID, and preserved Windows `taskkill /PID ... /T /F` for real processes and their Selenium/Chrome children.
- If Windows taskkill reports a failure, fallback to terminating the direct worker. Stop All still prevents remaining Run All jobs from starting. Already committed SQLite updates cannot be rolled back.
- Added four regression tests specifically covering a worker mock without PID, real PID, taskkill failure, and invalid PID.
- Updated installer filename/version to V0.1.12. No new Google credential behavior or database migration.

## Build on Windows

1. Extract the source ZIP to a fresh folder (do not run from inside ZIP).
2. Open Command Prompt in the extracted `Serveone_Dashboard_Desktop_V0_1_12` folder.
3. Run `build_windows.bat --exe-only` to build the program first (no Inno Setup required).
4. Program location after successful build: `dist\ServeoneDashboard\ServeoneDashboard.exe`.
5. If you also want the installer, install Inno Setup 6 and run `build_installer_windows.bat`; it must obtain the official signed offline WebView2 runtime.
6. Keep the Command Prompt open and share the FIRST error if build fails. The source ZIP contains no prebuilt EXE.

No database, credential, service account key, or WebView2 installer is bundled in this ZIP. Windows PyInstaller / Inno Setup still require validation on a Windows build machine.
