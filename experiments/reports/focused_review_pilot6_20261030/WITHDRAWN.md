# Withdrawn Before GPU Capture

The user rejected replacing complete dataset tasks with narrower derived
deliverables on 2026-10-30. This candidate must not be registered or launched.
Its manifests, preparation receipts, CPU results and failed checks remain as
historical preparation evidence.

Affected inputs:

- `configs/workloads/locobench_focused_review_pilot6_20261030.json`
- `configs/migration/workflow_focused_review_pilot6_native_20261030.json`
- `scripts/prepare_focused_review_pilot.py`

There was no GPU capture or comparison of this candidate. The full CPU run
completed with 5629 tests and 12 subtests passing. That result validates the
recorded source snapshot, not the suitability of the withdrawn tasks.

Future candidates must select complete original scenarios from the pinned
LoCoBench-Agent archive. Short-context originals are allowed, and the mixed
population must also retain long-context originals. Keep every workflow on a
different original project and validate prompt equality and scenario hashes.
Difficulty and context length are candidate descriptors, not measured work.
