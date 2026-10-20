# Serveone Dashboard Desktop V0.1.8 — Google Sheets Offline Sync

- Import/Vendor: first page visit starts one (nonblocking) sync per application session, never on every navigation.
- Explicit Sync Now triggers manual refresh. Existing page Manual Update (`refresh=1`) maps to background sync.
- Reuses original V63 Import Sheets parsing, vendor matching, UID matching, and upsert logic; native SQLite keeps offline Import data.
- Google Vendor Control persists an encrypted local snapshot (vendor credentials included) per Windows user; normal page GET and vendor login read offline snapshot, never Google.
- Google Sheets service account JSON is imported through an in-app connector button, encrypted using Windows DPAPI, and never bundled. Environment path/JSON remains available for approved local IT deployments.
- Network remains loopback-only except trusted sheet sync and explicit cell edits with allowlisted Google endpoints.
- On failures, existing offline dataset remains accessible. Partial Import source fetch disables destructive orphan/duplicate cleanup; no concurrent overlapping sync per page.
- Sync success publishes existing Live Analytics revision; frontend refreshes visible charts without full window reload.
- Offline popup displays sync progress discreetly and includes Connect + Sync Now; no full-screen loading.

**Build/runtime caveats:** Windows build and company Google Sheets credentials/network were not available for an end-to-end integration test. `requirements-windows.txt` now includes `google-auth`. Google service account needs Editor role for sheet writeback; use least-privilege sharing and obtain IT approval. Offline Vendor snapshot is Windows-account-bound; backup/recovery requires account access.
