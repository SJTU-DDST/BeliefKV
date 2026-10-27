# Experiment Invariants

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
