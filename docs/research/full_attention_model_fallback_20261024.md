# Full-Attention Model Migration

Status: activated by the user's 2026-10-24 instruction. The next workload is
24 simultaneous distinct-project workflows, running/decode 24, with Full-only
capacity/observer/planner support and model-directed nested delegation. Native
capacity must be discovered with the new model and matched in Full replay.
Only Full follows natural native capture; ablations are deferred until clear
native gains. No new-model performance result is accepted yet.

V152 is preserved as a rejected source after
a child entered a repeated-grep loop and required guard intervention. Fresh
V155 also reproduced prolonged repeated searches and was interrupted together
with its V156 queue. V158 exposed an incompatible CUDA compiler/runtime on
the stochastic sampling path; V159 stopped and V160 never started.
V161's sampler check passed,but model startup exposed a missing CCCL header;
V162 stopped and V163 never started. The isolated toolchain now includes CCCL,
and the expanded sampler plus native BF16 activation preflight passes on H200.
The next V164/V165/V166 sequence uses the explicit coding profile with the
complete toolchain and preserves original native sampling work in replay.
V164 subsequently completed all 36 original workflows without a sustained
repeated-grep loop or guard-finalized completion; V166 Full improved mean
JCT 1.911% and output throughput 1.356% on exactly that work.
These generation-policy and environment corrections do not
qualify a KV-management gain or change the conditional model-migration test.
The implementation and next configuration are now tracked in
`configs/migration/qwen3_coder_24project_running24_20261024.json`.

## Decision

Proceed with the Full-only adapter and new model under the user's explicit
instruction, preserving the latest 24-project workload requirement.
Keep the hybrid-model results as evidence of the design's operating boundary.

The preferred model is `Qwen/Qwen3-Coder-30B-A3B-Instruct`:

- Apache-2.0, 30.5B total parameters, 3.3B activated, and 48 attention layers.
- Four KV heads, head dimension 128, no sliding-window attention in the local
  config, and a native 262144-token context.
- Coding/tool-oriented behavior fits the existing repository tasks.
- The BF16 model is already present locally. All 16 weight shard files declared
  in its index are present. This is an availability check, not weight-integrity
  or GPU-serving verification.

`Qwen/Qwen3-30B-A3B-Instruct-2507` is an alternative with the same Full KV
geometry and native context length. A dense model such as `Qwen/Qwen3-32B`
matches total size less well in active computation: replacing an approximately
3B-active MoE with a dense 32B model could increase model-execution cost.

Official model cards/configs and the SGLang HiCache documentation were reviewed.
The exact local config, tokenizer and index evidence is recorded in V3420 and
V3421. A direct upstream metadata request failed with a TLS error; no pinned
remote revision or byte-identical local/upstream match is claimed.

## KV And Capacity

For BF16 and TP1, logical Full KV bytes per token are:

`full_layers * 2(K,V) * kv_heads * head_dimension * 2(bytes)`

| Model | Full layers | KV heads | Head dim | Full KV/token | At 131072 tokens | At 262144 tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current Qwen3.5-35B-A3B | 10 | 2 | 256 | 20 KiB | 2.5 GiB | 5 GiB |
| Qwen3-Coder-30B-A3B-Instruct | 48 | 4 | 128 | 96 KiB | 12 GiB | 24 GiB |

The candidate has 4.8 times the logical Full KV per token. The current model's
Mamba state is excluded from this comparison. V152 actually allocates
36.822 GB of device Full and 33.096 GB of device Mamba state. Removing Mamba
therefore releases a substantial pool; allocate the real available Full-only
capacity rather than retaining a fictitious Mamba reservation.

More Full layers also increase attention computation. Native layerwise H2D
overlap remains relevant, while the Mamba pre-forward state dependency goes
away. These changes do not guarantee greater exposed restoration wait.
GPU utilization alone does not isolate arithmetic, HBM bandwidth or PCIe
stall time. Measure layer readiness, capacity wait, prefill work and model
service intervals before attributing a bottleneck.

V3421 retokenizes all 36 unchanged source packs with the local candidate
tokenizer. The source-only range is 33126--178191 tokens, mean 83539.
These counts exclude system prompts, tool schemas, chat framing and generated
history. Complete first inputs still need template-level validation.

| Roots | Source tokens with candidate tokenizer | Source-only Full KV, GB |
| --- | ---: | ---: |
| 8 | 646309 | 63.535 |
| 12 | 942545 | 92.656 |
| 16 | 1263261 | 124.184 |
| 24 | 2079636 | 204.437 |
| 36 | 3007406 | 295.640 |

Each row uses the first N projects in the existing seeded manifest, without
outcome selection. These are logical working-set estimates, not simultaneous
physical residence or proof that Host caching will retain all useful state.
The user's current population is 24 different projects. Size running/decode
separately using the measured Full pool and request headroom. Increase or
decrease workflow concurrency based on useful-state loss and extra prefill.
Keep one original project per workflow in every population.

## Adaptation Work

1. Determine model components once at startup. Support the real `{FULL}` layout
   alongside `{FULL,MAMBA}`; keep absent state capacity at zero rather than
   inventing slots. Update pool ownership/byte observations, free-list headroom,
   telemetry, capacity equality checks and capture validation.
2. Add Full-only session input inspection and restore planning. The native
   `inspect_beliefkv_reentry` currently indexes Mamba state, and the application
   checkpoint burst requires both Full and Mamba leaves. A Full-only reusable
   prefix must retain actual input matching, generation/ancestry checks, native
   allocation checks and outstanding-DMA correctness dependencies.
3. Reuse Full-component backup, reclaim, transfer receipts, layer readiness
   and native admission. Adapt release/locking and byte estimates to the
   detected components. Preserve EOS JOIN triggering, selective background
   backup, tool-duration offload, and the agreed scheduling policy.
4. Introduce model-specific launcher settings. Remove hybrid-only Host split,
   Mamba slot/ratio assumptions and reasoning-parser defaults for the candidate's
   non-thinking output. Keep BF16 KV, fixed seeds and matched capacities.
   Rebuild transfer calibration for 48 Full layers; validate tool-duration
   prediction on the new command mixture with only causally available history.
5. CPU-qualify real native Full-only fixtures for incremental backup, partial
   layer readiness, capacity rejection/retry, parent/child lifecycle, cancellation
   and no-op state paths. Then run a bounded GPU check of real pools, exact token
   receipts and recovery before recording a new natural native trajectory.

The main application blockers are in `sglang_v0520_observer.py`,
`sglang_v0520_physical.py`, `sglang_v0520_runtime.py`, source/compare capacity
validation, and the hybrid launch/environment code. The native unified cache
already has Full-only components, but our added hooks still need adaptation.
Changing `MODEL_PATH` alone is insufficient.

## Live Native Evidence

V168b now serves the new model from application `5da64f84a769` and engine
`413cc14e6c53`. The first V168 attempt is preserved as a startup failure:
periodic usage telemetry indexed the absent Mamba high-water entry. The fix
publishes only actual pools. Native capacity is 805198 device FULL tokens
(79.154GB), 2034506 Host FULL tokens (200.000GB), and zero Mamba slots.
V169b waits for the completed source before bounded replay qualification;
V170b will replay Full only on the same work.

Before Full started,V3513 reproduced an EOS adaptation defect with the real
Qwen3-Coder tokenizer/template: a nonempty normal report had no accepted EOS
proof because the validator required a reasoning wrapper. The new tokenizer
has thinking-token IDs, but its chat template does not emit thinking markers.
V3514 administratively stopped the waiting V169b before GPU use, preserving
V168b. Replacement V169c/V170c must use the qualified fix. Startup now records
whether a known string chat template has no thinking wrappers; completed
plain reports can then supply body evidence. Unknown templates, unclosed
thinking, tool output, empty reports, and non-normal stops remain guarded.
The change adds no per-token work. V3515 passes 312 focused cases, including
the actual model's EOS-to-JOIN-stage path with the semantic worker disabled.
V3517 then passes all 5744 required cases, retaining the prior 5721 identities
and adding five FULL-only lifecycle cases plus 18 EOS cases. The engine is
explicitly first in PYTHONPATH and the cached sandbox image is selected.
V3518 repeats the actual tokenizer reproduction with accepted EOS evidence.
V3516's broader run is retained as an invalid environment attempt: collection
loaded installed unpatched SGLang before fixture path setup. It does not
qualify the source or establish that all repository tests pass.

Supplemental main commit `e68c2261` adds real FULL-only CPU tree/allocator
lifecycle tests. V3507 passes 23 focused cases, including five new cases for
incremental backup, ACK-gated capacity release, ordered recovery, stale/input
validation, rejected enqueue, and outstanding-load cleanup. Copy queues remain
stand-ins: neither partial-layer DMA correctness nor GPU speedup is inferred.
The registered serving source hashes and active frozen trees are unchanged.

The V3510 sealed live window has 1193 completed H2D batches, 13.888TB payload,
and 441.570 seconds of cumulative H2D stream time. All 75 sampled restoring
prefills pair with actual worker intervals. GPU dependency wait P50/P90 is
255.418/498.824ms; sampled wait sums to 19.765 seconds and 70.274% of observed
GPU layer spans. Of the sampled wait, 98.870% occurs after the first layer.
Among 29 short single-request samples, wait P50 is 239.477ms. These observations
show that native layerwise overlap leaves material exposed wait in this
workload. They do not establish complete-workflow savings.

V3511 checks the one-in-16 sampling sequence, successful pairings, and absence
of missing-layer/bounded-queue skips. Its mean-times-eligible-count estimate of
314.401 seconds is a scale estimate, not an all-batch measurement. Separately,
36 unique sampled batches lie on the observed last-completing dependency paths
of 11 completed workflows, totaling 11.048 seconds. Shared batches contribute
to several workflows; summed per-workflow sampled latency is 14.682 seconds.
In-progress workflows and unsampled waits prevent a complete JCT attribution.

The opportunity is mainly short-tool reentry. V3512 assigns 73 of the older
75 samples to tool successors. In its newer live prefix, 1805 of 2414 served
tool successors follow tools shorter than 50ms and only 65 follow tools lasting
at least one second. Tool-successor queue-to-service P50 is 11.749 seconds.
The 28 served JOIN successors have queue P50 9.687 seconds; the observed last
child model completion to parent client submission P50 is 71.576ms. Do not
present total native H2D or exposed wait as attainable JOIN/tool anticipation.
Full must demonstrate actual residence preservation, less repeated recovery,
and better complete-workflow metrics. A future continuity policy needs bounded
service and revocable capacity, rather than unlimited tool-return priority.

One-second external thermal observation started after V168b was already
running and follows the source/qualification/Full chain. V3511's approximately
27-minute window records SM clocks from 1335 to 1785MHz, constant 3201MHz HBM,
and zero thermal-slowdown counter increments. Power capping is present. Earlier
startup thermal history remains unknown. Preserve the current pair's unchanged
clock/power policy; a future controlled-frequency pair needs exclusive ownership
and automatic restoration before GPU release.

Evidence:

- `experiments/reports/v3507_full_only_lifecycle_migration_cpu_20261024.xml`
- `experiments/reports/v3509_full_only_native_lifecycle_qualification_20261024.json`
- `experiments/reports/v3510_v168b_restore_critical_window_20261024.json`
- `experiments/reports/v3511_v168b_restore_wait_estimate_and_clock_scope_20261024.json`
- `experiments/reports/v3512_v168b_join_tool_restore_opportunities_20261024.json`
- `experiments/reports/v3513_qwen3_coder_plain_eos_reproduction_cpu_20261024.json`
- `experiments/reports/v3514_v169b_plain_eos_qualification_stop_20261024.json`
- `experiments/reports/v3515_plain_template_eos_focused_cpu_20261024.xml`
- `experiments/reports/v3517_plain_template_eos_qualified_cpu_20261024.xml`
- `experiments/reports/v3518_qwen3_coder_plain_eos_fixed_cpu_20261024.json`

## Acceptance

Collect a new natural native trajectory with the new model and reuse it as the
native reference. Do not replay an old model's token trace or compare different
work across models as a scheduler speedup. Replay Full on
the same new tokens, dependencies, tool delays and original stopping behavior.
Retain normal tails, guards and workload-quality limitations.

The evidence must show useful migration and exposed native restoration wait
without excessive useful-state loss or extra computation. Accept the migration
direction only when Full improves complete-workflow JCT and throughput before
proceeding to independent module or scheduling controls.

CPU evidence:

- `experiments/reports/v3420_full_attention_model_selection_cpu_20261024.json`
- `experiments/reports/v3421_qwen3_coder_project_contexts_cpu_20261024.json`
