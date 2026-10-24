# Experiment Invariants

- Every future workload must use a different original project for each root
  workflow. Different task IDs, labels or sessions from the same project do not
  satisfy this rule. Retain it when selecting a smaller workload population.
- Keep historical shared-project experiments as historical evidence. Source
  capture, replay qualification and comparison startup must reject new
  shared-project populations.
- The current workload has 36 roots from 36 projects. If sustained useful-state
  loss and extra prefill work show substantially increased recomputation, reduce
  simultaneous workflows to 32 and further if needed. Running/decode limits are
  separate settings; do not silently substitute a batch-size change.
- Count initial cold prefill separately from missing previously served
  same-session prefixes. Cache occupancy and prefix-loss proxies alone do not
  prove expensive recomputation.
- After changing the workload population, collect its matching natural native
  trajectory. Reuse that capture as the native baseline; do not schedule another
  full native replay. Compare Full and controls with the same tokens,
  dependencies, stopping conditions, capacities and seeds.
- Preserve the frozen source trees of running experiments. Qualify and replace
  pending registrations explicitly when launcher code changes.
