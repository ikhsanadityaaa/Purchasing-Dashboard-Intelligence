# V0.1.3 — Windows prerequisite installer patch

## Implemented

- Inno Setup per-user installer for Windows x64 (no admin privilege requirement by default).
- Detect WebView2 Runtime using Microsoft EdgeUpdate registry (HKCU / HKLM 32-bit view / HKLM native view).
- Ask permission only if WebView2 missing, then install bundled official signed offline runtime, verify after installation, and prevent auto-launch on failure.
- Keep support installer beside the app so missing WebView2 can be repaired later via a popup on launch, without internet.
- Windows launch guard also checks .NET Framework >=4.6.2 and gives safe error if unsupported.
- Windows build script prepares WebView2 on **build machine only** and verifies Microsoft Authenticode signer.
- Python & its libraries are bundled into exe by PyInstaller; employees do not need pip/Python.
- Existing dashboard, local database storage, upload/upsert and automation logic left unchanged.

## Not yet verified / delivered

- No Windows binary or WebView2 prerequisite payload in source ZIP; `build_windows.bat` must be run on Windows.
- End-to-end Windows QA is pending, including verification of vendor runtimes, installation permissions, Windows corporate policies.
- Multiuser Host–Client, shared dashboard data, offline snapshots and new Access Monitor (V0.2) NOT implemented in this prerequisite patch.

## Security

Installer obtains ONLY a public Microsoft runtime on the build PC. Source code, company database and credentials are not uploaded by the setup script. Source ZIP excludes binaries, data, passwords and `.env`.
