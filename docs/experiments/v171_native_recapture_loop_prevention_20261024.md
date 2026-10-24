# V171 native recapture and child-local loop recovery

## Decision

V168b remains a rejected natural source. Its four graph-fuse interventions
cannot be repaired after capture, and three involve roots rather than
children. Child-local return alone therefore cannot repair that source.
Collect one replacement native trajectory, V171, with the common preventive
prompt below. Do not replay Full on V168b or remove its four affected workflows.

Keep the existing natural-source eligibility checks. A recovery-assisted return
remains explicitly marked in the audit; it does not become a naturally completed
baseline merely because it has final text. Full replay waits for completion,
exact token/dependency/stopping evidence and source qualification.

## Prevention and recovery fixes

The common tool-progress instruction now distinguishes actual work from
completion bookkeeping. Renaming completed todos and printing success claims
do not validate a task. A completed child gives its final assistant report
after its completion notice; todo/echo updates alone do not justify announcing
again. Partial and blocked child reports return control to the parent.
This changes no delegation counts, DAG structure, task requirements or success
criteria, and applies equally to the native capture and its later replay work.

`_credible_progress_key` no longer counts a plain literal `echo`/`printf`,
optionally after a `cd ... &&` prefix, as fresh execution evidence. It uses
`shlex` for this narrow recognition. Shell substitutions, glob expansion,
pipelines, redirections and additional commands retain their previous evidence
handling; proven workspace changes still take precedence. This is not a
general shell-effect analyzer or a semantic task-success classifier.

On the same three V168b root histories, excluding their guard-assisted final
response, the final consecutive-no-progress counts are:

| Root | Previous detector | Fixed detector |
| --- | ---: | ---: |
| Data lake 050 | 307 | 329 |
| Desktop media 020 | 365 | 365 |
| Web CMS 074 | 9 | 119 |

Two existing recovery defects are fixed:

- During bounded RECOVERY, regular tools retained in the model request are
  also preserved in the returned response. The terminal sanitizer previously
  removed them, preventing the alternate action from executing.
- FINALIZE now respects natural-language mode. A child reports existing
  evidence, actual validation, unfinished work and blockers in plain text to
  its parent; it is not asked for an unavailable structured completion tool.
  The parent can continue its remaining work. Structured-mode finalization,
  audit intervention labels, terminal tool rejection and hard fuses remain.

The native reactive profile continues to observe semantic and soft-budget
signals without globally enforcing semantic finalization. Do not enable root
semantic finalization to make a capture finish. Child-local recovery is CPU
verified, but neither model compliance nor the absence of loops is guaranteed
by a prompt or scripted-model test.

## CPU evidence

V3545 passes 5810 cases across 175 modules, retaining every previous 5790
identity and adding 20 cases; V3546 seals the sources. New cases cover sync
and async recovery calls, terminal tool rejection, plain-text finalization,
literal completion prints and real shell evidence. Actual LangChain agent
graphs with scripted models cover both successful alternate evidence and
exhausted child recovery: each returns a blocked/partial report and the
parent executes its validation tool afterward. The intervention remains
visible in the child audit and is absent from the parent return.

V3541/V3542 retain earlier fixture/schema or test-environment failures and
are not qualification evidence. V3543 passes the initial 192 focused cases;
V3545 includes the final expanded child-exhaustion case as well.

The native engine remains canonical tree `413cc14e6c53`, with its unchanged
666-case CPU qualification. A separate prepared engine checkout reproduces
the qualified tracked/index state; old frozen capture trees and the existing
development engine are left intact.

## Capture contract

Use `configs/migration/qwen3_coder_24project_running24_20261024.json` and the
same 24-project manifest. Keep Qwen3-Coder-30B-A3B-Instruct, simultaneous
24-root arrival, running/decode 24, seed 21, context 262144, compression at 95%,
output limit 16384 and Host pool 200 GB. Native keeps the custom short-tool
continuation and custom KV-management policies disabled.

Registration target:
`/home/longhao/experiment/ops/v171_qwen3_coder_24project_native_source_20261024.registration.json`.
Run root:
`experiments/raw/qwen3_coder_24project_native_source_20261024_v171`.
The registration freezes main and the engine and waits for verified resource
availability. A new capture is not a claim of a qualified trajectory or a
performance improvement. No additional complete native replay or ablation
is authorized by this recapture.
