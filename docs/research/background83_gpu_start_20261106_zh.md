# 83 项目持续到达 GPU 对照启动

2026-11-06，America/Los_Angeles。用户明确要求“进行GPU实验”后，解除
2026-11-04 的 GPU 暂停。旧暂停文件按原字节归档，释放记录绑定其 SHA256。
启动时只安排本轮持续压力诊断及匹配 Oracle，不启动旧的已取消重复实验。
用户随后明确的条件后续顺序见本文最后一节。

## 工作与资源

| 项目 | 本轮声明 |
| --- | --- |
| 来源 | 83 个不同原项目，复用七份完整原始采集 |
| 到达 | 20 个初始 workflow，平均 60 秒泊松到达 |
| 测量 | 原固定 18 成员，保留完整 JCT，全部自然完成后停止背景 |
| 模型 | Qwen3-Coder-30B-A3B-Instruct，BF16，完整 attention |
| HBM | 805198 FULL token，79154282496 字节 |
| Host | 4069011 FULL token，400000057344 字节 |
| 原生执行 | 相同 2048-token chunk、精确 decode graph、running/decode24 |
| NUMA | 相同分配策略，实测页面比例只作诊断 |
| Full / Oracle | 相同 2 秒统一控制、400ms 工具检查，仅切换工作量排序 |
| 保留 | JOIN/EOS 预取、预测工具卸载、prepare_host、连续性和 sibling |
| 关闭 | 工具预取、逐层 handoff、组合成本分母 |

本轮是增加背景人口与持续供给的诊断，原 73 项目、20+70 秒的已完成结果
继续独立保留。CPU 来源和配置依据见
[来源绑定](background83_source_binding_20261106_zh.md)。

服务冻结代码为 `631911fb`，沿用 8188 项与 23 个子测试的完整 CPU 证据。
分析冻结为 `6db3aa39`，含最新结果读器修复。未因本次启动重跑完整 CPU
回归或独立 400GB CUDA 组件；原生引擎保持 `434c35dd6621`。

## 已通过的 GPU 检查

在端口 19016 上执行了 24 个源绑定短请求：

- 24 个请求通过原始 token、session 和原生停止边界检查。
- 原生 position sampling 与真实 GPU 进程检查通过。
- 采样成本覆盖 batch 1、8、24，保留原 sampler，并在其后执行固定 token 回放。
- 实际 Device/Host 容量与上表完全一致。

资格服务完成后，已注册非排序 Full；正式服务初始化与实际重放分别记录状态，
当前不能用注册或启动证明完整性能收益。

## 实际 Full 与接续进程

Full 已实际进入 `running_full`。独立 CPU 读器在
2026-11-06 21:15:57 America/Los_Angeles 完成检查：

- 745 个已完成请求、55527 个输出 token 与原始包和精确输出哈希一致。
- 实际 Device/Host 容量与表中预期值一致。
- GPU 唯一计算进程为注册 server 的 scheduler 272413，服务进程 nice0。
- 实际工作量排序与未来工作决策关闭，保留统一 2 秒控制和既有恢复机制。
- 固定 cohort 观察器和条件 Oracle 协调器都实际存活，前者 nice19。

证据为 `gpu_pair_20261106_r2/live_full_verified_r2.json`。
它只验证已封闭前缀和当时存活状态，不建立完整 JCT、平均吞吐或收益。

初次独立读器将 GPU worker 的 `/proc/.../environ` 缺失字段判为配置关闭，
在发布证明前失败。原读器及 `live_full_reader_failure_r1.json` 保留。
父 server 的实际初始环境包含正确开关；worker 的初始环境区域已无法提供
该字段，沿用已有原生执行观察器对这类可见性缺失的处理。新读器分别记录
父启动配置、源绑定继承与 worker 字段不可见，不将其写成直接观察到 hook
安装。没有修改或重启 GPU 实验，也未改写原始 token/遥测。

## 首个中途压力前缀

`gpu_pair_20261106_r2/pressure_prefix_r1/` 的只读 CPU 分析分别封闭了
周期服务和局部 Oracle 输入。两份 journal 前缀不同，计数不相加。

| 周期服务读器 | 已观察结果 |
| --- | ---: |
| 实际周期控制 | 405 |
| 存在 ready-paused 的控制 | 386 |
| 跨 workflow、各自可放下而不能共同放下的逻辑竞争 | 59 |
| 实际授予且后续有精确原生服务的 episode | 344 / 344 |
| 下一次控制或同 agent 干预前完成有效 batch | 301 / 344 |

后续服务按相同 request/context/attempt/session 身份核对。未能在限定窗口内
完成的 episode 不直接算作失败或可删除等待；其中 4 个窗口尚未闭合。
共同吞吐窗口内的已观察部分有 374 次控制，均有 ready-paused，
其中 58 次竞争、182 次有剩余额度但没有单体可放下。观察截止相对时间
823.535 秒，固定共同窗口为 47.737--1404.818 秒，尚不完整。
这些比例按控制点计，不是时间加权占比或物理页利用率。

另一读器重放了 407 个实际同状态选择：60 次逻辑竞争，Oracle 改变
36 个恢复集合，共同窗口已观察部分为 35 次改变。36 次中，23 次成员数
相同、12 次更少、1 次更多；30 次选择的完整逻辑预算更大。没有只有删除
而无新成员的严格子集，全部保留实际入选的 protected JOIN 且满足记录预算。
15 次差异集合涉及测量成员，覆盖 4 个测量 workflow；这不是 4 个已经受益
的 workflow，也没有反事实服务或 JCT 预测。准入成员数不等于 decode 宽度。

独立保存的原生遥测快照中，Host 高水位为 327875395584 字节，同会话
已服务前缀丢失代理为 0，FULL block 重算为 **12155 token**。该快照晚于
最初查询到 6054 token 的时刻，也不与两份读器前缀原子对齐。两项重算口径
继续分列，不称零重算，不把 Host 高水位当作有效活跃 KV 利用率。

结果支持继续当前完整配对，无需现在增加并发、再次修改到达表或换负载。
它尚不证明稳态、完整背景覆盖、Oracle 性能收益或全部准入入口的选择空间。
`oracle_choices.json`、`periodic_service.json`、`native_telemetry_observation.json`
及 `summary.json` 保留各自哈希和范围；`summarize_prefix.py` 可复现汇总。

## 完整共同窗口的中途审计

`gpu_pair_20261106_r2/common_window_complete_r1/` 已独立封闭完整
47.737--1404.818 秒共同窗口。测量成员的尾部仍在执行，以下不涉及部分
JCT，也不是 Oracle 的实际性能结果。

| 窗口内观察 | 结果 |
| --- | ---: |
| 实际周期控制 | 658 |
| 控制前存在 ready-paused 候选 | 607 / 658 |
| 跨 workflow 逻辑容量竞争 | 87 |
| 相同状态下局部 Oracle 改变恢复集合 | 52 |
| 实际周期授予且压力协调后保留 | 664 / 664 |
| 全部 workflow 的有效输出 | 461020 token |
| 其中 decode 输出 | 457131 token |
| 输出 / decode 吞吐 | 339.714235 / 336.848528 token/s |

前两份选择读器使用各自封闭前缀，但共同窗口的周期控制和竞争计数一致；
窗口外计数不相加、不替代最终完整执行审计。607/658 是按控制点计的比例，
不是时间加权的 ready-paused 占比。52 次集合改变保持原记录候选、预算、
buffer 和 protected JOIN；没有模拟改变后的服务、压力协调或 JCT。

`window_costs.py` 复用源绑定的有效 token 成本读器，逐项核对已观察输出序号，
并将 30 秒分箱之和与同一累计器核对。窗口内 prefill 执行 5276859 个
extend token，worker 区间约 564.174 秒；decode worker 区间约 792.972 秒，
有效 decode 宽度按 worker 时间加权约 15.426。它们是 worker 区间和
实际输出口径，不是纯 CUDA 时间、物理 HBM 利用率或新增输入全部需要重算
的证明。精确 decode graph 在该窗口未产生 padding row。

读器明确保留 `completed_request_receipts_reconciled=false`、
`complete_measured_execution_verified=false` 和 `telemetry_drained=false`；
封闭窗口不能代替测量组自然完成后的回执、排空和真实退出。当前证据支持
完成已登记的同负载配对，不支持中途更换到达表或宣称调度收益。

## 启动前的路径绑定失败

首次 entry 在 GPU 分配前被拒绝：原生 CPU 证据将 canonical patch 路径绑定到
旧冻结目录。新执行目录中的补丁字节、原生 tree、commit 和全部其他状态字段
逐项一致；只重新绑定路径，没有改动原生实现、放宽验证或重跑组件。

`gpu_pair_20261106_r1` 保留失败输出和原 operator；有效启动使用
`gpu_pair_20261106_r2`，含新的 `native_sources.json` 与路径比较证据。
两次 entry 的 operator 都按当时哈希保留。

## 执行入口

```text
ops/workflow_original83_host400_mean60_position_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_full_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_full_cohort_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_matched_oracle_20261106_r1.plan.json
ops/run_background83_matched_oracle_20261106.py
```

原始 GPU 结果位于：

```text
experiments/raw/workflow_original83_host400_mean60_position_20261106_r1/
experiments/raw/workflow_original83_host400_mean60_full_20261106_r1/
```

Oracle 条件接续已在实际 Full 和固定 cohort 观察器存活后注册。它仅在测量成员
全部完成、原始回执、fresh drain、真实退出、负载供给及可改变集合的竞争
审计通过后执行。复用本轮短 GPU 检查，重新核对资源和新暂停；没有自动扩展
到参数扫描、旧重复实验或其他 baseline。

验收仍是完整测量组平均 JCT、固定共同窗口吞吐，以及选择到实际服务的传递。
单对结果不建立重复性或完成当前 goal。

## 条件后续顺序

2026-11-06，用户补充：如果当前窗口合适，而且 Oracle 短优先与预测短优先
调度均有可观提升，接下来在同一 workload 上运行 ThunderAgent 和
SGLang native 对照。这是后续实验的条件授权，不改动正在执行的冻结代码、
当前到达表或已登记的 Oracle 接续器。

执行顺序为：

1. 完成非排序 Full 及既有负载审计，确认完整测量、背景覆盖、候选竞争和
   有效状态容量等已声明条件；不能只依据共同窗口内存在候选就认定合适。
2. 完成同条件 Oracle 短优先对照，检查完整平均 JCT、共同窗口吞吐，
   以及改变选择后是否实际改变后续服务。
3. 在同一 workload 上验证预测短优先。相对同配置非排序 Full，分别报告
   Oracle 的排序收益和预测方案的实际收益；预测开销计入结果，
   不能用 Oracle 收益替代预测方案实测。
4. 两种短优先均满足收益目标后，运行 ThunderAgent reproduction，
   仅使用 acting-decay 版本，保留其原有控制和准入策略。
5. 运行 SGLang native 匹配回放，使用相同共享数据面和资源配置，
   关闭 Full 与论文调度策略。它是新的同 workload 系统对照，
   不重新采集自然轨迹，也不使用旧采集的不同到达条件来替代。

以上阶段固定同一组 83 个原项目、完整 token 与依赖、工具时长、
20 初始加平均 60 秒到达表，以及预先选定的 18 个测量成员。
同时固定模型、精度、实际 Device/Host 容量、running/decode24、
chunk2048、共同执行配置和 NUMA 放置策略。算法自身的控制周期和策略
按各自声明保留，不给 baseline 注入 Full 的 JOIN 或工具预测优先权。

所有方法都保留 18 个成员的完整 JCT，全部自然完成后停止非测量背景；
吞吐在同一到达表确定的墙钟窗口中包含背景输出，并展示 decode 吞吐的
时间趋势或分布。完整平均 JCT 使用两组均值的相对变化，另列长尾，
不以最先完成的成员、局部请求或最后一条完成时间替代。

当前实际已登记的 GPU 接续仍只有 Oracle；预测、ThunderAgent 和 native
的运行注册尚未创建。条件满足后按上述顺序执行，沿用必要的输入、
实际资源及停止协议检查，避免为未改变的部分新增独立 GPU 小实验。
