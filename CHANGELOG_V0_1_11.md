# V0.1.11 — Google Sheets read-only / Activity / sidebar and automation

- Google Sheets: public/link-accessible read-only CSV, or private read-only Google OAuth Desktop client; NO service-account private keys or writeback. Import to SQLite, Vendor to Windows DPAPI encrypted offline snapshot. Auto-sync once per visited page per app session; manual Sync Now; failures preserve data.
- Windows UTF-8 stream fallback for charmap issues; scheduled Run All uses separate Processes date ranges, not a schedule-level range.
- Stop All cancels the current worker and Windows child-process tree; remaining Run All steps are marked Stopped rather than launched. Force-stop may interrupt in-flight file operations: check outputs before retrying.
- Headless Mode On/Off stored in settings, passed independently to all automation workers.
- Activity Log suppresses repeated transient browser blob entries; worker failures show the latest useful error directly on the process card.
- Restore navigation/upload loading toast while ignoring background status polling.
- Fix sidebar z-index above floating/frozen table headers and horizontal scrollbar but below dialogs.
- Windows build, company Google/OAuth/DPAPI/SAP, real SQLite and browser layout NOT integration tested; source preview only.
