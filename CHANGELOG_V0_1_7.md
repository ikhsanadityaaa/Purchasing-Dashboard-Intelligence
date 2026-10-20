# Serveone Dashboard Desktop V0.1.7 — Live Analytics & Header Polish

Build basis: V0.1.6, dashboard V63.27.73, Automation V43.

## Audit findings

- The dashboard backend already had a tiny `data_revision.txt` marker and response-cache invalidation after asynchronous uploads, but the compiled React UI did not listen for local data changes. Only the active page's manual upload callback caused immediate refetch, leaving background automation, Task Scheduler and other pages stale.
- Settlement KPIs have an independent five-minute memo, so expiring only the HTTP-response cache did not guarantee new KPI numbers after an upsert. The desktop copy now expires that memo and the Settlement filter-option memo in `_soft_invalidate_response_caches()`.
- Cost Reduction uses its own React component and a long-lived navigation cache; it requires a separate refresh listener.
- The Summary Pending Settlement widget is a standalone JS widget with a 60-second timer, requiring its own event-triggered refresh.
- The Auto Update header button was absolutely positioned with a fixed -133px offset and did not reliably match Manual Update when the layout or zoom changed.

## Fixes

1. Add **GET `/api/desktop/data-revision`**, a localhost-authenticated, no-store endpoint returning a short opaque revision marker only. No table contents or company data in the polling response.
2. Use a 2.5-second lightweight poll **while the desktop window is visible**. Only when the marker changes, dispatch a `serveone:data-changed` event and revalidate the active view. Include bounded follow-up refreshes to account for the backend's stale-while-revalidate cache rebuild.
3. Connect React callbacks for Summary, Settlement, Pending Delivery, Item Registration, RFQ, Import, Vendor and All Registered Items; preserve existing filters, pagination, and scroll positions. Connect Cost Reduction and the standalone Summary Pending Settlement widget too.
4. Publish a revision **after successful (2xx, excluding 202) inline data-changing API requests**. Async imports already publish on successful job completion. Failed or queued jobs do not publish a new revision. All URLs stay on localhost.
5. Align Auto Update / Manual Update as normal flex siblings; no hard-coded absolute offset. Match the Manual button's layout height even when the dashboard zoom is adjusted; replace the Auto Update icon with an uncluttered `refresh-cw` SVG. No new logo inside the dashboard.
6. Exclude the frequent revision check from the global page-loader tracker to prevent its presence from causing loading-toast flicker.

## Scope / limitations

- **This is a source preview**, not a Windows-tested release. No production database, company credentials or Microsoft runtime binaries are bundled.
- The polling detects database changes performed through the packaged application APIs/workers. Direct external changes to the SQLite file bypassing application mutation APIs are not guaranteed to publish an event.
- Actual update latency depends on query duration, stale-response rebuild and UI load; it is **not guaranteed instantaneous**.
- LAN Host–Client multiuser and offline cache are **not** implemented in V0.1.7.
- Manual Upload session problems, Google Sheets-based Vendor/Import credential dependencies and data import accuracy still require separate real Windows testing; this change does not claim to fix those issues.

## Testing

Run `build_windows.bat` on an approved Windows build PC. It runs Python unittests first and then builds the `.exe`; the installer is created if Inno Setup and the signed Microsoft WebView2 redistributable are available.

After installing V0.1.7, use **authorized synthetic Excel** to test each import. Keep the active Summary/Settlement/Cost Reduction view open, complete an import, and confirm updated data appears without F5 or losing filters/pagination. Test Run All, a scheduled import, and at least one failed import (must not falsely indicate updated data). For the header, verify both buttons share the same height at default and Ctrl+wheel zoom levels.

Keep `%LOCALAPPDATA%\ServeoneDashboardDesktop` (database, schedule and local credentials) untouched during upgrade. PythonAnywhere is unchanged.
