# Storage Maintenance, 2026-11-17

Completed maintenance reclaims 253,447,507,968 allocated bytes, approximately
253.45 GB. The total is based on original allocation minus retained compressed
archives, independently of other users' concurrent writes.

| Category | Items | Original GB | Reclaimed GB |
| --- | ---: | ---: | ---: |
| Historical detached worktrees | 201 | 228.475 | 227.967 |
| October telemetry | 26 files in 8 runs | 26.138 | 25.481 |

`worktree_review.json` verifies original commits, Git trees, retired paths and
archive hashes. `restoration_review.json` records an actual full restoration
of a checkout and 407 ignored files, including one symbolic link, followed by
complete tar comparison and ordinary non-forced removal of the temporary
checkout. Source commits and HEAD reflog history remain under
`refs/archive/20261117-worktrees/`.

`log_review.json` verifies archive SHA256, exact member lists, original
experiment metadata and restoration markers. Every original log passed
complete tar comparison before removal. Request token bundles, workflow events,
results and configuration remain in place. One historical run failed the
private-regular-file check and remains untouched.

Recovery commands and archive indices:

- `experiments/archive/20261117_retired_worktrees/README.md`
- `experiments/archive/20261117_historical_logs/README.md`

`disk_usage_after.json` records allocated blocks by home directory, selected
experiment paths, shared directories and final filesystem headroom. Home
directory allocation is not a filesystem-wide UID quota. Scans do not follow
symbolic links or cross filesystems. Concurrent writes can change the observed
directory and df differences.

Larger local selection inventories, execution receipts and per-worker progress
records remain alongside these compact tracked reviews. The operators reuse
the November 1 process, container mount, reference and Git safety checks.
Worktrees created on or after November 10, attached branches, locked or modified
copies, current references and active inputs are retained. No GPU work or
other-user cleanup is part of this operation.
