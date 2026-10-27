# V286 匹配的 ThunderAgent 与 Full

## 完整默认对照与 Full 交接

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
