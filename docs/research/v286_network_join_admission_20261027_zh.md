# V286 匹配的 ThunderAgent 与 Full

## 后续强对照 V295

V295 已于北京时间 2026-10-27 17:37:57 完整结束，在完全相同工作量、
V269 计时表、流式指标、engine、容量和 seed 下启用作者可选的
ACTING decay。平均 JCT 1700.062 秒、全程吞吐 236.450 token/s，
P95 JCT 2292.546 秒。相对本轮 Full，其平均 JCT 高 1.8432%，但
吞吐高 24.8816%，P95 低 6.1392%。因此，下述 Full 相对默认策略
的改善仍成立，但不支持已超过这一更强配置的吞吐结论。

V295 H2D/D2H 分别为 2440.873/2033.621 GB，平均 decode batch
5.023；更多迁移没有阻止它更早完成全部工作。完整三组比较见
`docs/research/thunderagent_acting_decay_20261027_zh.md` 和
`/home/longhao/experiment/ops/compare_v295_complete_20261027.json`。
三组均保留全部任务与尾部，仍是单次结果，最终性能验收待定。

## 最新完整对比

两组均已完整结束。监督器于北京时间 2026-10-27 16:55:47 记录
`collected_performance_review_pending`，两个 arm exit code 均为零，
新鲜 idle telemetry、零丢失/错误和 owned cleanup 均验证。每组全部
18 个 workflow、3674 次请求、145565966 输入 token、549671 输出
token 完成；逐请求 prompt/output SHA256、token 数与原生结束原因
相等。使用同一 V269 网络工具计时表、流式开关、共同 engine 参数、
容量、seed21 和 3600 秒客户端超时，保留全部正常等待与尾部。

| 完整系统指标 | 默认 ThunderAgent | Full predictive | Full 相对变化 |
| --- | ---: | ---: | ---: |
| 平均 workflow JCT，秒 | 1876.335 | 1669.294 | -11.03% |
| P50 workflow JCT，秒 | 1649.153 | 1669.635 | +1.24% |
| P95 workflow JCT，秒 | 2937.667 | 2442.497 | -16.86% |
| workflow event makespan，秒 | 3138.105 | 2903.095 | -7.49% |
| 全程输出吞吐，token/s | 175.160 | 189.340 | +8.10% |
| 平均 decode batch | 2.280 | 3.143 | +37.85% |
| worker 区间加权 decode batch | 2.861 | 4.295 | +50.11% |
| H2D，十进制 GB | 291.851 | 698.516 | +139.34% |
| D2H，十进制 GB | 445.433 | 258.999 | -41.85% |

平均 JCT 与全程吞吐在本轮同时改善，但只有平均 JCT 达到预设 10%
门槛；吞吐未达到 10%，没有三次重复比较，`performance_accepted`
仍为 false。本表比较公开实现默认策略；另行声明的 acting-token
decay arm 不在这次两组结果中，不能推广为优于所有可选配置。

11/18 个 workflow 更快，七个更慢。Fintech Trading 从 2634.086 秒
降至 1077.245 秒，System Security 从 2627.508 秒降至 1681.372 秒；
API Microservice 从 685.602 秒增至 1338.075 秒，增加 95.17%，Data
Analytics 增加 46.08%。不能因均值改善而忽略个别任务服务分配的回退。

### 迁移与真实预取

Full 698.516 GB H2D 包含 531.388 GB 原生需求恢复和 167.128 GB
JOIN-tagged 恢复。967 条节点命令合并为 27 个物理 H2D batch，
不是 967 次独立 JOIN。966 条命令、167.110 GB 具有首次 FULL
复用证明；另一条 18.28 MB 未通过身份复用证明，不能将其直接
认定为物理未使用。

严格按完成边界前启动且后继请求确认复用统计，覆盖 14 个独立
JOIN wait episode、92.867 GB FULL；不能把全部 JOIN-tagged 字节
计为提前预取，也不声称这些字节在 JOIN 完成前全部传完。
生命周期审计关联到 68 个 JOIN episode，原始完整 JOIN 为 72；
独立 reentry 审计覆盖全部 72 个，不补造缺失的预取归因。
剩余已复用的约 74.243 GB 在完成后启动，工具预测 H2D 为零。

Full native demand H2D 约为默认对照的 1.82 倍，全部 H2D 约为
2.39 倍。因此本轮吞吐收益没有依赖更少的 H2D。实际平均 decode
batch 从 2.280 增至 3.143，decode worker 区间累计从 2124.328 秒
降至 1876.615 秒，符合准入和并发利用改善的观察，但这些 worker
区间包含 runtime、等待与交错处理，不是 CUDA kernel 时间，不能
直接从 JCT 扣除，也不能独立归因给某个策略模块。

两侧旧的同上下文已服务输入前缀丢失代理量均为零。这不代表任意
额外 prefill 或其他 GPU 工作均不存在，也不支持“大量避免重算”
作为本轮主要收益解释。

### 工具路径与服务分配

工具等待 PREPARE 有 583 个独立已知工具决策、实际备份 5.847 GB，
其中实际剩余时间超过一秒的比例为 95.03%。这是选中决策的精确率，
不是全部工具的覆盖率。580 个有预测的决策中，剩余时间 P50 点估计
的绝对误差中位数为 2.945 秒、P90 为 18.010 秒。

实际工具 HBM demotion 仅 28 个节点事件、0.180 GB FULL，JOIN wait
释放累计为 282.777 GB。节点释放累计不能当作独占容量峰值或完整
agent 卸载次数。较长工具计时尚未使工具卸载与预测恢复路径充分
发挥作用，不能因为长等待分类精确率高而宣称返回时刻已准确。

全部 72 个 JOIN 后继的 native queue 到首次服务，P50 从 16.793 ms
变为 20.504 ms，P90 从 16.209 秒变为 26.404 秒，最大值从 648.525
秒降至 188.856 秒。极端等待缩短不等于每个分位数都改善。
workflow 初始 TTFT P90 从 1843.843 秒降至 683.235 秒，但 Full
仍存在较长初始等待；其首次输出后最大 ready 输出间隙的总体最大值
为 1003.287 秒，对照为 649.003 秒。单请求生成交互速率 P50 从
88.706 降至 61.305 token/s，反映工作流吞吐与个体响应速率的取舍。

### 完整审计

汇总：
`/home/longhao/experiment/ops/compare_v286_complete_20261027.json`，
绑定原始比较、逐请求回执、共同参数、容量、工具计时、drain 和
cleanup 的实际 hash。详细报告：

- `ops/v286_complete_decode_20261027.json`
- `ops/v286_complete_restore_churn_20261027.json`
- `ops/v286_complete_full_h2d_sources_20261027.json`
- `ops/v286_complete_full_prefetch_lifecycle_20261027.json`
- `ops/v286_complete_full_wait_offload_20261027.json`
- `ops/v286_complete_join_reentry_20261027.json`

这些是完整同工作量系统观察，不是模块消融、自然 shell 时长结果或
重复性验收。本次只分析和更新现有文档，不改写冻结的实验产物。

## 历史：默认对照结束与 Full 交接

默认 ThunderAgent 已在 2026-10-27 16:04 Asia/Shanghai 完整结束，
退出码零、全部 18 个 workflow 和 3674 次请求均完成。输入
145565966 token、输出 549671 token，原始停止规则和精确 token
检查通过；没有删除正常尾部。

| 完整默认 ThunderAgent 指标 | 数值 |
| --- | ---: |
| 平均 workflow JCT | 1876.334933 秒 |
| WORKFLOW_START 到最后 WORKFLOW_END | 3138.104909 秒 |
| 全程输出吞吐 | 175.160173 token/s |
| workflow TTFT P50 | 101.956985 秒 |
| workflow TTFT P90 | 1843.842805 秒 |

TTFT 采用任一 descendant 的首个非空流式输出，包含 workflow 初始的
实际准备等待；不是单纯的 scheduler 排队时间，也不保证等同最终
用户可见 root 回答。全部 3674 个请求都有输出计时。先前 1800 秒
客户端超时修补允许作者原 force-resume 生效；两条超过该时长的请求
已实际完成，而非截掉其等待。

原生 telemetry 无丢失/错误/排队遗留，两个新鲜 idle 快照及自有进程
退出均验证。独立完整检查保存在
`experiments/reports/v297_v286_thunderagent_complete_20261027.json`。
这是一组完整对照结果，尚不是 Full 的收益或整轮验收。

监督器已按原顺序启动 Full，独立确认 server 4060371/start ticks
725854916、GPU scheduler 4060747、driver 4062095/start 725869576
实际归属并运行。早期 1181 条完成回执精确，实际开关包括
`join_dependency_admission=true`、`blocked_budget_proof=observed_blocked`、
JOIN-only 恢复预留、cold-fit loan recall、prepare_host 和有条件工具
预取，普通 execution-frontier restore 关闭。

新状态快照的每 agent token 加 buffer 与总逻辑占用一致。共记录
19 次实际逻辑预算交接，其中七次受益者被策略记录为当前唯一剩余
JOIN child；这些记录不等于独立 DAG 重建、释放了相应物理页或改善
了 JCT。完整 Full 和独立衰减对照仍待结束。早期证明：
`experiments/reports/v298_v286_join_admission_full_live_gpu_20261027.json`。

用户要求先运行新 trace 上的 ThunderAgent，再处理 Full 的容量与
ALL-JOIN 准入缺口。V273 已实际启动，但因客户端等待期限与作者
force-resume 期限相撞而中止；保留它的 2430 条精确回执和原失败。
不使用部分 workflow 或部分输出吞吐作为性能对照。

V286 是完成诊断和共同客户端修补之后的独立完整重试。顺序为
`thunderagent`、`full`；前者完整成功、两次新鲜 idle telemetry 与
自有进程清理都完成后，监督器才启动后者。失败或新用户暂停会停止
后续步骤，不在部分工作量上续跑或拼接结果。

## 固定条件

- 注册：`/home/longhao/experiment/ops/v286_locobench_network_join_admission_20261027.registration.json`
- 运行目录：`experiments/raw/locobench_network_join_admission_20261027_v286`
- 应用 commit：`1e8c8ca75eef3ce1da3f9219bd479531c2d59ec3`。
- 共同 native tree：`11b84f7c31986782be3fbe699bae2574ef092389`。
- 模型：Qwen3-Coder-30B-A3B-Instruct，running/decode24，seed21。
- Device 805198 slots；Host 200000077824 bytes。
- 全部 18 个不同项目 workflow、3674 次请求、145565966 输入 token、
  549671 输出 token、72 次 JOIN；保留全部正常长尾。
- V269 工具计时表 SHA256：
  `fa75164726e37277772d181f3601f6eaf234d3e55d8503710bdc0fc966cca5be`。
- 共同流式指标与 free-page H2D fence 开启。
- 共同客户端 request timeout 显式为 3600 秒；作者策略的 1800 秒
  force-resume 保持原值，所有实际排队时间计入完整 JCT。

这仍是 V230 图与 token 上的公开网络工具计时敏感性负载，只替换
931 次 execute 的计时，不是新的自然 shell 轨迹；未来结束时间不
交给策略。自然 native 计时不作为该负载的性能 baseline，不重跑
完整 native。

## 策略区别

ThunderAgent 的 policy/runtime adapter 与 V273 逐字节相同；其
agent 仍按串行 invocation 映射 program，Full 调度和迁移开关全部
隔离。它运行公开实现的默认配置，可选 acting-token decay 尚未
适配；不能据此宣称已经超过该方法所有长工具配置。

Full 保留 EOS/JOIN warming、冷 prepare_host、长工具卸载、有条件
工具 warming、agent 连续性、容量内 sibling co-admission、已记录
借出预算归还与 cold-fit 选择。普通 execution-frontier restore 关闭。

新增候选只在现有准入控制点优先处理实际阻塞 ALL-JOIN 的必要 child，
并允许当前实际阻塞且完整 Host-backed 的 owner 借出预算，而无需
长工具预测命中。运行中、pending、critical、恢复保护、路径缺失和
DMA 等检查继续有效；没有新增 ready-peer 驱逐权限或物理共享页折扣。
每秒至多一次借出、八个 donor 证明、每条路径最多 64 个节点。

完整 V230 的 92 对 parent/child 均只重叠三个前缀 token，因此此次
保守计费中没有可观的共享前缀折扣依据。物理 extent union 仍未实现，
不能把同 workflow 内不同上下文当作重复容量。

## 资格与启动

主分支实现分别提交为 `f23c3c60`（准入/预算候选）和 `62e5e755`
（共同客户端超时修补）。原生 engine、实际 sampler、四个已通过
GPU 的 replay sampling/receipt 组件保持原字节。准备路径只重绑定
相同 native patch 的路径，保留主分支历史 staging patch。

独立冻结目录一次通过 2350 个 shared CPU case 及四个 subtest，
无失败或跳过，保留 V279 的全部 2342 个 case 并新增八个超时检查：

- `experiments/reports/v285_network_retry_prepared_shared_cpu_20261027.xml`
- `experiments/reports/v285_network_retry_prepared_sources_cpu_20261027.json`
- `experiments/reports/v285_network_retry_native_state_cpu_20261027.json`

CPU native tree/allocator 夹具使用 copy/event 替身；共同 engine 复用
此前真实 GPU writer/copy 与 unforced session 模型证据。它们不是本轮
完整正确性或性能结论。注册后的 supervisor 4025266/start ticks
725518522、server 4025775/start 725521000 已检查存活，初始状态为
`starting_thunderagent`。

随后已独立确认状态为 `running_thunderagent`，driver 4027494/start
725535972、GPU scheduler 4026143 均归该 supervisor/server 所有。
实际 driver 显式使用 3600 秒等待期限、同一 V269 map 和流式指标。
最新检查时已注册 118 次请求，99 条已封存完成回执验证 token 精确；
Full admission 为 null，作者策略隔离成立。证据：
`experiments/reports/v287_network_join_admission_live_gpu_20261027.json`。
这些早期回执仍不是整轮正确性、最终 JCT 或吞吐验收。

配置中的旧 `comparison.reuse_v273_thunderagent=true` 是未执行的
描述性字段，已在主分支改为 false，并改指同轮完整 ThunderAgent。
保留冻结配置不改写；V286 的实际 `order` 和两个完整 arm 从一开始
就不复用中止的 V273。
