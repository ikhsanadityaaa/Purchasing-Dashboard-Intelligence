# V0.1.9 — Unicode Sync and Run All Scheduler

- Use UTF-8 with safe fallback for Windows application output streams, fixing `charmap` encoding errors from printable Google Sheets sync logs. Does not change data encoding.
- Scheduler Run All no longer exposes an independent Date Range; existing schedules of type Run All use all three saved Processes date ranges.
- Single-process schedules retain their selected date ranges.
- Rotate any previously exposed service-account private key before reuse; do not bundle credentials.
