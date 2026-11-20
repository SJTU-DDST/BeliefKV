# Storage Maintenance, 2026-11-21

This maintenance covers three disjoint checkout populations and two historical
telemetry layouts. It reuses the previously verified Git, process, container,
reference, snapshot and archive guards without changing serving behavior.

Completed: 98 checkouts retired and 221 historical log files from 106 runs
archived. Independent reviews observe 211,659,100,160 net reclaimed bytes.
Final available disk space is 322,273,931,264 bytes; df usage fell from 97% to
92%. Home-directory allocation fell from approximately 952.02 GB to 741.16 GB.

Checkout selections:

- 28 inactive detached checkouts under `.worktrees`.
- 34 inactive branch checkouts, retaining their original named branches.
- 36 inactive detached review checkouts stored directly under `experiment`.

Checkouts created on or after November 18, local modifications, current
references, locks and live process/container inputs are excluded. Original
commits and HEAD reflog history remain pinned. Ignored files, symbolic links
and original Git administration are archived before ordinary non-forced
worktree removal. The independent reviews also verify that the original
named branches remain at their original commits.

Historical log selections predate November 14. The first batch uses existing
top-level metadata. The second accepts existing stage metadata, requiring
original server identity and launch configuration and hashing all selected
small stage JSON files. This adapts the archive check to an existing directory
layout; it does not invent or repair experiment outcomes.

Each log passes complete tar byte comparison before original-copy removal.
The independent reviews verify compressed SHA256, exact archive membership,
unchanged metadata and per-run restoration markers. Request tokens, workflow
events, source captures, results, configuration and current inputs remain.

The following compact reviews are tracked; larger selection and execution
receipts remain locally alongside them:

- `worktree_review.json`
- `branch_checkouts/worktree_review.json`
- `direct_checkouts/worktree_review.json`
- `log_review.json`
- `stage_logs/log_review.json`
- `restoration_review.json`
- `final_storage_review.json`

The final storage review records allocated blocks, filesystem headroom,
independent reclaimed totals and the hashes of all review inputs. Other-user
and running-experiment writes can change directory and df differences.

Recovery indices are in the corresponding archive roots:

- `experiments/archive/20261121_retired_worktrees/README.md`
- `experiments/archive/20261121_retired_branch_checkouts/README.md`
- `experiments/archive/20261121_retired_direct_checkouts/README.md`
- `experiments/archive/20261121_historical_logs/README.md`
- `experiments/archive/20261121_historical_stage_logs/README.md`

Compression uses one zstd thread per archive, nice19 and idle I/O. This
maintenance does not launch GPU work, stop jobs or clean another user's data.
