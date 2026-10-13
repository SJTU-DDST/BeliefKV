# Experiment archive

Superseded, interrupted, and debugging-only raw runs are moved here rather than
deleted. The archive is local-only and excluded from Git; this README is the
tracked inventory.

## 2026-10-13 verified workspace compression

Archive: `experiments/archive/20261013/v46_development_workspaces.tar.zst`

SHA256: `dfb2b1b31af6cde6adb4f53904073e8390b60b98148cf165a14c350457a4dad3`

All 108 V46 workspaces passed full GNU tar comparison before cleanup at
08:50:28 CST: 20,961,181,696 allocated source bytes and 2,234,026,108 archive
bytes. The administrative cutoff was 08:47:12.309023: 69 completed,
one existing incomplete, 38 created unfinished and 48 unarrived. Preserve
the latter 86 as administrative censoring; V240 records the existing
output-length incomplete separately. Only 38 owned stopped containers
were removed. Raw traces, results, manifest and frozen plan remain.
V241 excludes six post-cutoff tool completions and four first services
from training; early confirmed FULL reuse stays 42 JOINs/1.951 GB and
150 tools/11.536 GB. Maintenance preceded V47's 08:51:32 CST cold start.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v46_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v238_v46_development_stop_20261013.json`
and `experiments/reports/v238_v46_workspace_archive_20261013.json`.
Final audit: `experiments/reports/v238_v46_final_wait_prefetch_20261013.json.gz`
and `experiments/reports/v241_v46_cutoff_qualification_20261013.json`.

Archive: `experiments/archive/20261013/v45_development_workspaces.tar.zst`

SHA256: `48b5637421ae845ba6917df26ef455b666819952520e70b5b922f7c587b181cb`

All 156 V45 workspaces passed full GNU tar comparison before removal
at 07:49:31 CST: 38,815,416,320 allocated source bytes and
4,057,170,357 archive bytes. Collection stopped at
07:43:41.038655 CST with 87 completed and 69 created unfinished
workflows, all 156 arrivals present. Preserve the latter 69 as
administrative censoring. Only this run's 69 stopped owned containers
were removed. Raw traces, results, manifest and frozen plan remain.
Maintenance precedes V46's 07:50:54 CST cold start. V229 excludes
two post-cutoff tool completions and five first services from terminal
training labels; sealed early confirmed FULL reuse is unchanged.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v45_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v227_v45_development_stop_20261013.json`
and `experiments/reports/v227_v45_workspace_archive_20261013.json`.
Final event audit: `experiments/reports/v227_v45_final_wait_prefetch_20261013.json.gz`
and `experiments/reports/v229_v45_cutoff_qualification_20261013.json`.

Archive: `experiments/archive/20261013/v44_development_workspaces.tar.zst`

SHA256: `498d9111194fa5433c240e9ad372164bde613e3406c5a5b73272aee0978df59d`

All 108 V44 workspaces passed full GNU tar comparison before removal
at 06:29:09 CST: 20,932,284,416 allocated source bytes and
2,248,823,195 archive bytes. Collection stopped at 06:24:32 CST with
34 completed workflows, 74 created unfinished and 48 unarrived.
Preserve the latter 122 as administrative censoring. The final
read-only audit exited before cleanup. Only this run's 74 stopped
owned containers were removed. Raw traces, outcomes, manifest and
frozen plan remain. Maintenance precedes V45's 06:29:48 CST cold
start; available space after cleanup was 143,605,469,184 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v44_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v216_v44_development_stop_20261013.json`
and `experiments/reports/v216_v44_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v43_development_workspaces.tar.zst`

SHA256: `7f11113f6fdef22458d685eded9773455dab6dc9e42fc43008d49bb58ef4f85a`

All 108 V43 workspaces passed full GNU tar comparison before removal
at 05:32:08 CST: 20,973,236,224 allocated source bytes and
2,501,382,112 archive bytes. Collection stopped at 05:27:59 CST with
78 completed workflows, 30 created unfinished and 48 unarrived.
Keep the latter 78 administratively censored. The final read-only
audit exited before cleanup. Only this run's 30 stopped owned
containers were removed. Raw traces, results, manifest and frozen
plan remain. Available space after cleanup was 148,708,614,144 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v43_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v206_v43_development_stop_20261013.json`
and `experiments/reports/v206_v43_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v42_development_workspaces.tar.zst`

SHA256: `7a3a5c7f4488396181479f505445d24ff44aad6fa095624631797ca4f5549e99`

All 108 V42 workspaces passed full GNU tar comparison before removal
at 04:28:01 CST: 20,956,565,504 allocated source bytes and
2,137,021,267 archive bytes. The collector stopped at 04:24:59 CST
with 69 completed workflows, 39 created unfinished and 48 unarrived.
Preserve the latter 87 as administrative censoring. Only this run's
39 stopped owned containers were removed. The subsequent process
check was blocked by the still-running final read-only audit; no
workspace was deleted. That audit exited before archive comparison
and cleanup. Raw traces, outcomes, manifest and frozen plan remain.
Maintenance precedes V43's 04:28:33 CST cold start; available space
after cleanup was 154,340,007,936 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v42_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v196_v42_development_stop_20261013.json`
and `experiments/reports/v196_v42_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v41_development_workspaces.tar.zst`

SHA256: `5868d61781a39eb7bd90b0bdd39e57e8a4d25403d3d439353e175e648addf504`

All 108 V41 workspaces passed full GNU tar comparison before removal
at 03:27:39 CST: 20,861,030,400 allocated source bytes and
2,296,022,307 archive bytes. The collector stopped at 03:24:35 CST
with 21 completed workflows, 87 created unfinished and 48 unarrived.
Preserve the latter 135 as administrative censoring. Only this run's
87 stopped owned containers were removed. Raw traces, outcomes,
manifest and frozen plan remain. The final read-only audit exited
before archive cleanup. Maintenance precedes V42's 03:27:54 CST cold
start; available space after cleanup was 159,852,335,104 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v41_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v187_v41_development_stop_20261013.json`
and `experiments/reports/v187_v41_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v40_development_workspaces.tar.zst`

SHA256: `6d5c4add7795686d8adf679d57d3fc1b9b43bbc7aeedfd1a89b1288abd088162`

All 108 V40 workspaces passed full GNU tar comparison before removal
at 02:51:17 CST: 20,943,118,336 allocated source bytes and
2,117,199,034 archive bytes. The collector stopped at 02:46:16 CST
with 62 completed workflows, 46 created unfinished and 48 unarrived.
Preserve the latter 94 as administrative censoring. Only this run's
46 stopped owned containers were removed. Raw traces, outcomes,
manifest and frozen plan remain. The initial cleanup was blocked by
the read-only final audit still referencing raw; no workspace was removed.
After that audit exited, full comparison was repeated before cleanup.
Maintenance precedes the V41 cold start at 02:52:03 CST; available
space after cleanup was 163,970,043,904 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v40_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v180_v40_development_stop_20261013.json`
and `experiments/reports/v180_v40_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v39_development_workspaces.tar.zst`

SHA256: `07625acf7057dddc23f1174a8ecb44d0f8131dbbe657b01f06c63533ff286582`

All 108 V39 workspaces passed full GNU tar comparison before removal
at 01:57:11 CST: 20,890,152,960 allocated source bytes and
2,491,774,952 archive bytes. The collector stopped at 01:53:51 CST
with 31 completed workflows, 77 created unfinished and 48 unarrived.
Preserve the latter 125 as administrative censoring. Only this run's
77 stopped owned containers were removed. Raw traces, outcomes,
manifest and frozen plan remain. Maintenance precedes the V40 cold
start at 01:57:38 CST; available space was 168,831,299,584 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v39_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v173_v39_development_stop_20261013.json`
and `experiments/reports/v173_v39_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v38_development_workspaces.tar.zst`

SHA256: `ca67c9033e2f68efc78af4700ed9ff8fb32baded0658d88ef32f8fef6d5443af`

All 156 V38 workspaces passed full GNU tar comparison before removal
at 01:19:21 CST: 38,843,097,088 allocated source bytes and
4,029,015,716 archive bytes. The collector stopped at 01:15:52 CST
with 84 completed and 72 created unfinished workflows; all arrivals
had occurred. Preserve the latter as administrative censoring, not
autonomous agent failures. Only this run's 72 stopped owned containers
were removed. Raw traces, outcomes, manifest and frozen plan remain.
Maintenance precedes the V39 cold start at 01:19:38 CST; available space
after cleanup was 173,278,490,624 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v38_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v168_v38_development_stop_20261013.json`
and `experiments/reports/v168_v38_workspace_archive_20261013.json`.

Archive: `experiments/archive/20261013/v37_development_workspaces.tar.zst`

SHA256: `ed523dc91103258849a10fa834c1f813ce1eadde081db6e19c8e60750c9fb87a`

All 108 V37 workspaces passed full GNU tar comparison before removal
at 00:04:30 CST: 20,932,739,072 allocated source bytes and
2,497,263,666 archive bytes. The collector stopped at 00:01:59 CST
with 49 completed workflows and 107 administratively censored tasks:
59 created unfinished and 48 unarrived. Only this run's 59 stopped
owned containers were removed. Raw traces, outcomes, manifest and
frozen plan remain. Maintenance precedes the V38 cold start;
available space was 181,146,755,072 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261013/v37_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v159_v37_development_stop_20261013.json`
and `experiments/reports/v159_v37_workspace_archive_20261013.json`.

## 2026-10-12 verified superseded trace compression

Large JSONL/log files from V10, V12-V15 and V17-V26 are preserved in
`experiments/archive/20261012/vN_superseded_traces.tar.zst`, where `N`
is the run version. All 3,814 files passed GNU tar comparison before
source removal: 154,156,393,660 logical source bytes compressed to
3,787,630,948 archive bytes. Per-run `TRACE_ARCHIVED.md` pointers,
outcomes, summaries, frozen plans, models and HTML remain in raw.
V29 native, V27-V33 recent evidence and training/calibration traces
were excluded.

Receipts: `experiments/reports/v138_superseded_trace_inventory_20261012.json`
and `experiments/reports/v138_superseded_trace_archive_20261012.json`.
The receipt records each archive SHA256, comparison and exact restore
command. Restore the required run from the repository root, for example:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v26_superseded_traces.tar.zst \
  --directory experiments/raw
```

Maintenance at 19:00:39-19:10:29 CST used two zstd workers, nice 19
and idle I/O with the GPU collector stopped. Actual filesystem free
space grew from 65,009,352,704 to 199,390,887,936 bytes: 125.153 GiB
reclaimed and 185.697 GiB available. Hard links mean the logical
source-minus-archive difference is not the physical reclaimed space.

## 2026-10-12 verified workspace compression

Archive: `experiments/archive/20261012/v36_development_workspaces.tar.zst`

SHA256: `e7f0bd61ddd96565dd612d4882afc4b48ae84b4cb7dd6d955789ab5310004bab`

All 108 V36 workspaces passed full GNU tar comparison before source
removal at 23:10:45 CST: 20,905,279,488 allocated source bytes and
2,407,891,014 archive bytes. The collector stopped at 23:08:23 CST
with 39 completed and 117 administratively censored tasks: 69 created
unfinished and 48 unarrived. Only its 69 stopped owned containers were
removed. Raw traces, outcomes, manifest and frozen plan remain.
Maintenance precedes V37; available space was 186,651,115,520 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v36_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v153_v36_development_stop_20261012.json`
and `experiments/reports/v153_v36_workspace_archive_20261012.json`.

Archive: `experiments/archive/20261012/v35_development_workspaces.tar.zst`

SHA256: `910520b960a88ec2237ac9c9a05cbc7a26728d4429febac0b9a011602b3893bb`

All 108 V35 workspaces passed full GNU tar comparison before source
removal at 22:25:23 CST: 20,934,483,968 source allocated bytes and
2,331,636,254 archive bytes. The collector stopped at 22:21:14 CST
with 39 completed workflows and 117 administratively censored tasks
(69 created unfinished, 48 unarrived). Only its 69 remaining owned
containers were removed. Raw traces, outcomes, manifest and frozen
plan remain. Maintenance finished before the V36 cold start;
available filesystem space was 191,366,623,232 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v35_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v146_v35_development_stop_20261012.json`
and `experiments/reports/v146_v35_workspace_archive_20261012.json`.

Archive: `experiments/archive/20261012/v34_failed_workspaces.tar.zst`

SHA256: `887c2e7006fc278739c2bbf5dc99604e27e8b04b1e85ce79652604d12f202a12`

All 108 V34 workspaces are preserved in this 2,112,889,955-byte archive.
Source allocation was 20,840,755,200 bytes. GNU tar compared every archived
file before source removal at 21:29:17 CST, with no GPU collector running.
Only the 102 remaining containers with binds under V34 were removed.
Six completed results remain; 102 created and 48 unarrived tasks are
runtime-failure censored by the NumPy boolean writer failure at 20:15:59 CST.
Raw traces, outcomes, manifest, frozen plan and the failed launch state
remain. Available disk after verification was 196,249,567,232 bytes.

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v34_failed_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v142_v34_runtime_failure_20261012.json` and
`experiments/reports/v142_v34_workspace_archive_20261012.json`.
The raw run's `ARCHIVED.md` repeats this restore path. Fault classification
remains runtime failure, not an administrative stop.

Archive: `experiments/archive/20261012/v33_development_workspaces.tar.zst`

SHA256: `5b8969803c5746c781330fc99e6b70c9aea28273251b15d158e753a77de522db`

All 156 V33 workspaces are preserved in this 4,351,509,208-byte archive.
Source allocation was 38,897,971,200 bytes. GNU tar compared every archived
file before source removal. V33 stopped at 18:40:59 CST with 106 completed,
one invalid historical tool-argument error and 49 administratively censored
workflows. Raw traces, outcomes, manifest and frozen plan remain with an
`ARCHIVED.md` restore pointer. Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v33_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipts: `experiments/reports/v136_v33_development_stop_20261012.json` and
`experiments/reports/v136_v33_workspace_archive_20261012.json`.
Maintenance at 18:42:30-18:44:38 CST took place after stopping the collector.
Net source-minus-archive allocation is about 32.17 GiB; available disk at
verification was 65,010,868,224 bytes.

Archive: `experiments/archive/20261012/v31_development_workspaces.tar.zst`

SHA256: `5a6ed74a07ad5415a2d02bcd954508009ad9eaa5bbf6dc10de4b1a835575d3da`

All 108 V31 workspaces are preserved in this 2,135,196,774-byte archive.
Source allocation was 20,954,169,344 bytes. GNU tar compared every archived
file before source removal. V31 was administratively stopped at
13:50:40.618 CST; raw traces, recorded outcomes, manifest and frozen plan
remain, with an `ARCHIVED.md` restore pointer. Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v31_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipt: `experiments/reports/v133_v31_workspace_archive_20261012.json`.
One compression thread, nice 19 and idle I/O were used. Maintenance at
14:10:09-14:11:20 CST overlaps V32 service and must be qualified in
performance comparisons. Net reclaimed allocation is about 17.53 GiB.

Archive: `experiments/archive/20261012/v30_development_workspaces.tar.zst`

SHA256: `1d8f8c24d6e7ed7d614bf22a1725ff54d53065b0e602b804b527eb91563656d5`

All 156 V30 workspaces are preserved in this 4,174,974,847-byte archive.
Source allocation was 38,681,817,088 bytes. GNU tar compared every archived
file before deleting the uncompressed copies. Raw traces, written outcomes,
frozen plan and manifest remain with an `ARCHIVED.md` restore pointer.
V30 was administratively stopped at 12:49:22 CST; the cutoff snapshot
observes 80 completed, 1 incomplete and 75 censored workflows. Only the
75 containers with `/workspace` binds under this stopped run were removed.
Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v30_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipt: `experiments/reports/v130_v30_workspace_archive_20261012.json`.
One compression thread, nice 19 and idle I/O were used. Maintenance at
12:56:26-12:59:24 CST overlaps early V31 service and must be qualified
in performance comparisons. Available space at verification was
52,752,445,440 bytes, including concurrent V31 writes.

Archive: `experiments/archive/20261012/v16b_stopped_workspaces.tar.zst`

SHA256: `dc6a77c1172261af4a6e2a8dbb1167b2767cb95946607d01111dad95f7dd7223`

All 76 remaining V16b workspaces are preserved in this 3,202,382,046-byte
archive. They belong to the development run stopped on October 10; their
unfinished workflows remain administratively censored. Source allocation was
24,619,732,992 bytes. GNU tar compared every archived file before source
removal. The 76 old containers were removed only after verifying their
`/workspace` mounts belonged to this stopped run. Raw traces, outcomes,
frozen plan and restore pointer remain. Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v16b_stopped_workspaces.tar.zst \
  --directory experiments/raw
```

Receipt: `experiments/reports/v121_v16b_workspace_archive_20261012.json`.
One compression thread, nice 19 and idle I/O were used. Maintenance at
07:45:42-07:47:55 CST overlaps live V29 service and must be qualified in
performance windows. Net reclaimed allocation is about 19.95 GiB. Available
space at verification was 60,490,625,024 bytes, including concurrent writes.

Archive: `experiments/archive/20261012/v28_development_workspaces.tar.zst`

SHA256: `1100e67fdbf7b5a6522e746d3c0b66399bd6822bfe5e7442ca4c9e1b00b781aa`

All 156 V28 workspaces are preserved in this 4,289,396,535-byte archive.
The source allocation was 38,987,358,208 bytes. GNU tar comparison passed
before removing the uncompressed copies; raw traces, written outcomes and
the frozen plan remain, with an `ARCHIVED.md` restore pointer. V28 was
administratively stopped at 06:27:24 CST with 112 completed and 44 censored
workflows; it is not a completed comparison. Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v28_development_workspaces.tar.zst \
  --directory experiments/raw
```

Receipt: `experiments/reports/v118_v28_workspace_archive_20261012.json`.
One compression thread, nice 19 and idle I/O were used. Maintenance at
06:29:52-06:33:04 CST overlaps V29 loading and initial service, which must
be qualified in early performance windows. Available space at cleanup
verification was 61,463,670,784 bytes, including concurrent V29 writes.

Archive: `experiments/archive/20261012/v19b_partial_workspaces.tar.zst`

SHA256: `502cc37b0902f553b6bb2074e17bba9d9038a689ec7bd5a9fd489792f18e2536`

The 3,014,713,292-byte archive preserves all 76 remaining V19b workspaces.
GNU tar compared the archived files against their sources before removal.
Raw traces, outcomes, manifests and model artifacts remain in place; an
`ARCHIVED.md` restore pointer is present in the stopped raw run. The source
workspace allocation was 23,633,416,192 bytes. Compression uses one zstd
thread, nice 19, idle I/O priority and a long window to deduplicate repeated
compressed Git objects. Restore from the repository root:

```bash
tar --extract --use-compress-program='zstd --long=30' \
  --file experiments/archive/20261012/v19b_partial_workspaces.tar.zst \
  --directory experiments/raw
```

Receipt: `experiments/reports/v112_v19b_workspace_archive_20261012.json`.
Maintenance after 04:08 CST overlaps later V27 native collection windows.
This archive is evidence preservation and disk cleanup, not a performance
optimization or proof of GPU/JCT improvement.

Archive: `experiments/archive/20261012/v26_partial_workspaces.tar.zst`

SHA256: `bee0d39e5ca913b6bede50f39b8f78d2c59f84029bc7761706eb24f6086e7da4`

The 2,340,329,743-byte archive preserves the 50 administratively truncated
V26 workspaces. The source allocation was 16,142,340,096 bytes. GNU tar
comparison passed before cleanup, and the interrupted run retains its
traces, outcomes, manifest, frozen plan and an `ARCHIVED.md` pointer.
Restore with the same command above, substituting the V26 archive name.
Receipt: `experiments/reports/v112_v26_workspace_archive_20261012.json`.
The conservative workspace archive interval is 04:08:00-04:23:32 CST.
Available filesystem space was 56,780,939,264 bytes at the final check;
concurrent experiment writes are included in that observation.

## 2026-10-11 verified compression and snapshot deduplication

Current archive:
`experiments/archive/20261011/september27_failed_repeat_exports.tar.zst`

SHA256: `a19fd9e58447fc25f32efc49cbc7c62a163e238317f952630d9a6a5711cd91e5`

This 493,602,439-byte archive preserves the two
`dataset_failed_repeat_v10_20260927` directories beneath the September 25
training and calibration runs. The original raw server/workflow traces,
usable datasets, manifests and model artifacts remain in place. All 18
archived files were compared with their sources using GNU tar before removing
the 16 large JSONL originals. Both source dataset manifests and `ARCHIVED.md`
pointers remain under `experiments/raw/`. Net reclaimed allocation is about
28.51 GiB. Restore the original paths from the repository root with:

```bash
tar --extract --use-compress-program=zstd \
  --file experiments/archive/20261011/september27_failed_repeat_exports.tar.zst \
  --directory experiments/raw
```

Separately, four byte-identical V13/V14 server snapshots now share hardlinks
with the original completed-run logs, reclaiming about 7.22 GiB without
changing any paths or bytes. The two differing `server.log` snapshots were
preserved. Receipts are
`experiments/reports/v86_september_failed_exports_archive_20261011.json` and
`experiments/reports/v86_v13_v14_snapshot_dedup_20261011.json`.

Read/compression work used idle I/O priority and one compression thread.
The conservative maintenance interval is 2026-10-11 17:52:00-18:04:35 CST;
record its overlap when evaluating live V23 native windows. V23/V22 raw data
and the model artifacts were preserved. Older sections below are historical
operation records, not a fresh verification of those archives' availability.

## 2026-07-27 P1-P5 raw consolidation

Archive root: `experiments/archive/20260727/superseded_raw/`

- `deepagents_swebench/`: P1/P1.5/P2 development and validation runs. The
  original contents are preserved because historical reports cite their
  telemetry, but they are no longer active P5 reproducibility anchors.
- `p3_agentic_waves/` and `p3_gpu_ready_probe/`: P3 workload probes superseded
  by the fixed 24-workflow trace.
- `p4_joint_shadow_24/`: all P4 shadow attempts, including interrupted runs and
  the negative chunked-prefill correctness gate.
- `p5_joint_retraction_24/`: empty/incomplete P5 launch attempt.
- `p5_overlap_retraction_fix_probe/`: interrupted overlap/retraction probe.
- `p5_performance_24/`: invalid 24-workflow run; zero workflows completed.
- `p5_fixed_trace_stability_24/`: fixed-trace control-plane stability evidence.
  Retraction and barrier lifecycles passed, but all workflows hit the 600-second
  activation deadline, so it is excluded from performance results.

This cleanup moved about 33 GiB from active raw storage into the archive. It did
not delete data or reclaim disk space.

## 2026-07-17 cleanup

Archive root: `experiments/archive/20260717/raw/`

- `deepagents_swebench/20260716T160210Z/`: initial smoke iterations superseded
  by concurrent runs.
- `deepagents_swebench/20260716T162833Z/`: early planned run with server errors.
- `deepagents_swebench/20260716T165200Z/`: pre-liveness-fix comparison and
  pressure runs retained as historical evidence.
- `deepagents_swebench/20260716T175417Z/`: H2D trigger deadlock diagnosis.
- `deepagents_swebench/20260716T181114Z/`: prefetch-trigger run that exposed the
  non-idempotent `COMMIT_CPU` acknowledgement.
- `codex_qwen14b/`: all Codex/Qwen2.5-14B gate, timeout, join-guard, incomplete,
  and superseded reactive runs except baseline r5 and reactive r16.

No artifacts were permanently deleted in this cleanup. To restore an archived
run, move it back under `experiments/raw/` using its original basename.

## 2026-07-22 P3 workload reclassification

Archive root: `experiments/archive/20260722/p3_correctness_only/`

- `raw/p3_dynamic/`: one-shot or tool-free cyclic/mixed runs, including the
  12-workflow pressure run. These validate topology, event delivery, physical
  correctness, and capacity pressure only.
- `raw/p3_agentic/`: single-workflow development runs with deliberately small
  turn/model/tool limits. These validate the persistent peer and dynamic task
  mechanism only.
- `raw/deepagents_swebench-20260721T074957Z/`: the single-workflow P3 policy
  snapshot smoke.
- `processed/p3_20260721T074957Z/` and `processed/p3_dynamic_*`: derived B0-B4,
  source-context, timeline, and rolling-oracle artifacts based on the same weak
  workload assumptions.

The files remain available for correctness regression and historical auditing,
but must not be used in a performance table or as evidence for realistic Agent
load. Replacement experiments use productive multi-turn subagents, minimum
LLM/tool intensity gates, and recorded batched root arrivals.

## 2026-07-22 invalid high-pressure attempt

Archive root: `experiments/archive/20260722/p3_invalid_guard_limited/`

- `20260722T062148Z/`: the first eight-workflow batched-arrival run reached high
  HBM and Host-KV pressure, but the workload was interrupted after the legacy
  diagnostic-probe heuristic forced productive agents to complete. At each
  observed trigger, the recent `python -c` calls and outputs were distinct and
  `consecutive_no_progress` was zero. The run is retained only as failure
  evidence and must not be used for performance claims.

The diagnostic-only heuristic was removed. Subsequent runs are invalid unless
every workflow completes naturally without a stuck-guard trigger or forced
semantic completion.

Archive root: `experiments/archive/20260722/p3_invalid_task_loop/`

- `20260722T064010Z/`: diagnostic rerun after removing the false-positive
  probe guard. It reached 14.83/16.11 GB physical HBM KV and 7.16 GB Host KV,
  with 236 LLM requests, 223 repository-tool calls, and five runtime-spawned
  children before deliberate termination. `sympy__sympy-17630` repeated the
  same failing command three times, correctly triggering the generic progress
  guard, so the run is excluded. It established the load envelope and informed
  task selection only.
- `20260722T070454Z/`: eight-workflow rerun with the first task replacement.
  It produced 257 LLM requests, 299 repository-tool calls, four dynamic
  children, and one natural child RETURN/JOIN before termination.
  `sympy__sympy-17318` then accumulated three consecutive command failures and
  repeated the final failing command exactly, so this run is also excluded.

Archive root: `experiments/archive/20260722/p3_invalid_guard_tuning/`

- `20260722T072106Z/`: seven-workflow candidate run stopped when a productive
  child repeated the same successful file read three times. The detection was
  mechanically correct, but the three-call threshold was too aggressive for
  the intended high-intensity workload. Agentic experiment defaults now allow
  six identical calls/errors, while the eight-call no-progress guard and the
  48/128 activation budgets remain as hard liveness bounds.

Archive root: `experiments/archive/20260722/p3_invalid_guard_bug/`

- `20260722T072855Z/`: seven-workflow run that exposed a structured-content
  accounting bug. One child issued 35 distinct successful commands in a
  parallel tool batch, with unique command and output hashes, but
  `ToolMessage.text` flattened the structured results to an empty string and
  falsely reported 35 no-progress calls. Progress hashing now canonicalizes
  the raw `ToolMessage.content`; a regression test covers unmatched parallel
  call IDs and distinct structured outputs.
- `20260722T074817Z/`: follow-up run that exposed call-versus-batch accounting.
  A single AI response launched 44 parallel failing `read_file` calls. The old
  guard counted these as 44 consecutive failed decisions and forced the child;
  the corrected guard counts them as one failed decision batch while retaining
  all 44 calls toward the 128-call hard budget.

Archive root: `experiments/archive/20260722/p3_aborted_autonomous/`

- `20260722T080336Z/`: explicitly stopped at the user's request after confirming
  that unconstrained autonomous spawn remained unstable for a clean workload
  gate. It is retained as resource-characterization evidence only. The next
  bounded probe uses runtime-enforced initial fan-out of 2--4 subagents.
