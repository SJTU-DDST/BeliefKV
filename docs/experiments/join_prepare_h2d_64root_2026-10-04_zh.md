# 多轮 JOIN 备份与预取的 64-root 对照

本轮由用户在 2026-10-04 明确批准，替代 36-root 单侧诊断，
不回填旧实验。目的不是增加 ACK，而是形成可恢复的安全
parent 输入、验证 JOIN 前真实 H2D 和端到端吞吐/JCT。

## 实现与公平性

prompt 明确要求实质性修复使用调查、改后审查、独立验证至少
三个有用 delegation round，每轮由模型选择 1-4 个 child；
出现新证据或失败时继续委派。确已解决或存在具体阻碍时允许
诚实结束，不增加 runtime round guard，不虚构重复任务。
删除该 prompt 末尾残留的强制 WorkflowCompletion 指示。

两侧共用完成通知、有限收尾优先级、选择性 JOIN parent
PREPARE_HOST 和实际分配压力下的 backed parent 回收规则。
唯一主要变量是 JOIN 前预测 H2D；reactive 不加载语义 worker，
只在真实请求恢复时由 native H2D。此对照隔离预测提前恢复，
不是完全未经 BeliefKV residency policy 修改的 native baseline。
后续完整算法消融需另列 native baseline，不能混淆。

PREPARE 只扫描活跃 WAIT_JOIN parent 的安全输入祖先，不需要
旧 WAIT_TOOL 模型 hint；只在物理 free-list 低于待服务 child
需求余量时提前备份，一次一 node、50 ms 有界检查，Host 不足
不回收其他有用 Host 数据来强行备份。原生 D2H 副本同样可用。
源码原已支持 write-back shadow，但缺 JOIN dispatch 入口。

Device 回收仅在 native 真实分配短缺时执行：先让零 session ref
的冷数据沿原规则回收；再选择仍 WAIT_JOIN、独占该状态、
未锁定且 Host 备份 ACK 已完成的 parent checkpoint。
Mamba 内部状态只释放 Device 层，保留 FULL 和 Host；FULL
仅允许原生可驱逐 leaf 且全部被回收层均已备份。不在 PREPARE
enqueue 后立即驱逐，也不人工清空池来制造恢复。

latest session snapshot 读取最近已完成请求的真实 FULL/Mamba
anchor，仍检验 incarnation、存活对象和 session 归属；不因
历史 leaves 超过八个就整条拒绝。失效原因单独记录，不补造引用。
PREPARE ACK 可在同一仍存活 session/generation 下跨一个逻辑
epoch 完成旧输入的记账，不授权新请求，也不放宽节点或字节证明。

H2D 冷启动加载 22 个可与物理账本精确对齐的历史原生完成
ACK（合并了未归因 native 动作的记录不入选）。产物为
`experiments/models/native_h2d_ack_seed_20261004.json`，启动时
校验 SHA256、模型、FULL/Mamba 物理字节粒度；仅作为 native
submit-to-ACK 服务估计，不是传输授权或净收益训练标签。
新实际 H2D ACK 滚动替换历史样本，不要求本轮先触发三次 H2D。

时机条件以估计剩余工作/当前服务速率及实测 H2D 耗时滚动决定，
latest-start 上界收紧为 500 ms。该上界只是 runtime 估计，
不意味着真实 child RETURN 提前量已经满足目标。

## 配置与验收

- 64 个原 manifest 首批 task，单波到达，running=48。
- Qwen3.5-35B-A3B、SGLang 0.5.20 staging、BF16。
- HBM static fraction=0.94，Mamba/FULL=0.9。
- Host 200 GB，NUMA node 1，比例匹配实际 Device 字节。
- 相同 seed=21、context=131072、completion=8192。
- Workflow 预算 14400 秒、graph=2048、32-step FINALIZE reserve。
- native-reactive guard profile，无格式/重复工具语义强制终止。
- 两侧冷启动，源码/模型在整个 pair 内冻结。

验收同时记录 PREPARE issue/ACK、真实压力下 Host-only 形成、
predictive H2D issue/ACK、首次 FULL/Mamba 复用、Host churn、
可归因重算及端到端完成吞吐/JCT。无官方 grading 时只称
completed-workflow throughput，不称正确任务吞吐。
未要求结构化回复时 correctness 记为未独立评测，不再写
`missing_structured_completion` 或把未知正确率误报为零。

`audit_join_transfer_windows.py` 用 paired wall/monotonic clock
关联 controller submit、child RETURN、完整 JOIN 和首次服务，
逐 action 报告实际提前量，单列 0-500 / 100-500 ms 命中、
RETURN 后才启动和 JOIN 前 ACK 完成。不能以 parent 首次服务
时刻或未锚定的 DMA event 时刻代替 child RETURN。

目标是大多数有效动作在 child RETURN 前数百毫秒启动，并在
JOIN 后首次服务复用；有净吞吐改善且没有明显增加有用 KV 丢失。
零动作、缺 Host-only 状态、提前驻留过久或提前量不符均照实报告。

## 等待期间的预测研究

当前上线阶段头/encoder/阈值保持不变，工作头使用原冻结组合
产物。GPU pair 期间只在独立 CPU 研究脚本中比较非线性条件
剩余工作与语义/长度/结构特征，训练、selector、interval 和
held project 不混用。受 graph FINALIZE 干预的轨迹不作自然
RETURN 拟合。研究候选不在 pair 中途替换部署权重。

目录：
`experiments/raw/qwen35_join_prepare_h2d_ab_64root_20261004_v1/`。
本文件记录设计和配置，不提前填入收益或预测精度结论。
