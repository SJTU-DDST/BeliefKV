# Experiment Invariants

- Keep experiment, configuration and analysis identities separate. Historical
  `vNNN` references remain immutable, but new CPU checks, progress observations,
  plots and result analyses belong to their parent experiment's named evidence
  directory and must not each consume a new main experiment version. A failed
  startup or partial GPU attempt remains explicitly identified as such, never
  counted as a completed comparison or silently resumed.
- Every future naturally captured source workload must use a different original project for each root
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
- Dynamic roots must complete at least one real model-selected parallel JOIN with
  at least two model-served direct children before final return. Separate singleton
  rounds do not satisfy this minimum. Keep initial tool choice automatic; defer an unmet final
  answer with a recorded reminder and at most two required task attempts. The
  model chooses assignments, fanout, timing, later rounds and nesting. Failed
  attempts and safety-finalized violations do not qualify a source. Children
  retain their independent soft budgets and may naturally return without nesting.
- Test-environment qualification must show actual test-call execution. Public
  dependency imports, collect-only success, skipped-only suites, and collection
  or fixture-setup failures are insufficient. Preserve task assertion failures
  separately from missing dependencies, paths, settings, and incomplete source.
  Do not fabricate project modules or pad tool durations to qualify a workload.
- New natural-source registration also requires separately observed calls into
  project implementation during actual test calls. Test-local fallback classes,
  successful imports and fixture-only setup do not establish this. Keep the
  opt-in CPU production-call probe outside measured tool execution and GPU
  scheduling. Pin and recheck its evidence before resource allocation; preserve
  original normal tool timing and every rejected population.
- Root direct-work phase budgets are optional workload controls: remind at 16
  root calls and use 24 as a soft phase target, resetting only after a real
  completed delegated JOIN. Do not cap total workflow turns or truncate work.
  A changed prompt/budget requires its matching natural native capture.
- The next separately declared root-phase calibration warns at 8 calls and
  targets 16 root calls per phase, with child budgets unchanged at 24/32.
  Preserve the v187 16/24 source. Require a new matching natural native capture
  and inspect delegation/JOIN density and completion evidence before Full replay.
  Use the historical `waiting` cold-reclaim policy for this calibration; v194
  did not establish a performance improvement for `selected_queue`.
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
- A separately declared `selected_queue` cold-reclaim policy may lend settled
  Host-backed duplicates from at most 16 unstarted queue tails to the actual
  selected consumer. Its synchronous authority must expire with that selection
  and retain the raw queue head, selected consumer, ready JOIN dependencies and
  all native lock/DMA guards. Require capacity for the complete safe input
  checkpoint plus the native reserve before ordinary speculative enqueue; this
  is a necessary capacity check, not native admission permission. Keep the
  historical `waiting` policy and v193 configuration frozen.
- The segmented-checkpoint repair candidate keeps at most sixteen queued donor
  requests and the 200 ms global probe backoff, but permits at most 64 native
  extent checks within `selected_queue`. Stop as soon as the safe input
  checkpoint plus native reserve fits. The default `waiting` path retains
  sixteen native checks. This candidate changes no native engine code, creates
  no backups, and must be CPU-qualified and separately compared on matched work
  before any performance claim. Preserve the historical sixteen-extent v194.
- During critical parent recovery, free request rows are not a selected prefill
  batch. Protect the actual queue head and ready JOIN dependencies; validate
  unstarted ordinary queue tails as possible native FULL donors. Preserve all
  native lock, allocation, identity, Host-copy and DMA checks.
- For the next unified execution-restore candidate, tool successors and other
  ready requests share the selected-consumer path. Keep online long-tool
  offload prediction and JOIN/EOS recovery independent. Use the `waiting` cold
  reclaim policy; an unstarted ready queue tail is not intrinsically cold.
  Preserve last-child dependency protection. The historical `selected_queue`
  experiments establish safe capacity access, not a net migration benefit.
- Count initial cold prefill separately from missing previously served
  same-session prefixes. Cache occupancy and prefix-loss proxies alone do not
  prove expensive recomputation.
- After changing a natural workload population, collect its matching natural native
  trajectory. Reuse that capture as the native baseline; do not schedule another
  full native replay. Compare Full and controls with the same tokens,
  dependencies, stopping conditions, capacities and seeds.
- Retain the original pretokenized `/generate` replay, as explicitly selected
  by the user. Do not require chat-ingress alignment or raw chat-body capture.
- Preserve the frozen source trees of running experiments. Qualify and replace
  pending registrations explicitly when launcher code changes.
- Successful native captures and causal comparisons must observe two freshly
  published idle native telemetry snapshots before server cleanup. Keep this bounded collection drain
  outside measured workflow JCT. A stale queue-depth snapshot is not proof of a
  complete shutdown trace; retain the original status and failed audit.
- Do not run ablations until predictive establishes clear gains over native.
  Keep completed native/Full results and administratively interrupted controls.
- The user now explicitly requests paper baselines before implementation of the
  combined recovery/admission proposal. Reproduce the declared external methods
  on the current FULL-only model and shared data plane first. This authorizes
  their comparisons independently of the older ablation restriction.
  Keep paper policy, public artifact and any dynamic-graph extension distinct.
  Missing predictors, preemption, TTL enforcement or physical actions must not
  be silently replaced by our policy or counted as a completed reproduction.
- The user subsequently authorizes the combined system change at agent
  granularity, explicitly correcting workflow granularity. Develop the separate
  Full agent-working-set candidate alongside the remaining paper baselines.
  Preserve the running frozen baseline and all completed paper policies; do not
  inject Full migration or JOIN priorities into their results. Use one sequential
  invocation as the admission identity, retain observed-JOIN liveness, and bind
  recovery reservations to the same agent's actual native admission.
- The user explicitly requests starting from ThunderAgent's scheduling policy
  for the next combined candidate. Reuse the reference ThunderWorkingSet core,
  retaining its continuation groups, capacity selection and five-second control.
  Declare observed-JOIN yielding and bounded migration reservations as Full
  extensions. Keep the V209 paper arm and V214 initial candidate frozen.
- A separate retained ThunderAgent candidate decouples physical KV offload from
  logical eligibility. Keep blocked owners' eligibility while eligible compute
  can progress. Lend only blocked, non-running, unprotected budgets when no
  eligible query/in-flight work exists and a real ready request is waiting.
  JOIN recovery may claim free logical capacity but cannot evict a ready peer.
  Preserve V215 and qualify this candidate independently.
- In the retained candidate, keep JOIN/EOS recovery reservations but disable
  ordinary execution-frontier prefetch and its recovery/execution binding.
  Agent eligibility maintains ordinary continuity; offloaded ordinary state
  still uses native demand recovery without an additional 1500 ms reservation.
- After JOIN/EOS restoration, retain the same normal agent eligibility policy.
  Expiring an unused temporary recovery reservation must not itself pause the
  agent or force physical eviction, including when warming added eligibility.
- The separately declared `thunderagent_workflow` candidate adds root-shared
  service opportunities to retained agent admission. Keep the reference paper
  arm unchanged. Use safe request boundaries, bounded handover cadence, minimum
  eligibility tenure and native physical authority. Co-admit observed ALL-JOIN
  siblings only within available capacity and the shared workflow budget; never
  wait for an unready sibling or count fanout as extra service entitlement.
  Initial-service and sustained-ready-gap targets are experimental, not hard
  guarantees or evidence of throughput/JCT gains. Qualify healthy multi-child
  work before a new GPU source and retain complete normal tails.
- The latest user order is healthy trace preparation, then Agentix, KVFlow,
  ThunderAgent and TokenCake reproductions, before more integrated-policy work.
  Agentix PLAS/ATLAS already provide program service inheritance and fairness;
  replacing its request identity with an agent is not a novelty claim.
- AgentX is the user's explicitly selected primary evaluation workload. Do not
  prepare or promote backup workloads. Use its pinned published traces directly;
  do not run native to collect a replacement source. A native comparison arm
  or bounded data-plane qualification is distinct from source collection.
  Its published-session JCT is a valid systems metric; unavailable original
  application correctness does not disqualify that metric. Keep its anonymized
  structural replay separate from application-execution qualification. Preserve
  recorded requests, hash-prefix relationships, auxiliary calls and explicit
  children; disclose inferred chains/JOINs and unavailable tool semantics.
  Distinct session IDs cannot prove distinct original projects. Do not silently
  register an AgentX population as a project-disjoint natural LoCoBench capture
  or use its future trace graph as online policy input. Original project
  provenance and natural-test requirements do not apply to this explicitly
  separate published-trace source kind.
- For AgentX, retain whole published sessions fitting the common context limit.
  Do not reject a session merely because an unlabelled external gap exceeds
  300 seconds. Declare the official `agentx` runtime idle guards explicitly:
  300 seconds across an idle root and its descendants, 10 seconds for an idle
  system. Keep requests queued at the server in flight; never advance a JOIN
  barrier or clamp individual turns independently. Preserve raw delays, log
  every timer shift, and label run-to-completion JCT as benchmark wall time
  under that timing policy. Keep all normal tails and complete every request.
- Report request interactivity alongside workflow TTFT and complete JCT.
  Measure first tokens from streaming output, not request launch. Give each
  workflow equal weight in workflow aggregates so fanout cannot inflate its
  representation; disclose undefined single-token rates and timing coverage.
  Ready-workflow service gaps exclude genuine all-branch external blocking,
  but include local admission and KV recovery waiting.
- The latest user order supersedes the earlier Agentix-first sequence: start
  ThunderAgent, KVFlow and TokenCake reproductions before resuming Agentix
  adaptation. Its incomplete native swap remains disabled.
- Name the current baseline "ThunderAgent reproduction". AgentX identifies only
  its shared published-trace workload; Agentix is a separate deferred method.
  Do not describe the run as a combined ThunderAgent/AgentX baseline or alter
  ThunderAgent's paper policy to create one.
- The KVFlow `kvflow_observed_graph` arm uses only already observed invocations
  and completed same-context input-prefix history. Keep it distinct from the
  paper's application-declared graph information group. Preserve whole-request
  loading checks and native demand fallback. Its new transfer path requires
  `qualify_paper_prefix_transfers.py` on real GPU/Host pools before the full
  model launch; V217c does not qualify these new physical components.
  Speculative reclaim must not call native `cache.evict()` with its global
  write-back barrier. Advance the native heap with a bounded number of checks,
  back up missing victims asynchronously, and release only settled Host-backed
  nodes under the original locks and ACK authority.
- An AgentX invocation return is not context closure. Only an explicit temporary
  lifetime permits immediate retirement; persistent or unknown contexts retain
  ordinary capacity-evictable cache until explicit closure or workflow end.
  Do not infer lifetime from role, completed status, last observed call or future
  reuse. This retention grants no permanent Host/HBM lock or active-agent permit.
- AgentX does not record the parent's actual JOIN-wait tool boundary. Its
  timestamp-inferred JOIN_CREATE/WAIT/SATISFIED events belong to private replay
  dependencies and the audit log, not the online scheduling graph. Withhold
  inferred JOIN boundaries at replay delivery and reject them at runtime ingress,
  including old plans whose wait/satisfied events have only inferred confidence.
  Do not use them to revoke eligibility, offload, grant JOIN priority, group
  siblings, or issue recovery tickets. Background SPAWN permits the parent to
  continue; its incremental backup grants no physical eviction authority.
  Genuine declared/observed runtime waits keep their original behavior.
- Preserve the current frozen paper cycle and its original population. Complete
  the requested four baselines plus Full before reducing concurrent workflows;
  do not silently hot-edit their graph-information contracts. Retain their
  reconstructed-JOIN information limitation in comparison reports. New runs
  declare and verify separate private/online event counts per workflow.
  Lower simultaneous workflow admission separately from total sessions and
  running/decode limits, and include admission waiting in complete JCT.
- AgentX sessionless replay is supported explicitly by the paper prefix-transfer
  adapters; it does not qualify Full's session-bound migration paths. Port and
  physically qualify Full's ownership/lifecycle before a Full AgentX comparison.
  Official chat transport does not require our token bundle, but permitting
  arbitrary model EOS changes published work. A synthetic terminal token or
  future-known final call cannot supply an observed child-completion signal.
- The opt-in `BELIEFKV_SESSIONLESS_PREFIX_MIGRATION` Full candidate now binds
  actual plain-prefix ownership, split-safe incremental backup, ACK-gated
  demotion, observed JOIN/EOS recovery and native admission to agent eligibility.
  Keep its default off and control/paper arms isolated. The 961-case CPU report
  uses real native trees/allocators with copy/event stand-ins; it is not GPU,
  whole-model or performance qualification. Register real components and model
  checks before the Full AgentX arm. Its declaration is
  `configs/migration/full_sessionless_prefix_candidate_20261025.json`.
  A separate Full-only `observed_idle` lending rule may yield an owner with no
  submitted call for at least one second only when no eligible compute remains
  and an actual pending caller needs admission. It leaves RCCG READY unchanged
  and supplies no JOIN/EOS label. Keep ordinary recovery binding disabled and
  retain native demand fallback. Do not change the frozen paper cycle.
- TokenCake spatial accounting must preserve shared native extents and their
  first capacity owner across splits. Reserve pending private slots before
  batch allocation and settle the entire selected batch before refreshing
  native free capacity. Use a separately declared generation budget, never
  the replay sampler's recorded final output length. Native admission,
  lifecycle, retraction and consumer ownership now have bounded GPU evidence;
  the separate `tokencake_paper_observed` full arm now requires the explicit
  `configs/baselines/tokencake_agentx_20261025.json` declaration. Before its full
  model starts, its frozen supervisor must rerun real native prefix and paper
  lifecycle checks and validate actual serving parameters.
- Paper progressive upload grants allocate at most their granted new token
  slots using verified observed prefix keys and native Host-node splits.
  Keep outstanding copies and full-source Host leases safe. Partial-layer
  loading is a different operation and does not imply partial token capacity.
  The frozen V220 predates these new TokenCake components and cannot qualify
  their real GPU behavior by inheritance.
- The TokenCake spatial adapter now binds observed metadata, sequential native
  prefill guards, pending slots, complete selected-batch settlement and dynamic
  pressure victims to the runtime. Release capacity ownership before native
  request memory; native requeue is too late. Preserve arrival and one history
  sample across attempts. The separate engine is
  `sglang-tokencake-runtime-20261025`; its patch and CPU evidence do not qualify
  full lifecycle preservation, paper predictive upload or GPU performance.
  V223 supplied the missing native lifecycle and consumer bindings. Register
  only the complete paper adapter, keeping the public and spatial components
  separate and requiring fresh source-bound GPU checks before each full run.
  Preserve V219c and V220 and their older frozen engines.
- Keep TokenCake's post-physical-retraction retry gate: while other admitted
  work remains, retry only after actual uncommitted capacity grows or an
  admitted request completes. Count its 500-step partition window with native
  decode scheduling steps, and do not double advance at prefill planning.
- Sessionless paper transfers own a separate native receipt ledger, registered
  before enqueue. Route their reconciled ACKs to their issuing paper adapter,
  rather than Full's transaction ledger. Keep duplicate, foreign, direction,
  byte-count and split-publication validation. Untagged demand transfers remain
  native-owned. CPU copy stand-ins must execute the before-enqueue callback and
  emit the same tagged child-receipt shape as the real controller.
- Preserve V220's CUDA-toolchain failure and V220b's partial run with the
  misrouted paper ACK, plus V220c's allocation failure and V220d's native
  producer-event reuse failure. V220d stopped after 77 completions in its last
  snapshot; it is not a performance result. The new
  prefix qualifier exercises actual runtime ACK routing as well as GPU bytes;
  older standalone byte checks do not establish that integration property.
- Speculative paper H2D checks all three native overlap finish events before
  allocating or claiming receipts, leaving native demand/overlap producer slots
  available. Use nonblocking event queries. Batch one maintenance round's
  targets into one producer submission, including flushing earlier successful
  enqueues when a later target fails. V220e passed real busy-event backpressure,
  multi-owner ACK and byte checks before starting the model. The isolated
  application is `d8612e402dab5a170cb712ce9e7d26e6f8f9916d`. Preserve its later
  V220e allocation failure: last snapshot 70 registered, 55 completed, 1189
  paper commands; 4096 slots requested with only 1156 free and no evictable
  slots. Its processes exited and cleanup completed.
- KVFlow speculative capacity must leave all already admitted but unallocated
  observed input plus one decode step available. Never read recorded final
  output limits for that reserve. Recompute only at the existing 200 ms
  maintenance point over admitted/running requests. Defer optional D2H if its
  native locks would consume this reserve, and do not start maintenance from a
  queue plan after the native adder already selected a chunk. The new GPU
  qualifier proved the 9-free/8-prefetch/4-chunk case preserves real chunk
  allocation and resumes prefetch after the admitted input finishes. V220f
  passed 29 commands and 21 native ACKs on the reference engine before model
  startup; frozen application `db74aabe312a82abae12e4205c1ec55459a19534`.
  Its complete result is pending. Shared CPU regression: 1102; reference
  engine regression: 90. Neither is performance acceptance.
- TokenCake's public observed lifecycle is CPU-bound, with incremental Host
  ownership, deadlines, resets and early-return reuse. It still grants no
  predicted upload. Keep it distinct from the paper temporal policy.
  V222 qualified the separate TokenCake engine with fifteen bounded model
  probes; V221c qualified its current prefix, capacity, ACK and allocation
  primitives. Neither is a full TokenCake policy execution. Queue dependent
  qualifications behind actual supervisor exit plus terminal artifacts before
  resource checks; a transiently empty GPU during startup is not permission to
  overlap the predecessor.
- TokenCake's separate paper temporal adapter now has CPU-bound first-fit
  observed-throughput checks, prediction deadlines, half-deficit uploads and
  exact-context native capacity reservations. Keep public lifecycle behavior
  distinct. GPU validation must exercise actual paper lifecycle and consumer
  handoff before a full paper run; older V221c primitives do not qualify these
  new components. V223 now qualifies all six real native GPU/Host lifecycle
  scenarios, including full upload, return during D2H/H2D, expiry, retirement
  and pressure. Its frozen application is `69a1f4d90dcfbf22b822217dd2946dd775a452f0`.
  Consumer handoff must validate the native receipt's node and FULL component;
  a default non-null empty receipt does not establish a consumer lock.
  Declare unspecified ranking, retry and serving-budget choices without using
  future trace lengths. This is still not a whole-model or performance result.
- The formal TokenCake paper configuration uses generation budget 16384,
  context 262144, running/decode 24, prefill budget 16384, chunk 4096, native
  policy block 1 and the public 128-block eviction window. The independent
  generation budget is not a replay stopping rule; all published outputs must
  still execute. Neutral metadata is used for unavailable future graph fields.
  Verify effective native context, batch, prefill, chunk and page size before
  measured submission. Keep actual policy parameters in runtime observations.
- Dependent baseline startup requires the predecessor's actual supervisor exit,
  terminal state, completed owned-process cleanup and no surviving owned child.
  Keep request/token/capacity identities equal and pin the predecessor
  registration. A terminal status file alone cannot release the GPU queue.
- TokenCake V224 is frozen at `5dd06b6fe9d56303d68f2caa4147127741c910f5`
  and queued after the actual V220f supervisor and cleanup. Its native engine
  remains `04fe085312a8578a581a8fb47ac7d0b915e3b404`; preserve that pending
  snapshot while continuing development. CPU evidence for that frozen entry
  is 1344 cases. Its model and policy results remain pending.
- With ThunderAgent completed, KVFlow running and TokenCake queued, resume
  Agentix CPU adaptation. Its swap actor now holds split-safe complete Host
  paths, protects old settled extents during incremental backup, and hands
  them off only with the restored consumer's real native lock/allocation.
  Paused ownership has no invented TTL; explicit resume/cancellation releases
  it. Keep timed leases finite and keep manual leases out of expiry heaps.
  The 1409-case shared CPU regression is not GPU or native-LLM qualification.
  Agentix stays unregistered until its actual executor, drain, preemption and
  resumption scheduler hooks are complete and physically verified.
- When an actual native allocation is short, its first eviction walk may find
  no eligible leaf while a paper D2H holds the native path locks. The existing
  write-back wait can unlock that path only after the walk finishes. Retry
  native eviction for the remaining shortfall after that ACK, without enabling
  Full policy flags, removing locks, or adding a background write-back barrier.
  The sessionless transfer owner restores the native allocation entry on close.
- Agentix's explicit per-worker execution binding now validates native compute
  streams and root ownership and preserves normal/deferred sampling. The
  148-case targeted CPU report includes the pinned worker method body with a
  stand-in runner, not actual native LLM execution. Keep the formal arm disabled
  until scheduler integration, real GPU preemption/resumption and independently
  declared parameters are qualified. Cancelling only a swap preparation releases
  controller Host references without touching GPU rows or native DMA locks; it
  must remain possible after an abort or while another forward is in flight.
  Actual checkpoint preparation/commit and policy cancellation still require
  their native drain and execution-accounting proofs.
- Agentix now has an unregistered native adapter combining root call lifetimes,
  observed shared-prefix capacity, native selection hooks and ACK-driven
  selective batch filtering/requeue. The 604-case CPU report covers actual
  native cache/allocator/batch code with worker/copy stand-ins. The new
  `attach_native_scheduler` runtime entry is not yet called by an isolated
  engine constructor. Preserve deferred completion until all overlapped
  forwards settle and retain the original arrival age of deferred successors.
  Retained multi-step rounds must skip whole-queue scans. Declare decode growth
  independently of trace lengths, at least the cadence plus two overlap steps.
  Before formal registration, finish the engine hook, real LLM/GPU preemption
  and resumption, Host-capacity backpressure/progress, native decode pressure,
  and formal parameters. Do not count native uncheckpointed retraction or Host
  backup refusal as a successful paper swap; these currently fail qualification.
- The isolated Agentix engine now calls scheduler attach after cache attach;
  tree `c489dbb85265efce25622a52abfd698e9199688d`, canonical patch SHA256
  `15fe59ebe16721e5ee07244d0d25381b0187b91b1afa57f8fd06281fe051cd09`.
  Preserve V220f/V224 engines. The explicit finite-Host adaptation budgets live
  checkpoint unions and growth while reserving one configured maximum context
  for progress; under pressure it retains one paper-selected live call only
  until that call completes. It adds no agent continuation or JOIN privilege.
  Capacity-confirmed backup refusal now retains actual running work and retries
  after capacity/DMA/lifecycle changes, without queue rescans on unchanged state.
  Unexplained refusal and uncheckpointed native decode retraction still fail.
  The 628-case CPU report includes the real constructor method/factory binding.
  Only the four-request `agentix_atlas_native_qualification` entry is exposed,
  behind explicit enablement and its separate fixture configuration. It is not
  a formal baseline, and its running=1/small quanta must never become benchmark
  defaults. The replacement V225b freezes the bounded source-bound native LLM/GPU
  supervisor at application `2b80c0d59af9b92ae0e226b7c6cd48c6e00f55ae`, queued
  behind V224b's actual exit and completed cleanup. Supervisor PID 3047217,
  start ticks `715454684`; port 18638. Its four probes use the published 110720-input,
  18-output-token request with independent context namespaces, then one resident
  repeat. The 667-case CPU report is
  `agentix_runtime_supervisor_all_baselines_cpu_20261025.xml`. V225b must rerun
  source-bound real prefix/swap checks before its actual model preemption and
  resumption probes. This does not qualify finite Host pressure, whole-population
  performance or formal parameters; complete those before baseline registration.
- Preserve V220f as all 2229 requests completed but idle-drain validation failed.
  Its nine native retractions were registered again in telemetry `_pending`
  while still `_active`, leaving nine phantom pending entries after completion.
  `on_enqueue` now excludes active/completed calls and retains their original
  arrival. CPU regression: 1091 cases, including the reproduced failure. Keep
  the original failure and diagnostic metrics; do not relabel it as qualified.
  V224 was stopped during model startup before formal submission, and waiting
  V225/V226 were stopped and cleaned to replace this shared instrumentation.
  Current sequence: V220g (PID 3046284/start `715446832`, app
  `bb63a5b66b5cd42689b87757b48f5929e4a75bd1`), V224b (PID 3046922/start
  `715451340`, app `dea4616ee40749363329d16af9d30fb76e12dbb1`), then V225b.
  Preserve all earlier frozen trees and the required two fresh idle snapshots.
- V226b queues actual small-pool Agentix pressure components after V225b:
  PID 3047528/start `715458321`, app
  `214f46cad2a6fcc033b21ed90883a6e7a1b8549b`, port 18639.
  It uses 128 native Device slots, 257 native Host slots and context 64, with
  observed growth, backing admission, real Host exhaustion/refusal/retry, and
  six paused consumers restored with their complete KV bytes and Host pins.
  The 690-case CPU report uses synchronous copy/event stand-ins; GPU evidence
  remains pending. This component fixture does not establish full-model
  Host-pressure progress, model numerical equivalence or formal performance.
- V227 now queues the Full sessionless native prefix component qualifier after
  V226b's actual exit and completed cleanup. PID 3071982/start `715994041`,
  application `e53af6c6f493b66dcdfc7381c0dea9c8be7aa746`, port 18640,
  engine tree `c489dbb85265efce25622a52abfd698e9199688d`, native patch SHA256
  `15fe59ebe16721e5ee07244d0d25381b0187b91b1afa57f8fd06281fe051cd09`.
  Its ten scenarios use 128 Device / 257 Host slots and two K/V layers,
  actual native requests/prefill admission, released-page poisoning, incremental
  copies, unstarted shared owners, early return, controlled stop versus length,
  inferred-JOIN refusal, producer backpressure, Host exhaustion and native chunk
  reservation. The 1011-case shared CPU report executes the same scenario
  functions with copy/event stand-ins; it does not qualify real GPU behavior.
  Keep V227 and prior snapshots frozen. This is neither a full-model Full run
  nor a formal Agentix baseline; both remain to be qualified and executed.
- The next Full model qualifier uses six unforced greedy requests from one
  observed 65600-token published prefix, comparing resident, actual native
  demand recovery and controlled JOIN recovery outputs/logprobs. Keep its
  eight-token fixture and 0.05 logprob tolerance separate from replay work;
  do not install a forced-output processor or call this original AgentX EOS.
  It preserves common pool capacities and running/decode24, reruns source-bound
  native components before model startup, and requires real Host/Device cache
  hits plus same-agent native reservation consumption. The shared CPU report
  `full_native_model_all_baselines_cpu_20261025.xml` has 1040 passing cases,
  no skips; model/GPU/performance acceptance remains pending. Preserve the
  original targeted failure and the 154-case retry report.
- V228 queues that Full model qualifier after V227's actual exit and complete
  cleanup: PID 3084149/start `716232632`, app
  `37318e5a843303ea9c175528f169d80e0a712132`, port 18641, NI=0.
  Registration is `experiments/raw/full_native_model_v228_20261025`.
  Its frozen source manifest, input/predecessor identities and unforced
  environment were rechecked in the frozen checkout. It currently owns no GPU
  child. Preserve this snapshot and all predecessors; a successful bounded
  model check still does not qualify whole-workload performance.
- On 2026-10-26 the user explicitly authorizes stopping a clearly slower
  TokenCake early and switching immediately to LoCoBench-Agent. This supersedes
  the AgentX-only and complete-the-cycle rules above. V224b and queued
  V225b/V226b/V227/V228 have exited with owned cleanup verified; preserve their
  administrative stops and partial results. Do not restart that queue.
- The separate `native_in_graph_short_workstreams` source profile gives each
  child one narrow deliverable, with 4/8 model-call soft reminders/targets,
  model-selected parallel follow-ups and depth at most two. Require real
  project/tool execution before a fresh natural capture. Do not manufacture
  long tools, fixed DAGs, unnecessary validation or hard-truncated children.
  Start capacity calibration from 18 different projects and running/decode24;
  choose intensity from blocked exclusive KV, restore demand and active
  headroom, rather than the outcome of Full versus a baseline.
- The latest user asks for quick environment repair in this order: pin real
  per-project dependencies/entry points and share them between root and child;
  separate environment and task errors without repairing the task target;
  expand original LoCoBench candidates instead of rebuilding inconsistent
  projects; verify on CPU before a new natural capture. Original production
  examples are explicitly only environment checks, not whole-suite or task
  correctness. Keep their scope in the workload and agent instructions.
- CPU sandbox qualification must use a private writable source copy with the
  live user's UID/GID. Preserve the image/project PYTEST_ADDOPTS when adding
  JUnit/profiling options. The old read-only probe and overwritten asyncio
  defaults could reject otherwise executable code; preserve those old reports.
  Passing business assertions is not required for actual-execution evidence.
- For the new LoCoBench calibration, running/decode12 is the initial upper
  bound, with bounded checks of 8/12/16. This supersedes the fixed-24 starting
  rule for new source captures only. Keep 18 different projects as the formal
  population target and use small CPU/live-sandbox batches first. Select the
  limit using actual unique active pages, native growth, one selected restore,
  compute progress, and ACK-to-first-use delay. Do not reserve a percentage
  merely to manufacture cold KV, evict a ready peer for equal-priority prefetch,
  or alter the frozen baselines. Use the same chosen limit in every formal arm.
- The latest user correction supersedes test/application-execution qualification
  as a source-start gate. Repair only tool infrastructure: Docker/shell, writable
  workspaces, interpreter/dependencies, real package paths and environment
  propagation. Project source, test syntax, fixtures and API defects belong to
  the agent task; do not fix them during workload preparation or require tests
  to pass before capture. Revert the earlier independent project repair copies
  and stop injecting custom test fixtures or replacement framework settings.
  Keep historical evidence, raw test outcomes and original task targets.
- Keep Full's ThunderAgent-based agent eligibility and continuity as the
  scheduling unit. Do not tune a small request running limit to manufacture
  cold KV; retain running/decode24 as a shared engine safety ceiling for the
  next source and controls. Agent capacity, blocked-state lending and native
  physical guards determine Full admission. Ordinary execution-frontier
  restore remains off. A fresh natural native capture is still needed after
  changing the workload/prompt/environment; it does not enable Full scheduling.
- The latest execution order is v229 natural-source audit, then predictive Full,
  then ThunderAgent. Run other baselines only after Full demonstrates clear
  complete-work JCT and full-run throughput gains over ThunderAgent; Agentix
  adaptation is no longer the immediate critical path. Preserve unsuccessful
  comparisons. Workload changes require a new declared source and a mechanism
  justification, not selection of a winning trace.
- The next Full candidate is declared in
  `configs/migration/predictive_agent_cold_tools_20261026.json`: retained agent
  eligibility, JOIN/EOS warming, conditional tool warming on already active
  tool-wait owners, spawn incremental backup and actual-blocking/long-tool
  offload, cold prepare_host, short-tool continuity and optional observed
  ALL-JOIN sibling co-admission. Tool warming grants no new ordinary recovery
  reservation or permission to reclaim ready queue tails. Co-admission uses
  available capacity, does not pause a ready peer, and never waits for an
  unready sibling. The paper ThunderAgent policy remains unchanged.
- V229 failed its unchanged parallel-root contract: one root completed four
  singleton rounds but no two-child JOIN. Preserve its failure and stopped
  processes; do not requalify the partial trajectory. V230 retries with the
  same 18 original projects, tools-only environment, seed21 and automatic
  initial tool choice. At an unmet final return only, required task attempts
  now use the existing native 2--4 parallel-call grammar; the model still
  chooses assignments, specialists, fanout within the bound and later work.
  Keep this workload intervention in its contract and source audit. No
  programmatic children, forced DAG, tool padding or pre-fixed project tests.
- The V231 follow-up to V230 queues source review and bounded position-replay
  qualification, followed by V232 Full then ThunderAgent. Review the complete
  population and drained native telemetry before allocating the next GPU job.
  Require actual Host-prefix recovery or old served-input loss for the Full
  mechanism probe; do not infer exclusive cold pages from those counters.
  Preserve sampled GPU waits without expanding them or subtracting them from
  JCT. The first Full run must still establish real native donor safety and
  first-use evidence. All other baseline arms remain deferred.
- V230 completed all 18 native workflows and 3674 model requests with 72
  JOINs: 52 singleton and 20 two-child rounds, with no nested delegation.
  Every root completed the declared parallel JOIN; all 18 used a final-return
  delegation reminder, which must remain disclosed. Original token and causal
  capture audits passed. Retain every normal tail, including a 180-second tool.
- V231 stopped before GPU allocation because its new source auditor incorrectly
  divided Device allocation bytes by allocatable token capacity. The native
  Device pool includes a reserved page; source Host bytes per token must come
  from observed Host geometry, checked against Host capacity. Keep that failed
  registration. V231b supersedes only the follow-up audit/qualification snapshot;
  it reuses V230 unchanged and still schedules Full then ThunderAgent as V232.
- V232 completed Full and ThunderAgent on all 3674 requests and identical
  tokens. Full mean JCT was 1316.524s versus 1339.764s for ThunderAgent;
  whole-run output throughput was 261.508 versus 262.225 token/s. This does
  not meet the declared improvement gate; preserve both results and keep
  other baselines deferred. All owned processes exited and telemetry drained.
  Actual H2D was 341.792GB for Full and 216.496GB for ThunderAgent. Matching
  decode geometry does not show a substantial worker-speed regression.
- The separate backed-agent-lending candidate is declared in
  `configs/migration/predictive_backed_agent_lending_20261026.json`. Keep
  retained continuity and the paper policy unchanged by default. In Full only,
  at the one-second maintenance point, admit at most one real queued agent
  using non-running, non-pending blocked owners with complete, settled
  native Host backing and no JOIN restore protection. Check at most eight
  donors with at most 64 nodes per path. This grants logical eligibility
  only, creates no D2H and never authorizes ready-agent physical eviction;
  native allocation remains authoritative. Preserve the historical idle-only
  lending configuration and compare on V230 unchanged before claiming gains.
- V233 independently registers that single Full candidate at application
  `c7562c31ec3b71139b4ca9d9f430d51e499e44b2`, engine tree
  `6437d7deeacc533f0d795ba166dfd5256b3f44b2`, port 18645 and
  `ops/v233_locobench_backed_agent_lending_20261026.registration.json`.
  Shared CPU regression passed 1396 cases without skips, retaining all 1232
  prior cases. Its source and parameters remain frozen while running. Reuse
  V230 and complete V232 reference metrics; do not rerun native or other paper
  baselines before reviewing this candidate. Preserve the pre-GPU registration
  failures and engine-evidence rebinding. CPU tree tests use copy/event
  stand-ins and do not establish new GPU numerical equivalence.
- V233 completed all 3674 requests with identical tokens/stopping and all
  18 normal workflow tails. Telemetry drained and owned processes exited.
  Mean JCT is 1324.729s and whole-run output throughput is 272.709 token/s:
  1.122% lower JCT and 3.998% higher throughput than V232 ThunderAgent,
  insufficient for the declared gate. Keep other baselines deferred.
  Complete decode audit is
  `experiments/reports/v234_decode_batch_with_v233_complete_20261026.json`.
  Actual mean batch rose from V232 Full's 5.035 to 5.657, while actual H2D
  rose to 445.022GB. Preserve the JCT/transfer tradeoff and negative evidence.
- The pinned ThunderAgent author router has an 1800-second force-resume
  escape, not continuous age-based priority promotion. Full's safe capacity
  handover is distinct; neither its starvation counter in V232/V233 nor the
  V232 paper adapter's timeout fired. Do not remove the escape under the
  mistaken premise that author code lacks it. Next decode work should audit
  effective width and bounded safe admission; dense graph tuning, if used,
  belongs to common engine controls with identical KV capacities. Do not
  rotate ready agents or inflate the request ceiling merely to fill batches.
- The next declared candidate is
  `configs/migration/predictive_join_loan_return_20261026.json`:
  `backed_demand_recall` retains V233 lending and adds conditional continuity
  for new admissions during open blocked-owner lending periods. Only an
  actually submitted JOIN successor can recall enough recorded, non-running,
  unprotected borrowed eligibility at a natural request boundary; EOS alone
  cannot recall, unrelated preexisting ready agents are excluded, and native
  KV allocation/DMA remains authoritative. CPU regression passed 1465 cases,
  no skips, retaining V233's 1396 cases. Preserve early targeted failures and
  the old V233 configuration. Reuse V230 and V232 ThunderAgent, run only Full,
  and evaluate the possible migration/JCT cost along with decode batch width.
- V235 is now running that single Full candidate from frozen application
  `f26f1998ae5f7e1526798a4cacff5379248eff3d`, engine tree
  `6437d7deeacc533f0d795ba166dfd5256b3f44b2`, port 18646.
  Registration is
  `ops/v235_locobench_join_loan_return_20261026.registration.json`.
  Supervisor PID 3463228/start `719247404`; server 3463571/start `719247867`;
  replay driver 3465018/start `719262732`. Live observations confirm
  `backed_demand_recall`, `join_loan_recall=true`, and physical_disabled=false.
  Keep these sources frozen. The engine proof was rebound only to the new
  canonical patch pathname after confirming every other engine-state field,
  canonical tree and patch byte hash identical; preserve its pre-GPU check
  failure and rebinding evidence. Full performance and numeric qualification
  remain unproven.
- V235 completed all 3674 requests with identical tokens/stopping, drained
  telemetry and owned cleanup. Mean JCT is 1344.922s versus ThunderAgent's
  1339.764s, while whole-run throughput is 280.631 versus 262.225 token/s.
  This 7.019% throughput gain with 0.385% worse JCT fails the shared gate.
  Actual mean decode batch increased to 6.418, worker-weighted width to 8.369,
  with 1518.026s decode worker intervals and 647.027GB actual H2D. Preserve
  the complete result, all ordinary tails and the additional migration cost.
  Fourteen JOIN recalls reduced summed successor queue waits to 1361.661s
  from V233's 3414.865s; overlapping intervals are not removable JCT.
  The offline loan/budget reports distinguish logical eligibility from physical
  exclusive/reclaimable pages, and retain unreconciled sampling gaps.
- The separate workflow-entry candidate keeps V235 agent continuity, lending,
  JOIN recall and native physical authority. In Full only, defer a workflow's
  first eligibility when at least eight other active agents have real pending,
  unblocked model work. Once native service starts, its children and subsequent
  requests bypass this gate; active agents are never rotated for it. Admit at
  most one newcomer in each ordinary five-second resume pass, with the original
  1800-second escape retained. All workflows' JCT still starts at their original
  submission; do not hide deferred entry. Check the gate before any donor pause
  or Host-backing proof. This is an unaccepted candidate, not evidence that
  narrowing initial workflow spread improves decode efficiency or mean JCT.
- V236 froze application `59a91c28ea8c73723db1fc54667229a42b546e16`
  with 1527 shared CPU cases and real runtime entry count eight verified.
  Its supervisor stopped before GPU allocation because the inherited launcher
  treated another user's busy GPU as a startup failure instead of queuing.
  Preserve that registration/outcome and use a new frozen retry. The repair
  waits for two fresh resource-ready observations both before initial setup
  and each cold arm, keeps hold/stop checks live, and never kills foreign jobs.
- V236b supersedes only V236's stopped launch, keeping its entry policy.
  Frozen application is `6f2ac58f42040dafcd74b41493cf95aa98dbb106`,
  engine tree `6437d7deeacc533f0d795ba166dfd5256b3f44b2`, port 18647.
  Registration is
  `ops/v236b_locobench_workflow_entry_20261026.registration.json`.
  Its 1534-case shared CPU report retains all 1527 V236 cases, no skips.
  Supervisor PID 3499431/start `719738488` was initially observed alive in
  `waiting_idle_gpu`, with no owned GPU children while a foreign job runs.
  Real runtime CPU preflight verifies entry count eight. Preserve this source
  and retain its original execution record; do not report performance gains or numerical
  qualification from the registration or CPU fixtures.
- The user's 2026-10-26 reservation prohibits all new GPU experiments until
  2026-10-27 11:00 Asia/Shanghai, as recorded in the shared hold file.
  V236b stopped on the new hold at 2026-10-26 23:34:53 Asia/Shanghai, with
  `HoldChanged` and all owned processes confirmed gone. Preserve its partial
  result as an administrative interruption, not a complete comparison. Do not
  restart the frozen partial arm or the earlier stopped AgentX queue.
  Continue CPU-only development with CUDA devices explicitly hidden.
- Completed implementation changes are now recorded on main in `a8c750ae`
  and `74afed24`. Commit further completed changes in coherent groups rather
  than accumulating an unreviewable working tree. Preserve source references
  required by committed workload manifests as well as executable code.
- The CPU-only tool-loan-return candidate defaults off. A matched final
  TOOL_END may mark an inactive recorded long-tool lender, but recall requires
  its actually submitted successor and enough recorded nonrunning, unprotected
  borrower budget. JOIN recall remains first; speculative tool warming cannot
  recall ready budgets. Independent tool/JOIN/foreground-child dependencies
  must all clear before eligibility is unblocked. See
  `docs/design/tool_loan_return_cpu_candidate_20261026_zh.md`.
  Its 494-case targeted CPU report is not shared GPU/numerical/performance
  qualification and does not complete inactive-owner tool warming.
- Captured replay workflow service measurement is opt-in through the common
  frozen `--stream-response-metrics` setting, applied identically to all arms.
  Count first actual descendant output separately from post-first ready output
  gaps, exclude genuine all-branch external blocking, preserve every root and
  disclose SSE coalescing and per-request starvation. Historical worker-launch
  proxies are not token TTFT. Offline common-budget SLO evaluation supplements
  rather than replaces full JCT/throughput acceptance. Do not force a poor
  fairness wrapper on the unchanged paper baseline or mix old nonstreaming
  results with new streaming controls. Progressive layer-capacity reuse remains
  a design investigation: current native allocation reserves all layers up
  front and no engine allocator change has been qualified.
- Future progressive layer-capacity reuse must coordinate with retained agent
  admission, which currently charges complete contexts even after physical
  offload. Use an explicit safe A-to-B handover: the final full working set must
  fit, each layer must have proven exclusive capacity and dependencies, and the
  donor's logical budget may transfer only once while its draining physical
  pages remain accounted. A warming consumer cannot bypass native admission
  without a separately qualified engine contract. Do not fractionally discount
  complete context costs, admit both owners against the same budget, or create
  ready-agent rotations to exercise the pipeline. V247's engine change is the
  common free-page fence; it has no progressive layer allocator.
- Shared CPU report `v238_workflow_service_shared_cpu_20261026.xml` passes
  1739 tests plus four subtests without skips, retaining the entire V236b
  1534-case set and adding graph/tool-return/streaming/SLO coverage.
  The final V230 offline graph replay retains all 22268 events across 18
  workflows and finds no changed invocation state. Its one append timestamp
  inversion is sorted explicitly for this offline chronological comparison.
  No GPU or performance result is implied.
- The CPU-only free-page H2D fence candidate is isolated in native branch
  `beliefkv-free-page-h2d-fence-20261026`, commit
  `d3f9f37648be64af598fb936b7631fa8e6c215ba`, tree
  `11b84f7c31986782be3fbe699bae2574ef092389`. Its delta applies only over
  base commit `fa5cb52afaeaad1f59f6d3eb2c5cf124f72bb491` / qualified tree
  `6437d7deeacc533f0d795ba166dfd5256b3f44b2`.
  Declaration: `configs/migration/predictive_free_page_fence_20261026.json`.
  Keep default off; no GPU job is registered. An immutable pre-forward event
  may replace the whole-forward wait only for allocations from an unchanged
  proven free set, with one shared proof for every merged operation and no
  extra-pool transfer. New or unproven reclaimed pages keep the original fence.
  Native demand and controlled loads receive the same optimization under the
  common flag; compare Full and ThunderAgent with matching enablement rather
  than giving the baseline event overhead without access to the fast path.
  Shared CPU: 1815 tests plus four subtests, retaining all 1739 previous cases.
  Native CPU: 726 tests plus 57 subtests. AST projection, CPU timing stand-ins
  and sub-microsecond Python guard measurements do not establish CUDA safety,
  event costs, model numerical equivalence or performance. Complete real
  writer/page-poison/copy tests and unforced session-model checks after the
  reservation before accepting this engine for a full comparison.
- The separate session model qualifier is
  `scripts/qualify_session_native_model.py`, declared by
  `configs/migration/session_native_model_qualification_20261026.json`.
  Its 49152-token Device / 8 GB Host fixture uses two 32768-token real-source
  inputs, unforced eight-token greedy output, actual demand/JOIN restoration,
  native session-generation capture and verified allocation reuse. These small
  capacities and synthetic JOIN events are only model correctness fixtures;
  retain the complete V230 work and original capacities for performance.
  Run its real writer/copy prerequisite and model checks after the reservation.
  CPU HTTP/event/parser tests do not qualify GPU or numerical behavior.
  Its shared CPU report `v241_session_native_model_shared_cpu_20261026.xml`
  passes 2083 cases plus four subtests, retaining all 1857 previous cases.
- The separate `AGENT_RECALL_SELECTION=cold_fit` candidate changes only
  recorded-loan victim choice. Keep `largest` as the default and isolate paper
  arms. Retain all in-flight/critical/restore exclusions, check enough total
  credit before pausing, rank actual blocked owners before between-request and
  ready owners, and prefer a single fitting budget within each tier. A ready
  sole JOIN blocker is a last resort, never an unlimited reservation.
  `agent_loan_recall_decision` records actual executed logical handovers; it
  grants no physical KV authority. Preserve V235 and its 647.027GB H2D result.
  The new complete audit reconciles native demand with FULL Host hits and
  finds 145.205GB associated with short-tool successors in V235; it does not
  prove a specific recall caused those bytes. Declaration:
  `configs/migration/predictive_cold_fit_recall_20261026.json`.
  Shared CPU report `v242_cold_fit_recall_shared_cpu_20261026.xml`: 2123 cases
  plus four subtests, retaining all 2083 prior cases. GPU effects remain pending.
- Agent admission may clear a JOIN wait only when that waiter still names
  the same JOIN and the causal graph is READY. Other tools, foreground children
  and later JOINs retain their blocking state; an old release cannot grant new
  critical protection. The complete historical serial replay projection found
  no suppressed releases in four arms of 72 explicit JOIN releases each, so do
  not claim this boundary fix explains V235 performance.
  `v243c_agent_join_release_shared_cpu_20261026.xml` assembles 2153 passing
  shared cases with the exact two passing Docker fixture retries, retaining all
  2123 previous cases and adding 32, plus four subtests. Preserve the original
  failure/skip reports and disclose that this is a two-execution assembly.
  The full causal JCT path audit shows V235 reduced submit-to-first-launch
  waiting but increased first-launch-to-complete intervals; those intervals
  include interleaving and are not pure GPU time or causal module attribution.
  See `docs/design/agent_join_release_dependencies_20261026_zh.md`.
- The read-only request interleaving auditor clips recorded worker membership
  to each real request lifetime, unions overlapping spans, and preserves stale
  post-completion descriptors as diagnostics. Its member union and complement
  reproduce all four complete historical JCT paths. V235's extra post-launch
  path time partitions into about 140.84s of member intervals and 54.33s of
  other intervals; neither is pure GPU time or an isolated migration cost.
  Matching decode geometry finds only about 0.16--0.21% worker-interval
  differences, supporting admission/service-shape review without proving a
  unique cause. Do not increase borrowed concurrency just to widen decode.
  The original source completes 69.916% of output before its first parallel
  ALL-JOIN wait, but only 7.652% before its first JOIN of any size; preserve that
  distinction and all normal tails. New audit coverage is 70 CPU cases, 21 new.
  Common runtime/engine sources are unchanged and retain V243 evidence.
  See `docs/research/request_service_interleaving_20261026_zh.md`.
- Session model registration must contain the frozen candidate's exact
  `reference_environment` for the actual component CUDA toolchain builder.
  Reject missing or changed toolchain/device declarations before execution.
  V245 reproduces the previous missing-field KeyError and passes 2180 shared
  CPU cases plus four subtests, retaining all 2176 earlier case identities.
  Independent prepared execution may bind the reviewed free-page delta to its
  local staging-patch path through the existing freeze utility; preserve main's
  historical patch and user indexes. Rebind native proof paths only after
  proving identical bytes, tree and all other source-state fields. A new engine
  needs its own GPU component/model and replay smoke evidence before comparison.
  See `docs/design/session_model_launch_preparation_20261026_zh.md`.
- `scripts/deferred_free_page_validation.py` may register a CPU-only waiter
  behind the exact 2026-10-27 11:00 Asia/Shanghai hold. It must not query or use
  GPUs during the hold. Pin the committed operator, prepared sources and inputs;
  stop on a new pause, reservation/source change or explicit stop. Require
  actual component/model success and complete owned cleanup before handing
  off to position replay and Full then ThunderAgent on the same new engine.
  Verify the live supervisor's PID/start ticks, argv, source map and order.
  Preserve partial failures and never restart them automatically. A queued
  CPU waiter is neither a running GPU experiment nor performance acceptance.
- The user explicitly releases the GPU reservation at approximately
  2026-10-27 10:45 Asia/Shanghai. Preserve the stopped V246 CPU waiter and the
  archived original hold. Component/session qualifiers may honor the new
  `explicit_user_release` only after checking its archived hold hash, original
  deadline, user-message provenance and timezone-aware times. Current shared
  hold/pause checks still apply. Qualify and freeze the updated launch guards;
  keep the prepared native engine and Full/ThunderAgent policy sources unchanged.
- V246's first real component attempt failed before model startup: the fixture's
  scalar indexed writer submission consumed its own overlap window. Preserve
  that failure and the V251/V251b diagnostics. Commit `c688f850` changes only
  the fixture to prewarmed `index_fill_` writers and records scenario-specific
  ordering failures; the native engine is unchanged. Targeted CPU: 148 passing
  cases, no skips (`v252_writer_fixture_retry_cpu_20261027.xml`).
- V246b passed all nine real native copy/writer scenarios and the unforced
  session model demand/JOIN checks at 2026-10-27 11:11:33 Asia/Shanghai.
  Actual greedy tokens match and model numerical checks pass; all owned
  processes exited. Frozen application is `7375e3aa1d47`, native tree
  `11b84f7c31986782be3fbe699bae2574ef092389`. The 49152-token Device / 8GB
  Host fixture remains distinct from measured population capacity. Proof:
  `experiments/reports/v252_session_model_gpu_verified_20261027.json`.
  This does not establish whole-population correctness or performance gains.
- V246 position replay now runs from the original qualified prepared policy
  sources: `ops/v246_free_page_position_20261027.registration.json`,
  supervisor PID 3880964/start ticks `724121790`, port 18649, application
  `b185ccaf19753516ac1cace00efc620ccb495949`. It owns its real GPU server
  and runs the eight-context/three-turn token/session/stop smoke before
  registering V247 Full then ThunderAgent. Model-to-position handoff is
  independently verified in `v252_model_to_position_handoff_20261027.json`.
  Keep these sources frozen, retain exact V230 work/capacity/seed21 and do not
  start other baselines before the shared JCT/throughput improvement gate.
- V246 completed its 24-request bounded position/session/stop GPU smoke and
  registered V247 at 2026-10-27 11:20 Asia/Shanghai. V247 is now `running_full`:
  `ops/v247_cold_fit_free_page_20261027.registration.json`, supervisor
  PID 3884554/start ticks `724148546`, GPU scheduler PID 3885477.
  Live runtime confirms physical control enabled, `thunderagent_retained`,
  `backed_demand_recall` and `cold_fit`. Its Device capacity is 805198 tokens,
  Host capacity 200000077824 bytes, running/decode24 and seed21.
  Full then ThunderAgent share engine tree
  `11b84f7c31986782be3fbe699bae2574ef092389` and the enabled free-page fence.
  Proof: `experiments/reports/v253_full_live_start_20261027.json`.
  The standalone smoke correctly leaves whole-workflow replay qualification
  false; independent GPU ownership and position evidence are in its separate
  `gpu_smoke.json`. Keep population/performance acceptance pending until each
  full arm completes with exact work, drained telemetry and owned cleanup.
- V247 Full completed all 3674 requests and exact tokens with original native
  stopping, all 18 workflow tails, two fresh idle telemetry snapshots and owned
  cleanup. Mean JCT is 1423.068672s and whole-run throughput 286.592505 token/s;
  actual H2D is 733.581902GB, including 606.647058GB native demand. Its mean
  JCT is worse than historical V235; do not call this a performance win.
  The common-engine ThunderAgent arm is running: server 3903870/start
  `724362693`, scheduler 3904251/start `724363947`, driver 3905991/start
  `724377980`, verified at 2026-10-27 12:13 Asia/Shanghai. Preserve its frozen
  sources and complete it before another GPU job or final acceptance.
- The separate observed-growth repair refreshes Full's ThunderAgent-derived
  context accounting from actual native output lists at new-agent arrivals
  and five-second maintenance points. Release live request references at
  completion/retirement/abort and rebind native attempts; reject stale visible
  identity as progress evidence. Keep native allocation authoritative and the
  original working_set and paper policies unchanged. V247 does not contain
  this repair. Its targeted CPU reports pass 136 and 248 cases without skips,
  including native lifecycle with copy/event stand-ins; this is not the entire
  shared regression or new GPU/performance qualification. The callback-only
  CPU benchmark is source-bound and grants no whole-controller cost or JCT
  claim. See `docs/research/v247_full_and_observed_growth_20261027_zh.md`.
- V247 completed both arms with identical complete work, original stopping,
  fresh idle telemetry and actual owned-process exit. Full mean JCT is
  1423.068672s versus ThunderAgent 1320.207142s; throughput is 286.592505
  versus 259.792268 token/s. The 10.3160% throughput gain and 7.7913% worse
  JCT fail the shared gate. Full actual H2D is 733.581902GB versus 242.571903GB.
  Preserve this negative result and keep other baselines deferred.
- The observed-growth repair now has 2249-case shared CPU coverage. Main's
  original 2247-pass/two-skip report and exact passing Docker retries remain
  separate; v257c is explicitly a two-execution assembly. Prepared application
  `d2e9835d64a328a3d6ad76482eea10010a06a015` independently passed all 2249
  cases plus four subtests in one execution, no skips. Native implementation,
  tree and patch bytes remain identical; only its canonical path provenance
  is rebound. Keep all earlier qualification/failure reports.
- V260 is running a single Full observed-growth comparison after V247's actual
  exit: `ops/v260_locobench_observed_growth_20261027.registration.json`,
  execution `beliefkv-causal-d2e9835d64a3`, supervisor 3932462/start
  `724661409`, port 18650. Source work, capacities, environment and common
  engine equal V247; only `beliefkv/runtime/agent_working_set.py` changes within
  the BeliefKV package. Reuse complete V247 ThunderAgent; do not rerun full
  native or other paper policies. Startup is not new-policy GPU/performance
  acceptance. Complete and review this candidate against both primary metrics.
- At 2026-10-27 12:52 Asia/Shanghai, V260's supervisor, server
  3932975/start `724664165`, GPU scheduler 3933294/start `724665579`, and
  driver 3934418/start `724679062` were independently verified live. Actual
  runtime shows observed-growth accounting and 326 updates; the first 339
  sealed completed receipts have exact tokens. Source and predecessor exit
  checks passed. See `v260_observed_growth_live_gpu_20261027.json`. Keep
  whole-population correctness and performance acceptance pending.
- A CPU-only real-runtime counterexample shows backed lending can choose an
  ordinary continuation while its donor parent is WAIT_JOIN and the sole
  remaining child has a pending but ineligible request. Host backing is an
  explicit stand-in; there is no physical copy, trace-frequency or JCT proof.
  See `v261_join_dependency_admission_counterexample_cpu_20261027.json` and
  `docs/design/join_dependency_agent_admission_20261027_zh.md`. The existing
  JOIN_LAST_CHILD_PRIORITY is restricted to request join_priority mode and
  cannot silently become an agent-admission option in workflow mode. Keep
  V260 frozen and finish it before choosing another GPU candidate.
- The user's latest reservation permits V260 to finish, then prohibits every
  new GPU experiment until an explicit availability notice. V260 completed at
  2026-10-27 13:21:22 Asia/Shanghai, exit code zero, with no owned processes
  remaining in its terminal state. The shared hold now disables automatic
  resume and `/tmp/beliefkv-experiments.paused` blocks new launches. Do not
  infer permission from idle GPUs, an old release or the previous 11:00
  deadline. Continue CPU-only analysis and implementation with CUDA devices
  hidden; preserve V260's performance review as pending until audited.
- V260's complete summary preserves all 3674 requests, 145565966 prompt tokens,
  549671 output tokens and 18 workflow tails, with exact tokens and original
  stopping verified. Its mean JCT is 1395.545895s and throughput 284.644416
  token/s. Against unchanged V247 ThunderAgent, JCT is 5.7066% worse and
  throughput 9.5662% higher; the shared gate still fails. Two fresh idle
  telemetry snapshots, cleanup and independent PID/port checks establish owned
  exit. Main comparison/qualification and frozen V260 launch guards reject the
  new reservation. See `v262_v260_completion_gpu_hold_20261027.json`; this
  arithmetic review is not a new GPU run or complete causal attribution.
- The user authorizes a separately declared synthetic tool-timing workload
  if the current natural trace offers limited opportunity. Derive durations
  from public records of actual tool execution, pin provenance and use one
  fixed identity-indexed duration map for every compared method. Preserve the
  source graph, requests, tokens, tool outputs and normal tails. Do not label
  synthesized timing as natural LoCoBench execution, expose future sampled
  completion times to the online policy, assume long tools necessarily cause
  ThunderAgent migrations, or launch this GPU workload during the reservation.
- Empirical tool timing is now CPU-prepared through
  `scripts/prepare_empirical_tool_timing.py` and the optional
  `--tool-duration-overlay` in the causal driver/comparison supervisor. Pin
  TraceLab v0.0.1 compressed data digest
  `9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b`.
  Use reported internal times without wall-gap imputation, duration filtering,
  scaling or tail clipping. Two predeclared profiles preserve V230's complete
  graph/work and change only 931 execute timers. The network profile has
  313 deduplicated observed samples, substantial missing-time coverage limits,
  and sampled median 6.167s; the shell profile retains many reported zeros.
  Keep both profiles and their negative outcomes. See
  `docs/research/empirical_tool_timing_cpu_20261027_zh.md` and final CPU maps
  `v268_v230_empirical_shell_timing_cpu_20261027.json` /
  `v269_v230_empirical_network_timing_cpu_20261027.json`.
  Timing samples stay private to replay; online TOOL_START gets no future
  duration. Every arm pins the same map, and natural native time is removed
  as a synthetic performance baseline. Complete CPU DAG and mock HTTP checks
  are not new GPU, whole shared regression or performance qualification.
  Nothing is registered for GPU during the active reservation. Before a
  long-tool-policy claim, separately qualify the public ThunderAgent optional
  acting-token-decay resume behavior; the historical paper projection omits it.
  Final targeted CPU coverage is 433 passing cases without skips in
  `v271_empirical_tool_timing_final_cpu_20261027.xml`; source/config/artifact
  digests and the explicit pending GPU/performance scope are in
  `v272_empirical_tool_timing_cpu_evidence_20261027.json`.
- The user has explicitly released the later GPU hold with "现在可以进行GPU实验了"
  and requests ThunderAgent on the new trace before the predictive repairs.
  This supersedes the previous reservation and Full-first ordering for this
  comparison. Preserve the archived hold and the frozen V260/V247 results.
  V273 is running only the unchanged ThunderAgent reproduction on V230 with
  the fixed V269 empirical network-tool timing map and streaming metrics:
  `ops/v273_locobench_network_thunderagent_20261027.registration.json`,
  application `29396b2f53a0b897d4d704fd65fd17859c84e1a6`, native tree
  `11b84f7c31986782be3fbe699bae2574ef092389`, port 18651. Supervisor
  3988473/start `725219729`, server 3988975/start `725222405`, scheduler
  3989350/start `725223819` and driver 3990697/start `725237362` were
  independently checked live. Keep this baseline frozen; no overlapping GPU
  job or restart based on a stale empty argv/receipt snapshot.
- V273 retains all 3674 requests, 145565966 prompt tokens, 549671 output
  tokens, 18 distinct-project workflows and 72 JOINs. Only the declared 931
  execute timers change. Pin V269 SHA256
  `fa75164726e37277772d181f3601f6eaf234d3e55d8503710bdc0fc966cca5be`
  in subsequent controls. Natural native timing is not its performance control.
  Live ownership, isolated policy and 275 early exact receipts are recorded in
  `v274_network_thunderagent_live_gpu_20261027.json`; complete work, drained
  telemetry and performance remain pending. Prepared shared CPU qualification
  is explicitly the 2304-case two-execution assembly V273c, with the original
  two Docker-environment failures and exact successful retries preserved.
- The next predictive repairs concern observed JOIN dependency in agent
  admission and conservative capacity accounting. Both current methods use
  invocation IDs, not a single workflow program. Do not claim that blocked
  budget lending happens only at GPU idleness: the existing backed-demand
  path also checks real queued demand at one-second maintenance. Shared-prefix
  credit requires current native identity/extent evidence; textual overlap,
  workflow membership and similar prompts alone grant no credit. V275's
  historical budget audit reconciles only about 109 seconds of V260 and does
  not establish whole-run blocked occupancy or physical sharing. Keep its
  unreconciled interval explicit and retain the original negative results.
- The separate JOIN-dependent agent admission candidate is now implemented in
  `configs/migration/predictive_join_dependency_admission_20261027.json`,
  default off and isolated from all paper/control arms. At existing periodic,
  idle and backed-lending selections, preserve ready JOIN parent precedence,
  then prefer the sole remaining necessary ALL-JOIN member and other necessary
  members of an actually blocked waiter. Recheck live observed dependencies;
  do not assume a child is short, await unready siblings or evict ready peers.
  The optional `observed_blocked` backing proof decouples logical budget lending
  from long-tool prediction while retaining current identity, complete settled
  Host checkpoint, native DMA, in-flight and restore guards. It does not grant
  physical eviction authority or zero a blocked owner's budget without demand.
- The complete V230 prefix audit V276 verifies 92 parent/child pairs in compatible
  namespaces, each with only three common tokens. This does not establish native
  physical sharing and provides no reason for blanket parent/child discounts.
  Keep conservative extent accounting explicit; actual physical shared-page
  union credit remains unimplemented. New candidate snapshots include actual
  per-agent logical budgets, so the budget auditor can reconcile observed decode
  growth without altering older incomplete telemetry. Targeted CPU reports
  V277/V278 do not establish GPU effects or performance acceptance.
- The final V279 shared CPU regression passes 2342 cases and four subtests in
  one execution without failures/skips, retaining all 2304 V273c identities and
  adding 38. V280 binds the source map, declarations and real runtime constructor
  checks. Native CPU tree/allocator fixtures retain copy/event stand-ins. This
  candidate has no GPU/performance acceptance yet; future Full must match V273's
  exact V269 overlay, streaming metrics, engine, capacities and seed21.
- V273 stopped with HTTP `ReadTimeout` at 2026-10-27 14:52 Asia/Shanghai,
  preserving 2430 exact completed requests of 3674. Its last pending capacity
  sample records 1799.648 seconds; the driver's default 1800-second read
  timeout races the public router's 1800-second force-resume escape and the
  adapter's five-second control interval. The server was still serving other
  work before cancellation; do not call this a complete baseline or JCT result.
  Owned cleanup and actual supervisor/server/driver exit are verified in V283.
  Keep the original frozen failed run and registration unchanged.
- New causal comparisons explicitly declare a common 3600-second client request
  timeout and pass it to every arm. Old registrations retain their 1800-second
  default. This is a transport-only repair: preserve the author's force-resume
  policy, full queued time, exact tokens, source graph and tool map. Requalify
  and register a distinct complete ThunderAgent-first retry; do not resume the
  partial population or continue into Full after a failed baseline.
- V286 is the separately frozen complete retry at application
  `1e8c8ca75eef3ce1da3f9219bd479531c2d59ec3`, common native tree
  `11b84f7c31986782be3fbe699bae2574ef092389`, port 18652:
  `ops/v286_locobench_network_join_admission_20261027.registration.json`.
  It runs unchanged ThunderAgent first, then the explicit JOIN-dependent Full,
  using the same V269 map, streaming, complete V230 work/capacity/seed21 and
  3600-second common client timeout. Initial supervisor 4025266/start
  `725518522` and server 4025775/start `725521000` were verified during model
  startup. Keep this execution frozen and do not overlap another GPU job.
  Independent prepared shared CPU qualification V285 passes 2350 cases plus
  four subtests in one execution, no skips/failures, retaining all V279 cases.
  The descriptive old reuse_v273 flag is corrected on main; V286's actual
  two-arm order never reuses the failed partial baseline. Complete GPU,
  correctness and performance acceptance remain pending.
- V287 independently verifies V286 as `running_thunderagent`, with owned GPU
  scheduler 4026143, driver 4027494/start `725535972`, unchanged frozen
  sources/inputs, 118 registered requests and 99 sealed exact completed
  receipts. Actual driver arguments contain the 3600-second common timeout,
  V269 overlay and streaming metrics; Full agent admission is null. Keep the
  full population and both primary metrics pending; early receipts do not
  establish complete correctness or a performance improvement.
- The separate `thunderagent_acting_decay` arm now implements the pinned
  author's optional one-second-half-life ACTING-token decay for periodic resume
  only. Keep default ThunderAgent and all Full policy classes unchanged.
  First direct admission and post-resume pressure use original undiscounted
  capacity; buffers never decay, no physical capacity credit is granted and
  native allocation stays authoritative. Track only actual native-completion
  ACTING age with a monotonic clock and clear it at the next request.
  No JOIN, predictor, residency or controlled migration privilege is added.
- V289 executes the actual pinned author capacity and scheduled-check methods,
  with fixed CPU program/metric fixtures and zero shared-token credit. All
  2000 seed21 fixtures match decayed capacity, selection order, post-control
  program state and raw capacity. This is not whole-workload or GPU evidence.
  V290 passes 2376 shared CPU cases plus four subtests in one execution, no
  skips/failures, retaining V285's 2350 cases and adding 26. Default policy AST
  and Full's three controller files match frozen V286. Preserve that running
  cycle; any independent decay GPU control must wait for its actual exit and
  complete cleanup and use the same V269 map, work, stream metrics, capacity
  and seed. See `docs/research/thunderagent_acting_decay_20261027_zh.md`.
- V293 verifies V286 continuing beyond the old HTTP timeout: the unchanged
  public force-resume counter reached two, and two exact completed requests
  exceeded 1800 seconds from HTTP submit to response. It records 2900 sealed
  exact completions while the original watcher/server/driver remain live.
  This proves transport continuation, not complete-workflow performance;
  preserve the V273 failure and all ordinary waiting.
- V295 queues only the separately named `thunderagent_acting_decay` control
  behind V286's actual complete exit and owned cleanup:
  `ops/v295_locobench_network_thunder_decay_20261027.registration.json`,
  application `d6a4ce4c7bc11d6fc64b20edf45bb655907141b1`, port 18653,
  native tree `11b84f7c31986782be3fbe699bae2574ef092389`.
  Prepared V294 independently passes all 2376 CPU cases plus four subtests
  in one execution without failures/skips. V296 verifies supervisor
  4052961/start `725763196` live in `waiting_previous_causal_comparison`,
  no server/driver/owned children, predecessor still live, unchanged frozen
  inputs, and exact equality of work, capacity, V269 map, streaming and 3600s
  transport deadline. This CPU waiter is not a running optional-decay GPU
  job or a performance result. Keep current V286 frozen and do not overlap
  GPU experiments or advance other paper baselines before the shared gate.
- V286 default ThunderAgent completed with exit zero, all 18 workflows and
  3674 exact requests, 145565966 prompt tokens and 549671 output tokens,
  original stopping, complete normal tails, two fresh idle snapshots and
  actual owned cleanup. V297 independently validates the complete result:
  mean JCT 1876.334933s, workflow event makespan 3138.104909s and whole-run
  output throughput 175.160173 token/s. This is V269 synthetic timing and
  streaming; do not compare it as equivalent to historical natural timing.
- V298 verifies V286 Full actually running from the original frozen sources:
  server 4060371/start `725854916`, GPU scheduler 4060747, driver
  4062095/start `725869576`. New dependency admission and observed-blocked
  Host proof are enabled, ordinary frontier recovery disabled, physical control
  healthy and paper policy absent. Its first 1181 sealed receipts are exact;
  same-snapshot logical budgets reconcile. Nineteen executed logical handovers
  include seven policy-emitted sole-JOIN-child ranks. These are not independent
  dependency reconstruction, physical freed-page or final performance proof.
  Complete Full JCT/throughput and V295 optional-decay results remain pending.
- The explicit predicted-tool physical-offload candidate is now implemented in
  `configs/migration/predictive_tool_offload_20261027.json`. It extends the V286
  Full profile only through `PREDICTED_TOOL_OFFLOAD=1`; preserve V286/V295's
  frozen execution. Prediction-only remaining survival >=90% at one second
  authorizes incremental PREPARE and settled private native-leaf Device
  release before allocator shortage. Critical JOIN demand is not tool-length
  evidence. Recheck authorization after event drain and before enqueue; keep
  original session/epoch, native lock, ownership and DMA guards. Never evict a
  ready peer or a restoring/prefetched consumer, start a global write barrier,
  use private future tool timers, or equate physical release with logical
  eligibility. Unsupported/cold prediction abstains.
- V307 passes 2523 shared CPU cases plus four subtests in one execution, no
  failures/skips, retaining every V294 case and adding 147. V305's segmented
  prefix continuation failure remains preserved; a bounded remembered
  frontier fixes it. V309 binds current sources and actual Full/default
  ThunderAgent/optional-decay constructors, proving explicit enablement and
  paper isolation. Native tree/allocator/ACK tests still use copy/event/Host
  index stand-ins. New offload GPU, complete-model correctness, control-cost
  and full-workload performance acceptance remain pending.
- V300/V308 audit every workflow's initial preparation without rewriting
  historical clocks: V230 source mean 36.283566s, V286 ThunderAgent mean
  36.297232s and V286 Full mean 36.297035s. Preserve end-to-end
  `workflow_start -> workflow_end` as primary JCT; first-submit-to-end is
  diagnostic. V302's CPU fake-model profile constructs 25 subagent graphs
  and one root graph per root, but is not a historical profile of the entire
  30-second gap. Lazy graph construction is not implemented by this change.
- V286 subsequently completed Full: all 18 workflows, 3674 exact requests,
  145565966 prompt tokens and 549671 output tokens, original stopping and
  normal tails retained. Its collected mean JCT is 1669.294213s and
  whole-run output throughput 189.339669 token/s versus default ThunderAgent
  1876.334933s / 175.160173 token/s, improvements 11.0343% / 8.0952%.
  These are one complete frozen comparison, without the new offload module;
  keep repeated-performance and all-baseline acceptance pending. Owned
  predecessor processes exited and V295 is now running the independently
  frozen optional-decay arm; do not overlap another GPU experiment.
- The separately declared adaptive tool candidate is
  `configs/migration/predictive_adaptive_tool_offload_20261027.json`.
  Keep the historical fixed-threshold candidate and all V269/V286/V295 evidence.
  `BELIEFKV_TOOL_OFFLOAD_POLICY=demand_cost` requires a real, unallocated waiting
  consumer with native-proven Host prefix and a current physical capacity deficit,
  eligible already or fitting after the actual blocked donor lends its budget.
  Budget incremental donor D2H, beneficiary H2D, cumulative evicted-donor H2D,
  two 200ms control periods and one logical tick if needed. Use measured P90
  including queue costs; the declared cold fallback is 8GB/s and at least one
  second. Require supported remaining survival >=90%, revalidate before enqueue
  and each private native-leaf release, and stop when the consumer fits.
  Physical release never grants logical admission or evicts a ready peer.
- V286's actual tool estimator was online-only, not the old SWE-bench artifact.
  New domain-specific empirical survival priors fuse with current-run completed
  history at eight equivalent prior samples. Pin the artifact SHA; keep runtime
  domain and surviving-tail support. Reset online history per arm. The legacy
  tool artifact is exclusive with this prior; do not describe it as automatically
  retrained or claim its old accuracy on new tool scenarios.
- V311 is a new, explicitly synthetic observable-category timing contract.
  Preserve V269's mixed WebFetch/WebSearch map. Assign category independently
  before drawing a conditional duration, reveal only domain/category at TOOL_START,
  and retain future samples/timers privately. Its offline prior uses 222 calls
  from 54 public sessions; evaluation uses 91 calls from 17 different sessions.
  Every new compared method must use this same V311 map and complete V230 work,
  not a historical V269 control. Registration validates prior/domain/held-out
  group identity. File SHA256 is
  `0a7b7cc72f3d41de83117c0bd4857574a5a1c70c941e8a3ee7e2e15c401f624d`;
  the prior SHA256 is
  `949e2b45b7f6c31c39ac4ff497a2a8bd9bcc6c1fdd055a3a94cc847bbc0835da`.
- Background prepare still uses the native incremental dirty index and bounded
  cold-owner batches. Optional `BELIEFKV_IDLE_PREPARE_IO_BACKEND=direct` supports
  FULL-only, idle, storage-free `layer_first` controllers, preserving native
  streams/ACKs and restoring kernel mode for ordinary migration. Current frozen
  experiments use `page_first`, so leave this optional path off. Its ACK timing
  is excluded from kernel service estimates; any layout follow-up must change
  the common declared layout for every compared arm and qualify real GPU copies.
  CPU success does not establish reduced SM interference or control overhead.
- The pinned author uses 100 tokens of per-program growth buffer, already retained
  here. An optional Full-only soft `BELIEFKV_AGENT_KV_HIGH_WATERMARK` reserves
  additional logical growth capacity without shrinking the physical pool and
  permits a fitting sole large request. Main adaptive comparison keeps it at 1,
  and keeps background DMA native, to avoid mixing unmeasured follow-ups.
- V321 passes 2890 shared CPU cases plus four subtests in one execution with no
  failures/skips, retaining all 2523 V307 cases. V322 binds sources and actual
  Full/default-ThunderAgent/optional-decay constructors. Preserve V316/V319
  development failures: the former also reproduced on frozen d6a4ce4c7bc1 and
  required a correct JOIN-then-tool test sequence; the latter needed competing
  ready compute to exercise backed lending rather than the existing idle path.
  Native copy/event/Host-index stand-ins remain explicit. This change registers
  no new GPU comparison and proves neither whole-model correctness nor performance.
- The separately declared demand-lending candidate uses
  `configs/migration/predictive_demand_lending_20261027.json` and
  `AGENT_BLOCKED_BUDGET_PROOF=observed_demand`. Logical donor proof requires
  actual blocking, current context/epoch/native session identity, no compute or
  recovery protection, and an elapsed one-second tool wait unless current long
  prediction permits earlier. It does not require a complete Host checkpoint
  and does not grant physical capacity: native demand allocation, write-back,
  locks and H2D remain authoritative. Do not weaken the predictive physical
  `demand_cost` threshold or grant ordinary prefetch ready-peer donor rights.
  Preserve old proof modes, V323/V324 failed fixture evidence and all V321
  shared CPU cases. Future comparisons use the same V311 categorized map for
  Full and optional-decay ThunderAgent; V286/V295 are V269 historical controls.
- V325 passes all 2916 shared CPU cases and four subtests in one execution,
  without failures/skips, retaining V321's 2890 identities and adding 26.
  V326 binds sources and actual constructor configuration: Full uses
  observed-demand lending, unchanged demand-cost predictive physical offload
  and the pinned TraceLab prior; both ThunderAgent arms inherit none of these.
  CPU native allocator tests prove logical lending grants no free Device pages.
  Whole-model correctness and matched V311 JCT/throughput remain pending.
- Current Full/baseline declarations select native base fa5cb52afaea. Regenerate
  the canonical patch against that base from the already qualified
  11b84f7c3198 tree; the historical main patch was relative to 94602c9c and
  fails repeated application on fa5cb52afaea. This is a patch-base packaging
  correction, not new native behavior. Preserve old commits and frozen engine
  trees, bind fresh provenance to the new patch path, and require the resulting
  actual tree to remain exactly 11b84f7c31986782be3fbe699bae2574ef092389.
- V330 independently registers Full then optional-decay ThunderAgent using
  commit bdd74e9c280b and the exact V311 map, V230 work, capacity and seed21:
  `ops/v330_locobench_demand_lending_20261027.registration.json`, port 18654.
  Actual supervisor 4170080, server 4170626 and GPU scheduler 4171097 were
  verified during startup; do not overlap another GPU job or change frozen
  inputs. V327/V329 were pre-GPU registration rejections, not performance arms.
  V331 reproduces the TraceLab prior from the pinned raw public gzip and obtains
  the identical 949e2b45b7f6 SHA. Keep whole-model, complete JCT/throughput and
  repeated/all-baseline acceptance pending until actual evidence proves them.
- V330 subsequently reaches running_full with actual driver 4172595. Runtime
  observes demand lending, demand-cost offload, the pinned TraceLab prior and
  healthy physical control; an early snapshot records seven executed demand
  admissions, which are not freed-page evidence. V332 independently seals
  228 exact completed receipts matching the full original work and native
  stopping, with a fixed complete-prefix hash. Preserve current frozen run;
  partial receipts do not establish complete correctness or performance.
- The new read-only collecting observer
  `scripts/audit_collecting_agent_pipeline.py` is outside frozen V330. V333
  passes 59 targeted CPU audit cases. V334 seals 1280 exact completed receipts
  and reconciles 630 logical snapshots: sampled active-budget token-time is
  90.594% model-pending and 5.963% blocked, including 2.018% JOIN. Prefix mean
  decode batch is 12.229. These are incomplete, independently sealed prefixes,
  not full JCT, whole-run throughput, pure kernel time or matched baseline
  improvements. Do not modify V330 or run another GPU job before its exit.
- V335 seals 2877 exact completed V330 receipts and reconciles 1441 logical
  snapshots; mean prefix decode batch is 11.035. V336 observes 94.933GB of
  controlled JOIN FULL copies with actual reuse, including 30.148GB started
  before observed JOIN completion, and zero controlled tool-prefetch copies.
  Retained tool warming still requires existing active eligibility; blocked
  budget lending does not itself provide a tool restore reservation. V337
  passes sampled loan-relationship invariants but has ten long sampling gaps.
  These prefixes do not establish complete work, exclusive causes or a matched
  performance win. Keep V330 frozen and review full results before expanding
  inactive-owner warming or any speculative donor permissions.
- Tool prefetch is optional by the user's explicit clarification. Zero copies
  and inactive-owner rejection counts alone are not bugs or benefit evidence:
  the eligibility gate precedes physical restore planning. Repair only a
  separately evidenced useful opportunity with missing KV, safe free/blocked
  backed capacity and timely normal admission; preserve ready peers and native
  authority. Account for occupancy, displaced work, repeated copies and
  recomputation in full JCT/throughput. Disable the feature if matched evidence
  shows net harm; never force transfers to increase coverage.
- V330 Full has completed all 3674 exact requests, 18 workflows and original
  stopping, with two fresh idle snapshots and actual owned cleanup. V341
  independently verifies mean JCT 1646.612839s and full-run throughput
  237.838282 token/s. V338 reconstructs all complete JCT paths; the slowest
  has 530.409s submit-to-first-launch waiting as well as tool intervals, so
  do not attribute the tail solely to fixed external waits. V339 reconciles
  3223.391207GB native demand H2D; V340 full mean decode batch is 6.250711,
  not its earlier 11.035 prefix. Matching V311 optional-decay ThunderAgent
  started (server 4193558, scheduler 4193919, driver 2218), then stopped for
  the new user reservation. V342 verifies cleanup before 20:00 and preserves
  847 exact partial receipts. Keep the raw administrative-stop evidence;
  its partial result cannot qualify a performance comparison.
- The user prohibits GPU experiments from 2026-10-27 20:00 through
  2026-10-28 08:00 Asia/Shanghai, consistent with the existing reservation
  timezone. The shared hold now rejects launches before the latter deadline.
  V330 owned cleanup completed at 19:53:15, including release of the GPU
  context. Do not restart its interrupted partial arm; continue CPU-only
  work with CUDA devices hidden during the reservation.
- All future ThunderAgent experiments use only `thunderagent_acting_decay`.
  Preserve historical default results. Reuse an existing matched control when
  only Full changes; V295's V269 timers differ from V330's V311 timers and
  therefore do not supply a same-timing performance control.
- Call fixed-trace total output divided by makespan end-to-end output
  throughput, never decode kernel throughput. It is not independent evidence
  from makespan. Keep complete mean JCT and all normal tails; separately
  report complete decode worker output rate, batch/context/padding geometry,
  and demand-outstanding service throughput as diagnostics. Worker intervals
  include runtime and waits, not pure kernel duration. See
  `docs/research/throughput_metric_contract_20261027_zh.md`.
- V344 adds CPU-only complete decode plots via
  `scripts/plot_causal_decode_diagnostics.py` (32 targeted tests passed).
  Historical V286 Full and V295 tool-decay ThunderAgent share V269 and may
  be overlaid; V330 uses V311 and has only a separate completed-run plot.
  Keep fixed 60s bins, all warmup/idle/normal tails, actual partial-bin widths,
  time-weighted CDFs, and no post-termination zero padding. Distinguish wall
  decode output rate from worker output rate; neither is pure kernel time.
  Actual KV capacity, free-page fence and exact work/timing guards passed.
  Final artifacts and captions are in
  `docs/research/decode_throughput_figures_20261027_zh.md`; plots do not satisfy
  repeated or matching-V311 performance acceptance.
- The user explicitly releases the latest GPU reservation at approximately
  2026-10-27 20:28 Asia/Shanghai and requests completion of the latest-tool
  ThunderAgent control. Archive the old hold with its digest and register a
  new complete `thunderagent_acting_decay` arm using V330's exact V311 timing,
  V230 requests/tokens/graph, native tree, capacity, streaming and seed21.
  Preserve V330's administrative stop and 847 partial receipts; do not resume
  or combine that partial run. The V345 registration itself contains only the
  requested complete tool-decay control.
  Current availability, ownership, port and newer hold checks still apply.
- V345 independently registers only `thunderagent_acting_decay` using commit
  `0b1ca5de0933`, port 18655 and the unchanged V330 native tree and V311 map:
  `ops/v345_locobench_categorized_thunder_decay_20261027.registration.json`.
  V348 verifies 17 matching common fields, unchanged qualified runtime sources,
  actual tool-decay configuration and live supervisor 27137/server 27645/
  GPU scheduler 28011/driver 29479 identities. V346 seals 141 exact completed
  requests against the complete V330 Full receipt population. Preserve the
  running frozen control; partial work supplies no complete performance claim.
  The one-shot CPU-only V347 postprocessor waits for complete success and
  owned process exit before drawing matched V330/V345 plots. It launches no
  GPU job and stops without plots if the control is interrupted. See
  `docs/research/v345_v311_thunder_decay_control_20261027_zh.md`.
- V349 audits complete V330 budgets: all 2209 snapshots reconcile, covering
  2277.917s of a 2279.442s native-request span. Model-pending budget token-time
  is 72.282%, known blocking 23.586% and between-request 4.132%; the earlier
  V335 prefix is not the final mix. Blocking with actual inactive submitted
  demand is only 2.974% of total active-budget token-time, not removable JCT or
  physical HBM. V350b reconciles actual guarded releases: allocation-pressure
  tools 13.240GB/355 events, JOIN 114.285GB/559 events and additional predicted
  tool offload 0.119GB/10 events. Do not conflate these paths or relax prediction
  just to increase copied bytes. Preserve the first offline geometry assertion:
  Device allocation includes one padding slot; exact FULL bytes/token come
  from divisible Host geometry. V345 is still frozen and live. See
  `docs/research/v330_complete_capacity_and_offload_audit_20261027_zh.md`.
- V351 independently seals 2088 exact complete V345 receipts. At 20:56
  Asia/Shanghai the original watcher/server/driver and CPU plotting waiter
  are actually alive by PID/start-tick checks. Keep its prefix batch and
  worker rate separate from complete V330 averages; final matching JCT,
  makespan and decode geometry remain pending.
- V345 subsequently completes all 3674 exact requests and 18 workflows, with
  exit zero, native stopping, two fresh idle snapshots and actual owned cleanup.
  V361 independently validates the matching V311 pair: Full mean JCT
  1646.612839s versus 1697.583645s (3.00255% lower), makespan 2311.112390s
  versus 2355.344551s, end-to-end output 237.838282 versus 233.371801 token/s
  (1.91389% higher), decode worker rate 354.557302 versus 340.158312 token/s.
  Sixteen workflows improve and two regress. V347 has final matched plots.
  This single small improvement does not meet clear/repeated/all-baseline
  acceptance. Preserve all tails and the V360 tool-demand H2D regression:
  Full 2686.823GB versus control 2137.059GB.
- The continuing user optimization goal now proceeds to the separately
  qualified Full `projected_release` pressure candidate, reusing complete
  V345 without another ThunderAgent arm. Native allocation receives no future
  capacity credit: existing marks only bound additional running-request marks,
  and actual completion/requeue remains the release boundary. Keep default
  and paper policies unchanged, protect JOIN restores, and never count an
  unstarted queued request as an impending release. The new config is
  `configs/migration/predictive_projected_pressure_20261027.json`.
  V358c passes all 2942 shared CPU cases plus four subtests, retaining every
  V325 case. V359c binds actual constructors and baseline isolation. Preserve
  V358's snapshot-schema failure and V358b's earlier 2940-case pass. V362
  proves a CPU overmarking counterexample; V330's sampled overcommit conditions
  do not prove actual historical mark transitions or a performance cause.
  Freeze sources and recheck current GPU resources/hold before Full execution;
  no newer reservation is superseded by this candidate. See
  `docs/design/projected_pressure_release_20261027_zh.md`.
- V364 independently runs only Full at commit `012765524309`, port 18656,
  with the unchanged native tree and V311 timers:
  `ops/v364_locobench_projected_pressure_20261027.registration.json`.
  V367 verifies live watcher 72569/server 73085/GPU scheduler 73491/driver
  74538 and the sole common environment addition `AGENT_PRESSURE_PAUSE_POLICY`.
  V365 seals 129 exact completed requests and 105 reconciled budget snapshots.
  Keep this execution frozen and do not overlap GPU work. Complete V345 is
  reused; the CPU-only V366 waiter draws matched plots after Full succeeds
  and exits, without launching GPUs. Complete correctness, control cost,
  JCT/throughput and repeated/all-baseline acceptance remain pending. See
  `docs/research/v364_projected_pressure_start_20261027_zh.md`.
- V364 subsequently completes all 3674 exact requests, 145565966 input tokens,
  549671 output tokens and all 18 workflows, with original native stopping,
  two fresh idle snapshots and actual owned exit. V373 independently verifies
  the unchanged V311 work/timing/capacity/native tree against complete V345:
  mean JCT 1641.531479s versus 1697.583645s (3.30188% lower), makespan
  2272.948399s versus 2355.344551s, end-to-end output 241.831711 versus
  233.371801 token/s (3.62508% higher), and decode worker rate 357.857794
  versus 340.158312 token/s. Seventeen workflows improve and one regresses;
  retain it and every normal tail. V366 figures are complete and inspected.
  V330 and V364 are distinct configurations sharing V345, not independent
  repeated trials. This modest gain does not meet clear/repeated/all-baseline
  acceptance. The requested latest-tool ThunderAgent control is complete;
  reuse V345 when only Full changes rather than launching another control.
- V372 reconciles complete native demand H2D: V330 3223.391GB, V364
  3330.043GB and V345 2809.042GB. V364 tool-successor demand H2D is
  2747.670GB versus 2137.059GB, a 28.5725% increase. Reduced JOIN demand
  bytes are not equal to total migration savings because Full also has tagged
  recovery. V364 records 505 logical pauses: 416 blocked-budget handovers,
  70 JOIN loan returns, 16 pressure ACTING pauses and 3 planned pressure
  pauses. These counters do not prove one-to-one physical migration causes.
  Prioritize lending/recall continuity evidence; do not spend another GPU
  run solely to demonstrate fewer rare pressure marks.
- Main now separately revalidates only its owned projected-pressure marks
  after observed budget changes, actual pauses and existing lifecycle events.
  Cancel obsolete/excess marks and newly protected JOIN owners without
  clearing external marks, adding timers or granting planned capacity.
  V370 passes 2951 shared CPU cases plus four subtests in one execution,
  without failures/skips, retaining all 2942 V358c identities and adding nine.
  V371 binds actual constructors and source hashes; V369's 18 environment
  skips remain preserved and are not the shared qualification. This later
  repair did not run in frozen V364 and has no new GPU performance evidence.
  See `docs/research/v364_v345_matched_results_20261027_zh.md` and the updated
  pressure design. No new GPU experiment is registered by this final audit.
- V374 independently joins complete V364 lending/recall events to subsequent
  native requests and V372 Host hits. Its 317 lending decisions pause 416
  owners, releasing 30395360 logical token budget against 16286396 required;
  per-decision release/need P50 is 1.606 and P90 10.231. The next native
  request arrives within five seconds for 175 selected owners and one second
  for 27. These are offline associations, not causal physical eviction, unique
  H2D savings or online future information. Preserve the original complete work.
- The separate fitting-lending candidate is
  `configs/migration/predictive_fitting_lending_20261027.json`, default
  `largest` unchanged. Explicit retained Full `AGENT_LENDING_SELECTION=fit`
  requires observed-demand proof. Keep the same eight-owner donor window and
  eight waiting targets; probe each current owner at most once per maintenance
  pass, select the minimum proven token budget covering the actual gap from
  at most 256 subsets, then prefer fewer owners and original stable order.
  Insufficient total proof pauses no partial set. Preserve all blocking,
  in-flight, JOIN/restore guards, native allocation/DMA authority and existing
  lending/recall scopes. There is no new timer, Host path scan, future-duration
  feature or ready-peer prefetch privilege.
- V375 passes 160 targeted cases. V376b passes all 3003 shared CPU cases and
  four subtests in one execution, retaining every V370 case and adding 52
  case identities. V377b binds current sources and actual constructors:
  Full uses fitting, both ThunderAgent arms remain largest/default-isolated.
  Preserve V376's passing shared report and initial source map: its subsequent
  preflight failed because the new qualification helper omitted production
  environment conversion; only that helper changed for the successful retry.
  Native tests retain copy/event/Host-index stand-ins. Local eight-owner
  selector thread CPU median/P99 is 41.28/46.09us over 1000 calls; this is not
  full controller or GPU performance evidence. Freeze the qualified candidate,
  recheck resources/hold, run only Full and reuse complete V345. See
  `docs/design/fitting_blocked_agent_lending_20261027_zh.md`.
- V378 independently registers only fitting Full at commit `83d7c3c2eb34`,
  port 18657, with the unchanged native tree, V230 complete work and V311
  timing: `ops/v378_locobench_fitting_lending_20261027.registration.json`.
  V379 verifies actual watcher 114948/server 115402/GPU scheduler 115750/
  driver 116684 identities, ownership and nice0. The actual runtime selects
  fitting plus observed-demand proof and pressure-mark revalidation.
  Sixteen executed fitting decisions independently attain the minimum proven
  budget/owner-count objective; V381 seals 405 exact complete requests against
  V330's full original work and stopping. These are prefixes, not full
  correctness or performance acceptance. Keep this run frozen, do not overlap
  GPU work or restart from a stale status. V378 includes the later V370 fix
  absent in V364, so it is not a one-module ablation or repeated V364 trial.
  Reuse complete V345. The verified nice19 CPU waiter 117226 generates V380
  matched plots only after complete success and actual owned exit; no GPU
  follow-up is queued. See `docs/research/v378_fitting_lending_start_20261027_zh.md`.
- V384 seals 2595 exact completed V378 requests and 1343 reconciled budget
  snapshots covering 1402.118 seconds, with 1.331 seconds uncovered.
  Prefix model-pending/known-blocked/between-request active-budget token-time
  is 88.606/6.866/4.528%; these are not physical occupancy or complete-run
  statistics. Do not compare its 373.244 worker token/s or 11.069 weighted
  batch against full V345 to claim a speedup.
- V385 independently verifies 347 actual fitting decisions. On each recorded
  proven donor set, selected budget totals 27785573 tokens versus 34879826
  for the original-order adequate prefix, but selected owners total 447 versus
  428: 16 decisions use more owners, three fewer and 328 the same. This local
  counterfactual is not an old-policy run or proof of lower physical migration.
  Preserve the tradeoff and await complete JCT/throughput/H2D before retention.
- V383 audits all V230 source invocations: roots 18/844 calls/188999 output
  tokens; direct children 92/2830 calls/360672 output tokens; no grandchildren.
  Child calls min10/P50 29.5/P90 44.9/max66 despite warning4/target8. All
  184 notices link through actual tool-call IDs to the 92 children, each with
  thresholds4 and8; return-count multisets reconcile with native child calls.
  Missing parameter propagation is ruled out, not a reason to repeat the same
  prompt/budget change. Nesting was already available; source shape or task
  scopes need evidence before a separately declared fresh capture.
- Every V230 child RETURN declares context_retired=true. V382 finds actual
  positive terminal release for all92 V364 children: cumulative183.522GB
  Device FULL/66.478GB Host FULL, first-release delay P50 3.174ms. Four last
  visible pending-anchor counts do not establish a final live backlog because
  no-release retries can be silent. These totals are not simultaneous free
  capacity, avoided H2D or proof of globally reclaimed unique state. They do
  not support blaming persistent metadata for retaining every finished child.
  See `docs/research/v378_collecting_and_source_geometry_20261027_zh.md`.
- V378 has completed all 3674 exact requests, 145565966 input tokens,
  549671 output tokens and 18 workflows with original stopping, all normal
  tails, two fresh idle snapshots and actual owned cleanup. V388 verifies
  mean JCT 1644.823912s and end-to-end output 238.234276 token/s, versus
  V364 1641.531479s / 241.831711 token/s and V345 optional-decay ThunderAgent
  1697.583645s / 233.371801 token/s. V378 is 0.20057% worse in mean JCT
  and 1.48758% worse in throughput than V364; this is not an isolated fitting
  ablation because V378 also includes pressure-mark revalidation. V386 finds
  total H2D 3270.265GB versus V364 3434.262GB, without improved performance.
  Keep fitting opt-in and unpromoted, preserve all reports and complete V380
  figures, reuse V345, and leave the clear/repeated/all-baseline goal incomplete.
  No next GPU experiment is queued.
- The user's latest critique is accepted: blocked-budget lending, recall and
  fitting are engineering variants of working-set switching, not independent
  research contributions. The executed path pauses donors and activates the
  selected target using the existing working-set core; relationship bookkeeping
  does not create a new migration primitive. Do not add or rename more loan,
  recall or lease variants as an innovation. Further design must first establish
  an exposed KV-transfer/compute bottleneck and demonstrate a distinct mechanism
  against a simpler strong control with complete matched JCT/throughput evidence.
  JOIN/EOS prefetch and prediction remain mechanisms to validate, not automatic
  novelty or performance claims. See
  `docs/research/v378_working_set_switching_results_20261027_zh.md`.
- V391 audits the existing complete V345/V364/V378 CUDA restore-wait samples,
  without expansion. V345 has 18 sampled load-consuming batches; first
  observed FULL waits total31.907ms and later FULL waits2630.928ms.
  Sixteen samples are tool successors and only one is JOIN, so this is evidence
  of late-layer stalls, not a whole-run estimate or matched cohort speedup.
  V392 reconstructs every workflow JCT in all three complete arms. V378
  reduces mean critical-path submit-to-first-launch from393.951s to281.658s,
  but first-launch-to-native-complete increases from877.326s to936.984s.
  The latter includes interleaving and runtime waits, not pure GPU execution
  or isolated transfer attribution.
- The separate simplification candidate is
  `configs/migration/predictive_reference_thunder_20261027.json`.
  Explicit Full `thunderagent_reference` uses the exact
  `ThunderActingDecayWorkingSet` and inherited reference adapter tick,
  completion, observed-growth, abort and native capacity attachment methods.
  It adds no logical loan, recall, recovery eligibility or reservation.
  JOIN/tool warming requires existing active eligibility; tools additionally
  require a real current tool wait. Keep ordinary frontier recovery off and
  reject the unqualified sessionless adapter. Preserve the retained candidate
  and all paper arms unchanged; do not call this composition a new algorithm.
  V394 passes3030 shared CPU cases plus4 subtests without failures/skips,
  retaining all3003 V376b identities. V395 binds all sources and three real
  constructors. Four240-step observed event fixtures match reference policy
  state after every step; this is not historical trace, GPU or performance proof.
  Preserve V393/V393b test-fixture failures and passing V393c. Native copy/event
  stand-ins remain explicit. Require frozen sources and current hold/resource
  checks before a Full-only GPU run, reusing complete V345 on the same full
  V230 work, V311 timing, native tree, capacity and seed21.
  See `docs/design/reference_thunder_migration_composition_20261027_zh.md`.
- V396's first registration attempt stopped before writing a registration or
  starting a GPU watcher: `after_registration` compares the entire environment,
  and V378 has a different Full admission configuration. Preserve both unused
  frozen checkouts and
  `v396_reference_thunder_pre_gpu_dependency_rejection_20261027.json`.
  The original V378 success and actual owned exit were independently rechecked.
  The distinct V396b retry omits that inapplicable same-configuration dependency,
  retaining current hold, resource, port and GPU-lock checks. Native binding V396
  changes only the canonical patch path; every other native-state field and
  actual11b84f7c3198 tree remain equal to the earlier proof. CPU-qualified runtime
  sources are unchanged by this startup record.
- V396b started the reference-core Full but stopped with scheduler
  `AttributeError: ThunderActingDecayWorkingSet has no attribute agents` on
  its first demand-aware predicted-offload path. The offloader had retained
  policy `.agents/.in_flight` assumptions not covered by the original separate
  admission and offload tests. V397 seals99 exact completed receipts out of3674,
  preserves the failure and independently verifies terminal owned cleanup.
  This partial run is not a performance result and must not be resumed.
- Main now gives reference admission an explicit `tool_offload_protected`
  predicate for current identity and submitted model work; physical transfer,
  prefetch-ticket, lease and native lock protection remains in the offloader.
  Existing eligibility is warming permission, not a physical restoration lock.
  Demand offload with this reference core requires an already eligible
  consumer and never invokes retained-budget lending. Old retained behavior
  stays unchanged. V398b passes109 targeted cases, including five combinations
  of the actual reference adapter and real native FULL-only allocator with
  copy/event stand-ins; V398's fixture-profile failure is retained. V399 passes
  all3035 shared CPU cases plus4 subtests in one execution without failures/skips,
  retaining all3030 V394 identities and adding5, and binds all current sources
  plus the three real constructors. GPU correctness/performance remain pending;
  use a separately frozen complete retry and reuse V345.
- V400's repaired-code registration froze7fe941c0dfdf but stopped before server
  or driver startup because the preserved V396b crash socket still exists at
  `/tmp/bkv-causal-18658.sock`. No model request or GPU serving started.
  V400 records terminal watcher exit and empty owned cleanup; preserve its
  registration, unused snapshots, socket and
  `v400_reference_thunder_socket_pre_gpu_rejection_20261027.json`.
  The distinct V400b retry uses verified-free port18659 and a fresh socket.
  Runtime and V399-qualified sources are unchanged.
- V400b independently runs only Full from a5e31ae7464f, containing the
  7fe941c0dfdf offload integration repair:
  `ops/v400b_locobench_reference_thunder_offload_20261027.registration.json`.
  V401 verifies actual watcher163362/server163860/driver165104, nice0,
  reference admission without logical loans/recalls, healthy physical control,
  26 equal work/timing/capacity/serving fields versus complete V345, and198
  sealed exact completed receipts out of3674. These are prefixes, not complete
  correctness or performance. Keep the frozen run unchanged and do not restart
  on a stale/empty argv observation or overlap another GPU job.
  The nice19 CUDA-hidden V402 CPU waiter167057 is confirmed live; it uses
  `scripts/deferred_causal_decode_plots.py` and only draws matched60s plots after
  full success plus actual owned exit. It launches no GPU work, stops on failure,
  and preserves all normal tails. No GPU follow-up is queued. See
  `docs/research/v400b_reference_thunder_start_20261027_zh.md`.
- V400b now completes all3674 exact requests,145565966 input and549671 output
  tokens,18 workflows, original stopping, two fresh idle snapshots and actual
  owned cleanup. Its reference-core Full mean JCT is1648.836305s versus
  V3451697.583645s (2.87157% lower), but output throughput233.458197 versus
  233.371801 token/s improves only0.03702%. Total H2D2679.208GB versus2809.042GB
  does not establish a clear throughput gain. Retain17 improved workflows,
  one25.491s regression and worse mean maximum post-first ready-output gaps.
  V402 plots are complete and inspected. Complete evidence/operator are under
  `experiments/reports/v400b_followup/`; reuse V345 and keep the global goal
  incomplete. No next GPU run is queued.
- Follow-up main repairs reference tool-offload consumer selection: an inactive
  or already fitting Host-recovery waiter must not hide a later eligible,
  capacity-stalled consumer within the existing eight-candidate/200ms bound.
  Two real-native CPU counterexamples reproduce the old failure and pass after
  repair, preserving Host backing and reference state. One shared execution
  passes3038 cases plus4 subtests, retaining all3035 prior identities. The
  offline restore reader keeps absent reference eligibility/budget fields
  unknown. Neither repair ran in frozen V400b; historical frequency, control
  cost and incremental GPU performance remain unverified. Do not equate zero
  extra predicted offload with absence of native pressure-triggered tool release.
- The descriptive `reference_consumer_fix_20261027_r1` Full run now uses frozen
  eecf7293e711 with the repaired offload consumer and once-per-second actual
  reference eligibility/pressure-mark identities. One shared CPU execution
  passes3039 cases plus4 subtests, retaining all3038 prior identities; no
  scheduling method or reference paper core changed. Its registry is
  `ops/reference_consumer_fix_20261027_r1.registration.json`, port18660.
  Watcher198769/start729631601, server199253/start729634027, GPU scheduler
  199600/start729635483 and driver200694/start729649161 are verified live/nice0.
  All work, V311 timers, native tree, capacity and seeds match completeV345.
  The startup receipt seals179 exact completions, not full correctness or
  performance. Keep this run frozen, do not overlap or restart on stale state.
  Nice19 CPU waiter200332/start729643148 draws matched figures only after
  complete success and actual owned exit; no follow-up GPU job is queued.
  A three-program CPU reference fixture proves possible control-phase-induced
  root pausing during a3s tool, without copies; it does not establish the cause
  of V400b's194s wait. Use actual new identities to diagnose it. See
  `docs/research/reference_consumer_fix_20261027_r1_zh.md`.
- R1 now completes all3674 exact requests,145565966 input and549671 output
  tokens,18 workflows, original stopping, two fresh idle snapshots and actual
  owned cleanup. Its mean JCT1641.251236s is3.3184% lower than completeV345;
  whole-run output236.995937token/s improves1.5529%. Retain17 faster workflows,
  desktop_media17.922s slower and worse mean maximum post-first ready gaps
  82.605s versus73.449s. H2D2587.286GB versus2809.042GB is7.8944% lower;
  no extra predicted-tool physical release or tool-prefetch copy occurred.
  Seven JOINs start before completion and prove reuse of36.369GB. Do not
  attribute the small output gain to a tool-offload repair that performed no
  physical release. Complete533 tool successors with at least1s queue wait
  spend94.7951% of their sampled interval inactive; this is a sample-hold
  observation, not exact transition timing or removable JCT.
  The CPU observer now preserves reference aggregate-only budgets as unknown;
  110 targeted cases pass. Its initial unsupported-budget failure is retained.
  Complete reports, operators and inspected matched plots belong to the R1
  named directory, without new main experiment version numbers. ReuseV345,
  keep the global goal incomplete and do not launch other baselines/ablations.
  A next isolated integration repair may admit a genuinely WAIT_JOIN parent
  for warming only against existing free complete logical budget, with no
  peer pause, logical lending/recall or new novelty claim. Keep R1 frozen.
- The default-off `BELIEFKV_AGENT_JOIN_FREE_ADMISSION` candidate now allows
  only a current exact-checkpoint, non-pending, unmarked, genuine WAIT_JOIN
  parent to claim existing full undiscounted logical capacity. No peer is
  paused and no lending/recall/TTL is added; ordinary/tool warming cannot
  claim eligibility. Native physical allocation, locks and ACKs remain separate.
  The declaration is `configs/migration/predictive_reference_join_free_20261027.json`.
  Targeted55 cases include actual native tree/allocator acceptance/refusal
  with copy/event stand-ins. One shared execution passes3143 cases plus4
  subtests, retaining all3039 earlier identities and adding104. Actual
  Full/default-TA/decay-TA constructor isolation and inherited reference
  methods are source-bound. The first two fixture failures remain under
  `experiments/reports/reference_join_free_admission_20261027/`.
  A synthetic33-program claim-only CPU check measures new-grant median
  8.393us/P9910.391us, not whole-controller cost or GPU performance.
  Qualify and freeze this separate Full, reuse completeV345 on unchanged
  complete work/V311 timing/native tree/capacity/seed; do not modify R1.
  See `docs/design/reference_join_free_admission_20261027_zh.md`.
- The isolated free-JOIN Full now runs from frozen3a23fe1028ce on port18661:
  `ops/reference_join_free_admission_20261027_r1.registration.json`.
  Actual nice0 watcher230037/start730081130, server230477/start730083676,
  GPU scheduler230812/start730085104 and driver232018/start730098727 are
  verified; the GPU PID matches NVML. Independent startup evidence seals179
  exact completed receipts, not full correctness/performance. Thirteen common
  work/timing/capacity/engine/serving fields equal completeV345; actual
  `full_with_reference_thunderagent_free_join_admission` is effective and
  physical control is healthy. Preserve the running frozen tree.
  Nice19 CUDA-hidden plotting waiter231428/start730089097 only draws after
  complete success plus actual owned exit and launches no GPU work. Its state
  is `ops/reference_join_free_admission_20261027_r1.plots.state.json`.
  No follow-up GPU job is queued. Reports/operators belong to
  `experiments/reports/reference_join_free_admission_20261027/`.
- Free-JOIN R1 now completes all3674 exact requests,145565966 input and549671
  output tokens, all18 normal workflow tails, original stopping, two fresh idle
  snapshots and independently verified actual owned exit. Its mean JCT is
  1653.668345s versus completeV3451697.583645s (2.58693% lower), whole-run
  output236.783654 versus233.371801token/s (1.46198% higher), and decode
  worker347.328338 versus340.158312token/s. Seventeen workflows improve;
  python_web_cms regresses66.417252s and mean maximum post-first ready gaps
  worsen91.578837 versus73.449107s. Eleven JOINs early-start and prove reuse
  of53.658845GB; all controlled JOIN copies92.888236GB prove reuse. Seven
  free-budget grants have no individual identity journal and cannot be directly
  attributed to those copies. Sampled layer waits3959.456ms/21batches versus
  2662.835ms/18batches are not whole-run or matched-cohort time. No superiority
  gate is satisfied; preserve all reports/inspected figures and reuseV345.
- The separate reference-core sibling candidate preserves the exact reference
  lifecycle/control and allows only visible submitted inactive ready members
  of an actual blocking ALL-JOIN with2--24 total members to use complete unused
  undiscounted budget as a group. It never waits for unsubmitted siblings or
  pauses a ready peer; native physical authority stays separate. The flag is
  BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN, default off. Two red CPU cases prove
  stale streaming size can overgrant parent or sibling capacity; extra grants
  now refresh actual visible native output lengths first. Frozen3a23fe10
  did not include this fix. New success-only identity journals permit future
  grant-to-transfer audits, not physical ownership claims. Targeted86 cases
  and one shared3174-case execution plus4subtests pass without failures/skips,
  retaining all3143 prior identities. Synthetic33-program callbacks with32
  pending records and real async journal measure parent/sibling new-grant
  medians78.703/68.328us, not whole-controller/GPU cost. No new GPU job is
  registered; preserve failures and see
  docs/design/reference_join_siblings_20261027_zh.md.
- The sibling Full is now running from frozen525c7cc66e1f on port18662:
  ops/reference_join_siblings_20261027_r1.registration.json. Actual watcher
  255889/start730446159, server256436/start730448880, GPU scheduler256898/
  start730450297 and driver257923/start730463855 are independently live/nice0;
  the GPU PID matches NVML. The real policy is
  full_with_reference_thunderagent_join_and_sibling_admission and grant-time
  streaming refresh is effective. Thirteen common work/timing/capacity/engine/
  serving fields match completeV345. The earliest live_start prefix contains
  zero completed receipts and proves neither request completion nor performance.
  Preserve frozen sources/config/external inputs. The nice19 CUDA-hidden CPU
  plot waiter256660/start730449328 waits for complete success and actual owned
  exit; its state is ops/reference_join_siblings_20261027_r1.plots.state.json.
  It launches no GPU work. No further GPU experiment is queued.
- The separate sibling collecting_pipeline_after_start.json now seals138 exact
  completed requests against completeV345, preserving hashes/tokens/native
  stopping. This is a prefix, not complete correctness/performance. Reference
  aggregate-only budgets remain unknown for per-agent token-time; do not infer
  physical occupancy or compare prefix decode rate to fullV345. Keep525c7cc6
  and its registered external inputs frozen while continuing the complete run.
- The independent CPU-only JOIN grant auditor now binds actual grant identities
  to completed native session/clock and exact-context service. It bounds older
  grants by later same-owner grants, keeps unproven rows and attempt identity
  unknown, and credits only separately witnessed version2 FULL reuse. Final104
  targeted cases pass; initial4 and later1 auditor boundary failures remain
  preserved. No frozen runtime/engine/source/config changed or new GPU job
  registered. The sibling collecting pipeline seals1722 exact completions.
  Its sealed grant prefix has18 grants/21 served requests:2 parent and16 sibling
  grants covering19 child requests, with22.242--166.213ms child grant-to-service;
  three two-member grants have0/0/137.904ms service-entry spread. Parent grants
  associate12.524519GB verified reuse including post-completion copies; only
  6.569165GB in the separate prefetch prefix is confirmed early reuse. Preserve
  an expired0.403GB recovery and127.6s ACK-to-service observation elsewhere.
  These are collecting associations, not complete JCT, pure GPU timing, an
  atomic snapshot or causal speedup. See
  docs/research/reference_join_sibling_grant_audit_20261027_zh.md.
- Sibling R1 completes all3674 exact requests,145565966 input/549671 output
  tokens and18 workflows, original stopping/tails, two fresh idle snapshots
  and actual owned exit. Mean JCT1643.381962s versusV3451697.583645s improves
  3.19287%; full-run output239.104804 versus233.371801token/s improves2.45660%.
  Sixteen workflows improve; desktop_media/game_simulation regress5.695610/
  19.749224s. Mean maximum post-first ready gap89.947849 versus73.449107s
  remains worse. Actual H2D2606.499GB/D2H1213.625GB;120 requests in112 grants
  all bind to completed native service. Nine JOINs early-start and prove reuse
  of38.132023GB, distinct from20.918993GB grant-associated reuse including
  post-completion copies. No tool prefetch copied; independent predicted offload
  released159tokens/15630336bytes. Keep the global dual-metric/repetition gate
  incomplete and reuseV345; no other baseline/ablation is enabled.
- The sibling plotting waiter failed only because the serving Python lacked
  matplotlib; preserve its stopped state/log and empty original figure directory.
  Correct CPU retry figures are in
  experiments/figures/reference_join_siblings_20261027_r1_cpu_retry/, with all
  three PNG/PDF inspected. Always use
  /home/longhao/experiment/ops/cpu_plotting_20261027/bin/python for future plot
  waiters, never sys.executable from beliefkv-next. Serving environments were
  not changed. Complete review is under the sibling named report directory.
- A real dashboard tool response has no151657 opener but does have151658
  </tool_call>; old EOS detection incorrectly predicted parent reentry124.8s
  before actual JOIN completion. Native post-completion queue wait was only
  3.02s, so its127.6s ACK-to-service interval is mostly an early-trigger error,
  not ready-queue fairness loss. Offline60-trigger classification finds4 such
  tool/intermediate responses and56 actual final requests. Main now includes
  the closing delimiter in the existing token-evidence set, with no extra
  decoding/predictor/queue scan or future information. A real58-token CPU case
  fails on old code and passes after repair. New source-bound shared execution
  passes3247 cases plus4subtests without failures/skips, retaining3174 and
  adding73, including real local tokenizer EOS and grant-audit tests. Preserve
  the initial red report and partial native-fixture skips. Frozen525c7cc6 did
  not run this fix; see docs/design/tool_closer_eos_guard_20261027_zh.md.
- The guarded Full is now running from frozenac94b2bcd118, unchanged native
  tree11b84f7c3198, port18663:
  ops/reference_tool_eos_guard_20261027_r1.registration.json. Actual nice0
  watcher280028/start730778349, server280459/start730781053, GPU scheduler
  280797/start730782464 and driver281924/start730796166 are independently live;
  the GPU PID matches NVML. Startup seals13 common work/timing/capacity/engine/
  serving fields equal to completeV345, qualified runtime hash and actual
  tokenizer file hashes. It has not reached/verified all four earlier false
  trigger cases and proves no complete correctness or performance yet. Preserve
  frozen sources/config/external inputs. Nice19 CUDA-hidden plot waiter281239/
  start730784077 explicitly selects ops/cpu_plotting_20261027/bin/python and
  only plots after complete success plus actual owned exit. State:
  ops/reference_tool_eos_guard_20261027_r1.plots.state.json. No further GPU
  experiment is queued. Keep the global goal active and other baselines deferred.
- The guarded Full's four old closing-only tool/EOS counterexamples now all
  verify on actual replay in a sealed2013-completion prefix. The offline
  `scripts/audit_tool_eos_guard_cases.py` requires exact old output/native
  identity/stop, a completed same-context higher-epoch successor and a later
  same-clock-domain FIFO runtime frontier beyond the1000ms EOS evidence window;
  missing evidence cannot pass and persistent false triggers stay failures.
  Targeted68 audit tests pass without failures/skips. Evidence is under
  `experiments/reports/reference_tool_eos_guard_20261027/`, including
  `collecting_guard_cases_after_resume.json` and `guard_case_audit_cpu.xml`.
  These are known-case collecting proofs, not complete work or performance.
  The actual watcher/server/GPU scheduler/driver remain live/nice0 and frozen;
  preserve ac94b2bcd118, reuseV345, and queue no extra GPU job before completion.
- The separate offline `scripts/audit_eos_trigger_lifecycle.py` now classifies
  every observed final-EOS trigger against exact native receipts and actual
  same-context successor/RETURN evidence. Missing RETURN or completion stays
  unknown; lifecycle labels never enter online policy. A sealed3543-completion
  prefix has55 triggers, all actual final requests and none with a dedicated
  tool boundary. Seventeen new tests plus the earlier68 audit tests pass85
  cases without failures/skips. The named report directory preserves that
  collecting result and a prepared complete_review.py requiring terminal work,
  actual owned exit, fresh idle drain, matched figures and complete independent
  migration/JCT/wait/prefetch/grant/known-case/lifecycle audits. This operator
  is not yet executed and all-baseline/performance acceptance remains pending.
- EOS-guard R1 now completes all3674 exact requests,145565966 input/549671
  output tokens and18 workflows, with original stopping/all normal tails,
  two fresh idle snapshots and actual owned exit; the CPU plot waiter also
  completes and all three matched PNGs are inspected. The actual complete
  operator succeeds: meanJCT1646.862394s versusV3451697.583645s improves
  2.98785%; whole-run output237.444652 versus233.371801token/s improves
  1.74522%. Seventeen workflows improve; desktop_media regresses2.567029s.
  Mean maximum post-first ready gap76.981333s remains worse than73.449107s.
  Compared with earlier sibling Full, meanJCT/throughput regress0.21178%/
  0.69432%; these single changed configurations are not repeated trials.
  Keep the real EOS bug fix without claiming performance superiority.
  Four known cases pass and all55 actual final-EOS triggers have real final
  RETURN evidence. All573 tagged JOIN commands prove reuse104.141685GB;
  ten JOINs early-start and prove25.818759GB, distinct from21.595914GB of
  grant-associated reuse including later copies. All110 unique granted
  requests bind to native service. Actual H2D2721.542504GB/D2H1189.520572GB;
  tool prefetch copies zero and independent predicted offload releases only
  82tokens/8060928bytes. Of506 tool successors waiting at least1s,92.990% of
  sampled cumulative wait is inactive; this is not exact or removable JCT.
  Twenty-four sampled restore batches wait2470.806ms versus18/2662.835ms,
  without expansion or matched-cohort claims. Preserve all complete evidence,
  source failures and ordinary tails; see the named complete research doc.
  Next inspect actual long inactive capacity decisions before choosing another
  qualified optimization. No new GPU job is queued; reuseV345 and leave the
  clear/repeated/all-baseline goal incomplete. Progressive layer handoff is
  still unimplemented and was not evaluated by this run.
- The complete same-snapshot capacity audit now partitions the506 long tool
  successors into241.582s active,813.651s inactive with full raw logical input
  budget fitting,2510.259s inactive shortfall and8.989s unknown. There are282
  waits with some fitting evidence; overlapping sampled intervals are not
  removable JCT. Cached physical headroom has no observation age and is not
  admission authority. Preserve complete_capacity_waits.json and27 passing
  audit tests. Do not schedule a run solely for the39 rare marked snapshots.
- A separate default-off Full candidate now adds event-driven free continuation
  admission through ReferenceFreeContinuationAdmission while retaining the
  exact reference working-set core/tick and normal native demand recovery.
  It checks current submitted non-new ready agents only, complete raw budgets
  with actual streaming refresh, at most24 known JOIN parents then8 ordinary
  candidates including the actual new submit, and grants at most one extra
  eligibility per relevant event. Empty decode/event callbacks return before
  scans. No timer, ready-peer pause, loan/recall, physical prefetch, speculative
  tool eligibility or ordinary restore reservation is added. Controls clear
  AGENT_FREE_CONTINUATION_ADMISSION. The declaration is
  configs/migration/predictive_reference_free_continuation_20261027.json.
  Targeted419 cases pass without failures/skips, including real runtime
  integration and existing native CPU stand-ins; preserve initial fixture
  failures. Full shared/source-bound qualification and three constructor
  isolation checks remain pending. No GPU run is registered. See
  docs/design/reference_free_continuation_20261027_zh.md; keep the global goal
  incomplete and reuse completeV345 only after qualification/current guards.
- The free-continuation candidate now passes one source-bound shared execution
  of3291 cases plus4 subtests without failures/skips, preserving all3247
  previous identities and adding44. The three actual constructor preflights
  verify Full enablement and both ThunderAgent controls disabled. Shared
  sources/evidence/XML are under reference_free_continuation_20261027.
  The grant auditor now recognizes the single exact current continuation
  request with optional JOIN identity, preserving native session/clock/epoch
  service proof. The candidate common environment changes only its two new
  flag forms relative to the previous sibling declaration; source/timing/
  control/order remain identical. Native11b84f7c3198 and the726-case binding
  were revalidated. Freeze committed sources and check the current reservation,
  actual previous exit, free GPU/port before a Full-only run; reuseV345.
  No new GPU run has yet been registered and performance acceptance is false.
- Free-continuation R1 now runs from frozen1f1678b266e1 on port18664:
  ops/reference_free_continuation_20261027_r1.registration.json. Actual nice0
  watcher318424/start731387723, server318870/start731390336, GPU scheduler
  319195/start731391750 and driver320163/start731405400 are independently
  live; GPU PID matches NVML. Source-bound live_start.py/.json seals164
  exact completed receipts and13 common work/timing/capacity/engine/serving
  fields equal to completeV345. The actual new policy/flags and healthy
  physical control are verified, not complete correctness/performance.
  A later independently sealed collecting_grants_after_start.json binds one
  actual new continuation grant of8967 complete logical tokens to its current
  native request/context/session service, with18.183ms grant-to-run_batch
  observation and no service before the grant. This is not GPU kernel timing
  or a performance gain. Preserve frozen sources and all external inputs.
  Nice19 CUDA-hidden CPU plot waiter320361/start731406698 explicitly uses
  ops/cpu_plotting_20261027/bin/python and only draws after full success plus
  actual owned exit. State: ops/reference_free_continuation_20261027_r1.plots.state.json.
  No further GPU experiment is queued. ReuseV345; goal remains incomplete.
- The source-bound ordinary continuation callback CPU fixture now measures
  33 programs/pending records with a real async journal: new-grant thread CPU
  median91.443us/P99118.244us/max926.538us; budget refusal32.153us median,
  live-growth refusal40.826us; empty completion/events0.506/0.376us medians
  and no candidate scans. It does not measure the24-parent JOIN window,
  writer-thread CPU, whole-controller cost or GPU/end-to-end gains. Preserve
  the initial dict-versus-integer counter-validation failure and550 complete
  initial journal rows; verified retry is under cpu_journal_verified/.
  Named admission_cpu_cost.py/.json bind running source hashes. The separate
  collecting pipeline seals1189 exact completions with unknown per-agent
  reference budget composition. A later grant prefix has54 grants (52 new
  continuation,2 sibling):50 continuation and2 sibling service associations
  verify;2 continuations still lack completion/session proof. These are
  independently sealed live prefixes, not final performance. Keep frozen
  1f1678b266e1 and V345 unchanged.
- The continuation named evidence directory now contains complete_review.py,
  pending actual full completion and matched plots. It requires original
  stopping/all tails, actual owned exit, two fresh idle snapshots, complete
  immutable audit inputs, exact grant consumption and separate event counts.
  Existing same-session prefix probes reconcile against final native counters;
  completeV345 has3564 matched requests/140952652 common tokens and zero
  observed missing old input. This does not measure all kernel recomputation.
  Real-control prechecks reject truncated and foreign-arm seals. One helper
  execution passes300 CPU cases without failures/skips; preserve its XML.
  No frozen runtime/config/engine changed, no final Full result is implied,
  and progressive layer-capacity handoff remains unimplemented.
- ContinuationR1 now completes all3674 exact requests,145565966 input/549671
  output tokens and18 normal workflow tails, original stopping, two fresh
  idle snapshots and actual owned exit. The plot waiter also exits and all
  three matched figures are inspected. The complete operator succeeds after
  preserving its original-source-graph seal boundary failure and authorizing
  only the registered source graph separately. MeanJCT1630.055849s versus
  V3451697.583645s improves3.97788%; full-run output236.040223 versus
  233.371801token/s improves1.14342%. Versus priorEOS Full, meanJCT improves
  1.02052% but throughput regresses0.59148%. Preserve17 faster workflows and
  game_simulation25.059875s slower. Do not promote this as clear/repeated
  superiority or start other baselines/ablations.
  All286 program service associations in283 grants verify, including191
  ordinary continuations. ActualH2D2679.432806GB/D2H1079.603331GB;366 JOIN
  copies47.787049GB prove reuse and five JOINs early-reuse22.562734GB.
  Tools copy zero. Both arms'140952652 common previously served input tokens
  have zero missing-prefix probes; this is not every kernel recomputation.
  Independent complete_offload_paths.py/.json separates612 allocation-pressure
  JOIN releases125.567631GB and189 tool releases2.006188GB from zero additional
  predictive early releases. Final recorded demand-offload no-waiter checks
  are3800; idle prepare has7251 Host-budget pre-scan refusals/9965 checks.
  These are not continuously observed opportunity recall or safe Host-victim
  proof. Inspect admission/demand timing and physical/Host capacity before
  another candidate, not more loan variants or forced ready-peer prefetch.
  Complete reports/figures and limitations remain in the named directory and
  docs/research/reference_free_continuation_complete_20261027_zh.md. ReuseV345,
  queue no extra GPU run by this audit, and keep the global goal incomplete.
  Progressive layer-capacity handoff remains unimplemented and unevaluated.
- The separately declared tool_offload_before_admission candidate now observes
  the actual selected native queue after the existing plan and before physical
  demand allocation. Keep Full/reference/demand_cost-only, default off and
  stripped from controls. It preserves complete eligibility, actual Host-miss/
  deficit, prediction costs, locked/private/settled-copy and lifecycle guards;
  grants no logical budget and evicts no ready peer. One first-seen current
  request may rearm a check; a512-entry identity cache bounds memory and the
  same pending request keeps200ms backoff. Empty/no-tool-source/disabled and
  budget-exhausted passes skip the added inspection. Event-drain revision
  changes rebuild the plan once; maintenance scope restores on exceptions.
  Positive actions record dispatch_stage before_native_admission versus
  maintenance. Actual native adder capacity properties remain authoritative;
  no engine/allocator/kernel changed and no progressive layer handoff exists.
  Real CPU native64-slot red case preserves4 free pages on old code and frees
  32 settled blocked-donor pages before the16-page native load after repair,
  keeping a ready peer's indices. The first shared3328-pass/one-fixture-fail
  report and constructor/empty-source red reports remain preserved.
  qualified_shared_cpu.xml passes3331 cases plus4subtests in one execution,
  retaining3291 identities and adding40; all three actual constructors and
  current source/tokenizer hashes verify. The single-consumer guarded CPU
  measurement has plan medians7.037us disabled,7.149us no hints,20.051us
  backoff,226.538us due/227.266us rearmed; due max3835.282us. This excludes
  successful release/journal/GPU/full-controller costs and the maximum window.
  Evidence is under experiments/reports/tool_offload_before_admission_20261027/
  and docs/design/tool_offload_before_admission_20261027_zh.md. Freeze qualified
  sources and recheck current hold/resources/actual predecessor exit before
  a Full-only comparison, reuse completeV345, and keep GPU/performance/global
  acceptance pending. Do not call this timing repair an independent novelty.
- The pre-admission offload Full now runs frozen716ef9a3e4ad on unchanged
  native11b84f7c3198, port18665:
  ops/tool_offload_before_admission_20261027_r1.registration.json. Actual nice0
  watcher363193/start732042783, server363623/start732045344, GPU scheduler
  363990/start732046759 and driver365023/start732060155 are independently live;
  NVML has that sole owned GPU process. Named live_start.py/.json binds the
  qualified runtime/dispatch sources,13 common work/timing/capacity/engine/
  serving fields equal to completeV345 and69 exact completed receipts.
  Actual early flag and demand-cost offloader are enabled, physical control
  healthy; its independent startup snapshot has one early check/rearm and
  zero extra early release. This is not final correctness or performance.
  Nice19 CUDA-hidden plotting waiter364601/start732051797 uses the dedicated
  ops/cpu_plotting_20261027/bin/python only after complete success/actual exit.
  State: ops/tool_offload_before_admission_20261027_r1.plots.state.json.
  Preserve frozen sources/config/inputs and normal stopping/tails; no next
  GPU job is queued. ReuseV345 and keep the global goal incomplete.
- A later independently sealed collecting_early_service.py/.json prefix has
  1731 exact completed receipts and21 actual before_native_admission releases,
  cumulatively231309312bytes for21 consumers. All21 bind to completed native
  request/context/epoch/session/clock service after their associated releases
  and were submitted beforehand. The release journal omits full release-time
  attempt/session identity and freed-index-to-allocation evidence; keep both
  unknown. Run-batch entry is not kernel start, cumulative release bytes are
  not simultaneous capacity, and no hidden-time/JCT gain follows. Keep frozen
  sources and complete-work performance acceptance pending.
- The named pre-admission complete_review.py and complete_offload_paths.py
  now require actual terminal work, common complete figures, immutable seals,
  native stopping/drain/cleanup and independent migration/JCT/wait/grant/EOS
  audits before comparison. The registered original source graph receives
  separate path authority; unrelated arm seals remain rejected. Fourteen
  review_precheck.py/.json CPU cases pass, covering actual zero-release input,
  missing/contradictory service identity and invalid complete seals. These
  operators have not yet reviewed a completed current Full and supply no new
  runtime/GPU/performance qualification. Preserve716ef9a3 and reuseV345.
- Pre-admissionR1 completes all3674 exact requests,145565966 input/549671
  output tokens and18 normal tails, original stopping, two fresh idle snapshots
  and actual owned exit. The complete operators succeed and all three matched
  figures are inspected. MeanJCT1631.194643s versusV3451697.583645s improves
  3.91079%; output238.499008 versus233.371801token/s improves2.19701%.
  Seventeen workflows improve; web_social regresses78.679925s and mean max
  post-first ready gap90.642600s is worse than73.449107s. Versus priorFull,
  JCT worsens0.06986% and output improves1.04168%; no repeated/clear gate passes.
  Actual41 before-admission releases total392724480bytes for41 completed
  consumers; full release-time attempt and freed-index assignment stay unknown.
  Native pressure releases are separate: JOIN108.957303GB/tool6.181945GB.
  All455 JOIN copies83.901383GB prove reuse; five early-reuse22.711566GB;
  tools copy zero. All277 grant/service associations and four EOS countercases/
  55 final triggers verify. Both arms'140952652 common old input tokens have
  zero missing-prefix probes, not a claim about every kernel recomputation.
  ActualFull H2D2714.687472GB/D2H1132.365251GB have complete transfer-stream
  interval sums85.968206s/27.797246s; control88.825712s/51.947387s. They are
  not exposed GPU waits or additive critical-path loss. Preserve normal tails,
  CPU/report evidence, frozen execution and all negative results. ReuseV345,
  keep other baselines deferred and the goal active; no next GPU job queued.
  Progressive layer handoff remains unimplemented and unevaluated. See
  docs/research/tool_offload_before_admission_complete_20261027_zh.md.
- The default-off Full-only IDLE_HOST_MIRROR_RECYCLE candidate reuses native
  Host-only duplicate release to back a current unbacked cold FULL owner under
  actual HBM pressure and idle transfer queues. Check at most32 dirty and32
  mirror candidates per existing maintenance callback with1s backoff. Mirrors
  require a current Device lock, complete Device/Host values, native Host/DMA
  guards and no prefetch-service protection. No Host-only checkpoint, Device
  page, logical eligibility or ready-agent execution is evicted. Require the
  complete actual Host budget before cold backup; a dropped mirror without a
  copy stays explicit. Rotate only the inspected dirty window. This is an
  engineering integration, not a new working-set algorithm or layer handoff.
  Config: configs/migration/predictive_idle_host_mirror_recycle_20261027.json.
  Shared CPU passes3381 cases plus4subtests in one execution, no failures/skips,
  retaining3331 identities and adding50; three actual constructors verify Full
  enabled and both ThunderAgent controls disabled, with inherited core methods.
  Preserve initial2-pass/one-red and first3-pass reports. A true native CPU
  8-mirror/4-cold-slot case preserves both agents' Device indices and free count.
  The whole idle callback CPU benchmark includes a real async journal and500
  measured samples per path: disabled21.613us median, backoff22.965us,
  32-stale rejection84.653us, success365.117us/P991907.773us. Setup/destruction,
  writer-thread CPU, GPU, maximum mirror window and worst ownership subtree
  are excluded. Evidence is under idle_host_mirror_recycle_20261027/. Native
  engine11b84f7c3198 is unchanged. Complete actual GPU bytes/lifecycle before
  freezing a Full-only comparison; reuseV345 and retain all prior negative
  results. No new GPU experiment is registered and the global goal is active.
- The frozenfc57f6c0 Host-mirror GPU component attempt stopped in disabled
  setup before exercising recycling: its257KiB Host fixture cannot satisfy
  the production SPAWN wrapper's64MiB reserve. Preserve gpu_components_r1.json
  and its failed operator snapshot; actual child exit and empty NVML are
  verified. A distinct fixture retry uses the existing bounded read-only
  backup primitive for the initial8-slot mirror, without changing production
  reserves, qualified policy sources or the native engine. GPU qualification
  and any complete Full comparison remain pending.
- The frozena2e1b235 Host-mirror GPU component retry also stopped before
  recycling, at runtime construction: its Full environment enables final-stage
  prefetch but supplied no required event socket. Preserve
  gpu_components_r2.json and the frozen operator. Actual exit and empty NVML
  are verified. The next fixture uses a separate temporary event socket for
  each scenario, preserving Full switches, production constructor guards,
  qualified policy hashes and the native engine. Real GPU lifecycle/bytes and
  complete-model performance remain pending; this is not layer handoff.
- Host-mirror componentR3 now succeeds from frozen3ef9460f on unchanged
  native11b84f7c3198. All four real GPU/Host scenarios pass: disabled,
  Host-locked and already-returned cold owners preserve mirrors; the positive
  case recycles8 Host slots during an actual protected GPU read and copies
  Cold4 into those released slots without changing Device ownership/free
  count. Demotion is refused before D2H ACK; afterward Cold4 is released,
  poisoned and restored with every two-layer K/V byte checked. Actual process
  exit and empty NVML are verified. Keep R1/R2 failures and the explicit small
  fixture, synthetic-reader and raw-controller scope; this is not model
  consumer admission, whole-model performance or progressive layer handoff.
  Freeze qualified production sources for a separate Full-only comparison,
  recheck hold/resources and reuse completeV345, preserving all normal tails.
- Host-mirror FullR1 now runs frozen80a6186cdc7b on unchanged native11b84f7c3198,
  port18666: ops/idle_host_mirror_recycle_20261027_r1.registration.json.
  Actual nice0 watcher412718/server413169/GPU scheduler413499/driver414704
  are independently live and NVML matches the sole scheduler. Named
  live_start.py/.json binds all qualified sources, actual mirror/offload flags
  and13 common work/timing/capacity/engine/serving fields equal to completeV345.
  Relative to previous Full, only the two mirror flags change. A subsequent
  sealed pipeline has425 exact completions, not full performance; keep
  reference per-agent aggregate-only budgets unknown. The nice19 CUDA-hidden
  plotting waiter414294 uses the dedicated plotting environment and waits for
  complete success and actual exit. Its state is
  ops/idle_host_mirror_recycle_20261027_r1.plots.state.json.
  The independent mirror_lifecycle.py operator has15 passing CPU checks and
  credits only exact-node/generation prepare plus actual native D2H extent/
  submission-window evidence. Its first sealed prefix haszero mirror releases,
  preserved without inferring whole-run opportunity. Backup ACK is not HBM
  release, first use, hidden time or causal JCT benefit. Keep frozen sources,
  all normal tails, completeV345 and the goal incomplete; no additional GPU
  run is queued and progressive layer-capacity handoff is still unimplemented.
- The later mirror collecting prefix seals2211 exact completions. Its separate
  lifecycle prefix observes3 releases/6 Host nodes/2.415919GB, linked to3 real
  native D2H completions totaling50.233344MB. Preserve this disparity; released
  Host space is not net migration benefit. The prepared named complete_review.py
  and run_complete_audits.py require full success, actual exit, fresh drain and
  matched plots.31 CPU audit-boundary cases pass, including actual prior Full
  andV345 complete work; current live Full is correctly rejected as incomplete.
  Runtime80a6186cdc7b stays frozen. No complete current result is implied.
- The user's newest order is to repair predicted-tool offload first, then
  implement actual per-layer D2H/H2D capacity overlap and disable/revert it if
  matched complete performance is negative. Long successor queue waits alone
  do not justify preempting a ready agent or granting blanket tool priority.
  Diagnose prediction, physical demand, Host readiness and native safety
  separately. Five-second logical working-set selection is not physical D2H.
- A confirmed predictor conversion bug is repaired on main: discrete offline
  and hybrid prior CDFs now retain EmpiricalActionTimingCurve, like existing
  pure online histories, instead of interpolating fictitious earlier completion.
  Six new cases fail on old code;196 targeted cases pass after repair, including
  three real-native CPU allocator profitable-offload counterexamples. Preserve
  initial_red_cpu.xml and targeted_cpu.xml under tool_prediction_curve_repair_20261027.
  The90% policy, priorities, physical safety and running frozen Full are
  unchanged. Full shared/source binding, GPU and performance remain pending;
  do not claim this one bug explains every missed tool offload or clear gains.
- The tool-curve repair now passes3387 shared cases plus4subtests in one
  execution without skips/failures, retaining all3381 prior identities.
  Actual Full/default-TA/decay-TA constructors and inherited reference methods
  remain isolated. Historical930 empirical forecast records have682 original
  versus717 corrected one-second gate passes at issuance;35 change. This is
  not actual dispatch opportunity recall or proof of35 profitable offloads.
  Keep the90% threshold unchanged until a separately evidenced policy change.
- MirrorFullR1 completes all3674 exact requests,145565966 input/549671 output
  tokens and18 normal tails with native stopping, fresh drain and actual exit.
  Complete review and all independent audits pass; three matched figures are
  inspected. MeanJCT1646.191049s/output237.431540token/s versus completeV345
  1697.583645s/233.371801 improves3.0274%/1.7396%, insufficient for the gate.
  Versus previous Full it regresses0.91935% JCT/0.44758% output. Preserve the
  single-run limitation and leave mirrors unpromoted/default-off. Five Host
  recycling rounds release11 nodes/3.773596GB and directly back77.463552MB;
  these bytes are not net gain. Final predicted release76/5.004165GB includes
  63 before-admission releases/0.525042GB with service.438 JOIN copies/
  85.390688GB prove reuse;283 grants/programs,4 EOS countercases and54 final
  EOS triggers verify. All normal tails and negative results remain.
- Actual layer-handoff implementation has begun in the isolated native branch
  beliefkv-layer-handoff-20261027, commitf4e1db4c2cab/tree2711ff946ceb, based
  on common11b84f7c3198. It adds MHA per-layer D2H and an L2 shared-extent
  D2H/H2D primitive: B layer0 waits only for A layer0 backup and never waits
  for A's final-layer ACK. The shared Device indices remain caller-reserved,
  never returned to the ordinary allocator. Fourteen CPU data/order/contract
  cases pass. The primitive owns no native cache transaction, Host allocation
  disjointness proof or logical eligibility; these bindings, actual GPU and
  whole-model performance remain pending. Separate delta patch and CPU proof
  are under patches/sglang-layer-handoff-primitives-20261027.patch and
  experiments/reports/layer_handoff_20261027/. Keep the common staging patch
  and controls unchanged. Continue tool prediction repair then actual pipeline
  integration; disable/revert overlap if complete matched performance is
  negative. Do not present this primitive as an integrated Full implementation.
- Prediction-curve FullR1 now completes all3674 exact requests and18 normal
  tails with original stopping, fresh drain and actual owned exit. Named
  complete_review.json verifies meanJCT1635.620442s/output236.597296token/s,
  0.2713%/0.7974% worse than previous Full and3.6501%/1.3821% better thanV345.
  Extra predicted release64/0.786629GB is not proof of a net performance win.
  ReuseV345; keep the goal incomplete.
- Native2a927b88939c/tree0d407f981c51 now binds one reserved layer handoff to
  real split-safe FULL cache paths, native ACK locks and layer consumer events.
  At most64 nodes per private path; donor and consumer sizes may differ.
  Only the already selected logically eligible consumer may use the default-off
  Full candidate; no ready peer or extra eligibility is displaced. Reserved
  tail indices remain protected until donor ACK and early donor demand backs
  off. Shared3421+4 CPU cases pass; source-bound GPU/model and complete matched
  performance remain pending. Keep the common staging patch unchanged.
- The latest question correctly distinguishes class-duration distributions from
  per-call prediction. Current synthetic network keys use observable domain/
  WebFetch-or-WebSearch category, then elapsed survival and workflow/run online
  histories. Same-category cold predictions share the same prior. Independent
  within-category sampled durations cannot be predicted from original command
  semantics. P90 coverage is not per-call accuracy; offload requires conservative
  remaining survival over actual migration cost, not a large P90 total duration.
- Full online empirical forecasts had a separate confirmed refresh defect:
  only `_tool_timing_only` entered periodic refresh, so Full with no legacy
  worker lost its hints after1-2s. Four old-code counterexamples fail; bounded
  online refresh plus deferred physical ancestry passes3433+4 shared CPU.
  Keep the90% gate and unsupported-tail/return revocation. The independent
  common11b84f7c3198 configuration passes3392+4, retaining3387.
- Online-refresh FullR1 runs frozen29c58c8b678f, port18672:
  ops/tool_online_refresh_20261027_r1.registration.json. Its actual nice0
  watcher498770/server499268/GPU scheduler499614/driver500431 are verified;
  startup seals942 exact receipts and2197 forecasts, including101 same-wait
  groups with refresh. All13 common work/timing/engine/capacity fields match
  completeV345; reuse that control. Nice19 CUDA-hidden plot waiter504790 uses
  the dedicated plotting environment after complete success/owned exit.
  Keep frozen execution and all tails. No final performance result is implied.
- Layer-handoff modelR2-R4 remain failed qualification attempts, preserved
  separately. R2 exposes speculative input reservation in actual demand; the
  actual selected-input fix preserves all growth slots and selected chunks.
  R3 exposes expired unrefreshed Full hints. R4 confirms fresh hints and a
  32-token reserve with4080 free, then native refusal. The newest CPU red case
  proves complete decode-tail load size differs from whole-extent checkpoint
  size. Restore only an exact complete private tail matching this request,
  price actual Host bytes and retain exact final extent authorization; partial
  or divergent tails stay refused. Shared3436+4 retains3433 and adds3.
  Native2a927b88939c remains unchanged; actual GPU/model and matched layer
  performance remain pending. Keep handoff off in running Full, and do not
  infer a performance win from these correctness repairs.
- ModelR5 is queued behind actual online-refresh Full cleanup:
  ops/layer_handoff_model_r5_20261027.registration.json. Actual nice0 CPU
  supervisor511897 is live in waiting_complete_full; it owns no GPU child.
  The application is isolated at269b6423 and the clean native checkout at
  2a927b88939c, identical to the real component proof. It rechecks3436-case
  sources, pins, reservation, actual predecessor exit and idle GPU/port before
  the numerical qualification. Do not overlap another GPU launch with this
  queue or call it a passed model/performance result. Preserve all R1-R4
  failures, current frozen Full and the incomplete global goal.
- The latest user explicitly permits changing the90% tool-offload survival
  gate and requests testing it together with actual per-layer D2H/H2D handoff
  for a quick joint-policy result. This supersedes the earlier unchanged90%
  instruction and defers an isolated handoff-off control. Preserve all old
  configurations; the new declaration is
  configs/migration/predictive_host_demand_layer_handoff_20261028.json.
  It explicitly selects75%,2GiB optional-backup Host reserve and layer handoff.
  This is a policy threshold, not predictor accuracy. Legacy quantile-only
  hints use an existing no-weaker quantile and never invent P25.
- HostDemandCapacity caps SPAWN/idle optional backups, keeps a one-second
  actual-demand reserve and reclaims at most64 settled complete Device-pinned
  Host mirrors only after a real tool-offload beneficiary/cost check. Preserve
  Host-only copies, DMA/Host locks, donors and prefetch-service protection.
  Native demand eviction already prefers duplicates; do not claim this is
  newly invented or that finite Host can never exhaust. Idle speculative
  mirror recycling remains disabled. Layer handoff still needs complete donor
  Host capacity; do not remove prepare_host without measured evidence.
- OnlineRefreshFull completes3674 exact requests/145565966 input/549671 output
  tokens and all18 normal tails, original stopping, fresh idle drain and actual
  owned exit. Its complete_core_review.json independently verifies meanJCT
  1639.208934s and output236.448501token/s,3.43869%/1.31837% better thanV345.
  Its three complete figures are inspected. These modest single-run results
  are not isolated attribution or repeated/all-baseline acceptance.
- ModelR5 passes actual layer handoff and unforced donor recovery with matching
  tokens and zero maximum logprob difference. The current589ff977 joint-policy
  model check independently passes with actual75% evidence and the same zero
  deltas. Its report is under
  experiments/reports/host_demand_layer_handoff_20261028/combined_model/.
  Shared current CPU passes3462 cases plus4subtests, retaining3436 and adding26;
  native related CPU passes102. All three constructors preserve paper-policy
  isolation. Initial fixture/quantile/test-curve failures remain preserved.
- Replay launchers accept only the explicitly reviewed layer tree
  0d407f981c5179b20859e82e7088d85589cd08b3 with its separate immutable patch.
  New current24-request GPU position/session/stop smoke passes before Full.
  Do not silently reuse the common-engine smoke or normalize away the engine
  difference from a performance report. Its native engine is the same exact
  tree as the real component and model proofs, packaged from basefa5cb52.
- JointFullR1 is registered from frozen589ff977cebc on port18675:
  ops/host_demand_layer_handoff_20261028_r1.registration.json, after the actual
  position qualifier cleanup. It preserves completeV230 work, V311 category
  timers, streaming, Device805198/Host200GB, running/decode24 and seed21.
  No native/ThunderAgent rerun or ablation is queued. Reuse completeV345 with
  its common11b84f7c3198 engine explicitly declared as different. This is a
  combined-system comparison, not isolated overlap performance attribution.
  Preserve frozen execution, inputs, all normal tails and any negative result.
  Verify actual server/driver/GPU state before claiming measured work started.
- The named nice19 CUDA-hidden deferred_plots.py waits for complete success
  and actual owned exit. It checks every work/timing/capacity field and the
  exact reviewed engine pair, then labels figures with that engine difference.
  Keep ops/host_demand_layer_handoff_20261028_r1.plots.state.json and the
  no-GPU scope. Benefit estimates are separately documented in
  docs/research/design_benefit_ranking_20261028_zh.md; none are additive
  isolated module gains. Keep the global goal active until actual acceptance.
- The user now cancels tool prefetch in all future Full experiments. Use the
  explicit no-tool-prefetch profile and zero both its enable flag and priority
  window; do not re-enable it through the tool-priority CLI override. Retain
  tool-duration forecasting for demand-cost offload, JOIN/EOS prefetch,
  prepare_host and agent continuity. Preserve historical configurations and
  frozen runs. Explicit TOOL_PREFETCH/BELIEFKV_ENABLE_TOOL_PREFETCH settings
  must reach actual runtime checks, rather than requiring an ablation label.
- JointFullR1 completes all3674 exact requests/145565966 input/549671 output
  tokens and18 normal tails, native stopping, two fresh idle snapshots and
  actual owned cleanup. The independent complete_core_review.py/.json verifies
  meanJCT1646.588432s/output235.272956token/s versusV3451697.583645s/
  233.371801token/s:3.00399%/0.81465% improvements. Both regress against
  online-refresh Full1639.208934s/236.448501token/s. Threshold, Host policy
  and the native layer engine changed together; no isolated overlap cause is
  proven. Do not promote the joint candidate. The user's newest correction
  retains layer overlap for an implementation audit while cancelling tool
  prefetch. configs/migration/predictive_no_tool_prefetch_20261028.json keeps
  layer_handoff and the0d407f981c51 engine,75% and demand Host management.
  Preserve54e04b8c and its3477-case CPU evidence for the earlier layer-off
  proposal; rebind current sources/configuration before future execution.
  Explain sibling admission from actual code and service evidence; logical
  eligibility is not simultaneous physical service or an isolated JCT gain.
  No new GPU job is queued by this review; reuseV345 and keep the goal active.
- The implementation audit confirms a Host-budget alignment bug: the512MiB
  idle-backup limit is not divisible by the observed98304-byte KV unit.
  Comparing the rounded optional budget to the raw limit falsely rejects
  all10114 checked JointFull idle dispatches, including ample-capacity cases.
  Main now compares whole-page budgets while preserving demand reserve.
  Keep the initial geometry-fixture failure, the corrected two-red/two-pass
  report and176 passing targeted cases. One source-bound shared execution
  passes3481 cases plus4subtests without failures/skips, retaining3477
  identities. Actual constructors verify overlap on/tool prefetch off and
  predicted offload retained. This is not new GPU performance evidence.
- Complete JointFull regression is+7.379498s versus online-refresh Full,
  including+6.750427s first-launch-to-complete and+6.368014s in critical
  decode-member worker intervals; worker intervals are not pure kernels.
  Actual13 handoffs restore107151360bytes,0.004% of totalH2D, mostly tiny
  private tails. Synchronous48-layer submission is a measured limitation,
  not proof of the entire regression. D2H/H2D increase149.526/27.935GB.
  The95 sibling grants add one member93 times and two members twice; all97
  child service associations verify, without an isolated JCT benefit claim.
  JointFull tool prefetch actually copies8.718GB with3.161GB version2 reuse;
  do not carry earlier zero-copy claims into this run. Future tool warming
  stays off as requested. Preserve complete named audits and figures; see
  docs/research/layer_overlap_regression_and_siblings_20261028_zh.md.
  No new GPU experiment is registered; retain V345 and the incomplete goal.
- The new explicit candidate is
  configs/migration/predictive_join_blocked_layer_groups_20261028.json.
  AGENT_JOIN_BLOCKED_HANDOFF bridges observed EOS/actual submitted JOIN
  parents to complete logical budget from active nonpending unprotected
  genuinely blocked agents. Tool donors retain the existing long prediction.
  Check at most64 donors and24 ready parents, commit only an adequate whole
  plan, keep200ms failure backoff, and grant no physical page credit. Existing
  native reclaim/ACK/H2D/admission remains mandatory. Its optional resume
  eligibility filter prevents still-blocked paused owners from immediately
  undoing a JOIN handover; paper/default behavior is unchanged. This is
  working-set integration, not a new loan/recall contribution.
- Native1f5fac9e9f7a/tree2ce5e5389979 adds independent fused layer groups using
  the existing HiCache all-layer JIT and pointer-table views. Explicit8/4
  groups reduce48-layer copy calls/completion events from96 to18; default1/1
  remains. Preserve old engines and the new immutable canonical patch.
  Exact group-matched GPU component, unforced-model and position proofs are
  required before new complete performance claims. CPU53 native cases and
  one3519-case shared execution plus4subtests pass, retaining3481 identities.
  Preserve the first shared nested-event-time failure and successful exact/
  complete retries. Callback success measures121.328us median in a33-program/
  31-pending synthetic fixture with real async logging; it is not whole-path
  or GPU cost. Named evidence belongs to join_blocked_layer_groups_20261028.
  See docs/design/join_blocked_layer_groups_20261028_zh.md. Tool prefetch
  stays off; reuseV345 and keep the clear/repeated/all-baseline goal active.
- Grouped GPU componentR1 from frozen0decf93f stopped before any handoff:
  the shared fixture hardcoded its old2-layer/1024-byte geometry although
  this declared group fixture has17 layers. Preserve gpu_components_r1.json
  and the original frozen sources. Main now checks512bytes per declared
  layer while retaining128 Device/257 Host slots; default2-layer checks
  remain identical.71 targeted CPU checks pass. Actual R1 process613553
  exited and NVML is empty. Native1f5fac9e and production policy are unchanged;
  retry only from a newly frozen fixture and keep GPU/performance pending.
- Grouped componentR2 now passes from frozen4675f4ad with the unchanged
  clean native1f5fac9e. The actual17-layer8/4 scenes preserve all donor Host/
  consumer Device K/V, outstanding donor reads, resident protection and
  ACK-gated4-to20 free-slot accounting. The delayed-final-group scene proves
  first consumer access before final donor ACK; the normal tiny scene does
  not. These finite-delay fixtures are not workload timing or performance.
  Actual process616670 exits and its NVML context is absent. Latest full
  fixture-bound CPU execution passes3524 cases plus4subtests, retaining3519.
  gpu_review.py/.json binds actual frozen/component/CPU sources and exit.
  Preserve R1 and every prior failure. No model or FULL run is started or
  queued here; unforced-model, position and matched complete performance
  remain pending. Keep tool prefetch off and the global goal incomplete.
- Grouped modelR1 now passes from frozen4675f4ad with clean native1f5fac9e.
  Actual8/4 handoff, unforced consumer and later donor recovery preserve all
  generated tokens with zero maximum logprob delta against independent
  references. Its49152-token/8GB fixture is not full workload or JOIN-budget
  GPU-service evidence. Model server623498 exits, cleanup has no owned
  remainder and NVML is empty. Named model_review.py/.json binds components,
  current CPU sources and the prepared cumulative native patch; native_sources
  supplies replay registration. Fresh grouped24-request position/session/stop
  and running24 sampler checks still precede a Full-only complete comparison.
  Keep tool prefetch off, all failures/tails, reuseV345 and the goal incomplete.
- Grouped positionR1 passes24 exact request/session/native-stop checks; all24
  stop natively, and running24 position dispatch overhead averages0.01036ms.
  After its actual cleanup, FullR1 runs frozen913e3238/native2ce5e5389979
  on port18677: ops/join_blocked_layer_groups_20261028_r1.registration.json.
  Named live_start.py/.json verifies actual nice0 serving identities, sole
  owned GPU scheduler, blocked JOIN handoff and8/4 groups, tool prefetch and
  priority both off,75% predicted offload and2GiB Host demand reserve.
  Reuse completeV345 with the explicit grouped/common engine difference.
  Timing-artifact locations differ across isolated checkouts; actual content
  hashes and every other overlay/provenance field match. Preserve both paths,
  all frozen inputs and normal tails; do not edit the running registration.
  complete_review.py requires exact complete work, fresh drain and actual
  exit before plots/performance. No next GPU run is queued; goal stays active.
- A collecting grant prefix exposes an audit-only epoch assumption: EOS
  parent grants target a later epoch, but blocked-budget grants may target
  the exact already submitted JOIN successor. Main's independent auditor
  now accepts the latter only for the exact request/epoch and retains JOIN,
  session/generation, receipt and clock proof. One old-code red case and45
  passing audit checks are preserved. Same sealed-prefix review raises
  verified blocked-grant associations4-to9 of10; one incomplete receipt
  remains unknown. Frozen913e3238 runtime/native sources are unchanged.
  Latest live observations are not complete JCT, GPU overlap or module gains.
- GroupedFullR1 now completes all3674 exact requests/145565966 input/549671
  output tokens,72 JOINs and18 normal tails, original stopping, fresh idle
  drain and actual owned exit. Complete meanJCT1647.484437s/output237.608725
  token/s improves2.9512%/1.8155% versusV345, but JCT is0.0544% worse than
  previous joint Full; do not promote or call the all-baseline goal complete.
  Three full figures are visually inspected; retain actual partial bins.
  All53 blocked-budget grants verify later same-parent service;25 bind later
  verified copies totaling128.240714GB. All23 actual8/4 handoffs bind paired
  ACKs and service,4.384555GB/0.1573% of allH2D. Submission-related wall median
  falls4.8201-to0.9253ms, but H2D/D2H grow111.801/23.907GB and prefill worker
  intervals grow3.618s; no isolated module/JCT causality is established.
  Tool prefetch commands arezero.24 of63 observed JOIN waits verify98.563GB
  early reuse; all190.789GB tagged H2D has use evidence, so extra migration
  is not simply unused warming. Current logical donor and native physical
  reclaim still lack one physical plan; preserve native allocation authority.
  Independent ACK review now respects expanded D2H paths and anchor-only H2D
  publication, with12 passing checks; preserve initial audit failures and the
  earlier45-case submitted-grant repair. Frozen913e3238 remains unchanged.
  New audits change main source hashes: renew its CPU manifest before a future
  main freeze, rather than claiming the old manifest covers these audit edits.
  See docs/research/join_blocked_layer_groups_result_20261028_zh.md and named
  final_index.*. Keep overlap experimental per user, reuseV345, no further
  GPU job queued, all normal tails and negative results, goal active.
- The separate sibling-service/capacity candidate now admits fitting observed
  ALL-JOIN subsets, prefers at most four actually ready members in native
  prefill while preserving the existing head/dependency/locality/aging guards,
  and retries eight waiting groups per capacity-event pass. Do not introduce
  global aging or divide repeated plan observations by native service counts.
  Record ready-to-selected and ready-to-forward-return separately. Requeue
  discards the old attempt's service identity; native first forward consumes
  the plan without expiring normal agent eligibility.
- Demand tool offload and handoff now share consumer_input_reserve: replace
  only a known empty adder's unselected whole-batch input allowance; retain
  growth, existing chunks, DLLM and unknown-state conservatism. JOIN recovery
  records blocked donor keys and advances actual native demotion, backup/ACK
  or private handoff for submitted parents and verified native-EOS targets.
  An already eligible parent can establish a physical plan without a new
  logical grant. Keep logical budgets, real free pages and reserved extents
  distinct; this is still not atomic physical reservation before the grant.
- Native873970a45a25/treecdd58a4acbb5 adds explicit small idle byte budgets
  with independent Host demand reserve, and permits only bounded proven
  unrelated outstanding ACKs. Keep unsubmitted queue, source, lock, producer
  and one-transaction guards. Multiple small donors use ordinary ACK-gated
  backup/reclaim; a combined multi-donor layer transaction and asynchronous
  group submission remain unimplemented. Preserve all earlier native trees.
  Canonical patch is patches/sglang-sibling-capacity-runtime-20261028.patch,
  sha5e4038068f793a46b5bdabbbc83d0d4ccf6f854fc0d7be00ea51a72a827bc997.
- configs/migration/predictive_sibling_service_capacity_20261028.json keeps
  exact V230/V311 work/timing,18 projects,running/decode24,capacity/seed21,
  overlap8/4,75% offload survival and2GiB Host reserve. Tool prefetch and
  ordinary recovery binding remain off. Appd3b45368/0c24dd85 implements the
  candidate. One final source-bound shared CPU run passes3580 cases plus4
  subtests, retaining3524 identities and adding56; native92 checks and all3
  constructors pass. final_cpu_review.json independently reconciles sources,
  native tree, comparison settings and preserved failures under the named
  sibling_service_capacity_20261028 evidence directory. Small3-program CPU
  prefill P50s are13.0385us disabled,88.143us fitting,15.8435us input-limited;
  these exclude writer/GPU/whole-controller costs. Native component/model/
  position and full GPU performance remain pending for this new exact tree.
  No new GPU job is registered; reuseV345, preserve all normal tails and
  negative results, and keep the global goal active.
- Current sibling-capacity native GPU components and unforced model now pass
  from clean application100ec17e and native873970a45a25/treecdd58a4acbb5.
  Components verify all donor/consumer/resident K/V, old donor reads, delayed
  first-layer progress and ACK-gated4-to20 free slots with actual8/4 groups.
  Seven unforced model calls verify the real12296-token handoff and later
  Host-backed donor recovery against independent outputs with zero logprob
  deltas. Both owned processes exited. gpu_model_review.json independently
  binds CPU3580/92, current1795 application files, native patch/tree, actual
  cleanup and output evidence. These fixtures do not qualify workload-level
  sibling service, JOIN-plan behavior or performance. The separately named
  sibling_service_capacity_position_20261028_r1 qualifier precedes FullR1;
  retain exactV230/V311 work, normal tails, tool-prefetch off and completeV345.
- Sibling-capacity positionR1 now passes24 exact token/session/stop checks,
  all24 native stops, with running24 sampling-dispatch increment0.009877ms.
  Its actual cleanup precedes FullR1 at frozen100ec17e/nativecdd58a4acbb5:
  ops/sibling_service_capacity_20261028_r1.registration.json, port18679.
  live_start.py/.json independently verifies nice0 watcher706513/server707041/
  driver708343/sole GPU scheduler707378, exact work/timing/capacity/control
  contract, new fitting subset and native sibling preference, physical control
  healthy and tool prefetch/priority/frontier restore off. Separate collecting
  evidence seals105 exact receipts of3674; reference per-agent budget remains
  unknown. These are startup/prefix proofs, not full JCT/throughput. Preserve
  the frozen run and all normal tails, reuseV345, and keep the goal incomplete.
- New read-only audit_sibling_native_service.py binds service journals to
  actual native batch membership/clock, an explicit parallel blocking ALL-JOIN,
  and completed native sessions. Missing evidence stays unproven; request
  attempt identity remains independently unknown and old services are retained.
  Its sealed current prefix verifies45 journals/46 request-admission attempts
  from one JOIN, including one two-member actual prefill batch. Do not turn
  those45 records into45 JOINs or divide repeated plans into a coverage ratio.
  A stronger exact-prefix recheck preserves all45 proofs. Targeted96 CPU
  checks pass; retain the earlier88-case evidence. These new observer files
  are outside the old3580 manifest and do not change the frozen Full.
  See docs/research/sibling_native_service_audit_20261028_zh.md. Complete
  workload performance and the clear/repeated/all-baseline goal remain pending.
- Sibling-capacity FullR1 now completes all3674 exact requests/145565966
  input/549671 output tokens and18 workflows with original stopping/tails,
  two fresh idle snapshots and actual owned cleanup. Independent complete
  review verifies meanJCT1649.876055s/output237.908369token/s versusV345
  1697.583645s/233.371801token/s:2.81032%/1.94392% gains, insufficient for
  the clear/repeated gate. Versus previous grouped Full, JCT is0.14517%
  worse and output0.12611% better. Retain16 improved workflows, game_simulation
  +7.793168s/web_cms+5.252606s and worse mean maximum ready gap86.649s/73.449s.
  All1034 sibling journals/1047 request attempts across20 parallel JOINs
  verify, including13 real multi-member prefill batches; only2 explicit
  reorder plans are recorded, so do not attribute all13 to the new preference.
  New JOIN physical release journals and workload layer handoffs arezero,
  despite91 plan attempts/71 later parent services. H2D2848.215663GB/
  D2H1333.847261GB exceed previous grouped Full by60.146516/59.320959GB;
  all1153 issued prefetch journals are JOIN and tool prefetch remainszero.
  Complete figures are inspected; the nice19 plot waiter exited. Preserve
  all named evidence and negative results, run no other baselines/ablations,
  and inspect the unexercised physical plan and added migration before any
  next GPU candidate. See docs/research/sibling_service_capacity_result_20261028_zh.md.
- Complete sibling follow-up verifies219.512537GB JOIN reuse with1.268613GB
  still unknown;22 of63 observed prefetch JOIN episodes early-start and reuse
  91.893105GB. Full/control restore-wait samples35/18 total2034.011/2662.835ms;
  never expand these differing cohorts. All6 native refusals have free below
  the actual input/growth reserve. Emitted-event reconstruction of74 logical
  donor references finds55 tool/19 JOIN waits; it is not scheduler delivery,
  physical occupancy or the denominator of91 physical-plan attempts.
  Main adds bounded per-attempt reasons and opt-in nested thread-CPU/wall
  sampling every64 root phase calls, without changing scheduling/native guards.
  Shared current sources pass3668 unique cases plus6subtests, retaining3580;
  new CPU manifest covers the sibling observer. Keep prior failed fixture/
  metadata evidence, profiling off in formal config, old control cost unknown,
  no new GPU result and the goal active. See
  docs/research/control_and_join_diagnostics_20261028_zh.md.
- The separate control-plan diagnostic declaration keeps sibling-capacity
  work/policy/native tree unchanged and adds PROFILE_SHARED_PATH=1 only.
  A red check proves arm_environment previously forced it off; main now honors
  validated explicit profiling and checks actual writer-enabled constructor
  timing, preserving default0. Current source-bound CPU passes3683 unique
  cases plus6subtests, retaining3668. Preserve initial red/seed-fixture reports.
  Freeze clean sources before model/position/Full; reuse identical native
  component proof only after tree/operator/group binding, and rerun the
  current unforced model and position smoke. This is diagnosis, not isolated
  optimization or repeated performance acceptance. Tool prefetch stays off.
  See docs/research/control_plan_diagnostics_20261028_zh.md.
- Diagnostic FullR1 now runs frozenfe0ce6b2/nativecdd58a4acbb5, port18681:
  ops/control_plan_diagnostics_20261028_r1.registration.json. Current unforced
  model actually hands off12296tokens and restores donor with zero logprob
  deltas; real GPU runtime observes thread-CPU/wall sampling64. Position24
  exact/session/native-stop checks pass, dispatch increment0.009996ms.
  Preserve its pre-GPU canonical-path refusal and identical-tree rebind.
  Independent startup verifies nice0 watcher774837/server775366/driver776815/
  scheduler775715 and168 exact receipts, not complete work/performance.
  Keep profiling1/tool-prefetch0/frontier0, unchanged work/timers/capacity,
  frozen sources and completeV345 with explicit engine/profiling differences.
  No other baseline or follow-up GPU job is authorized by the CPU plot waiter.
- Main's bounded JOIN recovery now traverses a settled Host-only tail to a
  resident private ancestor. Native session labels live at the verified leaf;
  ancestor labels may be None only with the same single path reference.
  Preserve shared/foreign ownership, Device/Host locks, ongoing load/write,
  pending DMA, consumer liveness, eight donors,64 nodes and200ms backoff.
  Traversal grants no capacity; native demotion and actual free slots remain
  authoritative. The original native CPU red case releases16 ancestor tokens
  after repair, then proves normal consumer H2D allocation4-to20-to4 while
  preserving a hot peer. One source-bound execution passes3705 cases plus6
  subtests, retaining3683 and adding22; all3 constructors/reference inheritance
  remain isolated. Preserve initial/targeted failures under the diagnostic
  named evidence directory. This repair did not run in frozenfe0ce6b2 and
  has no new GPU/performance proof. Its69 recorded nonresident-node checks
  do not establish69 reclaimable ancestor opportunities. See
  docs/design/join_host_tail_ancestor_reclaim_20261028_zh.md.
- DiagnosticFullR1 completes3674 exact requests/145565966 input/549671 output
  tokens and18 normal tails with native stopping, two fresh idle snapshots
  and actual owned exit. Complete named review verifies meanJCT1642.310207s/
  output235.033300token/s versusV3451697.583645s/233.371801token/s:
  3.25601%/0.71195% improvements, insufficient for the shared gate.
  Against preadmission Full, JCT grows11.115563s: critical-chain first-service
  waiting falls14.363240s while service-to-completion grows25.483385s.
  The historical24.981494s tool-successor delta belongs to grouped913e3238,
  not this diagnostic run. Keep both decompositions and all tails distinct.
  Sample64 thread CPU means maintenance0.018531/postphysical0.017721/
  admission0.293551/event0.195873ms; admission max26.846221ms. Last snapshot
  final=false and idle polling contributes. Never expand these measured
  samples to whole-run CPU, exposed GPU idle or historical incremental cost.
  All89 JOIN physical attempts reconcile;69 of73 node checks stop nonresident,
  not69 proven reclaim opportunities. Actual2 handoffs restore3.272245GB;
  all1037 JOIN commands prove227.702637GB reuse,26 of63 observed JOIN events
  early-reuse126.322606GB. All1045 sibling journals/1058 attempts verify;
  258 of260 grant/service identities verify,2 remain unknown. Restore samples
  36/18 total3055.029/2662.835ms are different cohorts. Complete plots are
  opened/checked; GPU and CPU plotting processes have exited. Native engine
  and profiling differ fromV345. Preserve reports/negative results, reuseV345,
  queue no next GPU job by this audit, and keep the goal active. See
  docs/research/control_plan_diagnostics_result_20261028_zh.md.
- Main repairs a confirmed FULL-only publication fallback: the original
  sparse path required both Device leaves and a Mamba LRU, but native(0,)
  has no Mamba index. Only that exact known layout sets Mamba quota0 and
  uses the existing live FULL index. Preserve order,8-item quotas, all
  native candidate checks and unknown/incomplete-hybrid fallback. No policy,
  logical capacity, physical allocation or native engine changes.
  Two real-native red cases previously inspected128/1536 nodes. Current
  3718-case shared CPU plus6subtests retains3705 and adds13; all constructors
  and inherited reference methods verify. Alternating same-tree200-sample
  publication medians at1536/3072 candidates fall642.714-to30.705us and
  1251.369-to35.3795us; unknown layout locally regresses about1.4%, preserved.
  This is not full-controller, GPU or JCT evidence. Use unchanged formal
  sibling-capacity configuration with profiling off for the next separately
  qualified Full; it also includes the prior Host-only-tail repair and is
  not an isolated publication ablation. Freeze and qualify before GPU work,
  reuseV345, retain all normal tails and negative results, keep the goal
  active. See docs/design/full_only_pressure_index_20261028_zh.md.
- Full-only-index candidate now runs frozena5164fe3/nativecdd58a4acbb5,
  port18685: ops/full_only_pressure_index_20261028_r1.registration.json.
  New seven-request unforced model actually hands off12296tokens in8/4
  groups and restores donor with zero maximum logprob deltas; active cleanup
  ends the model parent with-9, not natural exit. Position24 exact/session/
  native-stop checks pass, running24 dispatch increment0.010198ms; actual
  predecessor cleanup precedes Full. live_start.py/.json independently
  verifies nice0 watcher816387/server816848/driver817864/sole GPU816937,
  unchanged complete V230/V311 work/capacity/seed/stopping and153 exact
  completed receipts, not complete performance. Profiling/tool-prefetch/
  priority/frontier restore remain off. Preserve frozen sources and inputs.
  Nice19 CUDA-hidden plotting waiter817706/start738051857 is actually live
  in waiting_complete_full and draws only after full success/owned exit.
  It authorizes no GPU job. Reuse completeV345 with explicit native-engine
  difference, retain all normal tails, keep other baselines/ablations
  deferred and the goal active. See
  docs/research/full_only_pressure_index_start_20261028_zh.md.
- The pressure-index sealed collecting observation now verifies1435 exact
  completions, not complete performance. Its same-prefix physical audit
  reconciles30 attempts/2 native release journals/226652 cumulative tokens.
  The223 Host-only extent traversals are checks, not opportunities or proof
  those releases were caused by the repair. Reference per-agent occupancy
  and budgets remain unknown. Named complete_review/mechanism_review helpers
  require full work, fresh drain, actual exit and completed figures; they
  have not run as a complete comparison. Ten review_precheck CPU boundaries
  pass, including actual live-Full rejection and complete-control acceptance.
  No complete result or additional GPU job is created. Keepa5164fe3 frozen,
  all normal tails, completeV345 and the global goal active.
- Two read-only historical audits now split the old grouped24.981494s
  critical-tool delta further. Other-prefill-only coverage grows11.188698s,
  other-decode-only falls0.121374s, with no cross-phase/uncovered remainder.
  Both arms have464588 positive decode member observations; mean member
  intervals grow24.188944-to-24.754665ms and time-weighted actual batch
  10.193983-to-10.397393. Same-request nonoverlap and all original interval
  unions reconcile. Coarse-bin unmatched changes remain explicit; none of
  these worker intervals identify pure kernels, incremental control CPU or
  causal module effects. They are not currenta5164fe3 performance. See
  docs/research/critical_service_phase_geometry_20261028_zh.md and the two
  named review operators/results under full_only_pressure_index_20261028.
- Pressure-indexFull now completes3674 exact requests/145565966 input/
  549671 output tokens and18 normal tails, native stopping, two fresh idle
  snapshots and actual owned exit. MeanJCT1651.727199s/output235.457746
  improves2.70128%/0.89383% versusV345, insufficient for the clear/repeated
  gate. Against preadmission, JCT grows20.532555s with identical critical
  chains: first-service waiting-9.361696s/service-to-completion+29.911890s.
  Critical-tool other-prefill coverage grows16.683404s, other-decode falls
  0.115054s. These worker spans do not attribute control CPU or pure kernels.
  H2D2735.615312GB/D2H1281.826849GB exceed preadmission20.927840/
  149.461598GB. Two actual8/4 handoffs restore2.776302GB;88 physical plans
  reconcile with2 releases/226652tokens. All708 JOIN copies prove154.898792GB
  reuse;18 of63 observed JOIN events early-reuse62.677844GB. All1062 sibling
  journals/1075 attempts across20 parallel JOINs verify, including13 actual
  multi-member prefill batches;252 of255 grant/session identities verify,
  3 remain unknown. All54 final EOS and4 known guard cases verify. Preserve
  the original same-engine guard-auditor refusal and explicit reviewed-pair
  adaptation; production guard/classifier unchanged.34/27/18 restore samples
  are never expanded. Complete final_index and all3 inspected figures are
  under the named directory; raw CSV bytes are archived before LF formatting.
  GPU/plot processes exited. ReuseV345, preserve all tails/negative results,
  leave other baselines/ablations deferred and the goal active; no next GPU
  run is queued. See docs/research/full_only_pressure_index_result_20261028_zh.md.
- New default-off BELIEFKV_DEMAND_PREFILL_PIPELINE stages one actual native
  prefill batch after its normal PrefillAdder capacity/materialization pass,
  retains all selected members in the native waiting queue, and lets resident
  decode progress during untagged demand H2D. Multiple members/siblings remain
  together; no new agent permit, queue priority, TTL or tool prefetch. Release
  each old request receipt exactly once before native rematch/readmission;
  retain DMA locks/ACKs and publish the native shared-prefix consumer even on
  cancellation. Clear unrelated decode fences only with the existing bounded
  native path proof. Initial cache/pure/scheduler fixture failures are retained.
  shared_r3_cpu passes3839 unique identities plus6subtests, retaining3718 and
  adding121; actual native/Full/acting-decay constructors and control masking
  verify. The old free-page checker stays unchanged and correctly refuses the
  unprojected new tree; an independently tested exact default-off projection
  restores the reviewed parent before applying it. shared_r2 checks passed
  but its assembler missed the launcher online-timing alias; finalr3 uses the
  real launcher environment and binds sources before/after one complete run.
  Native clean commitd3da19c1/tree729aa0caa7a1 and the independent cumulative
  patch are content-bound to those CPU-tested bytes by native_seal.json.
  Native registration, actual installed/executed-state audit, complete staged
  demand/first-service integration, real GPU/cancellation/shared-prefix and
  model/position qualification remain pending. No GPU run or performance
  acceptance is created. Preserve normal tails, cancelled tool prefetch,
  completeV345 and the global goal. See docs/design/native_demand_prefill_pipeline_20261028_zh.md
  and docs/research/native_demand_prefill_pipeline_cpu_20261028_zh.md.
- Native demand service/engine/component-entry integration now passes4060
  unique CPU cases plus6subtests, retaining3839 and adding221 in one complete
  source-bound execution:prefill_pipeline_20261028/service_gpu_r1_cpu.
  Actual native/Full/acting-decay constructors verify installed helper/cache
  binding and baseline isolation. Exact tree729aa0caa7a1/patchc9b3ae50 is
  registered with fresh GPU/model/position requirements. Prefill-only telemetry
  adds attempt/session/generation; stage journals use the same monotonic domain.
  The complete demand auditor retains the old handoff scope, reconciles native
  Host hits/handoff/exact staged ACK bytes, and requires exact later service/
  completed receipts or explicit cancellation. Neither accounting nor CPU tests
  establish reuse or hidden DMA. The four-scene real GPU component entry uses
  finite delayed copies and actual accessors, not model decode or performance;
  its validator passes but real execution remains pending. Freeze sources and
  check resources before it, then independently qualify unforced concurrent
  model progress/output and current position/session/stopping before Full.
  Tool prefetch stays off; reuseV345, retain all tails/negative results, and
  keep the clear/repeated/all-baseline goal active.
- Preserve gpu_components_r1 as pre-scene CUDA_HOME import failure andr2 as
  first-scene delayed-copy shim is_draft TypeError, both from clean7e7e79e9.
  Only the qualifier shim now forwards native optional keywords. Full CPU
  service_gpu_r2 passes4060 plus6subtests with all4060 prior identities and
  actual constructors/source hashes rebound. Freeze that repair before the
  next component attempt; no completed GPU/model/performance proof yet.
- The clean165a331e/native d3da19c1 demand component r3 now actually passes
  all4 GPU scenes atnice0 with historical CUDA13, process exit0 and no GPU
  compute process afterward. Real accessors finish before delayed cold H2D
  ACK; every consumer K/V matches and each scene returns128/257 slots.
  Double-member32-token prefixes reconcile65536bytes, other scenes32768.
  Native fallback and cancellation/shared-prefix layer fences verify.
  Preserve both failed attempts. This is a finite delayed-copy two-layer
  component, not actual model decode, normal-transfer timing, position or
  whole-workload performance. Qualify unforced concurrent resident model
  progress and independent output/logprobs before current position/Full.
  No new complete Full comparison or other baseline was run; the goal remains
  active with tool prefetch off and completeV345 retained.
- The unforced demand-model entry now has normal-copy concurrent resident/
  cold requests with independent references. It declares24576-token prefixes,
  Device57344/Host8GB, running24 and512 resident greedy tokens; no artificial
  DMA delay or replay sampler. The default-off progress probe checks immutable
  ACK only after real native result processing, binds forward/request/clock,
  and requires post-stage batch launch plus positive actual worker output.
  Default off makes no extra CUDA query/log. Launcher now accepts the exact
  demand_prefill_pipeline cumulative patch. model_entry_r2_cpu passes4085
  plus6subtests, retains4060 and adds25, with sources/constructors rebound.
  Freeze and rerun current component scenes, then run the current model entry
  before position/Full. Actual concurrent model and performance remain pending.
- Preserve model r1 at clean55e54416 as failed, despite7 completed requests,
  actual20487-token/2013954048-byte H2D and11 post-stage completed resident
  decode batches while ACK pending. It misses the unchanged24575-token cold
  fixture gate. Raw output follow-up also finds A first token difference at
  ordinal125 and first >.05 logprob delta at50; B8 tokens agree but maxdelta
  .1041944623. Do not call this model qualified or enter Full. The proposed
 24576/24576/32768-token Device61440 fixture retains the original cold gate
  and passes4085+6 CPU again(model_fixture_r1), but numerical cause requires
  a pipeline-off same-original-fixture diagnostic before the next qualifier.
- New --diagnostic-native-demand restores the exact original3x24576/
  Device57344 fixture and disables only the new pipeline. It requires helper
  absent/no staged journal, reports output differences and never qualifies
  the model or performance. The original strict numerical checks remain.
  native_diagnostic_r1_cpu passes4086+6, retains4085 and adds1, with exact
  sources/constructors bound. Freeze and run its same-source components
  before the bounded diagnostic; no complete Full is authorized by CPU alone.
- Native diagnostic at cleand8837321 completes all7 calls with helper absent,
  no staged demand and B4096Device/20487Host. It also violates strict numerical
  checks: A first token difference81(vs125 in pipeline run), B8 equal tokens
  with identical .1041944623 maxlogprobdelta. The read-only native_diagnostic
  review verifies every actual body except real sessions, same source IDs/
  native tree and no beliefkv/patch changes; both owned servers cleaned with
  controlled -9 and no GPU process. Do not qualify either model or infer JCT.
  Components r5 pass on current frozen sources. Next audit prefix-building
  geometry and bounded deterministic math, preserving gates and all failures;
  do not silently enable deterministic math in formal performance settings.
  See docs/research/demand_prefill_model_diagnostic_20261028_zh.md. The overall
  goal remains active; no full/baseline/ablation was started.
- The independent read-only model_progress_review now reconciles original
 20487-token ACK and exact subsequent prefill identity/clock, plus11 distinct
  post-stage forward IDs with actual positive native decode worker deltas.
  The pre-stage launched result is excluded. This confirms model progress
  during normal DMA without requalifying the failed cold/numerical fixture.
- Numerical entry adds explicit --deterministic-numerics and optional
  --reference-build continuation. Deterministic math is fixture-only, has a
  distinct model-qualified status and must observe actual FA3/sampler/cache/
  capacity/running flags. Independent continuation references build the same
  initial8-token histories and compare those too; old cold references remain
  default and gates/failures unchanged. Complete deterministic_entry_r1_cpu
  passes4096+6, retains4086/adds10; focused34 pass. Prepared native basefa5cb52
  plus c9b3ae50 patch reproduces729aa0caa7a1 and92 native CPU tests pass.
  Freeze/rerun current components before actual deterministic model. Do not
  silently use its math flags for formal performance or claim ordinary math
  qualified from it. Goal remains active with tool prefetch off.
- Clean0c5bee90 deterministic model now actually qualifies full24583-token/
 2416607232-byte normal H2D and2 post-stage completed resident decode batches;
  A512/B8 unforced outputs and independent cold references agree with0 maxdelta.
  Actual FA3 deterministic/pytorch flags observed, controlled -9 cleanup/no
  survivors. Same-source ordinary continuation-reference run remains failed:
  A first token difference155; B8 agree/maxdelta .000630020746. Keep both.
  Current-fixture native diagnostic option is explicit/diagnostic-only.
  Formal JSON preserves old config except patch/pipeline/probe0. Runtime now
  observes actual probe bit; formal rejects missing/true and model expects
  actual true. formal_entry_r1_cpu passes4100+6, retains4096/adds4; focused63
  passed before the added diagnostic-option guard. Freeze then run current
  components/native current-fixture warm diagnostic/deterministic model,
  preserving ordinary-math failures, before position/Full. Tool prefetch stays0.
- Current01510ed5 components r7 and deterministic model r2 pass exact24583
  tokens/2416607232bytes,3 real post-stage resident decode completions and
  A512/B8 independent cold outputs/logprobs0. Same-current-fixture native
  ordinary continuation diagnostic also diverges(A210vs pipeline155);
  B8 agree/maxdelta .000630020746, initial histories0, all9 bodies match
  except sessions. Independent current_model_review binds sources/AST/ACK/
  service/owned exit and explicitly leaves ordinary long-greedy equivalence
  false. It permits position qualification, not performance claims.
- Position r1 now passes24 exact token/session/native-stop checks, all24
  native stops; running24 dispatch wall increment .009628375ms. Actual watcher
 967144/server cleanup precedes Full's server. Full now runs frozen01510ed5/
  native729aa0caa7a1 at ops/demand_prefill_pipeline_20261028_r1.registration.json,
  port18693. Independent live_start verifies nice0 watcher969993/server970533/
  driver971947/sole GPU970873, installed pipeline1, probe/profile/tool-prefetch/
  frontier0 and original non-deterministic FA3/flashinfer. It seals170 exact
  receipts, not full3674/performance. Preserve initial no-receipt refusal.
  Keep all frozen inputs/sources/tails and completeV345 with disclosed engine
  difference. No other baseline/ablation/follow-up GPU is queued; goal active.
  See docs/research/native_demand_pipeline_start_20261028_zh.md.
- Demand-pipeline complete_review/mechanism/known_guard/final_index helpers now
  live only in its named evidence directory. Actual CPU precheck passes13
  boundaries: live Full rejected, completeV345/preadmission accepted, common
  work/timing/seeds match, and wrong engine/helper/probe/profile/prefetch/group
  rejected. These observer additions do not change frozen01510ed5 or its
  prior4100-case manifest. Complete review requires actual owned exit/fresh
  drain before plots, exact registered native pair, original FA3/flashinfer,
  staged-demand ACK/bytes/member-service and known EOS guards. Retain legacy
  unexplained-byte scope and reconcile it explicitly as staged demand; do not
  relabel it as speculative prefetch or cache hits. No complete performance
  result or extra GPU job is created by these CPU checks. See
  docs/research/demand_pipeline_complete_review_plan_20261028_zh.md.
- Demand-pipeline FullR1 now completes3674 exact requests/145565966 input/
  549671 output tokens and18 normal tails with native stopping, two fresh
  idle snapshots and actual owned exit. MeanJCT1613.659476s/output239.544631
  improves4.94374%/2.64506% versusV345; against preadmission1631.194643/
  238.499008 the gains are1.07499%/.43842%. Keep modest/single-run and
  native-engine/policy differences explicit; no clear/repeated/all-baseline
  acceptance. All18 beatV345, but game_simulation only .015756s;13 beat
  preadmission and5 regress, including fintech+86.989850/ml+83.186084/
  game_simulation+76.244366s. Full worker output343.922082 is below
  preadmission346.325953 despite better end-to-end results.
- Complete demand audit reconciles2541578747904 untagged bytes into
  161633304576 request Host-hit +1797783552 handoff +2378147659776 staged
  demand bytes, remainder0. All466 stages/749 member associations verify
  native ACK and subsequent exact service. Do not call them speculative
  prefetch or infer hidden DMA. All1050 sibling journals/1071 attempts
  verify, with21 multi-member journals/20 distinct native forwards;250/253 grant-service associations
  verify and3 remain unknown/unobserved. All57 final EOS/4 known guards
  pass. Only3 restore samples remain; never compare their26.645600ms as
  a whole-run reduction against different old cohorts.
- Current web_blog critical chain changes107-to117 requests; other17 match.
  Keep the current2983 critical-tool cohort distinct from the original2973.
  Fixed_cohort_review binds the exact old2973/467561 output tokens: service
  delta versus preadmission is+2.166674s(prefill-2.968272/decode+9.017642/
  outside-3.882696), compared with old grouped+24.981494s. Actual full-chain
  waiting-12.007540/service-6.152920/event+.625293 givesJCT-17.535167s.
  None measures exposed historical control CPU. Original failed CPU paths
  and newer-validator refusal remain in initial_review_failures.json.
  Each complete arm now validates its own exact frozen observer; five actual
  compatibility checks pass without weakening the new sibling validator.
  All complete reports, original-byte archives and3 inspected figures live
  under prefill_pipeline_20261028/demand_prefill_pipeline_20261028_r1.
  No next GPU job is registered by this audit. Keep other baselines/ablations
  deferred and the global goal active. See
  docs/research/demand_pipeline_complete_result_20261028_zh.md.
- Complete batch_cost_review retains all raw workers and matched decode
  geometry: against preadmission, min32-support equal-bin means grow
  16.734878-to16.877008ms(+.84930%); versusV345 they fall .17472%.
  Prefill workers425.946088-to373.421013s/decode1576.540814-to1587.560174s.
  Sparse prefill CPU selection57.328213vs53.472004ms and result processing
  51.147408vs63.483543ms are different populations; staging moves work outside
  eventual prefill samples. No full/exposed incremental CPU is measured.
  Sibling_unique_review preserves the old mislabeled21-journal summary while
  independently binding20 distinct native forward/sample IDs. Do not call21
  journals21 independent batches or change old reports.
- Main now repairs stale running batch_is_full after staged native demand ACK:
  clear it only at completed ACK to request fresh native admission, with no
  permit/priority/capacity credit. Real native scheduler/Req/PrefillAdder/
  tree/allocator red test fails at missing post-ACK batch construction;
  the first empty-receipt fixture failure stays separate. Fixed targeted42
  and complete4102+6 CPU pass, retaining4100/add2 and real constructors/
  source manifests. New zero-slot pressure still rejects after ACK.
  Native remainsd3da19c1/tree729aa0caa7a1; current repair has no GPU/model/
  performance evidence. R1 remains frozen01510ed5. Keep all failures.
- Complete R1/Preadmission copy-envelope analysis retains all worker data:
  steady same-member matched ratio1.028224, steady outside all recorded
  submit-to-ACK envelopes .998220;13333 identical singleton ordinals across
  131 requests have5.583327/5.583465ms means. These conditional envelopes
  are not actual DMA times or causal control/transfer attribution. Default
  native JIT quota2 is source evidence, not actual runtime kernel/SM proof.
  The separate repeat entry reuses exactly R1's frozen source/config/engine,
  changes metadata only, and excludes main's ACK retry repair. Nine metadata
  CPU checks and actual complete-source/owned-exit entry check pass; no
  repeated performance result yet. See
  docs/research/demand_restore_readmission_retry_20261028_zh.md.
- Source-identical demand-pipeline R2 now actually runs through the original
  frozen01510ed5 supervisor/app and729aa0caa7a1 native, port18696:
  ops/demand_prefill_pipeline_20261028_r2.registration.json. Only six
  registration metadata fields change; all qualified work/timing/capacity/
  seeds/config/stopping and original external inputs remain identical.
  It excludes main5cf3bb8b's ACK retry repair; never call R2 proof for that
  repair or treat changed configs as repetition. Independent repeat_live_start
  verifies nice0 watcher1008187/server1008803/driver1010120/sole GPU1009132,
  actual installed pipeline1/probe/profile/tool-prefetch/frontier0 and
  original non-deterministic FA3/flashinfer. It seals171 exact completed
  receipts, not full3674/performance. Preserve this frozen repeat and
  current-main4102+6 CPU repair evidence separately. No other baseline/
  ablation/new-policy GPU is queued. Keep the full goal active and wait
  for actual completion/cleanup before complete repeated-metric review.
  See docs/research/demand_pipeline_repeat_start_20261028_zh.md.
- R2 completion reader now has an actual CPU precheck accepting complete R1
  and rejecting live R2, with metadata-only identity and no completed artifacts.
  Run it only after real completion/cleanup; demand ACK/service and JOIN/EOS
  audits remain separate. Keep its named reader/precheck distinct from the
  immutable R1 operators and registered inputs. The ACK retry repair has a
  separate clean5cf3bb8b checkout; exact CPU-manifest reuse is source evidence,
  not fresh GPU/model/position or performance qualification. Do not launch a
  competing GPU job while the frozen R2 is live.
- repeat_mechanism_review.py composes the unchanged demand ACK/service, JOIN
  grant, sibling and final-EOS/known-case classifiers after completed R2.
  It separately counts native forwards and preserves unknown associations;
  syntax checks alone are not complete mechanism evidence. Run sealed
  prefetch accounting first and retain all original classifiers and reports.
- Source-identical Full R2 now completes3674 requests/145565966 input/
  549671 output and18 normal tails with two fresh idle snapshots and actual
  owned exit. MeanJCT1625.203855/output238.306858 improves4.26370%/2.11468%
  versus the same single V345; R1 was4.94374%/2.64506%. Keep modest repeat
  gains and the single-control/engine limitation explicit. Against preadmission,
  R2 JCT improves5.990789s but output regresses.08057%; do not discard it.
  All18 actual critical chains match preadmission; full waiting-7.479293s/
  service+1.543954s. Fixed old2973 tool successors retain service+7.109160s,
  versus R1+2.166674/old grouped+24.981494s; do not mix these populations.
  All465 demand stages/720 member associations prove exact ACK/later service,
  2375979270144bytes and untagged byte remainder0. Handoff0 despite8/4 enabled.
  All1049 sibling journals/1080 attempts verify;31 multi-member journals
  represent30 native forwards.239/241 grant associations verify,2 remain
  unknown. All54 final EOS/four known guards pass. Keep tool prefetch0,
  all normal tails and negative results, other baselines/ablations deferred,
  and the overall goal active. See demand_pipeline_repeat_result_20261028_zh.md.
- Separate clean5cf3bb8b ACK repair now passes all4 current real GPU components
  and deterministic unforced model:24583tokens/2416607232bytes normal H2D,
  exact ACK/later prefill,two post-stage resident decode completions before
  ACK,A512/B8 independent cold outputs/logprobs0. Independent readmission_gpu_review
  rechecks raw bodies,source/CPU4102,native bytes/progress and actual server
  exit/no GPU process. Actual retry_native_capacity_check=false in this model;
  its stale-full branch remains real-native CPU evidence, not observed GPU
  activation or a measured performance gain. Preserve ordinary-math failures.
  New repair position/full performance remains pending; R2 excludes the repair.
- The generic three-arm plot CLI correctly refuses engine_tree differences.
  repeat_control_plots reproduces that refusal, then permits only the exact
  already-reviewed729aa0caa7a1/11b84f7c3198 pair with every other contract
  equal and explicit different-engine titles. Both complete trend/distribution
  figures are inspected; the generic matcher and original results stay intact.
  The repaired model archive preserves20 source-bound inputs byte-for-byte
  and excludes environment; no new Full performance run is implied.
- The ACK repair's fresh position entry now verifies all1815 manifest files,
  CPU4102/current GPU-model proof and completed R2 exit before registration.
  Prepared native sources are rebound only to the frozen5cf3bb8b patch path;
  exact729aa0caa7a1/92-case native source reuse is not a new CPU execution.
  Fresh position watcher1035591/server1035866/sole GPU1036196 are verified
  nice0 at ops/demand_prefill_ack_retry_position_20261028_r1.registration.json,
  port18698. It must finish24 exact token/session/stop checks and1/8/24
  calibration before registering only Full at demand_prefill_ack_retry_20261028_r1.
  Same formal config/work/tools/capacities/seeds/stopping/tails as R1/R2;
  ordinary non-deterministic FA3/flashinfer stays unchanged. Actual Full
  launch/ACK retry activation and complete performance remain pending.
  See docs/research/demand_restore_readmission_start_20261028_zh.md.
- Fresh repaired-source position now passes24 exact token/session/native-stop
  calls, all24 native stops;1/8/24 wall dispatch increments are
  .009288121/.003773884/.010093582ms. Position watcher/server actual exit is
  independently verified. Full runs frozen5cf3bb8b/native729aa0caa7a1
  at ops/demand_prefill_ack_retry_20261028_r1.registration.json,port18698.
  Independent readmission_live_start verifies nice0 watcher1038516/server1039033/
  driver1040363/sole GPU1039393 and230 exact completed receipts, actual
  pipeline1/probe/profile/tool-prefetch/priority/frontier0 and8/4 handoff.
  Ordinary FA3/flashinfer stays unchanged. Last observed retry counter0 is
  an early snapshot, not a complete opportunity or performance result.
  Preserve R1/R2 and the running frozen repair; no competing GPU job or
  other baseline/ablation is queued. Goal remains active.
- readmission_complete_review now has an actual CPU precheck accepting complete
  R1/R2 with owned exit and rejecting the currently live repaired Full. Same
  work/config and the sole changed runtime file demand_prefill_pipeline.py
  are verified. No complete repair result/figures are created. At completion
  require full work/drain/exit and separately report actual ACK retry journals
  versus the possibly older counter snapshot. Do not require positive gains
  or nonzero retry events to retain the completed run. Current demand ACK/
  service and JOIN/EOS/sibling mechanism audits remain separate requirements.
- readmission_mechanism_review now composes unchanged exact native demand ACK/
  service, physical-release accounting, JOIN grants, sibling and final-EOS/
  known-case classifiers. It retains unknown associations and separates
  multi-member journals/native forwards; zero retries remain valid evidence.
  Syntax only is checked before the running Full completes. After actual
  completion/cleanup run complete review, then sealed prefetch, then mechanism
  review sequentially so independent readers never race to create the same
  result directory. It does not alter frozen5cf3bb8b or qualify new performance.
- Repaired Full now completes3674 exact requests/145565966 input/549671 output,
  18 normal tails,two fresh idle and actual owned exit. MeanJCT1617.896789/
  output238.405436 improves4.69413%/2.15692% versus the same single V345.
  R1/R2 gains remain4.94374%/2.64506% and4.26370%/2.11468%; preserve modest
  repeated positive directions and different-engine/single-control limits.
  Preadmission JCT improves13.297855s but output regresses.03923%. Workflow
  max ready-output gap mean83.807889s exceeds control73.449107s; keep it.
  All445 ACK-ready journals have retry=false/counter0; do not credit this
  inactive repair. All447 stages/716 associations prove exact ACK/service,
  2274984198144bytes and native untagged remainder0. Eight8/4 handoffs restore
  4787699712bytes; one physical JOIN release44467tokens reconciles. All1056
  sibling journals/1090 attempts verify;34 multi-member journals=33 native
  forwards.235/237 grant associations verify,two remain unknown. All54 final
  EOS/four known guards pass. Fixed old2973 tool service delta-9.853128s;
  actual full waiting+1.350547/service-15.237101/other+.588699s stays separate.
  Complete reports and three inspected figures belong to the named repair.
  No further identical Full is queued for the inactive branch. Move next to
  remaining-baseline CPU adaptation/qualification on the same complete work,
  without recapturing native or rerunning V345. KVFlow's current plain-request
  guard rejects nonempty sessions actually present in this LoCoBench source;
  qualify a real session-compatible port, never just disable the contract.
  Agentix remains qualification-only, not a formal enabled arm. Keep the
  overall goal active and all-baseline acceptance unproven. See
  docs/research/demand_restore_readmission_result_20261028_zh.md.
- KVFlow now accepts only full-prompt native radix identities with explicit
  full_prompt_replay_guaranteed=true and a current open native generation.
  Conversation reconstruction, embeddings and positional overrides remain
  rejected before context mutation. Token keys/namespaces, observed graph,
  completed-input prefix learning and native/paper policies remain unchanged.
  New22 session cases and source-bound379 paper CPU cases pass on clean
  native11b84f7c3198; all1868 sources match before/after. Preserve fixture/red
  and prepared-HEAD binding failures. The generic prefix GPU qualifier adds
  exact session ingress/history, poisoned-page restore, split-peer and capacity
  proofs; its new scene has not run. Freeze/qualify it before a formal baseline.
  No new Full/V345/native run or performance result follows from this port.
  Keep tool prefetch off, same completeV230/V311 and the goal active. See
  docs/research/kvflow_radix_session_port_20261028_zh.md.
- Frozenf9f105bd now actually passes the session-aware paper prefix GPU
  qualifier on exact common11b84f7c3198. All23 physical proof fields pass;
  total native ACKs areD2H78/H2D54 tokens, including12/12 in the distinct
  session scene, with25 runtime-reconciled paper commits. Poisoned-page
  all-layer bytes, native session ownership, split peer and128-slot return
  verify. The source-bound entry checks379 CPU/1868 files, current hold,
  previous owned exit and exact prepared native patch before/after. Actual
  child exits0 and its GPU context is absent; frozen checkout stays clean.
  This small two-layer component is not LLM, complete baseline or performance
  evidence. Next bind this paper-prefix prerequisite into the LoCoBench causal
  supervisor, whose existing path lacks the AgentX supervisor's qualification
  hook, then qualify/execute formal KVFlow. No new Full/native/V345 run is
  implied; keep same complete work, all tails and the overall goal active.
- The causal LoCoBench supervisor now runs the existing current-source paper
  prefix qualifier before KVFlow/TokenCake model startup; TokenCake additionally
  runs its lifecycle qualifier. Only reviewed common11b84f7c3198 is accepted
  by this new entry. Preserve component owner-list identity across pulse so
  subsequent child registrations remain cleanable; a real watch red/fixed case
  proves failure before model setup and both owned children in cleanup.
  One merged source-bound CPU run passes726 unique cases with no skips/failures,
  retains all379 prior cases and binds1871 source files before/after. Formal
  KVFlow configuration is configs/baselines/kvflow_locobench_v230_v311_20261028.json;
  its real constructor verifies Full policies off, same18/running24.
  Preserve the fixture/red reports. register_formal.py is the prepared separate
  entry: freeze its committed application, verify current hold/resources and
  actual predecessor exit, then register only KVFlow on completeV230/V311.
  Reuse unchanged common-engine sampling proof only after exact module/native
  source checks; its fresh paper components still run before the model.
  No formal result is implied yet and the all-baseline goal remains active.
- KVFlow now actually runs frozen6dc4b892/common11b84f7c3198 on port18701:
  ops/kvflow_locobench_v230_v311_20261028_r1.registration.json.
  Actual current-source prefix GPU qualification passes before model startup.
  Independent observe_live.py/live_start.json verifies nice0 watcher1083607,
  server1084292,driver1085740 and sole owned GPU scheduler1084624 by live
  PID/start ticks/argv/ownership. The sealed9 completed receipts are exact
  and actual27 native session registrations prove ingress; common9 work/
  capacity/native fields and full timing metadata matchV345. These are early
  prefixes, not complete work, universal model equivalence or performance.
  Formal CPU726/1871 files and older unchanged native726 source reuse stay
  distinct; original24 position/sampler proof is reused only with exact
  unchanged modules/native sampler. No Full/native/V345 rerun or next GPU
  job is queued. Preserve this frozen run and wait for its actual exit/owned
  cleanup before other GPU work; continue TokenCake session qualification and
  remaining Agentix CPU adaptation meanwhile. All normal tails and global
  all-baseline acceptance remain pending. See
  docs/research/kvflow_locobench_start_20261028_zh.md.
- TokenCake CPU session ingress now explicitly uses real native Req and
  PrefillAdder, current session generations and null namespaces. Four new
  cases verify native admission, shared capacity once,4/2/1/1 upload and
  early-H2D return with4 native-demand tokens and real lock handoff.
  Production TokenCake policy/capacity/transfer code remains unchanged.
  Preserve r1/r2 fixture failures: missing real receipt, wrong lease timing
  and confusing stale ID strings with live manager ownership. Final60-case
  focused and source-bound742-case merged CPU executions pass; all726 prior
  identities retained,1873 sources unchanged, common native11b84f7c3198.
  GPU qualifier keeps six original plain scenes and adds six actual native
  session scenes with generation/source-anchor proof; validator requires
  each complete group. These new scenes have not run. Freeze before any GPU
  execution, after actual KVFlow exit and completed owned cleanup/current
  hold/resources. Its independent live prefix now seals2274 exact completions;
  unknown budgets stay null and no partial JCT/performance claim is made.
  Keep frozen6dc4b892 and full work/tails unchanged, no native/V345 rerun or
  ablation, and the overall goal active. See
  docs/research/tokencake_radix_session_20261028_zh.md.
- KVFlow now completes the exact3674-request V230/V311 population with fresh
  drain and actual owned exit: meanJCT1739.626663s/output237.259279token/s.
  Latest repaired Full is6.99747% lower JCT but only0.48308% higher output;
  KVFlow has better workflow first-output and ready-gap metrics. Retain those
  tradeoffs and the inspected matched figures; all-baseline acceptance is pending.
- Preserve TokenCake formalR1's zero-completion native admission-hook failure.
  Common11b84 reports admission but lacks its sequential guard and retraction
  bindings. Direct adapter/GPU components did not exercise that scheduler loop.
  Native7424373/tree131d175450fe2b74d31a0508c2e13c55a3750a56 adds only the
  original optional TokenCake guard/victim/before-release hooks to common.
  Its separate canonical patch/flavor, exact AST projection and812-case current
  CPU binding do not yet qualify GPU/model/performance. Future TokenCake
  component checks execute actual native scheduler loops and reject old common
  before GPU startup. Preserve old configurations/failures and qualify the new
  exact tree before a distinct complete retry; never bypass the capacity guard.
  See docs/research/tokencake_native_hooks_20261028_zh.md.
- Frozen8808c1ac now passes all twelve actual native-loop GPU TokenCake scenes
  on exact131d tree, exit0 and completed owned cleanup. Each plain/session
  group retains original bytes/ACK/consumer/capacity proofs and actual2/3/4
  loop counts. Initial proc argv is empty; only spawned PID/report ownership
  and actual exit are proved. Fresh24-request model/position and sampler
  qualification still precede a distinct complete TokenCake retry. Preserve
  the failed common-engine formalR1 and reuse completeV345/KVFlow unchanged.
- New-hook TokenCake position/model smoke now passes24 exact/session/native-stop
  requests, all24 native stops, then running1/8/24 sampling calibration. Complete
  TokenCake now runs frozen22caa011/native131d at18705:
  ops/tokencake_locobench_native_hooks_20261028_r1.registration.json.
  Independent live_start verifies nice0 watcher1122651/server1123520/
  driver1124738/sole GPU1123824, actual seven parameters, fresh native-loop
  components and318 exact completed receipts. Keep full work/performance pending,
  preserve the failed original run, and do not overlap another GPU experiment.
- Main-only Agentix full-prompt radix-session support preserves original call/
  token/sampler/namespace ownership and validates open native generations before
  registration, capacity and checkpoint release. Its actual752-case CPU run
  passes on originalc489 native, with copy/executor stand-ins. Preserve the
  strict retention refusal:686 of690 literal prior IDs remain; four historical
  cases became eight observed/inferred parameter cases, explicitly reviewed.
  This is not GPU/model/finite-pressure qualification or formal Agentix enablement,
  and it is absent from frozen TokenCake. See
  docs/research/agentix_radix_sessions_20261028_zh.md.
- Agentix byte/pressure qualifiers now use the actual PrefillAdder lock and
  native alloc_for_extend consumer before releasing paused Host ownership.
  Plain and explicit native-radix modes remain separate; byte-only fixtures
  return the uncomputed one-token extension without claiming a model forward.
  One source-bound762-case CPU run retains every prior752 literal identity;
  copy/executor stand-ins, real GPU/model and formal Agentix remain pending.
  The new TokenCake complete reader's actual precheck accepts the three
  completed controls and rejects its live2089-receipt prefix. Keep frozen
  TokenCake22caa011/131d unchanged and do not overlap GPU work.
- Native-hook TokenCake failed after2922 exact receipts, with a scheduler
  capacity-path ValueError and197 final path-depth refusals; socket failure
  is secondary. Actual owned cleanup/exit is verified. Preserve all raw work,
  forbid complete performance claims, and retain the dependent Agentix
  waiter's stop without GPU children. Its first nested-path import failure
  and the verified replacement8192b16e CPU waiter are separately preserved.
- Main TokenCake now avoids critical waiting probes when no upload is ready
  and accounts fragmented native prefixes with one context-bounded spatial
  walk, keeping original identity/loading/namespace guards and optional DMA
  bounds. One821-case CPU execution retains all812 prior identities with
  1879 unchanged sources; preserve initial red/fixture failures. No current
  GPU/model or complete performance is established for these fixes.
  The separately committed run_components_after_cleanup entry may execute
  only the four already-frozen762-source Agentix byte/pressure scenes after
  independently checking actual failed-predecessor exit, cleanup, hold and
  resources. It does not release the failed full comparison as successful
  or enable formal Agentix. Keep the global goal active.
- The independent Agentix r3 actually passes all four native GPU byte/pressure
  scenes from8192b16e/c489, with observed child argv, exit0, complete cleanup
  and no GPU context afterward. Both transports use real native admission;
  model preemption/resumption, full-model pressure and formal Agentix remain
  unqualified. The current821-source TokenCake retry entry reruns twelve
  physical scenes, then fresh24-request model/position and1/8/24 sampling
  before its distinct complete18707 arm. Preserve the failed2922 prefix,
  parameters/work/timing/tails and completed Full/KVFlow/V345 controls.
- Current TokenCake twelve-scene GPU and fresh24-request model/position pass
  fromd9f795b7/131d, with24 native stops and1/8/24 sampling. The new full
  repaired arm is actually running at18707, registration
  ops/tokencake_fragmented_capacity_20261029_r1.registration.json.
  Independent fragmented_retry_20261029/live_start.json verifies nice0
  watcher1159588/server1160394/driver1161949/sole GPU1160723 and the first3
  exact receipts, both installed repairs, seven parameters and all shared
  work/timing/capacity/seeds. Preserve the running frozen sources and failed
  2922 prefix. Explicit complete-reader paths retain original defaults and
  reject the live arm; no complete performance follows. No competing GPU job
  is queued. Agentix model/formal work and the global goal remain incomplete.
- The 2026-10-29 prefill/arrival CPU candidate passes4189 unique cases retaining
  all4102 prior identities, plus6 subtests, bound to1828 inputs. Initial600
  evidence is retained. Frozen completed Full R1/R2/ACK journals have2/0/8
  handoffs; all9/5/3 native refusal snapshots lack input/growth free-page reserve.
  Earlier unjournalled returns remain unknown. Keep handoff8/4 and survival.75;
  do not remove locks/reserves to force activity. The quoted68.55% is the
  time-weighted non-evictable KV-pool fraction, not measured GPU utilization.
  New seeded Poisson arrivals change only root releases and count JCT from
  scheduled arrival, retaining driver lag and rejecting idle compression.
  All methods must use identical tables; burst controls are not Poisson controls.
  Native2048/1024/mixed profiles require fresh actual parameter/model checks.
  Mixed telemetry includes native decoding_reqs and all-mode output conservation,
  with the whole mixed interval charged once, never added to prefill time.
  Demand staging now supports the first chunk before row allocation; existing
  row-owning chunk continuations stay native. GPU/model/performance validation
  for these changes is pending; preserve current frozen TokenCake and do not
  overlap it. No new formal GPU job is registered by this CPU work. See
  docs/research/prefill_arrivals_and_handoff_20261029_zh.md.
- The latest user order defers new arrival/prefill GPU experiments until both
  TokenCake and Agentix complete their full formal workloads and actual owned
  cleanup. Component/model qualification alone is not Agentix completion.
  Use only Poisson rate0.5 workflow/s with independent arrival seed21 afterward;
  defer0.2/0.35 and do not queue an arrival-rate sweep. Preserve historical
  CPU-bound0.35 candidates and all generated arrival tables. Active pending
  candidates are in experiments/reports/prefill_arrival_20261029/rate05_after_baselines/,
  with chunk2048/mixed-off first and other prefill profiles conditional on its
  results. All methods retain identical arrivals, work and complete JCT clocks.
- The latest 2026-10-29 user order supersedes the preceding baseline/arrival
  queue: skip further TokenCake reproduction, preserving both failed complete
  attempts and their cleanup. Run Agentix next. Its four real GPU byte scenes
  do not qualify actual model preemption/resumption or formal performance;
  finish those checks before a complete Agentix benchmark. Explicitly allowing
  a failed predecessor requires its terminal failure and actual owned exit,
  never relabelling it successful. New arrival trials remain deferred until
  Agentix completes; rate0.5 is now under reconsideration, not an automatic
  launch instruction. Choose rate/population from observed capacity and useful
  memory pressure; total workflows and simultaneous workflows are distinct.
  Keep every future naturally captured root from a different original project.
- Agentix 98a19139380c/c489 now passes782 source-bound CPU cases, four actual
  GPU byte/pressure scenes and all six unforced real-model calls. Preserve the
  earlier startup/session/socket/untagged-warmup failures. The warmup override
  avoids unregistered native startup calls; the logprob fix validates the actual
  protected prompt ancestor while retaining native suffix replay, locks and
  sampler/token identity. Real45 pauses/resumptions and414 executor intervals
  settle; all three token vectors and logprobs match serial references exactly.
  Raw review, fresh idle and actual owned exit are under
  experiments/reports/agentix_locobench_model_20261029/logprob_prefix_cycle_r1/.
  pressure_model_r1 also passes all six real calls with227 native pauses/resumes,
  identical token/logprob vectors and actual owned exit. Its91 backing drain
  rounds are control observations, not independent physical Host exhaustions.
  Neither fixture is a full baseline. Formal parameters, ordinary sampler/batch24 and complete
  V230/V311 still require qualification; never promote the tiny fixture quanta.
  No TokenCake retry, extra native/V345 run or arrival-rate sweep is authorized
  by these component/model results. Offline sizing recommends initial Poisson
  .008 workflow/s with48 distinct original projects (36 for shorter calibration);
  only18 currently have the matched complete source. This recommendation is
  not verified stationary capacity or a GPU arrival launch registration.
- Agentix now has a separate common-engine port25155f0701c4 with only two
  native files changed (9 insertions/2 deletions): constructor scheduler attach
  and default-empty checks before decode retraction/abort release. Exact AST
  projection restores common11b84; compute/copy/cache/sampler/fences and native
  victim order stay identical. Preserve legacyc489 and its actual GPU/model
  evidence; do not inherit them as qualification for the new native tree.
  The focused24-case CPU report passes, with full retained-population CPU and
  four GPU scenes plus roomy/pressure models still pending. Formal Agentix is
  still disabled; its parameters and complete3674 work remain to be qualified.
  Common source entry is experiments/reports/agentix_common_engine_20261029/.
  Keep TokenCake skipped and do not queue arrival/prefill experiments before
  complete Agentix. Latest arrival advice is a short18-workflow trial with10
  initial and8 subsequent Poisson arrivals at mean120s;24 is conditional,
  not36/48 by default, not verified stationary capacity or an active GPU plan.
- Common Agentix frozenf8d62e1d5277/native25155 now passes1124 unique CPU cases,
  retaining all782 Agentix and726 causal-control IDs (1109 in their union),
  with1889 unchanged inputs; all four actual GPU byte/pressure scenes pass.
  Preserve the pre-import nested-worktree failure and the roomy startup refusal
  on the existing18712 event socket; neither launched a model. The independent
  fresh-port roomy/pressure runs complete all12 model calls with45/227 actual
  pauses/resumptions and exactly equal greedy tokens/logprobs. Raw request/
  response/source/drain/owned-exit reviews are model_review_r2.json in
  experiments/reports/agentix_common_engine_20261029/{roomy,pressure}_fresh_port_r1/.
  Pressure91 drain rounds are control observations, not91 independent Host
  exhaustions. Formal policy is separately declared with10/100s service bands,
  .5/1/2s quanta,beta10,N16,context262144/running24/growth100; its11 CPU cases
  pass but author defaults are explicitly unverified. Do not promote fixtures
  to full-workload or formal-parameter qualification. RUNTIME_ARMS still lacks
  formal Agentix; next bind its factory, root-workflow identity, complete-call
  accounting and fresh batch24/position checks before complete3674 comparison.
  No GPU job remains after these two owned model exits; do not restart
  TokenCake or launch arrival/prefill work before complete formal Agentix.
- The latest user questions120s interarrivals for useful KV pressure. Actual
  complete Full/Thunder events show childP25=90.72/93.87s,P50=159.34/163.50s
  and29/27 of92 children return within120s. Child return does not end its root;
  burst first root ends970.24/1058.07s, which is not a new-arrival prediction.
  Supersede the prior warm-start advice with a finite short trial: same18
  different-project workflows,10 initially,8 subsequent Poisson arrivals at
  mean60s,seed21,running/decode24. Seeded last arrival431.72s/max gap98.97s,
  versus863.43s/197.94s for120s. No new workflow admission gate or larger
  population is introduced. This is offline advice, not an implemented mixed
  arrival contract or GPU registration; Agentix formal remains first. Do not
  call the1/min finite load stationary or its7.2min injection long-term steady
  pressure. Source-bound CPU evidence is arrival_capacity_20261029/
  warm_start_reconsideration.json; retain older arrival analyses and all tails.
- Latest user cancels Agentix reproduction without verified author source and
  explicitly authorizes the new-workload ThunderAgent control in parallel
  with our optimization. This supersedes all Agentix-first GPU prerequisites.
  Preserve Agentix/TokenCake code, qualifications, failures and raw evidence;
  neither formal baseline is now queued. Implement and qualify the18-project
  warm start (10 at0,8 Poisson arrivals mean60s,seed21); original requests,
  dependencies,931 categorized execute timers, capacities and normal tails stay
  unchanged. Run only acting-decay ThunderAgent on common11b84,4096 prefill,
  mixed off,running/decode24. Frozen baseline GPU work and local CPU changes
  may overlap; never overlap another GPU experiment or modify frozen sources.
  Use experiments/reports/warm_start_thunder_20261029/ for named evidence.
- The authorized warm-start acting-decay ThunderAgent control now runs from
  frozen33a34b61/common11b84 on18851, registration
  ops/warm_start_thunder_20261029_r1.registration.json. Actual bound CPU883
  retains every prior726 identity, no failures/skips. Independent19:08:17
  Asia/Shanghai live_start verifies nice0 watcher1270225/server1270700/
  sole GPU1271085 and172 exact completed receipts, plus15 delivered arrivals
  including the10 initial roots. These are live prefixes, not complete JCT,
  throughput or pressure qualification. Preserve its frozen sources and sole
  GPU ownership while developing predictive on CPU; Agentix and TokenCake
  remain deferred, no competing GPU job is queued. All comparison methods must
  use the same warm-start manifest; burst controls are historical only.
- Parallel predictive CPU work adds default-off
  BELIEFKV_LAYER_HANDOFF_FREE_EXTENT on nativec5ad39a1/tree434c35dd,
  based ond3da19c1/tree729aa0. Combine one private donor with slots actually
  allocated from free capacity after the full input/growth reserve; never
  credit future D2H release, shared pages or ready peers. Keep8/4 grouping,
  writer/Host/Device/ACK guards and the original path when off. Paper controls
  clear the flag and attach rejects old native capability. CPU4694+6subtests
  retains all4627 Full/control union IDs with1910 unchanged sources; preserve
  the initial fixture allocation failure. Exact five-file native delta and
  reverse-apply seal are in layer_free_extent_20261029. This is CPU ownership/
  event-stand-in evidence, not GPU/model/performance; formal native whitelist
  is deliberately unchanged. Qualify real bytes/model after actual Thunder
  exit/owned cleanup before any complete candidate. No second GPU job queued.
  warm_start_thunder_20261029/live_after_cpu.json independently rechecks the
  frozen control with1776 exact receipts/all18 arrivals and sole owned GPU.
  Keep the global optimization goal active and all complete-work results pending.
- Warm-start ThunderAgent now completes3674 exact requests, original exit0 and
  actual owned cleanup/fresh idle drain verified. MeanJCT1599.890513s,
  makespan2323.047855s,output236.616305token/s. Its standalone complete reader
  and decode evidence remain in warm_start_thunder_20261029; never use burst
  Full as a matched JCT control. Frozen1fe85898 CPU4712+6 passes after supplying
  the two legacy AST tests' ignored third_party dependency from the same clean
  C5 native tree; retain the missing-dependency failures and unchanged tests.
  Native103 and all four real17-layer8/4 GPU components pass. New24+8-to32
  scenes preserve4 growth slots and every byte/reader/ACK dependency.
  ModelR1 remains failed: it actually handed off12296-to12296 withzero new
  slots, correctly failing coverage. Preserve report/cleanup and only change
  the numerical fixture to add a normal6144-token A prefix before its16K
  continuation. Require positive actual additional slots and independent
  output/logprob equivalence. The exact C5 cumulative patch is now explicitly
  selectable for qualification; model/position/sampling and complete Full
  remain unqualified. No new full GPU comparison is registered yet.
- ModelR2 actually exercises10247 donor+2049 newly allocated slots restoring
  12296 tokens, with8 input/24 growth reserve. B's8 greedy tokens/logprobs
  exactly match; A's8 tokens match but one logprob differs.186333701, so keep
  it failed under the unchanged.05 bound. Do not attribute cause without
  evidence. Next independent reference must repeat the same6144-prefix,
  initial16K and continuation sequence in its own native namespace, also
  comparing both initial histories and the seed. CPU19 focused checks pass.
  Formal Full stays unregistered until the actual new model proof and fresh
  position/sampling checks pass. Preserve both failed model attempts and their
  actual cleanup; prepared registration native bytes equal the clean C5 tree.
- ModelR3 on frozene5f77eff/C5 now actually passes11 unforced calls and all
  five independent token/logprob comparisons withzero deltas, including the
  matched6144-prefix/initial histories. Raw_model_review also verifies all22
  raw request/response files, identical inputs/sampling and distinct reference
  namespaces/sessions; old failedR1/R2 remain preserved. CPU4713+6 retains
  all4712 prior IDs on1916 unchanged inputs. Fresh position24/native-stop24
  and sampler1/8/24 then pass. Registration
  ops/warm_start_free_extent_20261029_r1.registration.json now runs Full
  frome5f77eff/tree434c35dd at18861 after actual Thunder/model exit/cleanup.
  Preserve its frozen code and sole GPU use. Same complete work/tools/arrivals/
  capacity as the completed warm-start Thunder control; native is not rerun.
  All complete Full performance and applicable-baseline superiority remain
  pending. Do not infer gains from the model fixture or live prefixes.
- The new Full is actually running, not just queued. Independent
  layer_free_extent_20261029/full_live_start.json verifies251 exact receipts/
  15 actual arrivals (10 initial), all13 shared work/arrival/capacity fields
  against complete warm-start Thunder, nice0 watcher1325143/server1325666/
  driver1326959/sole GPU1326025 with source and ownership checks. New free-
  extent and demand pipeline are on; tool/frontier prefetch off. Preserve
  running frozen e5f77eff and its prepared native source. Do not queue other
  GPU work or claim complete Full JCT/throughput until all3674 normal requests,
  fresh drain and actual owned exit have been independently reviewed.
- The latest user now explicitly requests mean120-second workflow arrivals.
  Keep the eighteen-project population, ten initial roots and eight subsequent
  Poisson arrivals with seed21; retain the current mean60 Full and its control.
  New matched acting-decay ThunderAgent then Full may queue behind its actual
  complete work, fresh idle drain and owned exit. Change only the external
  arrival table; preserve each method's qualified native tree, policy, work,
  tool timing, capacity and serving controls. Both methods must use the same
  frozen mean120 table, count JCT from scheduled arrival and preserve normal
  gaps/tails. Do not claim reduced JCT or sufficient useful KV pressure before
  measurement. Named evidence is arrival_mean120_20261029. Independent
  registrations sharing a qualified commit use separate registration-bound
  application/native worktree paths. Agentix/TokenCake remain deferred.
- Mean60 warm-start Full now completes3674 exact requests, original exit0,
  fresh idle drain and actual owned exit. Independent complete review gives
  meanJCT1538.164561s vs Thunder1599.890513s and output240.950160 vs236.616305
  token/s:3.8581%/1.8316% gains, still below the overall clear/repeated gate.
  Exact native prefill3770429 vs3789634; H2D2790.839157 vs2826.104439GB,
  D2H1302.827729 vs2199.933616GB. All staged497 episodes/798 member
  associations have actual later service; complete physical bytes reconcile.
  One73-token handoff haszero new free extent; do not attribute performance
  to the new path. Restore samples19/2 cannot be extrapolated. Workflow mean
  maximum ready gap worsens67.508718 to71.137810s; retain this tradeoff.
  Source/figures are layer_free_extent_20261029/warm_start_complete_r2 and
  warm_start_mechanism_review_r1.json; failed R1 plotting evidence is retained.
  Mean120 Thunder now actually runs from frozen622c5feb/common11b84 on18871;
  thunder_live_start verifies1283 exact receipts/17 actual releases and nice0
  watcher1345254/server1345626/driver1346717/sole GPU1345920. Keep pending Full
  policy/native/work unchanged. The old frozen queue lacks matplotlib in its
  final postprocessing interpreter; use a separate source-pinned CPU reviewer
  with the existing cpu_plotting_20261027 env, without changing or restarting
  live GPU jobs. Do not promote a plotting failure to a GPU comparison failure.
  Keep Agentix/TokenCake deferred and the overall goal active.
- Latest HBM/dispatch audit of the complete mean60 Full finds99.35% physical
  KV-pool residency in300..1800s with100% two-second-capped coverage;68.27%
  non-evictable is not GPU utilization or proof of the useful active set.
  Actual shortfall releases1659 backed nodes;76 new predicted D2H commands
  serve70 beneficiaries, only7 same-owner/node/consumer ACK-release-restore-
  service chains are explicitly joined. None is an exhaustive mandatory
  A-before-B census.130 pre-service paused requests show full raw logical fit:
  40 first calls excluded by the continuation filter and90 continuations beyond
  the first8 live-request scan. hbm_dispatch_20261029 adds default-off
  AGENT_FREE_DISPATCH_INTERVAL_MS=500: event-driven complete-free-budget grants,
  pending-only rotation8,known JOIN checks24,max4 grants and500ms fallback,
  without replacing the five-second pressure tick, pausing peers or initiating
  copies. Handoff preflight now excludes non-private/locked/branched ancestors
  before Host budget checks; native guards and tree434c35dd remain unchanged.
  Preserve failed telemetry fixtures and original auditR1/source plusR2.
  CPU4760+6 retains all4734 previous IDs on1937 unchanged inputs; benchmark
  ~7..32us thread CPU/pass is not full-run cost or JCT evidence. Original
  mean120 Thunder completed; the separately frozen original Full is running
  on18872. Preserve it and its CPU reviewer. Qualify/register the new candidate
  separately after actual original Full completion, fresh drain and owned exit,
  reuse the matched mean120 Thunder control, and keep all normal tails and
  identical work/tools/arrivals/capacity/seeds. No superiority is established.
- Dispatch registration CPU preflightR1 refused only relocated canonical-patch
  path identity; exact engine/patch bytes were rechecked and preserved.
  R2 then correctly refused different reference_environment through the strict
  same-policy after-registration interface. Neither started GPU work. Keep that
  interface unchanged. The independent hbm_dispatch queue_candidate.py waits
  complete original Full work/fresh drain/actual owned exit, then registers the
  changed-policy Full and its CPU-only reviewer. New26-case queue CPU binding
  passes; preserve both registration entry failures. Candidate uses a fresh
  r2 registration identity because the refused r1 prepared frozen worktrees.
  Reuse the same actual mean120 control and original Full for review.
- Original mean120 Full now actually completes all3674 exact requests, fresh
  drain and actual owned exit. Independent CPU reviewer completes: JCT1448.785
  vs Thunder1494.243s (-3.04%),output248.040 vs239.596token/s (+3.52%).
  New complete capacity audit confirms99.48% middle physical KV residency
  (99.96% coverage),67.01% non-evictable,133 raw-fitting paused requests.
  Actual backed releases1499 nodes;50 new tool-D2H commands/49 beneficiaries,
  4 explicitly joined backup-release-restore-service chains. Last handoff
  counters61 attempts/1 submit,1047 restored tokens incl586 real free extent;
  do not extrapolate mandatory-D2H frequency or isolated performance.
  Queue11c14d6f successfully registers candidate r2 on18873 after actual original
  exit. candidate_live_start_r1 independently verifies22:44:54 Asia/Shanghai:
  live nice0 watcher1388810/server1390061/driver1392393/sole GPU1390529,
  healthy500ms free dispatch with5s pressure ticks, tool/frontier prefetch off.
  This is a startup prefix, not completed performance. Preserve frozen candidate
  and its CPU-only review_worker_r1; global optimization goal stays active.
- The500ms dispatch Full now completes all3674 exact requests/18 normal tails,
  fresh idle drain and actual owned exit. JCT1437.922687/output246.572144
  versus original Full1448.784951/248.040269 gives only.74975% JCT gain and
  .59189% throughput loss; versus matched Thunder1494.242817/239.595502,
  gains3.76914%/2.91184% remain modest, not clear/repeated acceptance.
  All133 unique grants prove exact native input/attempt/session first service
  and completed receipts, no unknowns; serviceP50=85.765ms/P90=806.241ms.
  New complete capacity review observes99.49648% middle physical residency,
  100% coverage/66.39025% non-evictable; raw-fitting inactive requests fall
  133-to56 without wider mean decode.1656 backed releases,64 new D2H commands/
  63 beneficiaries and3 explicit backup-release-restore-service chains use
  different units; none enumerates mandatory-D2H events. Handoff75attempts/
  3submits restores24478 tokens incl2027 real free extent. Offline handoff
  auditor now correctly includes free-extent consumer bytes; native unchanged,
  55 related cases pass and both complete Full untagged balances reconcile0.
  Keep failures/plots/negative result. Do not promote500ms; next candidate uses
  original dispatch0 and2048-token prefill,mixed off on the same complete
  mean120V230/V311 work. Qualify the changed serving profile afresh with24
  token/session/native-stop and sampler1/8/24 checks for each method-specific
  engine, then match acting-decay Thunder and Full sequentially. This shared
  tuning is not an agent-policy novelty. No native recapture, arrival sweep,
  extra baseline or tail truncation; global goal remains active.
- The matched2048/mixed-off mean120 candidate now has a separate prepared
  configuration and sequential position/formal queue in
  experiments/reports/prefill2048_mean120_20261029. Actual CPU4810+6 passes,
  retains all4760 prior identities,no failures/errors/skips,1952 bound inputs
  and native source unchanged. Preserve preparation's CPU-only wrong-factory
  refusal; it created no configurations or GPU process. Queue uses the existing
  paper_environment for Thunder,original Full dispatch0,one fresh24-position/
  session/stop and sampler1/8/24 qualification per method-specific native,
  then complete Thunder before Full. It does not weaken same-policy
  after-registration or promote500ms. Original mean120 arms and dispatch
  candidate all independently prove3674 exact requests/drain/actual owned exit;
  exact native rebinding also passes. Freeze committed sources before queue
  registration; no2048 GPU or performance claim exists from these CPU checks.
  Keep tool/frontier prefetch0,mean120 table/work/capacities/seeds/tails fixed
  and Agentix/TokenCake deferred.
- Common2048 position on frozenf502f87d now actually passes all24 exact token/
  session/native-stop calls (24 native stops) and sampler1/8/24; actual2048/
  mixed-false/running24/capacities match. Thunder formal is independently
  registered on18881 at ops/prefill2048_mean120_thunder_20261029_r1.registration.json.
  Preserve queue_r1's CPU startup-monitor failure: it mistook the interval
  between successful formal registration and first state publication for an
  absent comparison. The GPU control was not interrupted. Monitor now permits
  a bounded180s initial-state window only with exact successful registration.
  Separate continue_queue adopts only that failure/live control,requires old
  monitor exit and unregistered Full,and preserves original pending f502
  command/4810-case qualification.120 source-bound monitor/auditor checks pass;
  do not rerun the control or change its frozen inputs.
- Complete new consumer-shortfall audit finds307 unique request attempts,
  no repeats:273 before native admission/34maintenance,deficitP50=23272/
  P90=124769.6/max161037 tokens.45 private-path geometry refusals correspond
  to45 consumers,donor coverage of required recycled slotsP50=1.45843%.
  These require release or waiting,not necessarily new D2H; do not interpret
  them as an exhaustive mandatory-writeback census. Actual whole-run D2H
  999.837GB/H2D2443.981GB includes native transfers beyond the.60585GB of new
  prediction-targeted backups. Current single-private-path handoff coverage
  and high physical residency are separate concerns. Preserve its report,
  8 focused and120 source-bound checks; global optimization remains incomplete.
- Replacement CPU monitor now actually runs from frozencc4fe928 at
  prefill2048_mean120_20261029/queue_r2/registration.json,watcher1446109.
  It adopts existing Thunder and preserves the original frozenf502 Full
  position/formal command; do not restart or duplicate the control. Independent
  2026-10-30 00:09:02 Asia/Shanghai live observer verifies608 exact completed
  receipts/15 actual arrivals,original shared timing,actual2048/mixed-off/
  running24,and nice0 watcher1441579/server1442091/driver1443621/sole native
  GPU1442425 with actual identity/ownership. Explicit reused-observer bindings
  are in live_start_binding_r1.json. This is a live prefix,not complete JCT,
  throughput,Full qualification or superiority. Keep both frozen roots and
  original CPU4810 evidence unchanged; queue2 waits complete control/drain/
  actual exit before the pending Full qualifier. Goal remains active.
- Latest user asks to shorten the acting-decay control period and disables
  physical layer handoff for every future experiment. Distinguish decay's
  existing1s half-life,reference5s complete working-set tick,and the separate
  optional500ms free-budget dispatcher (still off). A new Full-only explicit
  AGENT_CONTROL_INTERVAL_MS/BELIEFKV_AGENT_CONTROL_INTERVAL_MS uses2000ms;
  default and isolated paper/native controls remain5000ms,half-life/formula
  and undiscounted/native physical capacity authority unchanged. Both layer
  handoff flags/free extent are0 in the new candidate; retain logical JOIN
  blocked eligibility/recovery,JOIN/EOS,tool offload,prepare_host and native
  demand pipeline. Do not restart the running2048 Thunder control or edit
  its frozen sources. Old queue2 CPU watcher1446109 was identity-checked,
  SIGTERM'd and independently observed dead before either old Full registration.
  Preserve its stale state and user_superseded_stop_r1 receipt. New named
  config is configs/migration/predictive_decay_tick2s_no_handoff_20261030.json;
  queue_full waits actual complete Thunder/drain/owned exit then freshly
  qualifies current Full at18883. Focused447 pass; preserve initial fixture/
  native-env skips and explicit-free-extent declaration refusal. Full retained
  CPU qualification and GPU/model/performance remain pending. No default
  layer-handoff re-enablement,new baseline,arrival sweep or tail truncation.
- New Full control-period/no-layer-handoff actual CPU4859+6 now passes,
  retains all4810 prior identities,no failures/errors/skips,1962 bound inputs
  and native source unchanged. Tests observe a real2s policy boundary using
  the unchanged1s decay and raw-pressure pause,while paper control stays5s.
  Config/registration guards require both raw and effective physical-handoff
  aliases0. Freeze committed sources before the separate queue_full registration;
  current2048 Thunder continues unchanged and only its actual complete/drain/
  owned exit may release fresh Full position/model checks. No new Full GPU
  qualification or performance follows from these CPU results.
- The user-selected2s/no-physical-handoff Full is now actually queued from
  clean frozen175d6925 (runtime implementation234de993),watcher1459481,nice0,
  at decay_tick2s_no_handoff_20261030/queue_r1/registration.json. Independent
  queue_live_start verifies frozen sources/inputs,old watcher dead,current
  Thunder still running,and no early Full position registration. It rechecks
  effective2000ms/layer0/free-extent0. Keep the new queue,existing frozenf502
  control and all original work/tails; wait complete control/drain/owned exit
  before fresh current-source Full position/native-stop/sampler qualification.
  New Full GPU qualification/performance and clear superiority remain pending.
- The frozen2s/no-physical-handoff Full now completes all3674 exact requests/
  18 normal tails,original exit0,fresh drain and actual owned exit. The existing
  independent paired reader gives meanJCT1439.457324 vs matched Thunder1504.072304s
  (-4.2960%),whole-run output243.890996 vs238.375989token/s (+2.3136%).
  Keep complete_review in decay_tick2s_no_handoff_20261030/queue_r1,including
  decode trends/distributions. This one pair is not clear/repeated superiority
  or an isolated2s effect; do not rerun native or enter deferred baselines.
- The latest user requests workflow remaining-work classes within reference
  paused recovery,with genuine blocking ALL-JOIN sibling progress. The separate
  default-off WORKFLOW_SIZE_RESUME composition now implements original category,
  cached workflow bucket,context size and queue sequence;skip nonfitting agents,
  retain decay/raw pressure/native capacity/1800s escape and existing event
  grants. Do not add full-raw-budget resume gating or choose new active victims.
  Supported predictions permit same-category/bucket ready sibling adjacency;
  uncertainty retains the original periodic ordering while existing sibling
  event/native preference remains. Preserve empty-budget fast return.
  Background CPU task encoding/progress/inference coalesces perworkflow200ms;
  no future trace/output lengths/tool times enter online features. The current
  work label is explicitly an uncalibrated0.01-new-input-plus-output token proxy,
  not exact GPU seconds or cross-context physical prefix-union work.
  CPU4903+6 now actually passes,retains all4902 prior identities/no skips,
  binds1974 inputs and the unchanged native source. Preserve the first fixture
  order failure and all CPU/model diagnostics in workflow_size_join_frontier_
  20261029. Only six historical non-evaluation projects exist for this model:
  R3 uses four train/two calibration projects,excludes all18 evaluation projects,
  and fails calibration macroF1.3274/long-to-short.4103. Its decision_eligible
  is false;runtime falls back to medium and formal preflight refuses it.
  Do not register a new GPU comparison or call62.75% overall accuracy a usable
  ranking result. The separately prepared predictive_workflow_size_resume_
  20261029 config waits a qualified predictor and actual paused-competition/
  cost evidence. No new GPU run or global-goal completion follows from this CPU
  implementation. Keep tool/frontier prefetch and physical layer handoff off.
- The user now authorizes a relaxed development split and extra native training
  collection. Project/task overlap across independent complete captures is
  permitted; it is not unseen-project generalization. Keep project holdout as
  the default and declare capture mode explicitly. Do not split snapshots from
  the same execution or relabel copied captures as independent. Historical
  workflow IDs are stable across reruns; bind complete event-file hashes.
  V174 trains24 workflows, V171 calibrates18 natural workflows, and V230
  evaluates18 complete workflows. Initial expanded fitting passes calibration
  macroF1.5830 but development macroF1.4650/long-to-short.4892 is inadequate:
  no workflow-size performance run is registered. Preserve all artifacts.
  The separately declared eight-task native source uses the first eight
  tool-qualified distinct projects and different original task categories,
  current4/8 child and8/16 root budgets, real tool durations, unchanged project
  sources/tests/environments and native policy. This is training collection,
  not a repeat of the old native performance baseline. Recheck sources, actual
  CUDA sampler, GPU ownership and reservation before launch; freeze committed
  application sources and retain normal tails. Keep handoff/tool/frontier
  prefetch off. Global performance goal remains incomplete.
- Expanded-data source-bound CPU qualification now actually passes4913 tests
  plus6 subtests,retains all4903 prior identities,no skips/failures and1987
  source inputs/native bytes unchanged. Capture registration must use the
  current common11b84 native base/patch and its726-case evidence; old V230
  pre-rebase6437 cannot accept the current incremental canonical patch. That
  precheck started no GPU. Preserve the failed one-workflow fixture and its
  passing three-workflow correction. Commit/freeze before native collection.
- Eight-task training source is now actually recording from frozena719f972 at
  ops/workflow_work_train8_source_20261029_r1.registration.json,port18891.
  Actual sampler1/8/24 passes;live_start_r1 independently verifies8 workflows,
  379 complete native outputs,frozen sources,actual2048/mixed-off/running24/
  native admission off,nice0 watcher/driver/server/scheduler and sole GPU owner.
  Preserve this live capture and its normal tails. CPU-only finish_training
  waits exact complete source/token/graph/fresh-drain and actual owned exit
  before exporting exactly24+8 natural workflows and fitting against the same
  independent18 calibration/18 development evaluation workflows. Its34 focused
  tests include7 completion-boundary cases. It registers no further GPU action;
  complete capture,expanded-model quality and scheduler gains remain pending.
- Training source now actually completes8 natural workflows/1673 exact requests/
  34JOINs/no nesting,zero task errors/safety guards and8 declared parallel-
  return deferrals. source_complete_review_r1 verifies token/causal/fresh drain,
  source bytes and actual watcher/driver/server/scheduler exit;GPU is released.
  CPU finish_r1 actually exports32 workflows and fits;current development
  long-to-short.5364 remains poor despite old-calibration eligibility.
- Explicit workflow holdout now permits different complete executions within
  one capture while rejecting shared event fingerprints;project and capture
  defaults retain their contracts. A declared SHA21 alternating split puts4
  current workflows in training and4 in calibration:28train/22cal/18evaluation.
  Three fixed regularization choices select compact16 using calibration only;
  development macroF1.4572/long-to-short.4607 is still weak. Retain all old
  splits/models and disclose this as adaptive development,not final blind eval.
  Actual sampled paused audit has2217states/637cross-workflow states,1027
  distinct-true-class pair observations involving242distinct request pairs.
  Compact16 corrects105 and harms83 context-size orders;combined650/1027
  vs628/1027 is not meaningful proven JCT gain. It uses post-control sets/
  call-boundary features only,no partial decode/cache emulation or capacity/
  native-selection counterfactual. Preserve the initial sequence-field CPU
  failure and passing stable-sort audit;no performance GPU run is registered.
- New source-bound CPU actually passes4929+6,retains all4913 literal IDs,
  no skips/errors/failures,1993inputs/native bytes unchanged. The later separate
  profile_text_r1 CPU prototype is outside that binding and not wired online.
  Same-task logical work ratio median is1.4483 currentV230/V174 vs.9805 old
  V171/V174. Recorded old dynamic_nested lacks the current parallel terminal/
  soft-budget contract;current short_workstreams uses4/8child,8/16root,min2
  JOIN. Adding these already-known instructions to compact16 input improves
  initial development accuracy.4444-to.6667/macroF1.4884,but long-to-short
  remains.4297. No future workload labels enter inputs. Never deploy that
  prototype without matching the live prefix contract;collect matched-current
  task executions or qualify better prediction/ranking next. Keep the full
  performance goal active and tool/frontier prefetch/physical handoff off.
- Declared execution instructions now have an explicit live input contract.
  Legacy task-only weights keep their exact input;profile-bound weights require
  the same5 declared fields at candidate preparation,formal preflight and Worker
  creation. Shared formatter has no future work hints. input_contract_r1 binds
  the unchanged numeric prototype and checks all18 actual root token tails,
  first-submit features,task projections/probabilities exactly on CPU. It does
  not qualify background-cache timing or GPU performance. Full-worker cycle CPU/
  wall now includes event/task/prefix work. Owned vector histories preserve
  all14458 serialized snapshots;retain initial tuple-vs-JSON-list check failure.
  100k-prefix micro median improves5.33-to2.31ms,not measured JCT.
- Input-bound profile model paused audit is negative:627/1027 combined-correct
  vs628 original;103 corrected/104 harmed reversals. Do not call global F1 or
  initial-accuracy gains a scheduler improvement. Continuous remaining-work
  pair metrics are added for comparison across changed training quantile bins;
  sampled call-boundary/post-control limitations remain. No Full performance
  run is registered by these CPU changes.
- A separately declared matched6 training capture keeps the first6 original
  tasks/projects from the existing seeded18 pool,unchanged sources/tests/tools,
  current4/8child and8/16root,2048/mixed-off,24running/decode,seed21 and real
  tool times. Native/predictor flags remain isolated. Existing performance
  workload/arrivals and all earlier captures are preserved. Expected registration
  is ops/workflow_work_matched6_source_20261029.registration.json,port18892,
  run experiments/raw/workflow_work_source_20261029_matched6 (unique capture
  label avoids reusing the old r1 native worktree). CPU-only finish_matched
  waits complete source/drain/actual exit then trains34 with unchanged22cal/
  18development,predeclared compact16,live input binding and paused audit;it
  registers no further GPU action. Fresh current-source CPU actually passes
  4946+6,retains all4929 identities,no skips/errors/failures,2001 inputs/native
  unchanged. Matched6 has15 verified tool/project inputs. Freeze committed
  sources and recheck reservation/resources/sampler before actual collection.
- Matched6 native training now actually records from frozen e3be927b on18892,
  ops/workflow_work_matched6_source_20261029.registration.json. Independent
  matched6_live_start_r1 verifies6 workflows/57 complete outputs/57 completed
  tools,all frozen inputs,actual2048/mixed-off/running24/seed21/admission-off,
  nice0 watcher/driver/server/scheduler and sole GPU owner. Sampler1/8/24 passes.
  CPU-only matched6_finish_r1 actually waits at nice19 from the same clean
  commit with CUDA hidden,unchanged34train/22cal/18development plan and no
  further GPU action. Preserve the live source,all normal tails and both
  frozen roots;this live prefix is not completion,prediction or performance
  qualification. Global goal remains active.
- Read-only paused_first_choice_opportunity_r1 audits the existing input-bound
  profile model within original categories,one minimum-context representative
  per workflow.593 sampled groups have112 model first-choice changes,
  40 smaller/72 larger true remaining-work selections. First observations of
  220 distinct candidate request sets give41 changes/21 better/20 worse;
  true-class-only diagnostic would change72. This shows possible ranking
  opportunity,not physical feasibility,actual admission or JCT counterfactual.
  Candidate sets are not independent workflows. Keep matched6 capture/CPU
  finisher running unchanged;no new performance or baseline run follows.
- Continuous paused reference fixes the changing-class-threshold comparison:
  same1803 sampled comparisons/440 distinct request pairs give context1075,
  old task-only1063,declared-execution1080 correct. These are repeated
  observations,not independent tasks or feasible/JCT counterfactuals.
  Profile error decomposition finds after-completed-JOIN effective accuracy
  .77305 but long-to-short .66371 versus .28887 before the first completed
  JOIN. All14458 development attained-work values remain within training's
  maximum total65088.18;simple beyond-maximum extrapolation is insufficient.
  These read-only artifacts change no predictor weights,thresholds or live
  capture. Wait complete matched6 before inspecting its fixed fit/ranking.
- Matched6 now actually completes6 natural workflows/1589 exact requests,
  60161319 full prompt tokens/218354 output,30JOIN/36SPAWN/no nesting.
  Independent matched6_source_complete_review_r1 rechecks frozen sources,
  zero runtime errors/completion guards,all6 parallel-JOIN contracts and6
  disclosed return deferrals,token/causal/fresh drain/actual owned exit;GPU
  released. Preserve original project/test failures and all normal tails.
- Fixed34train/22cal/18development compact16 fit now completes with live-bound
  SHA e010a8c12968cedaafed0ed15d8a873b02528f44999d0a53a04d1ebb0b5ca951.
  Calibration macroF1 .54484/long-to-short0 passes original numeric gate;
  development macroF1 .46528/long-to-short .22294 remains imperfect.
  Same1803 continuous paused comparisons improve context1075-to1360.
  First440 request pairs give233-to269 correct/41 corrections/5 harms;
  first220 candidate sets give40 changed choices/35 better/5 worse.
  Real18-root CPU input/text/features/probability parity passes. Microbench
  cached inference24.27us,prefix100k2.295ms,8-candidate resume64.82us versus
  reference39.67us is not measured JCT or end-to-end CPU overhead.
  These development diagnostics justify one matched Full verification,not
  superiority. Use input_binding/candidate.json with unchanged complete V230/
  V311/18projects/10+8mean120/2048/mixed-off/2s and handoff/tool/frontier off.
  Fresh position/session/native-stop/sampler qualification precedes Full.
  Reuse complete2048 Thunder and2s Full;no extra native or baseline. Freeze
  committed sources and recheck exact current CPU binding before registration.
- Workflow-size Full now actually serves from frozen11e1c2ad on18893 at
  ops/workflow_size_resume_20261029_r1.registration.json. Current4946-case CPU
  binding/2001 inputs and exact complete3674/145565966/549671 predecessor work
  reverified. Separate position registration passes24 exact token/session/
  native-stop calls(all24 native stops),plus sampler1/8/24. Independent
  workflow_size_gpu_r1/live_start_r1 observes21 native receipts/11 actual
  arrivals,sole GPU ownership/nice0 processes,actual2048/mixed-off/running24/
  seed21/native capacity,enabled workflow-size policy and2000ms control.
  Live predictor SHA e010a8c12968cedaafed0ed15d8a873b02528f44999d0a53a04d1ebb0b5ca951
  has real cached predictions/no error. This prefix is not complete performance.
  CPU-only review_worker_r1 actually waits at nice19 from the same clean
  commit,reusing complete Thunder and original2s Full;no further GPU action.
  Keep all frozen sources/model inputs/work/arrivals/tails and the global
  goal active;new full JCT/throughput and repeatable superiority remain pending.
- New read-only scripts/audit_workflow_size_selection.py replays logged local
  resume order/budget/selected sets against the exact frozen policy sources,
  compares original ordering on the same candidates/remaining capacity and
  associates only following exact request/attempt/context/session GPU service.
  It excludes earlier/wrong-attempt service and post-pressure revoked grants,
  separates episodes from unique attempts,and claims neither longitudinal
  pressure/physical feasibility nor exclusive causal/JCT attribution.
  Actual41 related audit/policy/runtime tests pass;running11e1 Full unchanged.
  live_selection_capacity_r1 seals517 controls:359 nonempty/263multi,78 order
  changes/8 local selected-set changes.301 candidate-budget excesses include
  only52 same-category cross-workflow capacity competitions,24 with different
  effective ranks.244 unique selections/237 following services,8 local-new
  selections served. Whole-control CPU83.48ms/P99 .28726ms and newer background
  workerCPU3.139s are prefix observations,not incremental overhead or complete
  performance. Keep open coverage/normal tails and wait the existing Full/
  CPU reviewer;do not start baselines or change workload from these prefixes.
- Workflow-size Full now completes the exact3674/18 workload and normal tails.
  MeanJCT1438.451359 vs original2sFull1439.457324s (-.0699%),whole-run output
  241.403124 vs243.890996token/s (-1.0201%);do not promote ranking or start
  other baselines. Final1132 controls change local selected sets only13times.
  Training/calibration/development running features were all0 while online
  emits partial output every128tokens. This is a confirmed input mismatch,
  not a proven performance root cause. New opt-in native-progress export uses
  actual journal counts,keeps final work/execution populations,and validates
  ownership/clock/coverage. Zero-delta post-result residues publish no progress.
  Actual104 focused tests pass. Keep fixed34/22/18 populations and compact16
  parameters for CPU repair,with actual Worker partial-progress parity.
  No new GPU action follows;keep tool/frontier prefetch and layer handoff off.
- Native-progress fixed34/22/18 CPU repair now fits once and passes18root/
  17128snapshot Worker parity,2670partial observations,17110probability checks.
  Preserve initial missing-client-output-count failure and successful
  input_binding_r2 retry using verified native source_plan lengths.
  Bound model3fe51fb34e9189a081087083d2d2a7169fcabca1baff8b5bcec2afb47872b45c
  is not promoted. Same repaired development data gives macroF1.45728-to.45016,
  long-to-short.26007-to.19182;streamed paused continuous pairs1358-to1332/1803,
  first request pairs268-to273/440. Mixed offline results do not establish JCT.
  Actual106 focused tests pass;repair_review_r1 preserves fixed work/populations
  and failed attempts. No new GPU registration or deferred baseline;global
  performance goal remains active. Relax project/task overlap only across
  independent whole executions,not copied captures or future online labels.
- New explicit workflow_dispatch candidate extends cached workflow classes to
  the existing bounded500ms free-capacity dispatcher,while full control remains
  2s and acting half-life1s. Preserve actual ready JOIN precedence,original
  resume categories,complete undiscounted raw budgets,8rotating ordinary checks/
  4grants,identity/executability/native authority,and active continuity.
  Unknown-all windows keep original rotation;event lookup must not replace
  periodic rank snapshots. No new eviction,prefetch,global aging or oracle input.
  Actual115 related tests pass. Original middle-run allocator occupancy99.67%
  contradicts blanket unused-HBM claims.78/2217 sampled necessary logical-fit
  opportunities span81.01s;this is not removable JCT. See
  docs/design/workflow_size_free_dispatch_20261029_zh.md. Complete fresh
  source-bound CPU and GPU position/sampling qualification before matched Full;
  reuse existing complete controls,normal tails and exact work. No promotion
  or baseline expansion follows from the implementation alone.
- Workflow-dispatch implementation is frozenaedfe19b. Current source-bound CPU
  actually passes5008+6,retains all4946 prior identities,no failures/errors/skips,
  binds2007inputs/native unchanged. Cached8-candidate sort median12.79us/
  P99 16.46us excludes complete dispatch/logging/background/end-to-end cost.
  Preflight rechecks exact3674/145565966/549671 complete controls,normal tails,
  drain/actual exits and current reservation/resources. Position qualification
  ops/workflow_dispatch_position_20261029.registration.json now actually runs
  watcher1688208 on18894,real token/session checks from frozenaedfe19b.
  It precedes ops/workflow_dispatch_20261029_r1.registration.json;do not
  confuse qualification with a completed Full or claim superiority.
  Keep user worktree edits and all earlier negative results;no deferred baseline.
- Workflow-dispatch position qualification now passes24exact token/session/
  native-stop requests(all24 native stops)and native sampler1/8/24. Its original
  supervisor actually exits;Full ops/workflow_dispatch_20261029_r1 now actually
  serves from frozenaedfe19b on18894. live_start_r1 at22:19:19UTC verifies sole
  GPU scheduler1692611,nice0 watcher1691395/server1692256/driver1694887,
  exact2048/mixed-off/running24/seed21,2000ms control/500ms dispatch,and live
  model3fe51fb34e9189a081087083d2d2a7169fcabca1baff8b5bcec2afb47872b45c.
  Prefix15exact receipts/11arrivals is not complete work or performance.
  CPU reviewer1692001 actually waits atnice19 using existing complete Thunder/
  originalFull and registers no further GPU action. Preserve all normal tails,
  frozen inputs and negative results;wait complete review before superiority.
- Read-only free-dispatch audit now excludes pre-grant exact service and checks
  completion follows each grant/following service in the same clock domain;
  rejects duplicate/conflicting receipts and verifies any reentry output growth.
  Preserve9 initial failing regression cases and44 passing focused cases.
  Runningaedfe19b Full is unchanged. Strict live prefix1572exact receipts/
  97grants has97following services/96completion receipts,one open;P50 .14675s/
  P90 .70206s/max17.03661s is not full JCT or an isolated gain. Two delayed
  examples are mostly active but have different first-service HBM/Host counters;
  neither arrival residency nor exposed copy wait follows. Preserve sealed
  live_periodic_selection_r1 (200controls,0local selected-set changes) and all
  normal tails. No further GPU action or deferred baseline is authorized by
  these prefix observations alone.
- Workflow-dispatch Full now completes the exact3674/18 workload,normal tails,
  fresh drain and actual owned exit. MeanJCT1432.302431/output241.696602
  versus original2sFull1439.457324/243.890996 gives only.4971% JCT gain and
  .8997% throughput loss;do not promote workflow ranking/500ms dispatch.
  Strict159grants/159distinct attempts all have following exact native service
  and completion. Final1132controls change19local selected sets. Complete
  decode plots are visually inspected. Preserve all results/normal tails.
- Current34train/22calibration/18development workflows already include two
  completed native training captures. User permits project/task overlap across
  independent executions;do not require strict project holdout or split one
  execution's snapshots. Current evaluation is adaptive development,not blind
  generalization. No further data capture is needed solely for separation.
- New complete prefill/decode-gap audits observe349.784501/305.164732/
  304.991335 prefill worker seconds between quiet continuing decode bookends
  for Thunder/originalFull/newFull,respectively;these are not removable JCT,
  continuous admission,residency or pure kernels. The matched mixed candidate
  uses original2sFull without workflow ranking/500ms dispatch and shared
  mixed1/chunk2048 only. Keep tool/frontier prefetch and physical handoff0.
  Each method needs fresh real24token/session/native-stop and1/8/24 sampler
  qualification. Require positive actual decode output inside prefill and
  all-mode output conservation after fresh idle drain/actual owned cleanup;
  formal registration revalidates/seals journals. Reuse complete work/arrivals/
  tools/capacities/seeds and sequence Thunder decay then Full;no extra native
  baseline,other baseline or automatic retry. New CPU/GPU/performance binding
  remains pending. See docs/research/workflow_dispatch_and_mixed_chunk_20261029_zh.md.
- Mixed candidate current source-bound CPU actually passes5064+6,retains all
  5008prior literal IDs,no failures/errors/skips,2016inputs/native unchanged.
  Independent complete-work/profile/native-rebinding precheck passes both
  retained methods and latest failed-gain candidate,with actual owned exit.
  Freeze committed sources before fresh mixed GPU qualification;these checks
  are not GPU/performance evidence or completion of the global goal.
- Mixed R1 froze db974231 and actually passed24exact/session/native-stop
  requests,but failed strict output ordinals before sampler/formal startup.
  Actual286mixed-prefill batches/422decode outputs include24zero-output
  completed overlap residues(8prefill/16decode)whose old early-completed guard
  omitted ordinal fields. Independent failure_review_r1 binds prior native
  completion/receipt counts,fresh drain and actual queue/position/owned exit.
  Preserve the failed raw logs;do not repair them into qualification.
  Main now observes actual output counts before the completed guard,retaining
  one terminal event and exact zero residues. Two old-code red cases and265
  fixed related passes are preserved. New full CPU and distinct attempt2 GPU
  qualification remain pending;no formal arm has started.
- Completed-residue repair now actually passes5066+6 full CPU,retains all5064
  prior identities,no failures/errors/skips,2016inputs/native unchanged.
  Freeze/recheck resources before separate attempt2 on fresh18897/18898 paths.
  Both methods use the same repaired telemetry;keep failedR1 and the global
  goal incomplete. Current GPU and complete mixed performance remain pending.
- Mixed R2 from e3a7e5f1 actually passes24exact/session/native-stop(24stops),
  473forwards/1043all-mode outputs conserved,285mixed-prefill batches with
  415actual decode outputs and sampler1/8/24. Independent position_complete_
  review_r2 verifies raw journals/fresh drain/actual position/model owned exit.
  Formal ops/mixed_chunk_thunder_20261029_r2.registration.json now actually
  runs on18897,nice0 watcher1752934/server1753433/driver1755241/sole GPU1753786.
  Queue1749034 uses frozen sources and waits exact complete control/drain/
  actual exit before new Full qualification/execution. Keep Full unstarted,
  original2s policy,tool/frontier prefetch/handoff0,normal tails and failedR1;
  full performance and clear/repeated/all-baseline goal remain pending.
- Independent mixed live_start_r2 now seals51exact completed receipts,actual
  mixed1/chunk2048/running24/seed21/paper configuration,and verifies all four
  serving processes nice0 with sole owned GPU context. This is a prefix,not
  complete performance. Preserve frozen e3a7e5f1 and sequential queue_r2;
  no extra native source/performance arm or deferred baseline is queued.
- New read-only mixed-prefix auditor preserves open work/common completion
  frontiers and rejects missing ordinals,duplicate samples/receipts,wrong
  ownership/clocks and false complete claims. Actual separate prefixes verify
  1033 then3144exact completed receipts;later prefix has3146observed requests/
  479422outputs,9later native completions/12outside receipts kept unknown.
  Separate mixed decode-gap prefix has12250continuing observations all served
  inside prefill and0quiet gaps;not independent requests or full JCT savings.
  CPU-only complete_after_pair waits actual queue exit/exact full work/drain,
  then audits full outputs,gaps,JCT,native bytes/JOIN/sibling/EOS,with no GPU
  action/retry.55related CPU cases pass;these observers are outside the frozen
  e3a7e5f1 GPU qualification and change no live sources. Keep full metrics/
  global goal pending and user worktree edits intact.
- Mixed R2 now completes both exact3674/18 arms with fresh idle drain and actual
  owned exit. Full meanJCT1475.712067/output241.251017 versus matched Thunder
  1525.197388/235.367543 gives3.2445%/2.4997% gains,but both regress versus
  retained mixed-off profiles;keep original2s/mixed-off Full. Nine complete CPU
  mechanism audits pass,all549671outputs conserved,Full479demand stages/682
  service associations and zero unexplained untagged bytes. Handoff0;prefetch
  source join_ticket only. All three complete PNGs inspected. Keep revision/
  telemetry and single-pair limits explicit,all normal tails and no extra GPU
  queue. Global goal remains active.
- A separate optional workflow_remaining_model_work_context_v2 appends observed
  initial-root/current-active/maximum submitted prompt lengths. These are logical
  inputs,not physical KV union or future work. Legacy v1 keeps17features and exact
  default input compatibility. Worker/export/training/parity/paused audit share
  the declared schema. Two initial red cases and167related CPU passes are under
  context_features_r1. Re-export and fit the same34/22/18 whole-execution split
  once with fixed compact16 parameters before quality/ranking review;do not
  collect more native solely for project holdout or promote this untrained
  candidate. Full source-bound runtime/GPU/performance qualification remains
  pending. Preserve unrelated user docs and the completed frozen GPU sources.
- Context v2 now completes the fixed34/22/18 export and one compact16 fit from
  frozend6001b9c with CUDA hidden/nice19. All old17features,snapshots,executions,
  labels,parameters and paused candidates remain equal. Actual18root/17128
  progress/17110prediction CPU parity passes. Development macroF1 rises.45016
  to.53146,but continuous paused correctness falls1332-to1252/1803 repeated
  observations;first440request pairs are273-to272 and real first-submit
  accuracy stays9/18. These diagnostics do not establish better native selection.
  Keep the schema
  optional,v1 compatible,ranking off and original2s/mixed-off Full retained.
  Review is under context_features_r1;no new native source or GPU experiment
  is registered. Preserve the classification/ranking tradeoff and incomplete
  global goal rather than promoting calibration eligibility as performance.
- The separate default-off predicted_acting pressure candidate uses live supported
  remaining-tool forecasts only when the reference core already must pause one
  of multiple ACTING owners. It retains reference resume/decay/raw budgets and
  reasoning marks,falls back to smallest context,and adds no timer,loan,physical
  capacity credit or ready-peer preemption. Require a control-period plus full
  owner H2D horizon under the existing.75 survival rule and native byte geometry;
  no future duration or KV-tree scan enters the choice. Keep workflow ranking,
  tool/frontier prefetch,mixed and physical handoff off. This is working-set
  integration,not a new migration primitive or automatic novelty claim.
- Predicted pressure actual source-bound cpu_r1 passes5141+6,retains all5066
  prior IDs,no failures/errors/skips,2027 inputs/native unchanged. Four400-step
  no-prediction fixtures match reference state;real runtime positive pressure
  choice and fallback/identity/tail/restore-cost controls pass. Preserve all
  focusedR1-R3 fixture/validator failures and CPU preparationR1 missing launcher
  alias failure. ConfigR2 actual Full/control constructors pass. Evidence and
  declaration are under predicted_acting_pressure_20261029. Freeze/check current
  hold/resources then qualify fresh position/session/native-stop/sampler before
  Full-only performance,reusing existing complete mixed-off controls. New GPU/
  performance acceptance remains pending and the full goal stays active.
- Predicted-pressure frozen46d5b37a now passes24exact/session/native-stop
  requests(all24native stops)and fresh1/8/24sampler qualification. This probe
  has no live Full policy receiver and proves only the shared data path.
  Formal ops/predicted_acting_pressure_20261029_r1.registration.json actually
  runs Full on18901 with nice0 watcher1823035/server1823516/driver1824335/
  sole owned GPU scheduler1823596. Preserve frozen sources,complete work,
  normal tails and reuse retained mixed-off Thunder/original2sFull.
  CPU-only complete_review.py has12passing focused cases and waits full work,
  fresh drain/actual owned exit before pair plots and mechanism audits.
  It creates no further GPU action. Record zero changed choices or negative
  results honestly;local logical choices do not prove physical migration/JCT.
  Existing34/22/18whole-execution development data already includes two extra
  native captures;do not collect more solely for strict project separation.
- Predicted-pressure complete CPU reviewer now actually waits from
  frozen8686451a,PID1828218/start749833184,nice19/CUDA hidden,no GPU action.
  Separate pressure_outcomes.py binds exact native request/context/session
  and same-host clocks to observed single-tool returns;20focused cases pass.
  Sealed collecting prefix has125predicted choices/121fully bound/108survived
  the recorded horizon/4unknown following boundaries and0after-return pauses.
  These are selected repeated decision observations,not whole-population
  accuracy,physical release or causal JCT. Keep all counterexamples and
  unknowns;wait complete Full/plots/mechanism audits before performance claims.
- Predicted-pressure46d5b37a Full now completes all18/3674/145565966/549671
  work,72JOINs/92children,original stops/normal tails,fresh drain/actual exit.
  CPU completion reviewer exits clean;all five review actions pass and three
  complete figures are visually inspected. MeanJCT1450.343294/output245.088522
  versus retained Thunder1504.072304/238.375989 gives3.5722%/2.8159% gains,
  but versus original2sFull1439.457324/243.890996 JCT regresses.7563% with
  only.4910% throughput gain. Do not promote;keep original2s/mixed-off Full,
  workflow ranking/tool-frontier prefetch/handoff off,and the goal active.
  Final423mandatory pauses include238predicted/111changed-local-victim
  choices;all238bind exact request/session/tool/following request,210survive
  the recorded horizon,28do not,0unknown/after-return pauses. This is selected
  survival evidence,not calibration or causal physical improvement.
  NativeACK H2D2850.771763vs2659.224748GB(+7.2031%),D2H988.092039vs
  939.598381GB(+5.1611%);both3564old-input probes sum0recompute proxy.
  Native512stages/721members have subsequent service and0unexplained bytes;
  join-only1411prefetch commands all reused,218.610696GB;sibling1055records/
  21multi-member batches verified,55EOSfinals observed. Preserve full results
  under predicted_acting_pressure_20261029 and all user worktree edits.
  Both GPU and CPU reviewers have actually exited;no next GPU job is queued.
- The user now explicitly requests a real GPU oracle experiment and then
  explicitly includes future tool durations in ranking. The separately
  declared model/tool critical-path oracle may read the exact fixed workload
  tokens, tool timing map and dependency DAG for rank computation only.
  Mark future information/deployable=false in runtime and registration;
  normal learned and paper modes retain future=false. Use actual observed
  progress, parallel max rather than summed tool branches, and a predeclared
  original-control model-time proxy. This is not an optimal-JCT bound.
  Retain the learned dispatch 2s/500ms settings, all fixed work/arrivals/tools/
  capacities/native stops and existing Full/Thunder controls. Complete fresh
  source-bound CPU and token/session/native-stop/sampler qualification before
  the oracle-only Full. Do not claim offline local-choice counts as JCT gain.
- Model/tool oracle frozene718d34e now completes all18/3674/145565966/549671,
  72JOINs/92children,original stops,normal tails,fresh drain and actual owned exit.
  Current bound CPU passes5178+6,retains all5141 prior identities;fresh position
  passes24exact/session/native-stop(all24stops)and sampler1/8/24. All seven
  complete read-only review actions pass. MeanJCT1428.715608/output241.601511
  versus originalFull1439.457324/243.890996 gives only.7462% JCT gain and
  .9387% throughput loss;versus learned dispatch1432.302431/241.696602 gives
  only.2504% JCT gain and.0393% throughput loss. Do not promote the oracle or
  workflow ranking;keep original2s Full and all user-disabled controls.
  Exact oracle progress ends0 across18workflows,4117249necessary input tokens/
  549671outputs,14966matched graph events,0unmodeled core/errors. 1132controls
  have34local selected-set changes,10changes from removing external delays.
  All196free grants have exact following service/completion. This is local
  evidence,not causal full-JCT attribution. Oracle rescans DAG per lookup:
  15293evaluations/16029800nodes cost28.452437sCPU,mostly26.517127s dispatch
  scoring versus learned.091388s. Do not present this as a zero-cost or optimal
  JCT bound;cache/incremental scoring needs separate source-bound validation.
  H2D2775.832855vs2659.224748GB(+4.3850%);D2H869.316035vs939.598381GB
  (-7.4800%)are ACK bytes,not exposed critical waits. Preserve complete results
  under workflow_resume_oracle_20261029,all normal tails and user worktree edits.
  No next GPU arm is queued;the global clear/repeated/all-baseline goal stays active.
- The latest workload direction requires whole-project-disjoint short/medium/long
  mixtures and actual same-category ready-paused capacity competition, not only
  broad output counts. The new CPU inventory groups declared execution controls,
  verifies exact complete native token work, binds JOIN parents from JOIN_WAIT,
  and separates logical model work/context from tool and critical-path time.
  The compatible current32 executions cover18 projects: model-work P90/P10
  2.2733,max/min4.3910,136JOINs and0nested. Preserve old single-child/no-delegation
  captures;do not mix their protocols to manufacture wider skew.
  Scope18 declares six original investigations,six implementation/refactoring
  tasks and six multi-stage development tasks on the existing18 distinct
  tool-qualified projects,balanced across context bands. These are hypotheses,
  not measured size labels. Keep one shared4/8child,8/16root protocol,natural
  returns,original sources/tests/environments and real tool times in collection.
  Freeze seeded shuffled burst diagnosis;main uses5warm roots plus independent
  Poisson slots,with rate pending matched control capacity,not the old120s.
  Keep5-10x a diagnostic target,not a result filter or hard validity threshold.
  New explicit ranking metadata is bounded root task/delegation/role only,
  never the full prompt/future DAG. It is recorded for new model preparation;
  no tool-aware learned model is trained or promoted by this instrumentation.
  Keep retained Full size ranking/tool-frontier prefetch/handoff/mixed chunk off.
  See docs/research/workflow_mix_preparation_20261029_zh.md and the named
  workflow_mix_20261029 evidence directory. The old oracle and performance
  controls cannot become matched results for a changed workload/arrival profile.
- Mixed-scope preparation bound_cpu_r2 actually passes5204 tests plus12 subtests,
  retains all5178 prior literal identities,no failures/errors/skips,native engine
  unchanged. New replay arrival formats explicitly bind population permutation
  and independent Poisson slots;simultaneous root event delivery honors the
  declared order while later requests retain natural dependencies. Old arrival
  contracts remain unchanged. The39 original tool/project/context inputs pass.
  New native collection still requires a clean frozen checkout and fresh
  sampler/resource/reservation checks;these CPU results are not size diversity,
  physical competition or performance qualification.
- Scope18 now actually records native from frozene7dc71a1 on18903,registered
  ops/workflow_mix_scope18_source_20261029_r1.registration.json. Its unique
  run label ends_scope18,avoiding historical engine-worktree label collisions.
  Preserve the pre-GPU gpu_entry_r1 failure and successful gpu_entry_r2;
  this is one natural source capture,not two GPU experiments. Native engine
  remains common11b84f7c,fresh sampler1/8/24 and BF16 checks pass. Independent
  live_start_r1 verifies18projects,actual2048/mixed-off/running24/seed21/
  admission-off,nice0 owned processes,sole GPU owner,191complete outputs and
  bounded semantic input on18roots/1child. This prefix is not complete-work,
  measured size-diversity or performance qualification. CPU finisher1917682
  actually waits at nice19 with CUDA hidden,then audits complete source/drain/
  actual owned exit and writes measured strata/arrival artifacts only.
  No next Full/Thunder/baseline GPU action is queued. Preserve the running
  frozen snapshot and all normal tails;the global optimization goal is active.
- Scope18 source and CPU finisher now actually complete18/4246/148235495/
  607248,97JOINs/116children,0guard interventions,original exit0,fresh drain
  and actual owned exit. Measured model-work P90/P10=2.9554,output3.1634;
  no nesting,maxrootJOINfanout2 and18parallel-return deferrals remain limits.
  Keep5-10x diagnostic,not a hard result filter. Retain complete source/tails;
  use this as a burst competition diagnostic before broader comparisons.
  New source population/strata/arrivals are in scope18_finish_r1;do not reuse
  oldV230 performance as a matched control.
- Oracle cache now invalidates on observed changes and maintains exact continuous
  external-timer envelopes between changes,with literal fallback for ahead-of-
  clock observations. Complete old call-boundary CPU comparison13,083queries/
  525DAGrebuilds shows0local priority order changes,maxdelta1.54e-8s,
  threadCPU18.3959-to1.2855s. This excludes native partial callback reconstruction:
  strict exporter rejects positive replay samples after client results;keep
  oracle_cache_native_progress_rejection_r1 and the original exporter intact.
  No GPU/JCT gain follows from this CPU microbenchmark. New pregrant logical
  readiness snapshots and audit_workflow_competition exclude blocked/unready
  owners,retain first-call admission rules and native physical authority.
  Bound CPU r3 actually passes5216+12,retains all5204prior identities,no skips/
  failures,native unchanged. New burst_full prepares retained Full with size
  ranking off and the same user-disabled features off,on fixed new arrivals/
  TraceLab timing. It has not yet run;fresh source-bound position/sampler and
  actual completion/cleanup remain required. Global performance goal is active.
- New scope18 burst position now passes24exact token/session/native-stop requests,
  all24native stops,and fresh sampler checks. Frozen44b4aa9c retained Full
  actually runs on18904,ops/workflow_mix_burst_full_20261030_r1.registration.json.
  Independent burst_full_entry_r1/live_start_r1 verifies1797completed prefix
  receipts,source counts/native exact-token flags/position path,nice0 owned
  watcher/server/driver/scheduler and sole GPU ownership. This is not complete
  work or performance. Preserve the initial read-only graph-summary type mismatch
  and its correction;no model retry or running-source change followed.
  New review_burst completion uses the declared new4246/148235495/607248 work,
  fresh drain and actual owned exit,instead of the old3674 hardcode.
- New mixed-workload oracle preparation waits the complete new retained Full
  and calibrates its model proxy from that control's complete weighted decode.
  Change only within-category continuous remaining model/tool critical-path
  ranking,with all non-ranking settings fixed;500ms free dispatch stays off.
  This is a separately declared comparison,not the old2s/500ms oracle replay.
  Keep explicit future=true/deployable=false,parallel-max tool semantics,
  no size-driven active preemption and complete normal tails. New measured
  model-work tertiles label diagnostics only. Actual complete-event progress,
  current source-bound CPU,fresh token/session/native-stop/sampler and resource/
  reservation checks precede the oracle GPU run. Tool-aware bounded-input
  learned training and sustained-arrival performance remain pending.
- Mixed-workload bound_cpu_r4 actually passes5243tests plus12subtests,retains
  all5216prior literal IDs,no failures/errors/skips,2063bound inputs/native
  unchanged. New completion/calibration tests reject stale work,timing,arrivals,
  model cost,live owners and stale idle drains. This qualifies the new CPU
  operators only;cached oracle GPU/performance remain pending. Preserve current
  Full,all normal tails,user worktree edits and the active incomplete goal.
- New mixed burst retained Full now actually completes18/4246/148235495/607248,
  original exit0,exact stopping/arrivals/configuration,fresh drain and actual
  owned exit. burst_full_review_r1 records meanJCT1444.020012s,makespan
  2149.987822s,whole-run output282.442530token/s,weighted decode batch7.98299.
  Both complete control PNGs are visually inspected. Use this changed-workload
  control for the new oracle,not oldV230 results.
- New cached model/tool oracle artifact888438f77dc9f838a8311013d202886478e9b29542a44e12c15795cc92c0bedc
  calibrates.02000504183s/model-work-unit from the complete new control.
  Actual complete-event CPU progress conserves4246requests/607248outputs/
  18closed workflows;173queries cost431.86msCPU at complete call boundaries.
  This is not actual online GPU control cost. Frozen c5bd820f registers
  workflow_mix_burst_oracle_position_20261030_r1 then formal
  workflow_mix_burst_oracle_20261030_r1 on18905. Preserve current qualification/
  formal sources and all normal tails;complete oracle gains remain pending.
- main_arrivals_r1 uses5warm roots,independent permutation21/Poisson22 and
  rate.0075349264/s from90%of complete burst throughput,mean132.72s. Its finite
  13slot realization has875.05s maximum gap and2485.85s final submission.
  Retain this table as an unqualified sustained-pressure diagnostic;do not
  silently clip gaps,resample a winning schedule,call it measured90%utilization,
  or queue its long GPU main run now. Burst competition/oracle net benefit
  precede a separately declared pool/arrival refinement. No other baseline
  or sustained-arrival GPU job is queued;global performance goal stays active.
- New oracle position actually passes24exact token/session/native-stop requests,
  all24native stops,and fresh sampler. Formal c5bd820f oracle actually runs
  on18905;burst_oracle_entry_r1/live_start_r1 independently verifies457complete
  prefix receipts,sole GPU,nice0 owned processes,size ranking enabled with
  2scontrol/free-dispatch0 and fixed disabled controls. This prefix is not
  complete work,online control-cost acceptance or performance. CPU-only
  review_oracle preserves complete equal work/drain/actual exit and binds the
  exact declared retained control before metrics/plots/mechanism audits.
- New oracle CPU complete reviewer now actually waits from frozen60d27783,
  PID2035256/start752041049,nice19/CUDA99999,registered in
  workflow_mix_20261029/burst_oracle_review_r1.registration.json. Independent
  liveness/priority/environment inspection passes. It waits complete equal
  work,fresh drain/actual owned exit then reports metrics/plots/five mechanism
  audits;it creates no GPU follow-up. Current c5bd820f oracle remains running
  unchanged and whole-run gains are pending. Keep the global goal active.
- New scope18 cached oracle and its CPU reviewer now actually complete/exit:
  exact18/4246/148235495/607248,meanJCT1445.406697 versus retained1444.020012,
  whole-run output283.233280 versus282.442530. JCT regresses.0960%,throughput
  rises.2800%;do not promote ranking. All three complete PNGs inspected.
  Only4/1067controls have defined ready-paused short/long capacity competition.
  New complete source shape audit finds78singleton/19double JOINs,116children,
  child model-call median29/P9044.5 and no nesting;all19parallel groups have
  positive shared native decode. Keep these diagnostics and all normal tails.
- User rejects proposed child2/4:retain shared4/8 soft reminders,root8/16.
  The separately declared six-project parallel checkpoint pilot uses fixed
  context bands1/4,two narrow diagnoses/two bounded edits/two original full
  multi-stage objectives. Explicit derived IDs/provenance distinguish reduced
  deliverables from original full-task success. Keep original source/tests/
  environment/context and true tool timing;new actual task semantics precede
  reference context. New parallel_workstreams profile removes child todos only,
  narrows roles/reports and prompts simultaneous independent task calls;keep
  auto first choice,real parallel minimum,natural returns and depth2. No new
  call/output hard cap or fixed DAG. Pilot is a shape diagnostic,not evidence
  of eighteen-root pressure or scheduler superiority. Inspect complete child
  lengths,fanout,nesting,offered overlap and positive native service before
  expansion;do not automatically launch eighteen or discard normal failures.
- User authorizes regenerating the bad seed22 arrival realization. main_arrivals_r2
  uses one predeclared seed23 draw with the same independent permutation21/
  five warm roots/old-population calibrated rate. Maximum gap393.93s and last
  arrival1579.22s;retain old875.05s/2485.85s r1 without clipping. This is still
  CPU preparation,not sustained-pressure qualification. New task scopes require
  new matching rate calibration;do not transfer the old132.72s parameter blindly.
- Parallel pilot bound CPU actually passes5266+12,retains all5243 prior literal
  identities,no failures/errors/skips,2072inputs and native engine unchanged.
  Fifteen external tool/project/context inputs revalidate. Source-bound tests
  include real nested harness/session/auto-first/todo boundaries,explicit derived
  task scope and complete shape/native ownership/positive-service diagnostics.
  New pilot GPU collection still requires clean freeze,fresh sampler,current
  reservation/resources and complete prior comparison/reviewer actual exit.
- First parallel pilot r1 passes fresh1/8/24sampler/nativeBF16 but exits2 in
  shell validation before model/server startup:new profile was missing from
  the shell fanout whitelist. Preserve r1 startup_failure.json and raw log/
  registration;all owned processes actually exit,no natural work is captured.
  Shell whitelist is repaired and both native/Full actual entry paths pass
  CPU-only launcher regressions. Explicit r2 uses a new registration/run-root,
  same tasks/seeds,updated source-bound CPU and fresh reservation/resources/
  sampler. This is a startup repair,not a second completed workload result.
- Repaired parallel pilot bound CPU r2 actually passes5268+12,retains all5266
  r1 literal identities,no failures/errors/skips,2072inputs/native unchanged.
  Its two added tests traverse actual shell native/Full launch entries with
  the new profile and no GPU. Freeze this repaired source for explicit r2.
- Parallel pilot GPU r2 actually records from frozen392490cc on18906 after
  fresh sampler;client has six declared derived tasks. Keep this GPU snapshot
  unchanged. Its first CPU-only reviewer exits because exclusive-create write()
  was used for repeated state heartbeats. Preserve its stale state/error log;
  repair only the offline observer with existing atomic write_json. Twenty
  focused CPU cases pass,including three heartbeats and successful/failed final
  states. Relaunch the CPU observer separately,never restart the GPU for this.
- Independent parallel_pilot_gpu_entry_r2/live_start verifies750complete output
  prefix receipts,six distinct projects/explicit derived semantic inputs,actual
  new profile/4-8child/8-16root/24running/2048chunk/mixed-off/admission-off/seed21,
  sole owned GPU and nice0 measured processes,frozen392490cc sources unchanged.
  This is not complete-work/size/JOIN-shape acceptance. Repaired CPU reviewer
  actually waits from frozen4f614ae6,PID2080046,at nice19/CUDA
  hidden,registered in parallel_pilot_finish_r3.registration.json. Verify actual
  identity from that receipt rather than the transient nice wrapper. Its state
  heartbeats update;it has no GPU follow-up. Keep all failed startup/observer
  attempts and user worktree edits;the global performance goal remains active.
- Latest user requires auditing why oracle working-set choices rarely change
  admission and whether changed choices reach service,before rejecting the
  direction. New complete admission_funnel_r3 distinguishes1067controls,
  750positive-budget/no-paused-candidate,310nonempty,171all-ready-fit and111
  no-individual-ready-fit. Current remaining-rank same-category competition is
  10,not the stricter4initial-short/long-tier diagnostic;6sets actually change.
  All6changed exact request attempts obtain positive native work23-324ms later,
  without prior service/immediate pressure revocation. Eight actual winner/
  reference-only pairs are all served in winner order,median lead17.34s;
  this is not counterfactual JCT. Matched Full propagation has4/6earlier first
  outputs:selected workflows gain55.85s total,others lose80.81s;meanJCT loses
  1.387s. Full oracle only ranks paused periodic recovery,free-dispatch0;
  ordinary83/JOINhandover73/JOINfree9/sibling46event-grant episodes bypass it.
  Eager first-arrival grants and event competing sets lack exact journals.
  RankCPU.945s/controlCPU1.059s is measured subset cost,not zero overhead.
  No online/paper policy was changed;54focused CPU cases pass. Preserve
  complete audits and see workflow_admission_opportunities_20261030_zh.md.
- User permits further arrival-seed changes on workload grounds. Fixed24-31
  slot-only screening initially chooses28,whose747.51s submission window is
  only43.33%of expected;retain main_arrivals_r3 but do not promote it. Add
  an80-120%expected-window condition in the next predeclared32-47family,
  retain every trial/raw interval and use no GPU winner/JCT in selection.
  Such screening is a conditioned development schedule,not an unconditional
  Poisson sample or proof of steady pressure. New workload needs matched
  recalibration;arrival changes alone cannot ensure admission competition.
- Six-project pilot and its CPU reviewer actually exit completely:
  1016requests,6natural eligible workflows,model-workP90/P10=2.151,
  18singleton/6double JOINs,1nested,child calls median24/P9039.1,all6roots
  have final-return parallel deferrals. Shape still inadequate;retain all
  outputs and do not automatically expand18or launch another GPU arm.
- Declared32-47arrival screening with80-120%expected-window guard selects
  seed32. main_arrivals_r5 binds exact screened/frozen schedule hashes,keeps
  permutation21/five warm roots/old calibrated rate. Nominal mean132.72s,
  realized141.38s,first10.69s,max384.21s,last1837.95s/window106.53%.
  Keep r4 and its rejection:screen/generator floating-point multiplication
  order produced sub1e-12s offsets but different digests;unify arithmetic and
  reject any future frozen-plan mismatch. No resampling or GPU action followed.
  All16raw trials remain;conditioned development schedule is not pressure/
  competition qualification. Focused CPU now58passes,including actual entry
  binding/rejection. Runtime/paper policies unchanged;global goal remains active.
- New user prioritizes more sustained workflow arrivals and permits R5 changes,
  retaining JOIN prefetch. sustained36_20261030/preparation_r1 keeps the prior
  eighteen original tasks plus eighteen different original projects,all36
  live root/child CPU tool checks pass on the existing immutable image.
  Sources/tests are unchanged. Natural36 capture and matched rate calibration
  remain pending;8warm/28later is preparation,not proved steady pressure.
- Explicit WORKFLOW_COMBINATION_ADMISSION defaults off and is isolated from
  paper/native arms. New bounded6/8candidate controller covers periodic decay
  resume and coalesced first/continuation/sibling grants,preserves S0 on invalid
  summaries/deadline and never chooses ready active victims. It scores shared
  workflow distributions/observed ALL-JOIN coverage against frozen batch-cost
  metadata,uniquely charges known missing extents and permits native chunked
  input without an all-input gate. Existing protected JOIN/EOS recovery and
  blocked-donor authority remain;do not claim unrestricted global JOIN search.
  Requires a fingerprinted admission_cost_profile;only CPU fixture profiles
  exist so far. No formal R5 GPU profile,component qualification or gains.
- Related shared CPU run passes986 with the native source path and no skips;
  retain the prior976pass/10missing-native-path skips. Final focused set now
  includes66cases for set changes reaching actual native candidate selection,
  periodic/event entry,real observed JOIN budget handover,active continuity,
  shared extent/resource guards and chunked eligibility. This is not the full
  prior5268source-bound qualification or new GPU/performance acceptance.
- User proposes fixed arrival-index measurement cohorts under continuing
  background traffic. See steady_workflow_cohort_20261030_zh.md:exclude declared
  warmup,retain every measured workflow's complete JCT,never select first
  completed or censor normal tails. Keep measured identity private to analysis,
  identical open arrivals/background across arms and verify background covers
  the entire cohort. Current36total with8warm/18measured leaves10background;
  oldFull P95~2064s/max2150s suggests this may be too short.48distinct projects
  is a planning candidate,not an accepted steady-state experiment. Parrot
  OSDI24background-targetJCT,PackServearXiv2609.33224arrival-window warmup and
  NSDI06OpenVersusClosed provide related evidence,not an exact reused protocol.
  No GPU job was launched this turn;global all-baseline goal remains active.
- Latest workload acceptance separates natural source shape from serving
  pressure and policy gains. New per-invocation intrinsic-work attribution
  counts necessary new inputs/output once,with root,children and runtime-internal
  work separate;do not confuse total child share with largest-child share or
  use scheduling-contaminated JCT as the size label. The new offline audit
  preserves original sources/tails and never selects on native recomputation
  or Full wins. Fixed arrival cohorts follow common warmup,retain complete
  JCT and need the same sufficiently long frozen background across arms.
  Ready short/long individually-fitting but jointly-infeasible competition,
  actual service after grant,physical live KV pressure,tool overlap and
  loss/recomputation require separate calibration before formal comparisons.
- workload_requirements_20261030 refreshes full exact work for scope18 and
  parallel pilot6. Respectively model-workP90/P10=2.955/2.151,multi-childJOIN
  fractions19.59%/25%,child-callP50/P90=29/44.5 and24/39.1,rootparallelround
  medians1/1,totalchildworkmedians79.84%/70.23%,largestchild21.36%/22.80%.
  Both need new candidates;36project tool qualification is not source-shape
  or serving-pressure acceptance. Actual4/8reminders exist in pilot audit,
  so do not assume a disconnected counter explains long children. Narrow
  task scopes/path context while retaining root implementation/integration,
  natural returns and the unchanged4/8child,8/16root protocol. The new focused
  CPU set passes68tests+6subtests,no failures/errors/skips. No GPU capture or
  comparison ran here. See workload_acceptance_20261030_zh.md and retained
  sustained36 readiness_review_r1.json;the global optimization goal is active.
- Source-grounded checkpoint pilot now keeps the same six project/context slots
  but replaces mismatched short/medium derived deliverables with explicit actual
  source boundaries. Public CivicNet/GlobalFeed names differ from actual
  Twitter/Slack adapters;utils has colour helpers rather than the specified
  email group;CMS has Page/PageRevision rather than Article. Do not repair
  source/tests,claim these observations reproduce all public bugs,or hide old
  negative runs. Two long tasks retain their complete public requirements.
  Initial known paths are verified against source provenance,snapshot and fixed
  git blobs,never future traces/reference solutions. Parallel profile checkpoint
  protocol2 records question/path/evidence/finish/write boundaries and root's
  primary implementation responsibility;4/8child,8/16root,natural return,
  automatic first tool and depth2 remain. No new cap,forced DAG or round quota.
- Checkpoint pilot code is committed293a6e55. Actual focusedCPU66passes;shared
  bound_cpu_r1 passes5308tests+12subtests,retains all5268previous literal IDs,
  no failures/errors/skips,2086inputs unchanged and native unchanged. Fifteen
  external project/tool/context inputs revalidate. Reservation/resources were
  fresh and GPU idle at2026-10-30T20:05Asia/Shanghai;check again at launch.
  Small six-project natural collection may now qualify shape only;never queue
  Full/Thunder or expand36 automatically. New CPU reviewer also writes the
  intrinsic work/shape assessment after complete tokens,drain and actual exit.
- Checkpoint pilot source now actually records from frozenbf75bbd7 on18907,
  ops/workflow_checkpoint_pilot6_source_20261030_r1.registration.json,
  execution .worktrees/beliefkv-pilot6r1-capture-bf75bbd71cc7 and unchanged
  native11b84f7c. Fresh1/8/24sampler/BF16 passed. Independent gpu_entry_r1/
  live_start verifies29complete prefix outputs,6distinct projects/rootsemantic
  boundaries,actual2048/mixed-off/running24/seed21/native-admission-off,
  checkpointprotocol2/4-8child/8-16root,nice0 measured owners and soleGPU.
  Read-only live_start operator now accepts declared populations and budgets;
  the running source/qualified snapshot is unchanged. CPU reviewer2160998/
  start753260279 actually waits from the clean bf75bbd7 checkout atnice19/
  CUDA99999;finish_r1.registration.json and live state bind its handle.
  No next GPU comparison is registered. Keep all natural tails and do not
  treat this partial launch evidence as source-shape/pressure/performance.
- Checkpoint pilot bf75bbd7 and reviewer actually complete/exit:6projects,
  1145requests/39454528prompt/145284outputtokens,fresh drain and exact complete
  source. Shape still fails:modelworkP90/P10=1.644,childP50/P90=23/38.8,max56,
  23singleton/7doubleJOINs,2nested,rootparallelroundmedian1,totalchildwork76.40%.
  Seven doublegroups have positive shareddecode;all6roots need terminalparallel
  reminders. Do not expand this population or queue Full/Thunder. Complete
  finish_r1 and all source/tail/negative evidence remain.
- New complete exact-token prompt_delivery_r1 checks everychild's selected
  callboundaries:36/37children exceed8calls,and all36ninthprompts contain the8
  reminder;all sampled childprompts includecheckpointcontract. Wide Session1/2/3
  assignments still take47/46/56calls.One read-only derivedtask edits original
  scene.py. Natural eligibility is not taskconformance. Two legacy patchfiles
  are corrupt from command_output.strip();their actual workspace rawdiffs
  match afterstripping,with both hashes/errors retained.
- Next source candidate keeps identical six objectives and project/context mix
  but uses checkpointprotocol3 explicit taskarguments:path/evidence/finish/write
  fields,atmost2 supportingwritefiles,and explicit readonlytask intent. Keep
  4/8child,8/16root,auto firsttool,naturalreturns and no fixedDAG/callcap.
  Readonly removes advertised editingtools/role but is not OS write isolation;
  audit actual diffs. Adapter schema must match actualtask validation before
  declaring SPAWN/JOIN.Native/paper scheduling policies unchanged. New
  structured_checkpoint_pilot6_20261030 is preparation,not qualified shape or
  performance;full current CPU/GPU入口/complete-source review remain required.
- Structured checkpoint candidate now actually passes bound_cpu_r1:
  5571tests+12subtests,retains all5308prior literal identities,no failures/
  errors/skips,2094sourceinputs and native unchanged. Actual native Qwen3-Coder
  parser additionally passes8CPUcases for both schemas,two calls,arrays/empty
  writes and1/7/64character streaming. Focused cpu_r1 wrong root-return-count
  assertion and cpu_r2 optional-Docker skip are retained;full suite actually
  runs cached Docker evidence. New required-field sync/async callback tests
  verify invalid calls create no phantom child/JOIN;read-only nested harness
  checks editing tools removed. Fifteen external workload inputs revalidate.
  No shape/performance accepted;fresh freeze/reservation/resources/sampler
  still precede new natural collection.
- Structured checkpoint6 source now actually records from frozend58efb67 on
  18908,ops/workflow_structured_checkpoint_pilot6_source_20261030_r1.registration.json.
  Execution .worktrees/beliefkv-structured6r1-capture-d58efb6701b7,native remains
  11b84f7c. Fresh1/8/24sampler/BF16 passes. Independent gpu_entry_r1/live_start
  observes196complete prefix outputs,6rootsemantic and6childcreate semantic
  boundaries,actualprotocol3/4-8child/8-16root/2048/mixed-off/running24/seed21/
  native-admission-off,nice0 measured processes and soleGPU. CPUreviewer2205954/
  start753590755 actually waits from clean d58efb67 atnice19/CUDA99999,
  finish_r1.registration.json. No follow-up GPU is registered. After complete
  source/drain/actualexit,inspect shape and run audit_checkpoint_prompt_delivery
  with the new declaration;do not infer taskconformance,pressure or gains from
  startup. Keep old6 and new6 whole/tail/negative evidence;goal remains active.
- Structured checkpoint6 and its CPU reviewer now actually complete/exit:
  805requests/33667535input/111073outputtokens,6projects,33children,
  21singleton/6double JOINs,0nested. WorkP90/P10=2.237,childP50/P90=16/28.6,
  max38,rootparallelrounds1each,totalchildworkmedian55.65%,largest15.26%.
  All6doublegroups have positive shareddecode;all6roots still need terminal
  parallel reminders. Shape remains insufficient;do not expand or run Full.
  Exact-token audit finds28children exceed8 and all28ninthprompts carry the
  target reminder;readonlysourcechanges0,patchparseerrors0. Preserve complete
  finish_r1/prompt_delivery_r1 and all failures/tails.
- The next six-project shape candidate retains objectives/access/context/
  4-8child/8-16root and adds parallel_tasks with2--4 explicit assignments,
  executing actual native membertasks concurrently then ALL-JOIN. task remains
  for single checkpoints;firsttoolauto/no fixedDAG/quota/call/outputcap.
  Keep checkpointformat3 and separate parallelAPI1/fingerprint. Record actual
  sourcecall/memberidentity without token rewriting. Existing at-most-two
  terminal minimum attempts use the group API only in this profile,without
  native outer-call fanout bounds. Targeted80CPU cases pass including actual
  sync/async concurrency,nestedharness/sessions and native nestedargument
  parser1/7/64chunks. Current full boundCPU,freshfreeze/reservation/resources/
  sampler and complete natural source review remain required. This runtime
  preparation is not a scheduler contribution or performance result.
- Parallel checkpoint candidate bound_cpu_r1 now passes5600tests+12subtests
  in one source-bound execution,retains all5571prior literalIDs,no failures/
  errors/skips,2100inputs/native unchanged. Fresh freeze,reservation/resources/
  sampler and six-project natural-shape audit still precede any comparison.
- Parallel checkpoint6 now actually records from frozenb736480b on18909,
  ops/workflow_parallel_checkpoint_pilot6_source_20261030_r1.registration.json.
  Execution .worktrees/beliefkv-parallel6r1-capture-b736480b2175,native11b84f7c.
  Fresh1/8/24sampler/BF16 pass. Independent gpu_entry_r1/live_start verifies
  28complete prefix outputs,6project/rootsemantic boundaries,actualAPI1/
  protocol3/4-8child/8-16root/2048/mixed-off/running24/seed21/nativeadmissionoff,
  nice0 measured owners,soleGPU and frozen sources. CPUreviewer2243876/
  start753919741 actually waits atnice19/CUDA99999 from cleanb736480b;
  finish_r1.registration.json. No Full/Thunder/expansion or followupGPU queued.
  Retain complete natural outputs/tails,then audit shape,promptdelivery and
  actualreadonlydiffs before qualification;startup is not performance evidence.
- Parallel API pilot6 and reviewer now actually complete/exit:
  660requests/30292564input/100317outputtokens,6projects,20recordedJOINs
  (11single/8double/1triple),9positive shareddecode groups,1nested,
  rootparallelroundmedian1.5,terminalparalleldeferrals2. WorkP90/P10=3.561;
  actual28model-servedchildren haveP50/P90=16.5/23,max31. Keep the30declared
  child/raw16median separately:two malformed nonexistent roles produced
  zero modelcalls but phantom child/singleton events. Source shape remains
  insufficient;do not expand or run Full/Thunder on this source.
- Main now registers actual checkpoint caller role/depth tool scopes before
  execution;unknown roles and leaf delegation cannot create dependencies.
  parallel_tasks validates all roles before running any member. Targeted69
  cases pass with9new actual sync/async invalid-role/retry/scope cases;
  full source-bound bound_cpu_r2 remains separately required. Preserve
  b736480b and old raw events;this repair has no matching natural GPU proof.
  prompt_delivery_r3 retains all30declarations but reconstructs only28served
  children;all21ninthprompts contain8targetnotice,readonlychanges0/patcherrors0.
  Keep r1/r2 auditfailures and complete_review/declared_assessment with actual
  predeclared.6multimember target,distinct from original.5diagnostic. No new
  GPU followup is registered;goal remains active.
- Final repaired-role bound_cpu_r2 now passes5609tests+12subtests in one
  execution,retains all5600prior literalIDs,no failures/errors/skips,
  2100inputs/native unchanged. Actual complete_review_r1 passes complete
  source/drain/ownedexit and retains six shape/conformance shortfalls.
  No natural capture of this role repair or formal comparison has run.
- Latest user rejects narrowing original dataset deliverables to create short
  tasks. Withdraw focused_review_pilot6_20261030 before GPU registration;keep
  its prepared inputs and CPU evidence,never launch it. Prior completed derived
  pilots retain their historical identities,not original-task success labels.
  New candidates must preserve complete original title/description/task_prompt,
  original scenario IDs and pinned hashes. Select genuine simpler originals.
  Short-context projects are explicitly allowed;also retain long-context tasks.
  Context length,dataset difficulty and measured work are separate dimensions.
  Keep each root on a distinct original project;do not select on Full wins or
  native recomputation. Source/tests remain agent responsibilities. See
  original_task_selection_20261030_zh.md and the withdrawn candidate's marker.
- The withdrawn candidate's complete bound_cpu_r1 actually passes5629tests
  and12subtests,retaining5609literalIDs,2107sourceinputs/native unchanged.
  Runtime glob now maps mount-absolute patterns back to the workspace root;
  seven actual sync/async regression cases fail on the prior code. Optional
  focused400 repository tools/readonly-child protocol remain disabled by
  default and do not authorize changing original task requirements. No new
  natural GPU source or comparison is qualified by these CPU results.
- Original-task mix6 now selects complete public ChirpPulse comprehension,
  PalettePulse offline-sync architecture,BizSphere maintenance mode,
  ProductivityPulse rate limiting,CampusConnect waitlist and NimbusCustodian
  correlation-engine tasks. All six original projects/source/tests/context
  bytes remain complete;no derived deliverable or child-readonly/focused400
  restriction is enabled. Context and work hypotheses cross categories.
  Initial preparation reused old36pool Qwen3.5 length metadata and is retained
  but superseded before GPU. preparation_r2 recomputes every full context with
  current pinned Qwen3-Coder tokenizer:30631/35046/50948/91651/118586/119129.
  New tests reject omitted later sessions,derived IDs/requirements and duplicate
  projects,and verify actual tokenization. Live tools,source-bound CPU/fresh
  freeze/resources and complete natural shape review precede comparison.
- Original mix6 coder preparation_r2 actually passes all6fresh live root/child
  tool checks and bound_cpu_r1:5644tests+12subtests,all5629prior literalIDs
  retained,2121inputs/native unchanged,no failures/errors/skips. All original
  prompt/scenario/source/context and real current-model tokenizer checks pass.
  No natural shape,physical pressure or comparative gain is qualified by this.
- Original complete-task mix6 now actually records from frozenb7ac1491 on18910:
  ops/workflow_original_task_mix6_source_20261030_r1.registration.json,
  execution .worktrees/beliefkv-original6r1-capture-b7ac1491dcd2,native11b84f7c.
  Fresh1/8/24sampler/BF16 and independent gpu_entry_r1/live_start verify26
  complete outputs,six distinct original projects/rootsemantic boundaries,
  actual2048/mixed-off/running24/seed21/native-admission-off,parallelAPI and
  4-8child/8-16root,nice0 measured owners/soleGPU/frozen sources. CPUreviewer
  2310043/start754404620 actually waits atnice19/CUDA99999 from the same clean
  capture checkout;finish_r1.registration.json binds it. No nextGPU arm is
  registered. Keep all complete natural outputs/tails;after actualexit/drain,
  inspect intrinsic work,child lengths and positive multi-member JOIN service.
  Startup does not qualify source shape,physical pressure or scheduler gains.
- Latest user allows reusing prior complete original workflow traces across
  capture concurrency;collect only missing task-size tiers or distinct projects.
  Changing arrivals/concurrency does not require whole natural recollection.
  The latest threshold is model-workP90/P10>=5,superseding the brief3minimum.
  Keep review_r1's3.081reuse candidate as superseded preparation,not accepted
  heterogeneity. Tool-aware tiers use model/observed-tool causal paths with
  parallel max,excluding source queue/runtime gaps;model ratio is independent.
  Preserve unique original projects/full tokens/dependencies/stopping/tools,
  normal tails and fixed original-task representatives;never choose lucky
  repeats or policy winners. No old JCT is a new-load performance result.
- Reuse inventory covers40different original tasks across18projects from four
  compatible completed sources. Original prompts and selected raw token/event/
  work identities are checked. preparation uses CPU-only metric refresh and
  no newGPU. Current six-root original capture and its reviewer remain frozen.
  Continue incremental completion of missing tiers/projects;qualify JOIN shape,
  background supply and physical pressure,and freeze multisource replay bindings
  before any new performance claim. See reused_original_workflows_20261030_zh.md.
- Current5threshold reuse review_r2 is explicitly unmet:the40checked compatible
  original-task pool has max/min model-work envelope4.46559,so cannot supply
  P90/P10>=5even ignoring project constraints. Keep threshold3review_r1/
  supersession marker and all full sources. Focused30tests+6subtests pass.
- Original mix6 source and CPUreviewer now actually complete/exit:
  970requests/145508outputtokens,6natural eligible roots,fresh drain/owned exit.
  ModelP90/P10=3.665878,below current5;33children callP50/P90=14/36,max105;
  22JOINs:14single/6double/1triple/1four,0nested,rootparallelroundmedian1.
  Keep whole new source and all tails;use its full original workflows only as
  incremental inventory,not automatic expansion or comparison. Do not re-run
  whole older populations merely because this shape remains insufficient.
- Latest user switches heterogeneity to model-workP80/P20:minimum3,report4as
  stricter diagnostic;P90/P10 is reference only. New summary/acceptance/reuse
  entrypoints name the actual ratio;preserve all old P90 reports unchanged.
  review_r3 old18ratio2.322 is unmet;completed original6ratio3.250 passes3
  but not4,with its other JOIN/child shortfalls retained.
- User explicitly requires moving beyond repeated six-task diagnosis. Reuse
  complete originals covering22distinct projects,then collect the26missing
  syntax-qualified original projects toward48project candidates. Source
  collection concurrency may be bounded separately;formal replay uses frozen
  sustained arrivals,warmup/arrival-index measured cohort/common background,
  complete JCT and actual ready-capacity/service/physical-KV evidence.
- The original-project supplement now materializes26complete original tasks:
  17comprehension/2architecture/7feature,pristine source/tests,real Qwen3-Coder
  contexts19068--178191. All26fresh live root/child tool/sharedworkspace/exitcode
  checks pass;project tests remain agent responsibilities. Source capture has
  population26/client rootwindow12/native running24 with no external schedule.
  Keep scheduled formal arrivals open:do not permit a hidden smaller client
  window. New source reviewers check the declared project population,not six.
  This preparation is not natural shape,pressure or performance qualification.
- Supplement26 bound_cpu_r1 actually passes5672tests+12subtests,retains all5644
  prior literal identities,no failures/errors/skips,2131inputs/native unchanged.
  Fresh freeze/reservation/resources/sampler and actual GPU collection remain
  distinct from this complete CPU result. Preserve all original-source evidence.
- Supplement26 now actually completes/exits:26natural eligible roots,3885
  requests/470090outputtokens,108JOINs(74single/24double/5triple/5four),1nested,
  zero guards,fresh two-snapshot drain and complete CPU reviewer. Source
  frozen2e8e45cd/native11b84f7c;workP80/P20=2.464,childP50/P90=17/32 and
  rootparallelroundmedian1retain shape limitations. Do not recapture old pools.
- Composite48_r1 independently compiles3source clock streams and retains8781requests/
  1168947outputs. ItsP80/P20=2.916240fails3. Composite46_r1 removes only2whole
  nearest-median intrinsic-work projects(NFT035bug/CampusConnect001bug),keeps
  both excluded sources/all tails,and reaches3.074715,not4. This is workload
  selection on intrinsic work,never policy wins or native recomputation.
- New composed replay requires explicit source_kind,bound original completion
  proofs and external arrivals;never fabricate a natural capture or native
  JCT baseline. Corpus bytes,stopping,source events/protocols and independent
  native clocks stay bound. The existing natural-source checks remain default.
  Sustained46_r1 declares6warm/18arrival-index measured/22background,seed31
  arrivals/permutation,mean80s,submissionend3353.249s,running24. Cohort identity
  remains private to analysis,full JCT includes tails,all arms share background.
  The retainedFull control initially keeps work sorting off;oracle/learned
  ranking and clear gains remain unachieved. Tool prefetch/layerhandoff/mixedoff,
  JOIN/EOS on;TraceLab observable network classes/heldout durations unchanged.
  Shape/physical competition/background coverage still require actual review.
- Before any policy GPU registration,sustained46_r3 supersedesr1/r2. Initial
  fill6is separate from arrival-index warmup10,then18measured/18background.
  Arrival seed31 and all offsets stay fixed;permutation33is the first integer
  seed from31meeting measured modelP80/P20>=3(31:2.406841,32:2.625945,
  33:3.287564). Full traffic ratio3.074715;4unmet. This disclosed source-work
  selection uses no policy performance. Common throughput window154.514--
  3353.249s includes all background output;normal measured tails remain complete.
  r1/r2have no policy GPU runs and preserve their complete CPU evidence.
- Final sustained46_r3 source-bound CPU passes5699tests+12subtests,retains all
  previous5699literal IDs,no failures/errors/skips,2153inputs/native unchanged.
  Fresh clean freeze/resource/model-position checks still precede policyGPU.
- Sustained46 positionr1 passes24exact token/session/native-stop GPU requests
  and1/8/24native-sampler benchmarks,then stops before Full registration on a
  composed-source duration_seconds KeyError. No Full work ran. Keep this failed
  registration/state and successful GPU component receipts. New timing-summary
  repair records unavailable aggregate source timing asNone,never a fabricated
  native baseline;5701tests+12subtests pass,retain5699,2153inputs/native unchanged.
  Only registration analysis changed;the four GPU-checked sampler/receipt
  modules,native engine,model,workload,tools and arrivals remain byte-identical.
- Sustained46 Fullr2 now actually runs from frozen8d29bb08/native434c35dd on
  18912:ops/workflow_sustained_original46_full_20261030_r2.registration.json,
  execution.worktrees/beliefkv-causal-8d29bb086928-83b30d9edbba. Reused successful
  positionr1 GPU receipts/sampler checks with unchanged4server modules and
  rebound native source path/state hash only. Independent live_full_r1 verifies
  401exact completed prefix receipts,declared46,soleGPU/nice0 measured owners,
  observed2scontrol,worksorting/toolprefetch/frontier/layeroff,predictedoffloadon.
  CPUreviewer2499455/start755385216waits atnice19/CUDA99999 with30sheartbeats,
  full_review_r1.registration.json;it runs complete_work/drain/ownedexit/decode
  then fixedcohort audit. No Thunder/otherGPU followup is registered. Finish all
  work and pressure/background review,then match ThunderAgent acting-decay.
  This startup is not source-shape acceptance,complete results or a speedup.
- Latest user resets the next objective:validate sustained workload/arrivals/
  fixed-cohort complete JCT first,then compare oracle short-workflow/JOIN-set
  admission with the identical retained Full control. Defer ThunderAgent and
  other baselines and any new learned work predictor until clear oracle gains.
  Diagnose actual ready-capacity competition,all admission entrypoints,packing,
  pressure revocation/native service and total control cost;retain JOIN/EOS.
  Replace inadequate original-task mixes/arrival supply with disclosed matched
  controls if needed,never select policy winners or promise SJF must improve.
  R5section14 is the predictor-stage reference:6candidates,8only when needed,
  0.5ms new combination softbudget,total metadata/native/background costs extra.
  See docs/design/oracle_first_validation_goal_20261030_zh.md. The active goal
  API refused replacement of an unfinished objective;do not falsely complete
  the old goal to bypass it. Latest user scope and this record govern work.
- Workload-validation CPU audit found a cohort-output bug:prefill token_delta
  counts input,so use output_token_delta in all phases and reconcile full output
  by workflow with exact receipts. Running GPU and JCT boundaries are unchanged.
  Frozen reviewer8d29bb08's cohort throughput must be superseded by corrected
  CPU analysis after full completion;do not hot-edit it or accept its old count.
  Supply r1 seals38arrivals/25ends/13of18measured,2230states/360ready-paused/
  3pair-competition states(two same-pair adjacent),not all actual decisions or
  physical feasibility. Source work reconstructs in topological order across
  independent capture clocks. Targeted84tests pass;full CPU binding separate.
  Host high usage is not useful loss:session-prefix proxy0,but other attributed
  Host-block FULL recompute4485units,retain both semantics. No workload/oracle
  gain accepted and no GPU followup. See original_project_supplement research.
- Validation fullCPU_r1 preserves5720pass/3nested-parallel timestamp failures/
  12subtests. Runtime callback publication now serializes causal timestamps
  under the existing lock,preserves raw callback_observed_ts_ms/adjustment and
  tool duration_ms,and sends identical trace/control batches. Two deterministic
  reorder regressions plus full adapter/nested/audit focused133pass;full CPUr2
  still required. This affects future capture only,not frozen running replay.
  Disabled R5 cached-metadata microcheck shows6candidates244/276usP50/P99,
  8all200softdeadlinefallback;free-page-only resource summary also needs audit.
  Keep these as CPU/source limitations,not end-to-end performance evidence.
- Corrected workload-validation boundCPU_r2 actually passes5725tests+12subtests,
  retains5701prior literalIDs,no failures/errors/skips,2158bound inputs/native
  unchanged. Preserve failedr1 and all source observations. Running8d29bb08
  control remains frozen;use new CPU cohort/output/supply analysis after complete
  work/drain/ownedexit. No oracle/learned/baseline performance accepted.
- Corrected completeCPUreview now actually waits from clean frozenaa12207a:
  original_project_supplement_20261030/corrected_full_review_r1.registration.json,
  PID2528822/start755762380,nice19/CUDA99999/OMP1/OpenBLAS1 verified. After
  completework/freshdrain/actualexit,it runs corrected cohort outputs/receipt
  reconciliation and complete supply audit. Preserve/supersede old cohort;
  do not hot-edit serving8d29bb08. No GPUfollowup registered and no workload/
  steady-state/oracle acceptance. Stage1remains active under the latest goal.
- Sustained46 control/reviewers now actually complete:all46/8388exact requests/
  1131444outputs,freshdrain and actualownedexit. Corrected cohort18meanJCT
  1088.584474s/P95=2030.000763s,commonwindow output310.806065tps,submission
  end3353.249coverslastmeasured3204.231. Full duration4243.830s/all46mean901.409s.
  Complete supply3116states/425ready-paused/5pairandsetcompetitionstates;
  3+set audit addsnone,not exacttick/nativefeasibility. Late ready supply falls.
  Corrected grant audit fixes same-epoch submitted freeJOIN successors:all426
  programgrants/420journalentries havefollowingnative service;preserve old11
  unproven and correctedaudit,not a JCT causal claim or completeentrycoverage.
  See docs/research/sustained_original46_control_20261030_zh.md. Phase1 continues,
  no oracle/learned/baseline GPU followup.
- Current-tokenizer scan finds5previously excluded syntax-qualified Python
  originals withfullcontexts188616--231616,plus250040-tokenexcludedproject.
  Prepareonlythese5,notrecaptureold48;53originalprojectcandidates possible.
  All5originalcomprehensiontasks(3easy/1medium/1expert),source/tests/fullcontext
  unchanged,4-8child/8-16root;sourcepopulation/clientwindow5,nativerunning24.
  Live root/childtools/sharedworkspace/nonzeroexit/cleanup allpass. Data at
  remaining_original_projects_20261030/preparation_r1;sourcecollector requires
  full currentCPU/cleanfreeze/reservation/resources/sampler. Do not treat this
  five-project supplement as formal comparison population or proven shortwork.
- Remaining-original5 pre-capture boundCPU_r1 now passes5738tests+12subtests,
  retains5725literalIDs,no failures/errors/skips,2169bound inputs/native unchanged.
  review_declaration.json is a separate bound diagnostic sidecar;original capture
  contract/tasks/configuration remain unchanged. Fresh cleanfreeze/resources/
  reservation/CUDAsampler still precede collection. Use existing dedicated
  /home/longhao/experiment/ops/cpu_plotting_20261027/bin/python for figures:
  beliefkv-next lacks matplotlib;preserve failed emptycontrol_figures,CPUretry
  goes to control_figures_cpu,not a GPU failure or reason to rerun serving.
- Remaining-original5 frozen4bcfb795 now actually completes777requests/
  104110outputs,all5naturallyeligible,noguards,freshdrain/ownedexit and independent
  CPUreviewer actualexit. Retain21JOINs(16single/3double/2triple),5positive
  shareddecodegroups,28childrenP50/P90calls18.5/33.3 androotparallelmedian1.
  New53projectpool selects52wholeoriginals by existing nearest-work-median
  removal rule:9313requests/1253882outputs,modelP80/P20=3.003432,4unmet.
  Preserve excludedproject andallnormal tails;no idealshape orGPUcompetition
  accepted. Nextsupplycalibration plansinitial12/mean80s ratherthanblindly
  doublingrate;18fixedarrival-indexmeasured,completeJCT/commonbackground.
  Oracleprototype repairs S0 beforeworkranking,retains bestknownfeasible on
  softdeadline,preserves protectedJOINrights andcounts onlybounded32settled
  privateblockedcoldleaves inphysicalsummary. Targeted80pass;boundCPU5754plus12
  subtestspass,retainall5738priorliteralIDs,2169inputs/nativeunchanged.
  These default-off oraclemechanics are not a learnedpredictor orperformance
  result. Add exactinitial/periodicdecisioncoverage before nextcontrol.
- Sustained52 preparationr1 passes5755testsplus12subtests but is superseded
  beforeGPU:its0msdispatch would differ from the plannedR5oracle500msdispatch.
  r2 declares common500msboundeddispatch,2sfullcontrol,12initial/16warm/
  18measured/18background,mean80s,arrivalseed31/permutation41. Population,
  exacttokens/tools/arrivals/resources remainunchanged;measuredworkP80/P20
  3.058225. Preservefirst31--41intrinsicworkseedsearch andno policyoutcomeuse.
  Full keepsworksortingoff;JOIN/EOSon,toolprefetch/frontier/layer/mixedoff.
  Exactinitial/periodiclogicaldecisionjournals use readonlyhooks inside the
  unchanged inheritedThunderAgent tick;do not override thatmethod. R5 can
  laterconsume the samecadence. Separateperiodicdecisionauditor11targeted
  tests(includingconfigoverride)pass;it doesnot implyphysicalfit orcausalJCT
  attribution. Freeze/currentbinding/realGPUposition still precede nextcontrol.
- Sustained52r2 boundCPU actually passes5756tests+12subtests,retainsall5755
  priorliteralIDs,no failures/errors/skips,2179inputs/native unchanged.
  Separate11periodic-auditor/configtests pass;preserve earlyjournalfixture's
  method-identityfailure and corrected inherited-tick proof. NoGPU comparison
  accepted;fresh committedfreeze/resources/position-and-sampler still required.
- Sustained52Fullr1 now actually runs from8eb485f1/native434c35dd on18914,
  ops/workflow_sustained_original52_full_20261030_r1.registration.json.
  Fresh24exact token/session/native-stop requests(all24native stops)and1/8/24
  sampler pass. Independentgpu_entry_sustained52_r1/live_full_r1 verifies
  216completeprefixreceipts/19arrivals,soleGPU/nice0,actual2048/running24/
  mixedoff/dispatch500/control2000/worksortingoff/toolprefetchoff/layeroff,
  predictedoffload/prepareon. CPUreviewer2598458/start756403143actuallywaits
  nice19/CUDA99999,full_review_sustained52_r1.registration.json;after complete
  work/freshdrain/ownedexit it runs control/cohort/supply/periodic audits.
  NoGPUfollowup registered. Early107commonstatesready-pausedP504,4logical
  setcompetitions/127actualticks,not fullentrycoverage/physicalfit/JCTgain.
  Intrinsicfixedtool/modelcausalpathP80/P20=3.911604isCPUproxyonly. Keepall
  normal tails and reviewlate supply/Hostbefore phase1acceptance ororacle.
- Sustained52 live prefix review now observes109cross-workflow logical set
  competitions/946actual periodic controls,462resumeepisodes/2pressure
  revocations/448following exact-attempt service at that prefix. These include
  outside-window times and are not directly comparable to the old46sampled5.
  Useful-state loss is nonzero:2,682,026/134,296,472old-inputprefix tokens
  (~2.0%,24requests);separateHost-block attributed recompute2,892,413FULLunits.
  CPU next-request-reuse-capped union inlive_demand_r2remains1.042--1.233of
  combined2,839,704token capacity at38probe/latest points. This future-only
  diagnosis removes obsolete/no-next-call suffixes and excludes returned
  temporarychildren;never deliver its future data to online policy. Keepall
  normal tails;do not acceptHostpressure or automaticallyqueueoracle.
  Idle mirror recyclefalse isintentional withmutuallyexclusive2GiB demand
  reserve;nativeHosteviction already reclaims safeFULLduplicates first.
- The next explicit oracle diagnostic usesglobal_ready_remaining_path across
  legacy recovery categories while retaining observedJOINpriority,ready-only
  candidates,activecontinuity andnativeauthority. Missingartifactfield keeps
  historicalwithin-categorybehavior. KeepR5costcombinationasaseparatebranch:
  cheapcontinuationutility isnotadirectSJFupperbound. BeforeoracleGPU,review
  completeworkload/Hostandsupply;freezeinitialtarget>=5%measuredmeanJCTgain
  and>=98%commonwindowoutputthroughput,nosingle-runstatisticalproof.
  global/demandboundCPU_r2actuallypasses5793tests+12subtests,retains5756,
  nofailure/error/skip,2188inputs/nativeunchanged. RunningFull8eb485f1stays
  frozen;newglobal/preparation/summarycodehasnoGPUresultandnopredictor.
- Sustained52r3 CPU preparation changes onlyarrivalsto10initial/mean100s,
  retaining16warm/18fixedmeasured/18background,seed31/permutation41,
  exactlysamecommonenvironment/tools/source/orderandcohortmembersasr2.
  Commonwindow231.442255776923--4343.970772418398s,fullnormalJCTtails.
  BoundCPU5793plus12subtestspasses,retainsall5793,2192inputs/nativeunchanged.
  This isnotyetGPUqualificationoracceptedpressure/background. Completeoldr1
  normally;reviewHostoverflowandcoveragebeforelaunchingmatchedr3control,
  nevercompareitsoraclewithold12initial/80sFull.
- Phase-one decision now usesreview_sustained_workload.py:completefixedcohort/
  receiptconservation/backgroundcoverage/modelratio>=3,atleast10actual
  competitioncontrols,ready-pausedin>=quarterwindowstates,zerosame-context
  prefixlossproxy,and>=10regular<=60sdemandobservationswhoseCPU next-reuse
  unionfitscombinedcapacity. Thesearediagnosticminima,notR5shapeorsteady-state
  proof. Oraclepreparationrequirespassingdecisionwithexactreviewinputhashes.
  live_demand_r3periodiclabelsareinvalid(overwritteneventloopvariable);keep
  SUPERSEDEDmarkerandfailedCLIregression. Correctedr4has79regular/63loss/
  1latestpoints;46of53commonwindowregularsamplesovercapacity,max1.329
  at2520s(27liveworkflows/50contexts). Fullr1remainsfrozenandrunning.
  workload_gate_bound_cpu_r2passes5805tests+12subtests,retains5804,
  2195inputs/nativeunchanged,nofailure/error/skip;focused30passes.
- First52controlactuallycompleted52/9313/1253882outputs,freshdrain/ownedexit.
  Fixed18meanJCT2510.579195s/P954365.823872s;commonwindowoutput240.193446tps.
  Phaseonedecisionrejectsbackgroundcoverage(1376.788suncovered),oldprefix
  loss6894726/332789113tokens(63requests),andregularreuseunionpeak1.329.
  Completeperiodicaudit2813controls/322competitions/1259resumes/8revoked/
  1231followingexactservice. Preservefailedcohortreview:8zero-outputinflight
  decode rowscompleteafterworkflowreturn,max375.296ms;onlyverifiedunchanged
  outputcountersandpre-returnlaunchareexempt,positiveoutput/JCTnotshifted.
  Correctedcohort_r2reconcilesall1253882outputswithreceipts,modulehashbound.
- Balanced52Fullr2nowactuallyrunsfrom068be28a/native434c35ddon18916,
  ops/workflow_sustained_original52_full_20261030_r2.registration.json.
  Fresh24exactposition/session/native-stop(24stops)and1/8/24samplerpass;
  independentliveproof404completedreceipts,soleGPU/nice0/500ms+2s/profiles.
  Early830sCPUprefixhas17actualperiodiccompetitions,0oldprefixloss,
  latestreusableunion84.6%combinedpool;notcompleteacceptanceorsteadystate.
- Globaloraclefirstregistrations/continuations/siblingnotificationsnowshare
  DeferredFreeAdmissionwithR5atnativedispatchboundaries;500msisfallback,
  notmandatoryper-requestdelay. PreserveJOIN/EOSrightsandactivecontinuity;
  defaultoff,newcodehasnoGPUoraclegain. shared_dispatch_bound_cpu_r2passes
  5814plus12subtests,retains5814,2196inputs/nativeunchanged. Targeted127pass.
  Priorcohort_tail_bound_cpu_r1keeps5810pass/1timing-dependentsemanticfixture
  failure;deterministiclogicalclockplusadapterdeadlinefallbacktestfixes
  fixturewithoutchangingproduction500000nsbudget. Keepallfailedproofs.
- Balanced52completeCPUrevieweractuallywaitsfromclean55671ffb:
  full_review_balanced52_r1.registration.json,PID2675603/start757283200,
  verifiednice19/CUDA99999/OMP1/OpenBLAS1. Itcollectsfourcompletereviews;
  runfinalregular-demandandphaseonedecisionseparatelybeforeoracle.
  register_sustained_oracle.pyrequiresacceptedhash-boundcompletecontrol,
  exactnonrankingconditions/aliases/streammetrics,completeoracleprogress,
  freshresources/position/sampleranduniqueregistrations. NoGPUoraclecalled.
  registration_bound_cpu_r1passes5828+12subtests,retains5814,
  2198inputs/nativeunchanged,nofailure/error/skip;focused37pass.
- Sustained oracle comparison now has a fixed-cohort CPU reader:
  scripts/review_sustained_oracle.py. Require the same frozen cohort/window,
  full per-workflow output and request conservation; whole-population JCT is
  secondary. Bound CPU cohort_comparison_bound_cpu_r1 passes5834+23subtests,
  retains5828 literal identities,2200inputs/native unchanged,no failure/error/
  skip. Balanced52 Full remains frozen068be28a and running. All18 measured
  members finished by3189.034921s, before final background arrival4343.970772s;
  wait for all52 normal tails, complete reviews/final demand and workload gate
  before oracle registration. No new predictor training or oracle GPU result.
