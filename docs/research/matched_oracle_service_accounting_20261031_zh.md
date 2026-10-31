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
