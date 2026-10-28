# Sibling 原生共同服务的独立审计

本次新增只读算子 `scripts/audit_sibling_native_service.py`，审计
正在执行的 sibling-service/capacity FULL。它不修改冻结应用、
原生引擎或调度线程。FULL 继续使用应用 `100ec17e`、原生 tree
`cdd58a4acbb5`、完整 V230/V311 工作及 V345 工具衰减对照。

## 证据定义

运行时 `agent_join_siblings_native_service` 在请求准入后的首次
native forward 返回时记录。它不是逻辑资格授权，也不是 agent
生命周期内的首次服务；同一 agent 的后续请求可以继续产生记录。

每条记录必须独立对应：

- 原生 worker 的唯一 sample/forward、实际成员及 workflow/context/epoch；
- journal 与 worker 的单调时钟区间及 wall-clock 对齐；
- 原始 runtime 明确记录、至少两成员的 ALL-JOIN，以及选批时仍存在的
  parent JOIN_WAIT；推断 JOIN 或未来才开始的等待不能作为证明；
- 真实完成回执的请求身份、session/generation、时钟域和生命周期。

READY 到 selected 与 READY 到 forward return 分开保留。共享 batch
不是纯 GPU kernel 的同时开始，也不直接证明 JOIN 或 workflow JCT
改善。未到达的原生行、JOIN 或完成回执保留为未证实；空 journal
不是正面证据。记录的 attempt ID 没有独立出现在 worker 行中，
因此其独立验证状态仍为 false；重试前的旧服务保留为单独诊断。

## 已封存前缀

`collecting_sibling_service_r1.json` 封存四份输入的完整 JSONL 前缀与
SHA256。加强边界后通过 `--sealed-audit` 重跑同一前缀，生成
`collecting_sibling_service_r1_strict_recheck.json`，结果不变：

- 45 条 journal、46 个请求准入尝试全部绑定真实 prefill 和完成 session；
- 它们只来自一个并行 JOIN，不能表述为 45 个 JOIN 或整体覆盖率；
- 一条记录包含两个在同一原生 batch 服务的 sibling，sample 为
  `native-service-000016356`，forward16367；
- 两者 READY 到 selected 为 11.054ms 与 679.642ms，
  READY 到 forward return 为 85.282ms 与 753.870ms。

两者 ready 时间不同，不能把上述等待差异当作原生服务开始偏斜。
这些日志只证明实现路径已经实际使用，不是独立的性能消融或提前量收益。

## CPU 边界与后续分析

初始 88 项针对性检查保留在 `sibling_service_audit_cpu_r1.xml`。
加强后的 96 项全部通过，包含缺失/错误身份、旧完成成员、session
generation 的布尔伪值、推断 JOIN、重复 forward、重试和封存前缀
追加/篡改/外部 arm 拒绝。新完整审计与测试不在旧 3580 项 manifest
中；未来主代码资格需更新，不能声称旧资格已覆盖这些新增观察代码。

`deferred_plots.py` 使用冻结 FULL 的绘图实现及已有 CPU plotting
环境，先核对同工作量、工具时间和明确的 sibling/common 引擎组合。
只有 FULL 完整成功且 owned 进程实际退出后才绘制 60 秒分箱、
分布及几何档位图。证据保留两侧真实引擎身份，所有正常长尾、空闲及
实际末箱宽度均保留。它没有 GPU 启动权限；失败时停止，不重启实验。

当前 FULL 尚未完成，不能用前缀吞吐替代完整 JCT 或吞吐结论。
新增材料均归属 `experiments/reports/sibling_service_capacity_20261028/`，
没有分配新的主实验版本。
