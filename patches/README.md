# SGLang Runtime Patches

Updated: 2026-10-24.

The2026-10-24 canonical CPU follow-up repairs discovery of a surviving session
prefix when Host eviction deletes the latest request's leaf. It follows only
that leaf's ancestor path,uses the native component resolver,and revalidates
the live object and current session markers before publishing a snapshot.
Warm reads reuse the validated anchor;no periodic scan is added.
`sglang-v0.5.20-latest-anchor-recovery.incremental.patch` applies over qualified
tree413cc14e. The combined canonical staging patch has SHA256
`85c4b8b429bf7eed86ae8cfebac3dfdcf28bb252a9c3291e32ad10077108c458`
and reconstructs tree58424f5fcc43f9f2d8ec09a38758017e41b92bc7 over94602c9.
V3580 retains all666 earlier engine cases and passes681;V3581 retains all5838
application cases and passes5839. V3588 binds sources and the original failing
reproduction. This change was made after V176 completed;its frozen source and
the V174 source remain unchanged. GPU benefit is still pending. Details are in
`docs/experiments/v177_latest_anchor_recovery_20261024.md`.

The following entries retain earlier frozen patch records.

The frozen V75/V76 canonical staging patch is
`sglang-v0.5.20-beliefkv-staging.patch`, SHA256
`ea859cf0e8d02db87e1b586fa149c7a8ddf798e867ee7888e21fa4a25f1785ce`,
reconstructing tree `53020ce3efef3ed362e9af0db77147b39070f064` over
native HEAD `94602c9`. V77 independently selects the registered native
session-close combined patch/tree `76cf97d2`; these running and queued
sources remain unchanged.

`sglang-v0.5.20-replayssm-decode-budget.patch` preserves the independent
ordinary ReplaySSM decode memory-accounting fix, candidate `91a9359`,
tree `f231ed2420212d072e8d0ad4061f45e9690d1f31`. It applies over the complete
V75 tree `53020ce3`, not clean `94602c9` or the V77 tree. Persistent d/k/g
decode rings, including padding, are charged per Mamba slot; speculative
verification scratch remains separately accounted. Disabled ReplaySSM retains
the existing sizing behavior. V1263 passes 39 CPU cases and eight subtests,
including actual CPU MambaPool allocations; V1261/V1265 retain the failing
base reproductions. Forward application reconstructs the exact candidate
tree and reverse application passes. V1264 binds sources, patch and receipts.
The patch is not selected by the launcher and does not enable ReplaySSM or
change the frozen experiments. GPU memory peaks, numerical correctness and
performance have not been qualified.

`sglang-v0.5.20-waiting-full-only.patch` is an independent incremental
engineering candidate over the complete V75 tree `53020ce3`, not over
clean `94602c9` or the V77 tree. Candidate `de4118f`, tree `35cf6411`,
retains a waiting session's live Mamba state when native FULL reclamation
has no remaining Mamba deficit. Joint deficits retain native cascading;
later Mamba pressure can still back up, ACK and reclaim retained state.
The original causal, identity, reference and DMA checks remain in force.
V1237 covers 236 CPU regressions and 27 subtests; the overlapping final
candidate file passes 13 checks in V1239, including later state pressure.
V1240 binds sources and 237 distinct testcase identities across the suites.
This patch is not selected by current launchers and has no GPU performance
or quality result. It does not replace the canonical patch or establish
a paper contribution.

The follow-up descriptions and patch hashes below are historical records.

The V267 followup stops FULL Host eviction before building the leaf heap
when write-back duplicate reclamation has already met the requested capacity.
Unsatisfied calls retain native session priority, locks, heap ordering and
frees. Four cumulative counters are copied into existing one-second Host
telemetry; no per-call I/O is added. The canonical patch SHA256 is
`10ea49f7513ff1c0eaa2f065cc69036e127452f1b627c962ddbf10d41d6d16a9`,
with exact expected/actual tree
`58e2f487320f6b8f0d32f75d89b4240224df86f9`. The engine delta contains only
`components/full.py` and `test_beliefkv_full_host_eviction.py`.
Qualification uses a new engine reconstructed from the entire current patch;
the older followup engine is preserved and must not replace the canonical
source. V49 keeps its frozen engine; deploy this change cold after collection.
CPU branch savings do not establish GPU/JCT benefit, and this shared native
optimization must also be included in a final same-version native comparison.

The V129 followup decouples a FULL-only opportunistic capacity
shortfall from backup of live unbacked Mamba. On an eligible backed
FULL/Mamba leaf, release FULL device pages and retain the state value,
references and native LRU. A simultaneous Mamba-slot shortfall retains
the existing asynchronous backup/ACK path; unsupported component
combinations retain cascading demotion. Locks, shared references,
identity, pending DMA and restore-target exclusions still apply.
The real CPU allocator regression reproduces the old extra state D2H,
then verifies immediate FULL release and state reuse after FULL
restoration. Related 157 native cases (plus 27 subtests) and 182 runtime
and audit cases pass. Exact source-tree and reverse-application checks
match canonical SHA256
7b4832ef4e998d36aff6c91847194c50f3cc869bf3296d01bca43de212f5a162.
This is an undeployed followup; collecting V30 retains its frozen
1c3c7fc4 patch. CPU correctness does not establish GPU speedup or the
fraction of actual prefetch losses attributable to this dependency.

Generic handoff capacity scans skip restoration-target exclusions before
spending their existing eight-candidate quota, for both FULL leaves and
Mamba LRU state. Four real CPU hybrid allocator regressions cover8/16
excluded target nodes preceding a safe cold victim. They fail before the
fix and pass afterward with95 related checks; target device/Host values
remain intact and no additional D2H is issued. Mamba-only reclaim retains
FULL. The canonical patch matches the isolated source and passes reverse
application, SHA256
826993c171b8bdc771828358f90a34acb5e9d3e6111bf135bc397d3627acfa65.
Deploy after the frozen V23 pair through the updated waiting V24 target;
these allocator checks do not establish GPU throughput or JCT improvement.

Terminal close and child retirement retain every valid indexed session leaf.
The former 64-anchor fallback to only the latest branch lost historical private
suffixes after releasing their references. Reclamation still uses bounded native
batches and retries; shared, locked and in-flight data remains protected, with
no terminal D2H. Twelve real CPU allocator regressions cover 65/128 branches,
both close/RETURN orders, shared parent prefixes and historical lock/H2D retry.
The follow-up passes 164 native and 464 BeliefKV checks and canonical forward/
reverse tree validation. Deploy after the frozen V22 pair, with predictive and
cold native using the same version; allocator tests do not prove GPU speedup.

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
source changes outside the canonical patch. Validation uses a scratch copy of
the Git index, supports linked worktrees and staged or intent-to-add files, and
includes untracked additions only when the canonical patch declares them.
The real index remains unchanged; six provenance checks cover these cases.

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

During a Mamba-only handoff shortfall, the follow-up also examines up to eight
unlocked native Mamba LRU entries, including interior nodes. It demotes only
settled, Host-backed, unreferenced device state and preserves FULL values,
references and Host state. Waiting-agent victims retain their existing causal
validation. Every node in the target restoration closure must be excluded from
reclamation by the runtime, including already resident ancestors.
`test/srt/test_beliefkv_terminal_capacity.py` exercises actual CPU hybrid pools:
Mamba capacity returns without FULL release or additional D2H. The live V20
arms retain their frozen engine; deploy this patch between completed paired runs
and measure GPU reuse, busy-window throughput and paired JCT separately.

Opportunistic handoff reclamation submits missing live Mamba state
asynchronously with native read locks. Pending copies do not count as reusable
Host state or reclaimed capacity; native ACK processing unlocks the source
before a later reclaim attempt. Actual native allocation/eviction keeps its
synchronous dependencies. 74 scoped CPU checks include real hybrid allocators,
pending-transfer exclusion and native ACK unlock processing. No GPU benefit is
claimed from those fixtures. The isolated complete patch passes exact
expected/actual tree and reverse-application checks, with SHA256
`98fc5af9c40121d6f7ba1824f1fd5b586d0b141d5b12370a2d9243c1b1b5e8f1`.
V20 remains on deployed `c58cc891` until its frozen pair is complete.
