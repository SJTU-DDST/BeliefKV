# Historical Experiment Log Cleanup, 2026-11-22

This batch archives inactive telemetry from runs dated before November 20.
Selected files must also predate `2026-11-20T00:00:00-08:00`.
Only large logs directly inside `server` and `opportunities` are selected.
Source capture runs, request token bundles, workflow events, results, configuration,
workspaces, environments, models and earlier recovery archives remain in place.

The existing archival operators check active processes, open files, live container
mounts, registered inputs and explicit code/document references. Each archive passes
a complete byte comparison against the source before original log copies are removed.
Two disjoint workers run with nice19, idle I/O and one zstd thread per archive.

Local evidence:

- `log_inventory.json`: immutable selection, original file statistics and metadata hashes.
- `batch_0/log_receipt.json`, `batch_1/log_receipt.json`: per-run execution receipts.
- `batch_0/log_review.json`, `batch_1/log_review.json`: archive hashes, members,
  preserved metadata and restoration marker verification.
- `final_storage_review.json`: aggregate space accounting and two actual restoration
  rehearsals with complete comparison of recovered files.

Every affected run has an `OLD_LOGS_ARCHIVED_20261122.md` marker containing its
archive SHA256 and exact restoration command. Archives and recovery indexes live at:

- `experiments/archive/20261122_historical_logs/batch_0/README.md`
- `experiments/archive/20261122_historical_logs/batch_1/README.md`

The inventory and execution receipts stay local. Only the maintenance operators,
compact independent reviews and summary are committed, avoiding large evidence
copies in future experiment checkouts.
