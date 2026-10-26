# Parallel Delegation And Test Execution Qualification

## V195 Findings

The frozen natural capture declared `native_in_graph_dynamic_nested`.
Its actual contract required only one completed root delegation; two to four
parallel children were a recommendation. The terminal middleware accepted a
successful singleton JOIN. The saved root conversations contain 37 model
responses with one task each; the runtime contains 38 singleton JOINs including
one nested JOIN. The adapter groups the valid task calls of one model response
under one JOIN; it did not split a multi-task response into singleton JOINs.

The root-output audit matches all 37 recorded root test commands to physical
sandbox execution records:

| Recorded Outcome | Commands |
| --- | ---: |
| Collection or configuration exit | 12 |
| Fixture setup or teardown error | 1 |
| Actual test calls reported | 23 |
| Saved output insufficient to prove execution | 1 |

The body evidence covers root conversations, not every child output. Full
runtime counts separately cover 545 shell executions and all descendants.
The audit does not interpret `pip install pytest` or a search for `pytest`
as test execution.

Two CMS invocations fail with unconfigured Django settings. The first reports
a 0.92-second pytest collection failure; physical sandbox execution takes about
2.43 seconds. The second reports 0.82 seconds and takes about 2.29 seconds.
These boundaries are distinct. The 23 commands with reported actual test calls
also remain short: their physical median is about 2.12 seconds.
Eliminating early exits cannot by itself establish a long-tool workload.

The generated CMS archive provides 43 source files and no settings module,
`manage.py`, or `conftest.py`. All 43 provided files are declared in its
normalized source metadata; the importer did not omit additional configuration
files. The mobile-social archive likewise has 21 declared/provided files.
Examples of other problems include a nonexistent application package,
an undefined session fixture, an incompatible fixture scope, and `request`
used as a reserved pytest parameter name. These are not all dependency-install
problems.

Evidence:

- `experiments/reports/v3853_v195_root_tool_execution_audit_20261025.json`
- `scripts/audit_workload_tool_execution.py`
- Original capture:
  `experiments/raw/qwen3_coder_18project_root_phase16_source_20261024_v195/native_capture`

## Root Contract

Each future dynamic root must complete at least one parallel JOIN containing
at least two direct children that actually received model service and returned
successfully. Serial singleton rounds cannot satisfy it. The prompt states this
in both the root system instructions and the collaborative task instructions.
The terminal middleware checks the runtime callback evidence with bounded
reminders; the source acceptance audit independently checks the event graph.

The model still chooses the assignments, roles, timing, later rounds and nesting.
Initial tool choice remains automatic. This adds neither a fixed DAG nor a
fixed number of rounds. Failed or safety-finalized attempts remain recorded and
cannot qualify a capture. A failed parallel round cannot satisfy the contract
merely because it declared two children.

The adapter maintains one maximum served-member count per parent, updated only
at completed JOINs. Terminal checks are constant-time dictionary lookups.
There is no new decode-step scan or serving-engine scheduling rule.

## Test Environment Gate

`qualify_locobench_environment.py` now runs real tests and reads the built-in
pytest JUnit XML. Public-library imports alone, collect-only success, empty or
skipped-only suites, collection errors, fixture errors, timeouts and missing XML
cannot qualify an environment. Completed failing test calls are retained
separately; passing this gate does not grade the task or establish that its
assertions pass. The reported executed-call count is a conservative lower bound
because XML errors may conceal an earlier successful call.

Successful pinning binds the test command, original manifest, image ID,
source commit and evidence report. The workload loader supplies the qualified
test command to both root and child repository instructions. New natural-source
registration requires this execution qualification before resource allocation.
Old artifacts and their original qualification statements remain unchanged.

CPU probes use owned temporary containers, an immutable source mount, no
network, fixed random seeds, the existing sandbox seed support, two CPUs and
6 GiB of memory. Their XML is collected outside measured workflow execution.
This adds no pytest logging or transcript processing to the GPU scheduler.

The separate test-ready image installs the real legacy Moto API and pytest-django.
Moto 3.1.19 needs its compatible Flask/Werkzeug pair; the image explicitly pins
them and passes `pip check`. It does not replace nonexistent application modules.

Image:
`sha256:d7699a143c7ce17e1d50d8b33ff9cd373bc0c924353db0f339b48663cc0adba8`.
Recipe: `configs/workloads/locobench_runtime/Dockerfile.test-ready`.
Build with the local classic builder because the base is a local image ID:

```bash
DOCKER_BUILDKIT=0 docker build --pull=false \
  -t beliefkv/locobench-agent:test-ready-20261025 \
  -f configs/workloads/locobench_runtime/Dockerfile.test-ready \
  configs/workloads/locobench_runtime
```

The complete original 18-project probes pass common dependency imports, but only
three declared/default test entry points meet the execution gate. Those three
have 13, 20 and 5 executed cases respectively, including preserved test failures.
The remaining projects have collection/fixture errors, no tests, or skipped-only
suites. This result must not be relabeled as a ready 18-project workload.

Evidence:
`experiments/reports/v3855_18project_test_execution_environment_cpu_20261025.json`.

Next capture preparation must correct actual package/settings configuration
where the source supports it and replace or separately repair incomplete
projects. Keep one original project per workflow, the 18-root/running24
calibration, causal pretokenized replay and complete normal tails. Validate
parallel JOIN structure and actual test execution before a new matching natural
native capture becomes a Full/baseline replay source. Do not reuse the old
trajectory with changed tool durations.

## Validation

`experiments/reports/v3856_parallel_root_test_execution_application_cpu_20261025.xml`
records 6453 passing cases, retaining all 6414 inherited case identities, plus
two passing subtests. The engine was not changed. Targeted tests additionally
exercise failed/cancelled or unserved children, serial singleton rounds,
collection-only success, skipped suites, fixture errors, assertion failures,
test-command visibility and owned-probe timeout cleanup.

The V216 retained Full run finished and its processes were cleaned up. Complete
matched-work audit reports mean JCT 1032.716 seconds and whole-run output
throughput 231.954 tokens/second; V209 ThunderAgent is 1091.940 seconds and
230.646 tokens/second. This is one short-tool, singleton-JOIN comparison,
not acceptance for the requested multi-branch workload or repeatable
improvement over all baselines. The new source preparation has launched no
additional GPU experiment.

V216 audit:
`/home/longhao/experiment/ops/v3849_retained_join_only_migration_audit_20261025.json`.
Dependency-path audit:
`/home/longhao/experiment/ops/v3851_retained_join_only_jct_paths_20261025.json`.

## Actual Environment Repairs

Per-project `sandbox_environment` settings now use the same validation and path
merge in the CPU qualifier and the live root/child Docker backend. They preserve
the seed instrumentation on `PYTHONPATH`; they cannot replace PATH, HOME, CUDA
selection, random seeds or plugin-loading controls. Qualification binds these
settings together with the immutable image, source, test entry point and report.

The new image supplies real markdown2/slowapi dependencies, a standard
session-scoped `monkeysession` fixture and async pytest mode. Opt-in MediVerse
settings configure its actual apps, user model and SQLite test database.
The image passes `pip check`:
`sha256:249cb0307771c98813fe4d14e0df60b2c99fbb5759396cebcbc0eca6d739423e`.
Recipe: `configs/workloads/locobench_runtime/Dockerfile.project-envs`.

Verification preserves each outcome rather than converting every nonzero test
exit into an environment failure:

| Project | Concrete Repair | Rechecked Result |
| --- | --- | --- |
| CampusConnect | Correct async fixture execution | 10 passed, 3 test-call failures; no collection or fixture errors |
| ProdFlow | Real Moto API, session fixture, correct us-east-1 S3 creation | All 3 test calls execute: 2 pass and 1 assertion failure |
| MediVerse | Actual Django settings, missing signal imports and valid pre-delete receiver in an isolated source fork | App registry initializes; original wellness tests import nonexistent model names and remain rejected |
| CinePulse | Required RSA public test key supplied; auth verification stays enabled | Settings load; original tests still reference absent middleware/bus interfaces and remain rejected |
| FocusFlow | Actual markdown dependency | 4 pass, 2 call failures import absent services; remains rejected |

The two source repairs are confined to
`workloads/sources/locobench_project_repairs_20261025/`. Original imported source
trees remain clean. Exact diffs, original commits, image and settings are saved
in `experiments/reports/project_source_repairs_cpu_20261025.json`.
Do not pin the old commit as a repaired source; freezing a repaired workload must
include the declared patch in its own commit and regenerate its context pack.
Environment-only results are in
`experiments/reports/project_environment_repairs_cpu_20261025.json`.

Some provided tests implement their own fallback services. Executing those tests
does not prove the intended production modules ran. In particular, the repaired
ProdFlow fixture demonstrates real test-call execution, not a complete
production integration qualification. No fabricated production module has been
added. The complete 18-project population remains unqualified; no replacement
GPU source has been launched.

Every live sandbox test command, including child commands, now saves bounded
execution diagnostics with its normal audit record. This runs in the client
sandbox, not the GPU scheduler, and adds no second shell invocation or tool delay.
Source measurement retains assertion failures separately and refuses
environment-broken/no-test-call source evidence after normal workflow completion.
It does not abort workflows, drop their normal tails or repair old frozen traces.
Owned-probe cleanup failure also preserves the original test result.

Healthy tests in these generated small suites can still finish in a few seconds.
Removing collection failures does not manufacture a long-tool workload; further
population selection must verify actual application execution and natural tool
duration before using it to evaluate long-tool migration.

The combined application regression is now
`experiments/reports/workflow_service_environment_application2_cpu_20261025.xml`:
6498 tests plus two subtests pass, with no failures, errors or skips, retaining
every inherited case. This includes the shared sandbox configuration, test-call
diagnostics, original parallel-delegation requirement and new scheduler candidate.
It does not qualify the incomplete project population or establish GPU benefit.
