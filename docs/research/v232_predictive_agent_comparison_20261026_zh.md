# v232 agent predictive 的完整工作量比较

日期：2026-10-26。Full 与 ThunderAgent 均完成全部工作量，
原生遥测排空与所属进程清理通过。性能验收尚未通过。

## 固定策略与工作量

声明文件：
`configs/migration/predictive_agent_cold_tools_20261026.json`。
实现使用 retained ThunderAgent 资格、JOIN/EOS 预取、条件工具预取、
实际阻塞后的卸载、增量 prepare_host、短工具连续性和容量允许下的
ALL-JOIN sibling 共同准入。普通 execution-frontier restore 关闭。

SPAWN 本身只授权增量备份。parent 仍能运行时不能据此卸载。
工具预取只使用空闲页或已备份、真实阻塞且满足原生条件的冷页；
已有 Host 副本的 ready/排队 agent 不能仅因此成为普通工具预取的牺牲者。
工具预取优先窗口为 1500ms，低于 JOIN。窗口过期不撤销正常 agent 资格。
部分层传输不减少当前分配器所需的跨层 token 容量。

自然来源为 v230：18 个不同原项目、72 次 JOIN，其中 52 次 singleton、
20 次双 child JOIN，没有嵌套。每个 root 都完成了真实双 child JOIN，
但所有 root 都使用了最终返回时的委派提醒，必须披露。
child 调用数 P50 为 29.5，不能将 4/8 软提醒宣称为八次硬上限。

三个 arm 使用相同的 3674 个请求、145565966 个输入 token、
549671 个输出 token、工具时长、依赖、停止条件和 seed21。
模型为 Qwen3-Coder-30B-A3B-Instruct，running/decode24，
Device 805198 token，Host 2034506 token。保留全部正常长尾，
包括 180 秒工具；不再运行完整 native 重放。

## 已完成的 Full 与 native

| 指标 | v230 natural native | v232 Full |
| --- | ---: | ---: |
| 完整 workflow 数 | 18 | 18 |
| 完整平均 JCT，秒 | 1750.365 | 1316.524 |
| workflow 事件 makespan，秒 | 2100.545 | 2101.930 |
| 全程输出吞吐，token/s | 261.680 | 261.508 |
| 实际 H2D payload，十进制 GB | 9086.313 | 341.792 |
| decode 平均 batch | 9.085 | 5.035 |
| decode 观测请求步的平均上下文 token | 55733.002 | 55729.782 |
| prefill worker 区间累计，秒 | 523.425 | 374.866 |
| decode worker 区间累计，秒 | 1542.558 | 1655.770 |
| native 请求全部为空的区间，秒 | 0.529 | 39.037 |

Full 平均 JCT 比来源 native 低 24.786%，全程输出吞吐低 0.066%。
吞吐目标尚未达到。最后两项 worker 区间包含调度/运行时处理，
不是纯 GPU kernel 时间，也不能相加后当作可移除的 JCT。
空档限定在首次 API 请求到最后完成之间，不删除 workflow 的工具等待。
平均 batch 从全部原生 `gpu_service_sample` 记录计算，未剔除小 batch
或长尾，亦未按 batch 大小推断 GPU 利用率。

Full 请求/token/原始停止条件校验通过；双新鲜 idle 快照完成，
所有遥测零丢弃、零失败，Full physical_disabled=false。
旧输入前缀丢失代理计数为零；这不代表完全没有任何额外 prefill。

证据：

- `experiments/raw/locobench_agent_cold_tools_20261026_v232/causal_comparison.json`
- `experiments/reports/v230_native_h2d_sources_complete_20261026.json`
- `experiments/reports/v232_full_h2d_sources_complete_20261026.json`
- `experiments/reports/v232_full_native_outstanding_throughput_20261026.json`

## 预取与卸载证据

Full 共有 202.837GB tagged JOIN H2D，138.955GB native demand H2D。
未观察到工具预取 H2D。不能将这些 JOIN tagged bytes 全部视为完成前
预取，也不能将它们全算成隐藏的 GPU 等待。

生命周期审计覆盖 71 个有归因的 JOIN wait episode。
33 个在完成前出现正 FULL 计划，13 个有完成前启动且随后确认复用的传输，
合计 66.212GB。此统计按开始时间归属，不声称所有字节在完成前传完。
72 次真实 JOIN 与 71 个有归因 episode 的差异保留，不补造一个命中或失败。
已确认复用的 allocation 总量为 201.568GB；未获得首次使用证明的部分
保持 unknown。CPU ACK 发布晚于首次服务不等于数据错误或零 GPU 等待。

真实容量压力下，JOIN wait 节点释放 211.876GB；工具 wait 节点仅释放
47.186MB。这些是累计节点释放量，可能含共享/重复活动，不能当作独占
冷容量峰值或整 agent 卸载次数。

工具准备产生 84 个独立工具决策，确认备份 672.104MB。
在该版本记录的 1 秒卸载阈值下，准备准确率为 78/84，
实际卸载准确率为 6/7。工具卸载样本太少，不能把旧 workload 的 97%
准确率直接用于当前来源，也不能由未发生工具 H2D 推断该设计普遍无效。

证据：

- `experiments/reports/v232_full_prefetch_lifecycle_complete_20261026.json.gz`
- `experiments/reports/v232_full_wait_offload_complete_20261026.json`

## 仍需判断的代价

Full 的 workflow 首次 worker 服务等待 P90 为 1314.382 秒、
最大 1634.555 秒；首次提交至首次 worker 服务最大 1595.628 秒。
这些是 launch 指标，不是首 token TTFT。LoCoBench 当前重放未启用
streaming response metrics，不能把 launch 时间改名为 TTFT。

逻辑预算采用各 agent 上下文加增长 buffer 的保守求和，
没有对共享原生 extent 去重；阻塞资格只在没有 eligible compute
能推进时出借。两者可能影响 batch 宽度，但当前证据不能证明安全、
独占、可回收的空余容量有多少。需要结合完整 ThunderAgent 结果判断，
不能仅凭逻辑预算或 HBM 占用率修改容量。

现有 Full 的老化阈值是 1800 秒。执行连续性不应解释为无限保留
长 agent 的优先权。需要同时报告首次服务和持续 ready gap，
保留平均 JCT 改善中可能存在的公平性代价。

## 实验闸门

保留本次完整 Full 与已完成的同轨迹 ThunderAgent，比较完整 JCT、
全程吞吐、prefill 工作量、迁移、重算及 ready 等待。
初始验收目标为平均 JCT 降低至少 10%、全程输出吞吐提高至少 10%，
并通过三次比较确认重复性。该目标尚未实现，不是保证。

在达到对 ThunderAgent 的明确、可重复收益前，不启动其他 baseline
或消融。若不达标，先基于该轨迹的瓶颈优化；workload 改变必须有机制理由，
不能按哪个 trace 让 Full 获胜来选择，失败结果和正常长尾均保留。

本轮只修复离线工具卸载审计：支持实际 replay 事件路径、
已配对的 runtime-state 时钟与没有 Mamba 的池。运行中的 application
和 engine 仍为冻结快照；没有通过热修改改变本轮实验。
相关回归通过 133 项，报告为
`experiments/reports/v232_complete_analysis_legacy_clock_cpu_20261026.xml`；
这些 CPU 检查不替代 GPU 数值正确性或性能验收。

## ThunderAgent 完整比较与后续候选

ThunderAgent 完成相同的 18 workflows、3674 请求和 549671 输出 token，
平均 JCT 为 1339.764 秒，全程吞吐为 262.225 token/s。
Full 相对它仅改善平均 JCT 约 1.73%，吞吐低约 0.27%，未达到验收目标。
不启动其他 baseline。

ThunderAgent 实际 H2D 为 216.496GB，低于 Full 的 341.792GB。
共同几何的 decode 区间均值比为 1.0011，不能归因为明显的算子慢化。
相同请求与输出位置、无已观察 Host 拷贝 envelope 的 15757 个 singleton
decode 点上，Full/ThunderAgent 均值为 5.602/5.594ms。
这些匹配是诊断样本，不是因果消融。

来源 token 审计排除了大量 parent/child 历史共享作为当前主要解释：
92 个 child 的首输入与其创建前已完成的 parent 历史均只有 3 个共同 token。
不能因此排除 siblings 或其他请求的共享，也不能由 token 共享推断
物理空闲页。报告为
`experiments/reports/v230_spawn_prefix_sharing_causal_20261026.json`。

单独声明的新候选为
`configs/migration/predictive_backed_agent_lending_20261026.json`。
保留 v232 并仅改变 Full 的阻塞预算出借：真实 pending agent 因逻辑容量
不能准入时，可以在每秒维护点从已完成完整 Host 备份的真实阻塞 owner
出借预算，即使另一 eligible agent 仍能计算。一次最多准入一个 agent，
最多验证八个 donor、每个 native path 最多 64 节点。ready/running owner
与 JOIN 恢复保护对象不受影响。此举不创建 D2H、不授权 HBM 物理驱逐，
后续分配仍由原生检查决定。

该候选针对阻塞 parent 的逻辑预算占用，不是已经证明的性能根因或收益。
保持相同 trace、引擎安全上限、工具时间和 seed，先完成 CPU/原生结构
检查，再单独运行 Full。ThunderAgent 参考策略不修改。

共享回归通过 1396 项，无跳过，包括实际 Docker fixture 和原生
FULL 树/分配器的备份进行中、ACK、卸载后及新增未备份尾部检查。
报告为 `experiments/reports/v233_backed_agent_lending_shared_cpu_20261026.xml`。
原生拷贝/事件在该 CPU fixture 中仍是替身，不能当作新 GPU 验证。

v233 已独立注册，application 为
`c7562c31ec3b71139b4ca9d9f430d51e499e44b2`，engine 仍为
`6437d7deeacc533f0d795ba166dfd5256b3f44b2`，端口 18645。
注册文件为
`/home/longhao/experiment/ops/v233_locobench_backed_agent_lending_20261026.registration.json`。
仅运行 Full；v232 完整比较是其参考，不是“同策略重复实验”的前序闸门。
注册阶段两次检查失败均在 GPU 分配前，记录在
`experiments/reports/v233_pre_gpu_registration_checks_20261026.json`。
