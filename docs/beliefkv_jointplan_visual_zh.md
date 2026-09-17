# BeliefKV JointPlan 图解

更新日期：2026-09-15

本页只解释当前算法图。旧 P5D 图解保存在
`docs/archive/snapshots/beliefkv_jointplan_visual_zh.md`。

## 当前总图

![BeliefKV 请求与 KV 联合调度](figures/beliefkv_joint_algorithm_overview.svg)

- [SVG 矢量图](figures/beliefkv_joint_algorithm_overview.svg)
- [PNG 汇报图](figures/beliefkv_joint_algorithm_overview.png)

颜色和连线含义：

- 绿色：Agent runtime event 与 RCCG；
- 黄色实线：P5 observed-state 在线主路径；
- 蓝色虚线：P6 FrontierBelief 预测旁路；
- 灰色：SGLang Radix/HiCache 物理数据面；
- 灰色虚线：native demand-load 或 raw-token recompute fallback。

## 图中的核心闭环

```text
RCCG 证明请求可运行
        |
        v
Bounded Seed 给出执行/准入顺序
        |
        v
JointPlan 同时选择请求和 KV 动作
        |
        +--> AdmissionTicket --> GPU Batch
        |
        +--> COMMIT_CPU/DROP victim
                 |
                 v
             reclaim ACK
                 |
                 v
          beneficiary service
```

关键点是：P5 不是先独立选择 Agent，再让 KV 管理器被动寻找 victim。它构造
beneficiary-bound package：

```text
{RUN/ADMIT beneficiary
 + COMMIT/DROP physical victims
 + transfer dependency
 + expected action unlock
 + HBM/Host envelope}
```

## P6 蓝色旁路

```text
deferred beneficiary + parked victim
        |
        v
FrontierBelief scenarios
        |
        v
benefit / causal slack / latest-start
        |
        v
PredictiveIntent
        |
        v
safe-point live validation
        |
        v
PREPARE_HOST
```

`PREPARE_HOST` 只建立 CPU shadow，GPU KV 继续保留。因此图中将它画成进入 HiCache 的
蓝色虚线，而不是“释放 HBM -> admission”的黄色实线。

当前 development 路径已支持完整/partial `PREFETCH_GPU`，以及由 commit-ready CPU shadow
victim 资助的 `RECLAIM_AND_PREFETCH`；这些路径尚未证明自然吞吐收益，正式 artifact 仍无
在线动作权限。未来可选的 predictive eviction 方案见
[当前设计](beliefkv_design.md#6-未来可选方案predictive-eviction)。

## 物理边界

RCCG 中的 context/agent 不是直接迁移单位。PageIndex 必须将其解析为 PhysicalBundle，
包含真实共享页、ancestor closure、owner、lock 和 generation。任何计划最终都可以被
SGLang allocator 或 Radix/HiCache 因物理不可行而拒绝。
