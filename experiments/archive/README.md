# Experiment archive

Superseded, interrupted, and debugging-only raw runs are moved here rather than
deleted. The archive is local-only and excluded from Git; this README is the
tracked inventory.

## 2026-10-12 verified workspace compression

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
