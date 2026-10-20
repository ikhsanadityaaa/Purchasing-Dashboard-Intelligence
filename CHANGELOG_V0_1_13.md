# Serveone Dashboard Desktop V0.1.13

- Fix All Product ID missing psutil and other Windows SAP automation dependencies.
- Bundle missing Win32/PyAutoGUI modules explicitly during Windows PyInstaller build.
- Replace Google OAuth client JSON-upload UI with a read-only OAuth Desktop setup popup accepting Client ID and Client Secret.
- Add direct Google Cloud setup links and instructions, without service-account private key.
- Save local DPAPI encrypted OAuth configuration and tokens; keep one first-open sync per page and the offline snapshot.
- Update asset cache-busting query for sheet_sync JS/CSS and installer version.
- Not validated against actual SAP/Sheets or Windows binary.
