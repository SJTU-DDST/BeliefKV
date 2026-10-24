# Conditional Full-Attention Model Migration

Status: researched and CPU-sized; Full-only serving adaptation and GPU
qualification have not started. V152 is preserved as a rejected source after
a child entered a repeated-grep loop and required guard intervention. Fresh
native capture V155 and the V156/V157 qualification/control queue replace it.
This document changes no serving behavior.

## Decision

If the current project-disjoint comparison still does not establish useful
workflow JCT and throughput gains over native, and an independent KV-management
gain over scheduler-only, proceed with a Full-only adapter and a new model.
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
Start capacity validation with 12 different projects. Size running/decode
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

## Acceptance

Collect a new natural native trajectory with the new model and reuse it as the
native reference. Do not replay an old model's token trace or compare different
work across models as a scheduler speedup. Replay Full and scheduler-only on
the same new tokens, dependencies, tool delays and original stopping behavior.
Retain normal tails, guards and workload-quality limitations.

The evidence must show useful migration and exposed native restoration wait
without excessive useful-state loss or extra computation. Accept the migration
direction only when Full improves complete-workflow JCT and throughput and
establishes a KV-management benefit over the scheduling control.

CPU evidence:

- `experiments/reports/v3420_full_attention_model_selection_cpu_20261024.json`
- `experiments/reports/v3421_qwen3_coder_project_contexts_cpu_20261024.json`
