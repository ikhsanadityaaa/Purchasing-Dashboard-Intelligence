# Serveone Dashboard Desktop V0.1.4

## Fixed
- `Desktop session required` on manual Search Client Odr / Product ID / Item Registration uploads: the authenticated desktop HTML now provides a short-lived, localhost-only upload token to the existing Axios upload function. Worker upload requests continue using the separate per-launch session key. Other endpoints still require the HttpOnly desktop session cookie.
- WebView2 manual upload Axios explicitly sets `withCredentials: true`.

## Auto Update
- New **Processes**, **Scheduler**, **Activity Log** tabs.
- SMRO ID and password fields. Use Save Account; the password is encrypted with Windows DPAPI under the current Windows user and never returned to the UI or written to settings JSON.
- Individual manual date ranges: 1 month, 3 months, 6 months, this year, custom dates. Empty custom end date means Today (evaluated when the worker runs).
- Run All respects each process's current range. Scheduled jobs keep their own range without overwriting other schedules. Old V0.1.3 schedules load with custom dates unchanged.
- Scheduler cards now white with a clock icon. Activity Log has a larger dedicated pane and follows new lines automatically.

## Validation
- 24 Python unit tests (using mock/synthetic data) and JavaScript syntax check passed in Linux development environment.
- NOT validated: real Windows installer, UI screenshot rendering, production database imports/upsert, SAP/S-MRO credentials and VPN automation. These require tests on the authorized company PC.
- V0.2 LAN Host/Client and offline client cache are NOT included.

## Existing user data
The installer does not contain, delete or overwrite data from `%LOCALAPPDATA%\ServeoneDashboardDesktop`. Do not copy `.env`, password files, real database or logs into the build directory.
