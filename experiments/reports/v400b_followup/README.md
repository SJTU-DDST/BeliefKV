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
