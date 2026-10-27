# Reference tool-offload consumer selection

This is follow-up CPU evidence associated with V400b. The running experiment
remains frozen at `a5e31ae7464f`; none of these changes ran in that experiment.

The production defect is in `DemandAwareToolOffload.dispatch()`: a first waiter
needing Host recovery without active reference eligibility, or without a current
physical capacity deficit, prevents selection of a later usable consumer.
The repair applies existing demand checks before selecting the reference-mode
consumer. It preserves the eight-candidate bound, 200ms cadence, prediction,
per-release revalidation and native physical authority. Retained admission and
paper policy behavior remain unchanged.

Evidence:

- `consumer_selection_before.xml`: an initial passing fixture with mismatched
  request/graph identities. It did not reach the target path and is not
  reproduction or repair evidence.
- `consumer_selection_reproduced.xml`: both corrected native counterexamples
  fail with the original production code.
- `consumer_selection_targeted.xml`: all 242 related cases pass after repair,
  with no skipped cases. Each corrected counterexample checks a real release
  of 32 Device slots, preservation of Host backing, and unchanged reference
  program state.

The valid runs use Python
`/home/longhao/miniconda3/envs/beliefkv-next/bin/python`,
`CUDA_VISIBLE_DEVICES=99999`, `PYTHONHASHSEED=21`, nice19 and idle I/O priority.
`SGLANG_SOURCE_CHECKOUT` is
`/home/longhao/experiment/.worktrees/sglang-causal-11b84f7c3198-bdd74e9c280b`,
with native tree `11b84f7c31986782be3fbe699bae2574ef092389`.
The first unconfigured invocation skipped both native cases and is not counted.

Reproduction used
`tests/test_reference_thunder_admission.py -k skips_unusable_waiter`.
The passing targeted suite consists of
`tests/test_reference_thunder_admission.py`,
`tests/test_predicted_tool_offload.py`,
`tests/test_tool_policy_adaptation.py` and
`tests/test_tool_predictive_transfers.py`.
All invocations use `python -m pytest -q ... --tb=short --junitxml=...`.

Native copy, event and Host-index stand-ins remain explicit. These tests do not
establish real GPU behavior, historical defect frequency, prediction quality,
control cost or a complete JCT/throughput improvement.

`reference_analysis_targeted.xml` contains 35 passing checks for the offline
restore, eligibility and sampled-wait readers. Reference program counts do not
identify active agents or complete logical budgets. The restore reader now
keeps those missing fields unknown while still reconciling actual request
Host hits with physical demand H2D bytes. No serving policy changes are
introduced by this analysis compatibility repair.

`shared_cpu.xml`, `shared_cpu.sources.json` and `shared_cpu.evidence.json`
record one complete source-bound regression: 3038 passing tests plus four
passing subtests, no failures or skips. All 3035 prior test identities are
retained and three are added. The actual Full, default ThunderAgent and
acting-decay ThunderAgent constructors remain isolated, and the reference
scheduling/lifecycle methods are still inherited without replacement.
The qualifier is `scripts/qualify_reference_thunder_admission.py`, using
`v399_reference_thunder_offload_cpu_evidence_20261027.json` as its prior.
No new GPU run is registered by this qualification.

`complete_review.py` independently verifies complete V400b against completed
V345, including exact receipt work, native stopping, final configuration,
unchanged plotted input prefixes, two fresh idle snapshots and actual owned
exit. Its output is `complete_review.json`; full restore reconciliation,
critical paths and unexpanded sampled waits are retained in the other
`complete_*.json` files. The final figures are in
`experiments/figures/decode_diagnostics_20261027_v402/`.
These describe the frozen original candidate, not the follow-up repair:
mean JCT improves 2.87157%, while complete output throughput improves only
0.03702%. The primary performance goal remains incomplete.

`tail_request_waits.py/.json` aligns all 201 requests on the identical realized
final-workflow paths. Epoch38 accounts for a 194.553-second queue increase:
Full waits194.641 seconds after a3.015-second tool, while the control waits
0.088 seconds. The cache counters are observed at service, not at arrival.
Old aggregate snapshots cannot identify this request's exact eligibility.

Follow-up reference snapshots now expose actual active, paused-pending and
marked-for-pause identities plus undiscounted logical budget once per second,
without changing policy state or paper controls. The48 passing targeted cases
are in`eligibility_snapshot_targeted.xml`. The32-program synthetic CPU cost
fixture in`snapshot_cpu_cost.py/.json` measures status construction only:
median10.691us andP99 13.734us, versus parent aggregate3.817/4.720us.
These additions do not retroactively change the frozen V400b evidence.
