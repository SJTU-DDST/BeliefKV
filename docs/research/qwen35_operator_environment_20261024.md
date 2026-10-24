# Qwen3.5 Operator Environment Audit

Observed on 2026-10-24, Asia/Shanghai. This audit reads the completed V164
native source and the collecting V166 Full prefix. It does not change either
frozen serving tree or start a GPU profiler.

The environment already uses specialized and fused Qwen3.5 GDN operators.
It still has incomplete optimizations, and existing telemetry cannot assign
an exclusive GPU-time share to linear attention, MoE or transfer.

## Runtime Evidence

The active engine tree is `7d77eeccefb31b2465050ddebb1b58ca9d9213a6`.
Installed package metadata reports SGLang 0.5.20, PyTorch 2.13.0,
Triton 3.7.1 and FlashInfer 0.6.18. The package version alone does not describe
the local engine patches; the report also hashes the inspected source files.

Both V164 and V166 startup logs resolve linear attention to
`decode=triton, prefill=flashinfer, verify=triton`. Both log active fused
GDN decode QKVZ/BA unpack plus indexed Conv1D, without an observed fusion
fallback log in the inspected prefixes.

| Path | Inspected implementation | Evidence and limitation |
| --- | --- | --- |
| Decode projection unpack and Conv1D | `gdn_backend.py:692` | Active fused-path startup log |
| Decode QKV extraction, gating, normalization and recurrence | `kernels/gdn_triton.py:45` | Packed decode supported on CUDA; `gdn_backend.py:773` dispatches it |
| GDN prefill core | `kernels/gdn_flashinfer.py:477` | Runtime resolves FlashInfer; wrapper still has independent preparation and state operations |
| Output RMSNorm and activation gate | `kernels/ops/attention/fla/layernorm_gated.py:489` | Combined Triton layernorm path on CUDA |
| Decode graph | full CUDA Graph | All 158951 native and 32228 collecting-Full workflow-attributed service records report replay |
| Prefill graph | breakable CUDA Graph | All 5653 native and 4052 collecting-Full workflow-attributed service records report replay; this does not put every operation inside one graph |

`enable_torch_compile=false` does not disable explicitly implemented Triton
or FlashInfer fusion. Likewise, graph replay reduces launch work but does
not establish that all graph nodes have become one fused kernel.

## Remaining Gaps

### Short-Prefill Submission

The FlashInfer wrapper still prepares Q/K/V, converts gate values and
sequence indices, gathers initial state and writes final state back to the
pool. A specialized core kernel does not fuse this entire wrapper.

For single-request prefills with fewer than 512 actual extend tokens:

| Observation | Completed native | Collecting Full |
| --- | ---: | ---: |
| Paired sampled batches | 128 | 84 |
| Mean selection thread CPU | 11.484 ms | 11.390 ms |
| Mean run_batch thread CPU | 55.553 ms | 48.437 ms |
| Mean run_batch wall interval | 66.107 ms | 49.538 ms |

These are different temporal populations, not matched-cohort speedup
estimates. `run_batch` includes Python dispatch, operators and graph
submission, and overlaps GPU work. Its CPU duration is not GPU idle time
or removable JCT. The samples support investigating the common short-prefill
submission path; they do not prove GDN accounts for all of this cost.

### Unconnected Merged Input Projection

`Qwen3_5GatedDeltaNet.finalize_fused_in_proj()` can join the QKVZ and BA
weights so small inputs use one GEMM. In the inspected tree the only caller
is the `qwen4_exp.py` loader. The Qwen3.5 loaders do not call it, and the
weight starts as `None`, so this optimization must not be reported as
enabled for the current model. Simply connecting it is not a validated
speedup: test the actual Qwen3.5 shapes, state correctness and short/long
prefill behavior before selecting it.

### MoE Tuning Lookup

Both arms report missing
`E=256,N=512,device_name=NVIDIA_H200_NVL.json` and
`E=256,N=512,device_name=NVIDIA_H200_NVL_down.json` configurations.
The lookup keys on the exact GPU name.
The same Triton-version directory contains `NVIDIA_H200` configurations for
the shape, but no matching NVL filename. The observed path consequently uses
the default heuristic rather than those tuned files.

For M=24 the BF16 default and the nearest available H200 up-projection entry
both select BLOCK_M=16, BLOCK_N=32, BLOCK_K=64 and GROUP_M=1. Therefore this
lookup warning does not itself establish a decode regression. For larger
prefills the available parameters differ. Validate actual H200 NVL
performance before adopting an alias, copying tuning files or retuning.

## Implication For KV Management

The original concern is valid as a conditional claim: large common compute
or dispatch costs can limit the throughput gain from hiding KV recovery.
The current environment has real fusion; it is not an unfused reference
implementation. It is also not proven to have eliminated unrelated
execution bottlenecks.

Keep V166 Full and scheduler-only frozen and complete their same-work
comparison. In a separate, bounded diagnostic after the timed arms, collect
operator-level GPU execution and CPU launch evidence for decode and short
prefill, plus restoration/admission waits. Use actual batch/context shapes,
not an aggregate utilization number, to decide between GDN wrapper fusion,
MoE tuning and further KV-path work. Shared engine improvements belong in
both native and Full; their absolute speedup is not evidence of an independent
KV-policy benefit. If they change the execution environment, retain this
baseline as historical evidence and requalify the next comparison.

The reproducible CPU-only audit is
`experiments/analysis/v3464_qwen35_operator_audit.py`; output is
`experiments/reports/v3464_qwen35_operator_environment_20261024.json`.
It seals complete JSONL prefixes, excludes service records without workflow
identity and reuses the existing paired prefill analyzer. No CUDA module is
imported. Exclusive GPU kernel proportions remain unmeasured.

## Executable Diagnostic

`scripts/benchmark_qwen35_execution.py` implements the next bounded check.
It compares separate and merged input projections for 1,24,64,256,512,1024
and4096 input rows,using the actual model dimensions and BF16 dispatch.
It also records the unchanged FlashInfer GDN wrapper at1,64,256,512 and4096
tokens,including256-token intermediate-state checkpoints where applicable.
Both unprofiled CPU/stream spans and a separate three-call CPU/CUDA trace
are retained. The weight tensors are repeatedly reused; these measurements
do not reproduce a whole model's weight-cache behavior.

The supervisor waits for successful completion of every V166 arm and the
exit of every recorded process,revalidates equal work,the frozen engine,
the user's hold and actual GPU ownership,and only then initializes CUDA.
V3468 freshly passes26 CPU checks. The profiler accounts only device CUDA
events rather than summing CPU parent and child GPU attribution. A missing
CUDA activity trace fails the diagnostic instead of being accepted as a
zero-time operator. GPU measurements are still pending.

The initial V167 supervisor was registered after V166 in
`waiting_comparison`. Its registration is preserved in
`experiments/reports/v3469_v167_execution_diagnostic_registration_20261024.json`.
It was explicitly stopped before GPU use to add the predecessor's
resource-wait and server-startup states. V3470 freshly passes32 CPU checks,
and V3471 preserves the controlled stop. V167b is registered with the
qualified transition handling and its supervisor is verified live in
`waiting_comparison`;V3472 preserves that registration. No profile is
inserted into either timed arm.

## Completed Full Checkpoint

V166 Full has completed all5604 native requests with matching prompt/output
tokens and no workflow exclusions. V3476 preserves mean JCT1643.427seconds
versus native1675.445seconds(-1.911%) and event-makespan throughput568.661
versus561.055tokens/s(+1.356%). V3474's complete native-event audit measures
demand-outstanding throughput at576.032 versus568.062tokens/s. These small
differences do not establish an independent KV-management gain.

V3473 reconstructs every measured Full workflow JCT along its realized
last-completing dependency path. Mean request-submit-to-first-launch time is
597.304seconds and first-launch-to-native-completion time is977.883seconds.
The categories combine queueing/restoration/admission and execution/dispatch/
interleaving respectively. They do not isolate CUDA kernels or justify
assigning the observed time to linear attention. Scheduler-only is now
running;V167b remains queued behind its completion and GPU release.
