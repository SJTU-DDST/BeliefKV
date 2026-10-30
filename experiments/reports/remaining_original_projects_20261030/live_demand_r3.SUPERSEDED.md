# Superseded CPU Sample Classification

`live_demand_r3.json` is retained but must not be used for the workload decision.
The new periodic observation label was overwritten by the runtime-event loop's
local `kind` variable. Token unions and timestamps are still observations, but
the regular-sample classification is invalid.

The CLI regression fails on that implementation:
`oracle_admission_repair_20261030/demand_observation_tag_prior_cpu_r1.xml`.
After separating `observation_kind`, the focused 30-case check passes:
`oracle_admission_repair_20261030/demand_observation_tag_fixed_cpu_r1.xml`.

Use `live_demand_r4.json` for the corrected live prefix, or the separately named
complete-demand report after the full control exits. No GPU source, request,
arrival, stopping condition or JCT boundary was changed by this CPU audit repair.
