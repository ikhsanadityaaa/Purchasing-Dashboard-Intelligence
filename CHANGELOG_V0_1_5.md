# Serveone Dashboard Desktop V0.1.5 — local import, authentication and display fixes

- Manual Upload: every same-origin `/api/` request gets the ephemeral session token from authenticated HTML; this includes upload and job-status checks. The backend still requires a local session or same-origin token. The isolated automation worker continues using its localhost-only upload key.
- Removed the redundant small blue loading pill. Kept the existing manual import progress dialog and success/error notifications.
- Auto Update: button for currently running process animates with a spinner; other run buttons are disabled until completion.
- Worker S-MRO logs now say **Local Import**, and no longer print the localhost URL containing a temporary session key.
- `Check Local SO Data`: aggregate-only diagnostics to compare last two months' `so_data` versus `Delivery Completed`, including historical count. Does not send records outside the PC.
- Windows EXE & Setup icon changed to royal blue rounded square with centered bold white S. Dashboard branding unchanged.
- Automatic fit to laptop on first launch; zoom controls (65–125%) or Ctrl +/-/0 with remembered preference.
- All data still located under `%LOCALAPPDATA%\ServeoneDashboardDesktop`; no production SQLite bundled or deleted.
- This package is still localhost single-user preview. Host–Client and encrypted offline cache are NOT implemented.

**Caution:** These are source-code/static regressions tested on Linux. Build and upload with real corporate files and SAP connectivity must be validated locally on a Windows machine approved by IT. Do not upload data or credentials to ChatGPT.
