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
`experiments/raw/qwen35_join_prepare_h2d_ab_64root_20261004_v2/`。
本文件记录设计和配置，不提前填入收益或预测精度结论。

## v1 中止与修复

v1 是完整 64-root 启动，不是短 canary。约 30 分钟后检查，
49 个 workflow 已正常完成，其中 45 个只有一轮、四个两轮，
所以加强后的静态 prompt 仍未提供所需的多轮 workload。
已停止 v1，未运行 reactive，不将其当作正式对照或训练集。

中止前有 132 个 PREPARE ACK、六个被直接观察到的压力停放，
两个 H2D 获得 ACK，且均有 FULL 首次复用与 Mamba forward
复用证明。虽然数据被消费，两个实际 controller submit 都
在 child RETURN 之后，平均提前量为 -94.76 ms，不是目标中的
提前预取，也不是吞吐收益证明。H2D 仍发生在服务端 RCCG
WAIT_JOIN，是控制镜像更新延迟和末段工作高估的共同风险。

v2 将阶段指示持久化追加到新的 task ToolMessage/Command
结果；一组并行 task 属于同一轮。指示不修改旧 system prompt，
也不只临时修改一次 model request，否则下一请求移除指示会
破坏缓存前缀。独立审查/验证仍是实质工作，已看似解决不再作为
跳过全部后续轮次的理由；具体阻碍仍允许诚实结束，不添加返回
门禁、工具抑制或 extra model call。

服务端每步有界处理 causal packet 数由 16 增至 128，以减少
64-root 突发下 RETURN/JOIN 镜像滞后。原生 EOS 后的确定性协议
窗口只允许 50 ms 内使用，超时单列，不再用宽松两秒窗口将
已经发生的 RETURN 当作预测机会。真实提前量仍需独立审计，
这些条件不是精度已经达标的保证。

CPU 首个非线性 work-only 候选在同一 Sphinx 隔离项目快照上的
末段 token 中位绝对误差由部署工作头的 110.00 降至 72.78，
但 P90 从 202.87 到 210.26 未改善；未部署、未声称亚秒效果，
继续比较低成本树模型与更合适的末段监督。

## v2 完整结果与诊断

| 指标 | Predictive | Reactive |
| --- | ---: | ---: |
| completed / error | 64 / 0 | 63 / 1 |
| 整轮秒数 | 3916.24 | 4369.60 |
| completed workflow/hour | 58.83 | 51.90 |
| 63 个共同完成任务平均 JCT，秒 | 1465.32 | 1350.02 |
| 模型调用 / 工具调用 | 13169 / 12549 | 11210 / 11036 |
| GPU 利用率均值，% | 84.54 | 70.62 |
| 预测 H2D ACK | 0 | 0 |
| PREPARE ACK | 647 | 657 |
| 直接观察的 backed parent 压力停放 | 9 | 8 |
| native H2D 请求服务样本 | 73 | 68 |

完成吞吐表面高 13.35%，但没有发生预测 H2D，不能归因于
提前恢复；共同完成任务平均 JCT 高约 8.54%，模型/工具轨迹
不同，reactive 还有一个异常。未做官方 patch grading，不作为
预测调度收益或正确任务吞吐的证明。

两侧 FULL Host 均达 105.36 GB、Mamba Host 达 94.65 GB。
FULL Host 累计驱逐约 139.63 / 117.14 GB，Mamba 约
55.05 / 68.25 GB。是有备份与恢复的负载，不能再用没有 HBM
竞争或没有原生 H2D 解释零预测动作。并非全部备份被消费，
647 个准备 ACK 和九次直接停放说明选择性准备仍有压缩空间。

predictive 的 64 个 workflow：52 个一轮、九个两轮、三个
三轮；reactive：52 个一轮、十个两轮、两个三轮。实际 child
分别 79 / 78，每轮仍主要一个 child。持久化阶段提示确实进入
了 tool result，但整个问题常在第一轮被交给 child 一次完成，
所以只加强“多轮”措辞不足以产生期望的阶段分工。

reactive 的 `pytest-dev__pytest-8399` 失败：模型将
`/workspace/src/_pytest` 等目录替换成指向 `/testbed` 的链接，
随后 `edit_file` 的虚拟 backend 检测越界，直接抛 ValueError
并终止 workflow。这不是简单的模型路径笔误，也不应该通过
允许 host 越界来修复。该失败 workspace 保留。

模型计算均值 14.17 ms，但接收时观察年龄中位数 432.69 ms；
队列/传递年龄不是单模型推理耗时。EOS 前最后快照的条件剩余
token 绝对误差中位数 60.12、有符号 +26.57，实际剩余中位数
36.5；末段仍高估。3155 次 EOS 窗口过期，不放宽两秒窗口将
RETURN 后恢复冒称提前动作。模型 worker 与物理账本未禁用，
没有控制投递或遥测丢失。

predictive 有八次允许的 graph FINALIZE、涉及七个 workflow；
reactive 有四次、涉及三个 workflow。聚合语义完成干预计数为零
不能代表没有 graph 收尾。此类时点后的自然 RETURN 标签仍
须剔除/标记，不因此取消整个已完成 workflow 的服务遥测。

## v2 后的优化

1. backend 将越界/遍历的 ValueError 转成文件工具可返回的
   PermissionError 反馈，不跟随外部链接，不终止整个 workflow；
   prompt 明确 `/testbed` 不是当前 mounted checkout。
2. 初始 delegation 明确为诊断/复现，不一次派发完整修复；
   root 集成并生成候选 patch，后续轮次分别独立审查与验证。
   不增加返回门禁、轮次 guard 或固定 child 数量。
3. 每个正文快照只推理一次；线上传输路径只提交有真实
   Host-only 恢复目标的关键 child，目标检查 100 ms 有界缓存，
   动作前仍重新验证容量和身份。通知活跃/最新观察优先，
   失效 pending request 取消，减少无用 FIFO 推理造成的年龄。
4. 接入 autonomous child 的 final-report thinking 控制。
   原开关仅 planned child 生效；开启时仅通知后的报告请求
   disable thinking，两侧共用，不增加 LLM 调用或强制格式。
5. 非线性 work-only 候选导出为校验 SHA256/encoder 的 JSON，
   复用一次冻结语义编码，只替换条件工作，不改变 phase 分数。
   上线接口补齐不等于已取得新的 GPU 或亚秒精度验收。
   v2 回放末段误差反而由原头 49.00 到候选 87.72 token，
   已否决默认替换，详见候选比较文档。

本轮 64 / 63 个归档完成 workspace 已自动清理，失败现场、
所有 trace/patch/配置保留。后续不把本轮的表面吞吐差当作
预测收益，也不盲目增加并发；先验证新的工作头和队列时效。
