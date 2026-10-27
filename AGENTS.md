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
