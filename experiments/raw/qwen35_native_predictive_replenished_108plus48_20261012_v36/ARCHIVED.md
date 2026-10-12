# V36 Workspace Archive

V36 was administratively stopped at 2026-10-12 23:08:23 CST for a
predictive development restart. The pre-signal inventory contains
39 completed workflows and 117 administratively censored tasks:
69 created unfinished and 48 unarrived. Shutdown records are separate.

All 108 workspaces passed full GNU tar comparison before source removal
at 23:10:45 CST. Only this run's 69 stopped owned containers were removed.
Raw traces, outcomes, manifest and the frozen experiment plan remain.

Archive: `experiments/archive/20261012/v36_development_workspaces.tar.zst`

SHA256: `e7f0bd61ddd96565dd612d4882afc4b48ae84b4cb7dd6d955789ab5310004bab`

Source allocation: 20,905,279,488 bytes. Archive size: 2,407,891,014 bytes.

Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v36_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v153_v36_development_stop_20261012.json`
and `experiments/reports/v153_v36_workspace_archive_20261012.json`.
