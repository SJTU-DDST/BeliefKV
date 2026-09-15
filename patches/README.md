# SGLang Runtime Patches

Updated: 2026-09-15.

The current canonical patch is
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
