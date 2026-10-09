# SGLang Runtime Patches

Updated: 2026-10-09.

The current Qwen3.5 / SGLang v0.5.20 integration uses
`sglang-v0.5.20-beliefkv-staging.patch`, pinned to
`94602c9c2b7cbdb8efd5c52802dac6a1c180089e`; details are below.
Do not substitute the archived v0.5.2rc1 patch for the current runner.

The archived Qwen3 / SGLang v0.5.2rc1 canonical patch is
`sglang-0.5.2rc1-beliefkv-perf-ownership.patch`. It is generated from and
applies only to:

```text
tag:    v0.5.2rc1
commit: 18f91eb639084825717c0e3c3c7273492812ab71
```

Apply and validate it from the SGLang repository root:

```bash
git apply --check /home/longhao/experiment/BeliefKV/patches/sglang-0.5.2rc1-beliefkv-perf-ownership.patch
git apply /home/longhao/experiment/BeliefKV/patches/sglang-0.5.2rc1-beliefkv-perf-ownership.patch
beliefkv check-sglang "$PWD"
```

Hardware and run manifests record both the SHA-256 of this canonical patch and
the fingerprint of the patched SGLang source tree. Environment capture also
compares the patched Git tree against this file. A result may be reported only
when the trees match and there are no additional tracked or untracked SGLang
source changes.

The current patch covers:

- request metadata propagation through OpenAI chat, tokenizer, session, and
  scheduler types;
- BeliefKV deferred admission and abort handling;
- scheduler safe-point execution and tagged waiting-queue ordering;
- capacity-aware retained chunked prefill, including final-page replay that
  prevents zero-token CUDA batches under HBM pressure;
- Radix/HiCache topology, lock, residency, and request-cache observer callbacks;
- runtime CLI flags.

All policy logic remains in `beliefkv/`. The patch deliberately calls private
HiCache methods only from the scheduler thread and is guarded by the exact
source contract. Do not apply with `--reject`, do not hand-resolve it onto a
newer release, and do not report results if `beliefkv check-sglang` fails.

## Historical Variants

The other patch files are retained because immutable experiment profiles refer
to them:

- `sglang-0.5.2rc1-beliefkv.patch`: original integration patch;
- `sglang-0.5.2rc1-beliefkv-perf.patch`: bundle-transfer performance branch;
- `sglang-0.5.2rc1-beliefkv-deadline-live.patch`: earlier deadline/liveness branch.

Do not select a patch by filename recency. Read the frozen profile's
`source_contract.canonical_sglang_patch` field. The current H200 v7 profile uses
the `perf-ownership` patch and also validates the expected patched-tree hash.

The separate
`sglang-v0.5.20-beliefkv-staging.patch` targets commit
`94602c9c2b7cbdb8efd5c52802dac6a1c180089e`. It includes the Qwen3.5 native
adapter, explicit FULL/MAMBA Host-pool split, and per-node Host eviction
observer required by `eviction_attribution.jsonl`. The v0.5.20 training runner
checks that this complete patch is present before starting SGLang.
For BeliefKV named `task` requests with `parallel_tool_calls=true`, it preserves
the full prompt tool schema and uses a required constraint over the task subset.
This avoids the installed XGrammar named-tool format's single-call limit while
leaving ordinary requests and explicit nonparallel calls unchanged. The optional
`beliefkv_task_fanout=[2,4]` request extension bounds that first-turn native tag
with XGrammar RepeatFormat; the model chooses count and content. Later rounds
remain prompt-driven. No generated response is rejected or expanded into
invented children.
The current staging patch also propagates native transfer submission timestamps,
merged physical bytes, unacknowledged bytes at submit, and synchronized CUDA
transfer-stream elapsed time into the cache ACK observer. These measurements
must not be reported as isolated PCIe DMA latency or instantaneous bus usage.

The integrated revision adds `beliefkv_include_mamba` to tagged PREPARE
and prefetch primitives. FULL ancestors omit historical state before transfer
allocation; the selected current input checkpoint retains necessary Mamba.
Native demand/backup defaults still include state. Tagged sessions release
older Mamba session references through native hooks, not forced physical
deletion of shared, locked or in-flight slots. The corresponding tests are
`test/srt/test_beliefkv_latest_mamba_state.py` and the existing shadow tests.
Native v9 completed fingerprint capture and HTML export before integration;
the active engine was updated incrementally, preserving its existing patch.
The scheduler now calls `dispatch_execution_handoff` before batch admission.
`inspect_beliefkv_reentry` matches the queued request's actual input to session
checkpoints read-only, without native match/COW/LRU mutations.
`reclaim_beliefkv_handoff_capacity` frees only settled Host-backed, unlocked
cold or unreferenced duplicates during an actual upcoming load shortfall.
Required device capacity is rechecked after release; in-flight D2H is not
credit. H2D remains scoped to native missing FULL ancestors/current state,
not an entire agent history. The new CPU tests are
`test/srt/test_beliefkv_execution_handoff.py`; no new GPU benefit is claimed.
