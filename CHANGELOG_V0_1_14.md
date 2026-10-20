# Serveone Dashboard Desktop V0.1.14 — React Summary Settlement

## What changed

- Diagnosed the visual flash of **Pending Settlement** appearing on unrelated pages: the old dashboard bundle appended a 61 KB independent DOM widget that searched for the Summary heading and inserted the entire section above the Pending Delivery Aging anchor. It polled every three seconds and ran a broad `MutationObserver`. Its insertion/removal raced against React route changes and data refresh.
- Removed that DOM widget and replaced it with `PSReactSummarySection`, a real native React component mounted directly above Pending Delivery Aging **only inside the Summary branch** of the existing React bundle.
- Restored all major features: 5 KPI cards and Pending Delivery navigation, Monthly Pending Settlements combo, Settlement Not Complete breakdown, Top 10 PO by Op Unit, Qty/Sales Amount heatmap, search, sticky headers/frozen Op Unit, row/column/detail drilldown, 50-row paginated popup and Excel export.
- Built a stable empty React template (cards/charts/table) that appears immediately. The backend fills it asynchronously using React state. Slower requests do not cause section insertions or replace the template.
- Scoped fetches to Summary + current filter URL. Canceled on unmount and ignored outdated responses; used session cache keyed by filter URL and bounded 60-second background refresh. Existing `serveone:data-changed` event continues to refresh the active Summary without navigation.
- Removed the global loading toast for the React-managed pending-settlement endpoints, without changing other page loading notices.
- Unchanged: original 4:5 design is not relevant here, local SQLite path/schema/data, import/upsert, Vendor/Google setup, SAP worker behavior, backend endpoint contract, and other pages' React styles.

## Verification

- `python -m unittest discover -s tests -p 'test*.py' -v` — **pass** (77 tests: 72 previously covered plus 5 V0.1.14 regressions).
- `node tests/test_v0114_react_settlement.js` — **pass** (synchronous React template, filter URL, stale response isolation, cleanup, KPI and heatmap fill).
- `node --check dashboard/static/assets/index-C9BxB1mr.js` — **pass**.
- **Not tested:** Windows EXE/Setup build, WebView2 real navigation, company database/API performance, corporate SSO/Google/SAP. Keep this labeled as source preview.

## Build & deployment

Build on an IT-approved Windows x64 machine using `build_windows.bat`, then Inno Setup produces `ServeoneDashboard-Desktop-Setup-V0.1.14.exe`. User data is not in this source package; the app continues to use `%LOCALAPPDATA%\ServeoneDashboardDesktop\instance\po_database.db`. Do not uninstall/delete the user's old database.

## Development maintenance

The original React project source was not included in the V63 desktop archive, only its compiled `dashboard/static/assets/index-C9BxB1mr.js`. For transparency, the editable implementation is included at `dashboard/static/desktop/pending_settlement_react.source.js`, and is **already inserted into the compiled bundle** in this release. If the upstream React app is rebuilt, move this component into the upstream Summary source tree and remove the source-injection step rather than reintroducing the legacy DOM widget.
