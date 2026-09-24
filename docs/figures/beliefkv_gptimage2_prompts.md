# BeliefKV 科研插图生成提示词

这些 prompt 依据 `docs/architecture_status_zh.md`、`docs/v0520_scheduler_redesign_zh.md`
和 `docs/beliefkv_design.md` 整理，面向 GPT Image 2 的单图生成。每张图独立生成，
推荐 `2048x1152`、`high`。本组图的英文标签是系统术语，图像生成后必须逐字校对。

## 共用风格约束

将以下约束附加到每一条 prompt：

```text
Create one camera-ready systems-research paper figure, not a slide, poster, dashboard,
or decorative illustration. Wide 16:9 landscape composition on pure white. Precise flat
2D vector-like technical drawing, thin consistent dark-gray strokes, restrained navy,
teal, and amber accents, generous whitespace, strong alignment, compact sans-serif
typography. Use concrete workflow nodes, queues, timelines, memory strips, and directed
edges; do not put the whole system inside one large box and do not turn each concept into
a large rounded card. No gradients, shadows, 3D, perspective, stock icons, texture,
background scenery, decorative flourishes, or oversized title. Draw only the listed
short labels, exactly as written; do not invent prose or extra labels. If an exact label
cannot be rendered, leave that label out rather than misspelling it. Keep arrow direction,
causal order, and solid-versus-dashed semantics exact.
```

## 1. 总体系统框架

```text
Use case: infographic-diagram
Asset type: camera-ready overview figure for a computer-systems paper
Primary request: Draw the BeliefKV architecture as three connected but visually distinct
flows: an observed agent-workflow flow, a scheduler control path, and a KV data/telemetry
path. Use recognizable small graph nodes and queue entries instead of large module cards.
Composition: Left-to-right, four compact regions. At left, an agent DAG: parent R spawns
children A and B; A waits on a tool; child returns feed JOIN_ALL; the parent becomes ready
only after confirmed return. In the center, show the same live dependencies as an RCCG
graph feeding a ranked action frontier. At right, show a waiting-request queue entering a
synchronous scheduler safe point, then SGLang PrefillAdder, then a GPU batch. Below that
main line, show an asynchronous predictor consuming a compact snapshot and returning a
short-lived candidate to the safe point over a dashed arrow; it must never bypass live
revalidation. Below, show separate FULL and MAMBA strips in GPU HBM and NUMA-local Host
DRAM, with D2H and H2D directions and an ACK ledger. At the bottom, show causal events,
request-token data, and pool/ACK telemetry joining into labels, audit, train/calibrate,
and a gated model artifact.
Text (verbatim): "R", "A", "B", "TOOL", "JOIN_ALL", "RCCG", "ACTION FRONTIER",
"WAITING QUEUE", "SAFE POINT", "PREFILLADDER", "GPU BATCH", "ASYNC PREDICTOR",
"CANDIDATE", "FULL", "MAMBA", "GPU HBM", "NUMA HOST", "D2H", "H2D", "ACK",
"TRACE JOIN", "LABEL + AUDIT", "TRAIN / CALIBRATE", "GATED MODEL".
Constraints: Solid dark arrows mean observed/native scheduling. Dashed blue arrows mean
prediction/candidate only. Teal dotted arrows mean ACK or telemetry feedback. Add one
small explicit status label: "PREDICTIVE PHYSICAL ACTIONS OFF BY DEFAULT ON QWEN3.5 / SGLANG 0.5.20".
Do not depict prediction as already producing physical transfers on that stack.
Avoid: One large system box, paragraphs, unexplained crossovers, generic cloud/server
clipart, inferred performance numbers, claims of measured throughput improvement.
```

## 2. Causal frontier

```text
Use case: infographic-diagram
Asset type: algorithm figure for a computer-systems paper
Primary request: Explain how observed causality creates an action frontier. On the left,
draw parent P waiting at JOIN_ALL with three child nodes A, B, C; A and C are RETURNED,
B is RUNNING, so B is the unique remaining blocker. Direct child-to-JOIN edges toward the
barrier, and show the barrier releasing the parent only after confirmed RETURN. On the
right, draw an ordered frontier with four compact entries: sole JOIN blocker, unlocking
chain, message producer, other ready work. Highlight B as rank one. Add a small dashed
time-distribution marker near B to show that a predicted return-time estimate can change
when prefetch is timed but cannot change the causal readiness state.
Text (verbatim): "P", "A", "B", "C", "JOIN_ALL", "RETURNED", "RUNNING",
"1 SOLE JOIN BLOCKER", "2 UNLOCKING CHAIN", "3 MESSAGE PRODUCER",
"4 OTHER READY WORK", "RETURN-TIME BELIEF: TIMING ONLY".
Constraints: Use directed dependency edges and a visible AND barrier. The parent is not
READY before B returns. Predictions are dashed and must not be drawn as causal edges.
Avoid: Generic ranked-list poster, decorative agent avatars, invented probabilities,
ambiguous edge directions, extra explanation paragraphs.
```

## 3. 三层调度职责与权限

```text
Use case: infographic-diagram
Asset type: scheduler-control figure for a computer-systems paper
Primary request: Show four narrow decision scopes in a left-to-right composition, using
objects rather than large boxes. First, a set of workflow tokens W1-W4 with a highlighted
active working set. Second, a visible request queue with short-lived eligibility tickets
bound to request ID, epoch, and generation; mark one stale-session request as skipped.
Third, show a candidate action package pairing beneficiary B with parkable victim V and
including scheduling order, an expiry, and possible KV actions. Fourth, show a synchronous
safe point rechecking identity, context generation, capacity, and lock before calling the
SGLang allocator, which retains final admit/reject authority. Under the drawing, separate
the solid native admission path from the dashed predictive physical-action path; mark the
latter as staged and disabled by default on Qwen3.5 / SGLang 0.5.20.
Text (verbatim): "DYNAMIC WORKING SET", "W1", "W2", "W3", "W4", "TICKET COMPILER",
"REQUEST ID", "EPOCH", "GENERATION", "SKIP: STALE SESSION", "JOINTPLAN",
"BENEFICIARY B", "VICTIM V", "ORDER", "ACTION", "EXPIRY", "SAFE POINT",
"CAPACITY + LOCK", "SGLANG ALLOCATOR", "ADMIT / REJECT", "NATIVE ADMISSION",
"PREDICTIVE ACTIONS: OFF".
Constraints: Working-set selection emits no request ticket and no KV command. A ticket is
not a GPU-memory reservation. A JointPlan candidate has no commit authority. Only the
safe-point/native path may authorize work.
Avoid: Four equally large rounded rectangles, a direct arrow from predictor to GPU,
implying candidate acceptance guarantees allocation.
```

## 4. Scheduler safe-point 时序

```text
Use case: infographic-diagram
Asset type: sequence diagram for a computer-systems paper
Primary request: Draw a six-lane sequence diagram with lanes Agent Runtime, Event Adapter,
Predictor Process, Scheduler Main, SGLang Allocator, and DMA Worker. Time flows downward.
Show TOOL / SPAWN / RETURN events entering a bounded event adapter. The adapter sends a
compact delta asynchronously to a predictor process. The predictor writes a latest-wins
result mailbox and wakes the scheduler through an event descriptor. In the scheduler's
batch-selection path, show one synchronous safe-point bracket containing: drain bounded
events, poll latest result without waiting, rebind request/context/epoch identity, recheck
live state, then call the native allocator. The allocator may start asynchronous DMA;
ACK returns later and updates the physical-state mirror. Keep the inference lane concurrent
with scheduler progress.
Text (verbatim): "AGENT RUNTIME", "EVENT ADAPTER", "PREDICTOR PROCESS",
"SCHEDULER MAIN", "SGLANG ALLOCATOR", "DMA WORKER", "TOOL / SPAWN / RETURN",
"COMPACT DELTA", "LATEST-WINS MAILBOX", "NON-BLOCKING POLL", "RECHECK ID / EPOCH",
"BATCH SELECTION", "ADMIT / SKIP", "ASYNC DMA", "ACK".
Constraints: The safe point is synchronous inside scheduler selection, not a periodic
thread. The predictor is asynchronous and never holds or accesses live scheduler state.
SGLang owns final physical admission.
Avoid: A generic flowchart, a separate safe-point thread, arrows implying the scheduler
waits for model inference, overlapping swimlane messages.
```

## 5. PREPARE / COMMIT / PREFETCH 时序

```text
Use case: infographic-diagram
Asset type: resource-transfer timeline for a computer-systems paper
Primary request: Draw a five-lane timeline for one parked context. Lanes are context,
belief, D2H, residency, and H2D/service. Time flows left to right. Begin with TOOL_START
or child wait. Show rolling P10/P50/P90 remaining-time estimates and an optional provisional
completion signal; neither is a confirmed return. Show PREPARE_HOST copying KV to Host
while the GPU copy remains resident, followed by D2H ACK. Separately show COMMIT_CPU only
after an observed beneficiary deficit, and only then release the GPU copy. Near latest
start, show PREFETCH_GPU/H2D overlapping the remaining wait. Require H2D ACK before
reentry/admission; confirmed RETURN or JOIN_SATISFIED remains the actual readiness event.
End with first GPU service. Mark the whole physical action path as a P6 target that is
disabled by default on Qwen3.5 / SGLang 0.5.20.
Text (verbatim): "TOOL_START", "P10", "P50", "P90", "PROVISIONAL",
"PREPARE_HOST / D2H", "D2H ACK", "COMMIT_CPU", "OBSERVED DEFICIT",
"GPU COPY", "HOST SHADOW", "LATEST START", "PREFETCH_GPU / H2D", "H2D ACK",
"CONFIRMED RETURN / JOIN_SATISFIED", "ADMIT", "FIRST SERVICE", "P6 TARGET: ACTION GATE OFF".
Constraints: PREPARE_HOST is a copy, not a reclaim. COMMIT_CPU is conditional. ACKs are
physical completion evidence. No arbitrary time scale or measured-duration claim.
Avoid: Making predicted return equivalent to confirmed JOIN, freeing HBM at PREPARE time,
placing H2D after reentry, implying this path is currently enabled in the new stack.
```

## 6. KV 共享与物理数据面

```text
Use case: infographic-diagram
Asset type: memory-tier and ownership figure for a computer-systems paper
Primary request: Draw a compact radix-prefix tree at left: contexts A and B share ancestor
p0, then each has separate private leaf pages. Mark shared owner count as two and private
leaves as the only candidates for exclusive reclaim. In the center, draw GPU HBM and
NUMA-local Host DRAM as two tiers, each with separate FULL-page and MAMBA-slot pool rows;
show D2H and H2D arrows separately for each resource class. At right, draw a physical
transaction path with compact checks for request/epoch, page generation, owner closure,
lock/transaction, and FULL+MAMBA capacity, followed by command and ACK. ACK, not enqueue,
changes residency. Along the bottom, draw state transitions GPU_ONLY -> DUAL -> CPU_ONLY
-> RESTORE -> GPU_ONLY. Include a small status note that v0.5.20 native observation exists
but a complete cross-pool action certificate is not yet implemented.
Text (verbatim): "A", "B", "p0 SHARED", "OWNER=2", "PRIVATE LEAF", "GPU HBM",
"NUMA-LOCAL HOST", "FULL PAGES", "MAMBA SLOTS", "FULL POOL", "MAMBA POOL",
"D2H", "H2D", "REQUEST / EPOCH", "PAGE GENERATION", "OWNER CLOSURE",
"LOCK / TRANSACTION", "FULL + MAMBA CAPACITY", "COMMAND", "ACK",
"GPU_ONLY", "DUAL", "CPU_ONLY", "RESTORE", "CROSS-POOL CERTIFICATE INCOMPLETE".
Constraints: Do not merge FULL tokens and MAMBA slots into one interchangeable pool.
Do not count shared ancestors as private reclaim. Do not change residency when command is
merely enqueued.
Avoid: A generic GPU-memory illustration, one unified pool, invented capacities or byte
counts, implying cross-pool proof is already complete.
```

## 7. 遥测与训练标签流水线

```text
Use case: infographic-diagram
Asset type: data pipeline figure for a computer-systems paper
Primary request: Draw four independent time-aligned evidence streams: causal runtime
events, per-request token/service telemetry, pool residency/eviction, and HiCache
transfer receipts. Join them by run ID, request ID, context epoch, and monotonic time into
request-aligned decision rows with token/unit conservation. From each row produce distinct
labels for remaining return time, future token demand, admission, KV actions, and later
hit-versus-recompute outcomes. Route interventions and missing evidence to a censor audit,
not to natural labels. Split by workflow identity into train, calibration, and test before
fitting a versioned model artifact and applying an online eligibility gate. Show PCIe
service labels as unavailable because native 0.5.20 ACKs lack DMA bytes and duration.
Text (verbatim): "CAUSAL EVENTS", "REQUEST TOKENS / SERVICE", "POOL / EVICTION",
"HICACHE ACK", "RUN ID", "REQUEST ID", "CONTEXT EPOCH", "MONOTONIC TIME",
"DECISION ROW", "RETURN TIME", "TOKEN DEMAND", "ADMISSION", "KV ACTION",
"HIT / RECOMPUTE", "CENSOR AUDIT", "TRAIN", "CALIBRATION", "TEST",
"VERSIONED ARTIFACT", "ONLINE ELIGIBILITY", "PCIE SERVICE LABEL: UNAVAILABLE".
Constraints: The split is by workflow, not by individual event rows. Missing DMA
measurements must remain unavailable; never infer transfer bytes or duration.
Avoid: Mixing adjacent decisions from one workflow across train/test, using censored
interventions as natural outcomes, fabricated metrics or accuracy values.
```
