# Experiment Invariants

- Every future workload must use a different original project for each root
  workflow. Different task IDs, labels or sessions from the same project do not
  satisfy this rule. Retain it when selecting a smaller workload population.
- Keep historical shared-project experiments as historical evidence. Source
  capture, replay qualification and comparison startup must reject new
  shared-project populations.
- The next workload calibration uses Qwen3-Coder-30B-A3B-Instruct and 18
  simultaneous roots from 18 projects, with model-directed multi-round/nested
  delegation and child soft work targets. Keep the historical 24-root sources.
  Running/decode limits remain 24 and are separate from workflow count. If
  sustained useful-state loss and extra prefill work show substantially increased
  recomputation, revisit simultaneous workflow count. Running/decode limits are
  separate settings; do not silently substitute a batch-size change.
- Dynamic roots must complete at least one real model-selected delegation before
  final return. Keep initial tool choice automatic; defer an undelegated final
  answer with a recorded reminder and at most two required task attempts. The
  model chooses assignments, fanout, timing, later rounds and nesting. Failed
  attempts and safety-finalized violations do not qualify a source. Children
  retain their independent soft budgets and may naturally return without nesting.
- Root direct-work phase budgets are optional workload controls: remind at 16
  root calls and use 24 as a soft phase target, resetting only after a real
  completed delegated JOIN. Do not cap total workflow turns or truncate work.
  A changed prompt/budget requires its matching natural native capture.
- V181 completed all 18 workflows with 36 JOINs and one nested JOIN, without
  root phase budgets. Keep it frozen. Its old
  waiting V182 qualification was stopped for replacement; the planned V183 must
  not launch. Qualify updated main before registering the repaired Full.
- FULL-only EOS planning must not require an absent Mamba pool when state demand
  is zero. Credit only actual pre-boundary H2D and verified first-use evidence.
- Ordinary selected-request prefetch is an explicit follow-up candidate. Allow
  it alongside tool prefetch; keep one selected consumer for at most 1500 ms,
  retain JOIN precedence and native admission, and require existing free capacity.
- A separately declared frontier cold-reclaim candidate may use a settled
  Host-backed duplicate for the actual selected queue head. Probe at most once
  per 200 ms with at most 16 native candidate checks; never create a D2H backup,
  preempt an active request, or grant ordinary requests critical-parent queue-tail
  donor rights. Prefer an already fitting prefix before reclaiming more space.
- During critical parent recovery, free request rows are not a selected prefill
  batch. Protect the actual queue head and ready JOIN dependencies; validate
  unstarted ordinary queue tails as possible native FULL donors. Preserve all
  native lock, allocation, identity, Host-copy and DMA checks.
- Count initial cold prefill separately from missing previously served
  same-session prefixes. Cache occupancy and prefix-loss proxies alone do not
  prove expensive recomputation.
- After changing the workload population, collect its matching natural native
  trajectory. Reuse that capture as the native baseline; do not schedule another
  full native replay. Compare Full and controls with the same tokens,
  dependencies, stopping conditions, capacities and seeds.
- Retain the original pretokenized `/generate` replay, as explicitly selected
  by the user. Do not require chat-ingress alignment or raw chat-body capture.
- Preserve the frozen source trees of running experiments. Qualify and replace
  pending registrations explicitly when launcher code changes.
- Successful causal comparisons must observe two freshly published idle native
  telemetry snapshots before server cleanup. Keep this bounded collection drain
  outside measured workflow JCT. A stale queue-depth snapshot is not proof of a
  complete shutdown trace; retain the original status and failed audit.
- Do not run ablations until predictive establishes clear gains over native.
  Keep completed native/Full results and administratively interrupted controls.
