# Workflow 服务与逐层容量复用

当前研究目标仍是完整 workflow 平均 JCT 和全程输出吞吐同时优于
ThunderAgent，并在重复比较中成立。服务公平性用于揭示局部性与服务
机会的权衡，不能取代尚未通过的原有性能验收。

GPU 禁止时段截至北京时间 2026-10-27 11:00。以下新增实现只做 CPU
验证，不重启已中止的 v236b，不修改任何冻结执行树。

## 已有证据

相同 v230 请求/token/工具时长的完整比较：

| 指标 | v232 ThunderAgent | v235 Full |
| --- | ---: | ---: |
| 平均 JCT，秒 | 1339.764 | 1344.922 |
| 全程输出 token/s | 262.225 | 280.631 |
| 平均 arrival 至首个 worker launch，秒 | 521.525 | 288.037 |
| 每 workflow 最大后续 ready 服务间隔的均值，秒 | 114.433 | 126.784 |
| 最大后续 ready 服务间隔的最差值，秒 | 493.040 | 631.361 |

表中后三项服务观察均不能混称 token TTFT/TBT。
worker launch 不等于首 token；间隔统计使用完整 worker 服务区间，
只排除当时全部分支都没有提交模型请求的时间。
Full 的首服务更早，但持续服务并非全面改善，平均 JCT 也未改善。
不能只引用首服务一项宣称超过 baseline。

依据为：
`experiments/reports/v232_thunderagent_workflow_service_complete_20261026.json`、
`experiments/reports/v235_full_workflow_service_complete_20261026.json` 和
对应完整 causal comparison。保留全部普通长尾和失败比较。

## 新增测量

`--stream-response-metrics` 为 captured LoCoBench replay 的显式可选项，
默认关闭。登记时冻结在 comparison plan 中，所有 arm 使用同一值，
operator 检查实际协议及完整覆盖；AgentX 原有流式模式保持启用。
该选项不更改 token、session、随机种子、原始停止预算或终止 receipt。
CPU 集成测试同时覆盖原非流式路径和 session-bound 流式路径。

- Workflow 首服务：从原始 WORKFLOW_START 到任一后代的首个非空输出。
  这是系统内部的模型服务，不能当作用户看见 root 答案的时延。
- 后续 ready 服务间隔：首输出之后，当至少一个后代有已提交且未完成
  的模型请求时，至下一次非空输出的间隔。包含资格、排队及 KV 等待。
  全部分支真正外部阻塞时停止累计，重新提交时从新 ready 时刻累计。
- 保留旧的含首次 ready 等待统计，并新增独立的 post-first 统计，
  避免把首次准入等待重复当作后续服务间隔。
- 以 root 等权聚合，不让 fanout 自动增加服务权重。另报各 workflow
  的最大 request TTFT 和最大 request 输出间隔，揭示聚合输出可能
  掩盖的单个 child 饥饿。critical JOIN 等待继续单列。
- SSE chunk 合并会降低时间分辨率，披露合并计数及 undefined rate，
  不宣称获得每个 token 的精确 TBT。间隔以收到输出的 chunk 为边界。
- 记录完整输出量、workflow event makespan、完成数及 timing 覆盖。
  无输出及未完成 workflow 留在 SLO 分母，不通过删去这些样本改善结果。
  单输出且没有后续 ready gap 的完整 workflow 不产生虚构的间隔。

`scripts/evaluate_workflow_service_slo.py` 在共同首服务/后续间隔预算网格
上离线生成达标比例、达标 workflow/s 及达标输出 token/s，并保留
完整 JCT 和全程输出吞吐。校验来源 token identity、请求量、workflow
集合及统一流式协议。工具不调整调度，不证明容量或物理执行等价。
历史非流式 arm 不能与新流式 arm 混为同一测量协议下的正式结果。

控制路径中没有新增逐 token 全队列扫描。客户端每个输出 chunk 更新
固定大小 histogram；per-request 摘要只在请求完成后写入已有 receipt
文件。最终汇总及预算网格均在测量后的离线路径执行。

## 后续设计与比较

应先保留原始 ThunderAgent，绘制完整吞吐/JCT 与共同服务预算的达标
关系。若需要额外的 SLO 约束调度变体，明确声明为独立变体，并给
baseline 同等的安全实现与调参机会。不能故意加一个低效轮转器，
也不能用只有我们启用的服务约束制造迁移优势。

服务期限只有在同时 ready 的工作集超过 HBM、且不能等自然结束或
阻塞时，才可能迫使不同 agent 交换资格；公平性不必然导致频繁迁移。
因此下一步应量化共同服务目标下真实暴露的恢复等待及其容量条件。
策略仍按 agent 保持连续执行，只在安全请求边界处理必要的交接，
JOIN、真实工具阻塞和原生锁/DMA 权限继续参与判断。
workflow-entry 候选可能增加新 workflow 的首服务等待，不能视为
本服务公平性设计的默认组成。

## 逐层容量复用的可行性

冻结 engine 的 `cache_controller.py` 中 `load()` 在任何 H2D 入队前
执行 `mem_pool_device_allocator.alloc(len(host_indices))`。
一个 token 的 native Device index 对应跨层空间。因此少传几层并不
少分配几层；仅调整 load 顺序无法实现“A 逐层卸载、B 逐层占用”。

实现用户提出的容量流水线至少需要：

1. 将实际分配/所有权扩展到可独立回收的层或 extent，或引入等价的
   分层地址映射。不能在 A 仍拥有后续层时，把其跨层 token index
   当成完全空闲页交给 B。
2. A 的每层在 D2H ACK 且最后使用该层的 GPU 工作完成后，才能覆盖。
   保留共享前缀、COW、native consumer lock、取消/重试和异步 ACK
   的所有权检查。A 必须是可安全阻塞的 donor。
3. 对 B 的后续每层建立 copy-ready 依赖。首层先到并不保证无 stall，
   必须检查后续 copy 能否赶上 B 的计算时刻。若赶不上，同批 resident
   请求也可能被 B 拖慢。
4. 测量双向 PCIe、HBM 带宽和 copy/compute 竞争，以及为流水线新增的
   小粒度调用开销。提前开始不等于整个 batch 更快。

当前没有修改 engine allocator。先按相同服务预算确认确有足够的
暴露 H2D 与真实冷 donor，再决定是否值得开展这项侵入性改动。
更直接的优先项仍是既有异步增量备份、settled 冷页即时释放、真实
JOIN/EOS 提前量，以及工具结束后及时归还已借出的 agent 资格。
这些机制也必须以完整 JCT、吞吐和额外迁移共同验收。

## CPU 验证

定向报告：
`experiments/reports/v238_workflow_response_metrics_targeted_cpu_20261026.xml`
通过 319 项，无跳过，包含新旧 HTTP 协议、真实终止 receipt、
session 关闭、工作量一致性、首服务/后续间隔分离和未完成样本。
共享报告：
`experiments/reports/v238_workflow_service_shared_cpu_20261026.xml`
通过 1739 项及四个子测试，无失败或跳过，纳入离线预算脚本、原有
全部 1534 项、依赖图、工具归还和原生组件/Docker fixture。
CPU HTTP/copy/event fixture 不建立 GPU 数值或性能结论。

当前依赖图也在 v230 的 18 个 workflow、22268 条原始事件上与冻结
旧图比较，每个事件之后的全部 invocation 状态均无差异。
报告为 `experiments/reports/v238_source_dependency_final_audit_20261026.json`。
原日志有一处并发追加产生的时间倒序；直接按文件追加顺序执行的
首次审计被旧图的时间检查拒绝，正式离线检查按每 workflow 稳定时间
排序并保留全部事件。该观察不证明线上执行、数值或性能等价。
