# 匹配修复对照的选择与服务审计

当前 oracle 对照的唯一策略差异是剩余工作量排序，两臂都保留 JOIN/EOS、sibling 分组、工具感知压力和相同进度记账。原 `audit_workflow_size_selection.py` 的局部 reference 则是较早的 ThunderAgent 类别、上下文顺序，不包含全部这些已有机制。它仍适合原历史实验的既定对照，不能把其全部差异归为本轮工作量排序。

## 新读器

`scripts/audit_matched_oracle_service.py` 首先复用原读器精确复现已经执行的 oracle 局部选择，再在相同候选、预算、100 token buffer、ready JOIN 零优先级及已观察 sibling 邻接分组下关闭工作量排序。两臂配置和 artifact 必须通过 `verify_ranking_only_pair`，不能只凭日志标签宣称匹配。

测试包含两个可能导致旧口径误归因的例子：受保护 JOIN parent 相对于小 KV 普通请求先执行，以及 JOIN sibling 邻接相对于单纯队列顺序改变集合。关闭工作量排序的本轮对照同样拥有这些行为，新读器不再把它们计算为剩余工作量收益。

新增成员的服务证据与实际周期授予逐一匹配，身份包括 request、workflow、invocation、context、epoch、attempt、session 和 generation，时间使用同一单调时钟。分别报告：

- 相对于匹配对照改变的局部集合；
- 实际保留资格及后续精确请求服务；
- 在下一控制或已记录同 agent 干预前完成的新有效批次；
- 缺失周期授予、服务证据或仍开放的观察窗口。

这些仍是局部条件比较和实际服务证据，不是完整反事实执行。改变选择之后的压力对象、物理恢复、未来组批、排队和完整 JCT 没有被模拟，也不能把记录次数相加为独立请求收益。

## 服务窗口边界

原服务窗口读器已经识别普通/Join 授予、初始注册和 JOIN donor，尚未识别新增路径的实际干预。本次补齐：

- 实际 KV 缺失触发的资格暂停 `agent_full_offload_paused`；
- `agent_predicted_tool_replacement` 的消费者再次授予和 donor 暂停；
- `workflow_remaining_pressure_pause` 中立即执行的暂停。

仅标记 `marked_for_pause=true` 尚未暂停当前请求，不以此关闭当前执行窗口。所有这些改动都只影响离线归因，未修改 GPU 工作集策略。

早期同一封存前缀的扩展干预原型未改变 147 条已确认有效批次的分类。不能据此断言原缺口解释了历史 JCT 回退；补齐它是为了后续范围内的归因完整性。仍可能存在未记录干预和跨日志发布差异，独立 JSONL 封存前缀不构成原子快照。

## 当前真实前缀

`oracle_toolaware_initial12_mean70_live_r1/matched_selection_service_prefix_r1.json` 记录 603 次实际 oracle 控制：

| 项目 | 前缀结果 |
| --- | ---: |
| 相对于匹配对照改变顺序 | 89 |
| 相对于匹配对照改变选中集合 | 4 |
| 新选成员与实际周期授予匹配 | 7 |
| 新选成员在压力后保留资格 | 7 |
| 新选成员有后续精确请求服务 | 7 |
| 在下一控制/已记录干预前完成有效批次 | 5 |
| 有后续服务的独立新选 request attempt | 7 |

共同吞吐窗口尚未完整观察，测量组也尚未全部完成。另两条记录没有在上述范围内确认有效批次，不能把它们直接叫作无效调度或失败请求。不能用已经完成的短任务计算本轮平均 JCT 并与完整对照比较。

最终 113 项针对性 CPU 测试已通过，报告为 `matched_service_reader_cpu_r2.xml`，包含新增的重复选择时间戳拒绝测试；保留此前 112 项报告。运行中的服务应用仍为 `93161529`，原生引擎仍为 `434c35dd`；没有热改服务代码，没有修改 trace 工具时长，也没有启动另一项 GPU 实验。此读器资格不是新的整套 GPU 资格。

## 冻结版本与较后前缀

最终分析代码已提交并冻结为 `97b3842519f0a713fd78de47f04f83bb72dc157f`。从该冻结树执行的 `matched_selection_service_prefix_r2.json` 包含 954 次实际控制、5 次匹配集合变化、8 个获得后续服务的新选 request attempt，其中 6 个在下一控制或已记录干预前完成有效批次。固定共同窗口已完整覆盖，其中为 4 次集合变化、7 个新选请求获得服务及 5 个有效批次完成；测量执行尾部尚未完成。

先前结果 CPU 观察器 PID 3092045 在仍等待测量组、没有运行子任务时被明确替换，实际退出已验证，原注册及原状态保留，另有 `.superseded.json` 记录。新结果观察器 PID 3102671 从 `97b38425` 分析树执行，注册为：

`/home/longhao/experiment/ops/workflow_original56_initial12_mean70_toolaware_oracle_results_20261031_r2.registration.json`

它保留完整 JCT 比较、两臂免费额度/实际服务、周期阶段和固定窗口绘图，额外执行本读器的完整测量组审计。独立 `results_observer_r2_verified.json` 核对全部输入哈希、实际进程、nice19、隐藏 CUDA、单线程及原观察器退出。测量停机观察器 PID 3091973 和 GPU 运行均未替换。2026-10-31 15:11 UTC 的实时状态为 14/18 测量成员完成、48 个 workflow 到达，仍无完整 JCT 结果。

## 共同窗口的计算供给

复用已有 200 ms 外部 GPU 采样器，通过原生 wall/monotonic 时钟对齐，未新建采样或修改实验。两臂均已覆盖同一 1583.262079 秒固定窗口：

| 指标 | 修复对照 | Oracle |
| --- | ---: | ---: |
| 时间加权 GPU 利用率 | 99.4440% | 99.3682% |
| GPU 零值采样折算 | 1.401 秒 | 0.801 秒 |
| 有 ready-paused 的时间比例 | 33.5555% | 35.9965% |
| 新鲜物理采样下 allocator 驻留比例 | 98.6219% | 98.6311% |

来源分别为 `control_common_idle_r1/report.json` 和 `oracle_common_idle_r1/report.json`。这里的时间比例不同于前文工作负载判定的状态样本比例。NVML 利用率不是 CUDA 计算占比或吞吐上界，allocator 驻留也包括可回收及在途 KV。结果支持当前共同窗口已有持续供给，不能继续用长时间 GPU 空闲解释性能差异；仍须用完整 JCT、共同窗口吞吐、组批及迁移来验收。不同报告均有各自封存边界，不构成原子快照。
