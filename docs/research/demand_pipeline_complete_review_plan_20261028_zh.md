# 原生需求恢复流水线的完整结果审计入口

2026-10-28。本轮仍在运行；本文记录结束后的审计方法，不发布完整性能
结论。应用冻结为 `01510ed528c2`，native 为 `729aa0caa7a1`。工具预取、
优先窗口、普通 frontier restore、shared profiling 及 decode progress
probe 均关闭。保留原生 stopping、全部正常尾部和完整 V230/V311 工作。

## 完整比较的边界

`experiments/reports/prefill_pipeline_20261028/complete_review.py` 单独绑定：

- 本轮 `demand_prefill_pipeline_20261028_r1` FULL；
- 已完成 V345 工具衰减 ThunderAgent；
- 已完成 `tool_offload_before_admission_20261027_r1` FULL。

三者必须保持18个不同项目、3674请求、145565966输入和549671输出
token、相同工具时间 provenance、容量、种子和停止条件。仅接受明确
声明的 native tree：当前需求流水线与两个旧共同引擎分别核验，不能
简单删除 engine 比较就接受任意源码。

审计先确认 supervisor 和所有 owned 进程实际退出、完整回执、两次
新鲜 idle 快照、实际 runtime 参数与原来的非确定性 FA3/flashinfer。
全部通过后才创建图和完整结果。图沿用60秒真实时间 bin，保留不满
一个 bin 的真实尾段及全部正常长尾，结束后不补零。主图比较本轮与
ThunderAgent，CSV和完整图证据额外保存准入前卸载版。

已执行 `review_precheck.py`，13项 CPU 边界检查通过：实际运行中的
FULL 被拒绝作为完整结果，两个旧完整对照的实际 owned 退出被接受；
三个注册的共同工作条件相同；错误引擎重标、安装状态缺失、开启或
漏报 progress probe、开启工具预取、frontier restore、profiling
及错误 layer group 均被拒绝。这不是新增 GPU 或完整性能证据，
不替代已绑定冻结应用的4100项CPU及模型/position资格。

## 迁移与服务的独立核对

`scripts/audit_demand_prefill_pipeline.py` 单独生成
`complete_demand_h2d_service.json`。它保留旧 handoff 审计原口径，
将旧口径中尚未解释的 untagged H2D 明确与暂存 native demand 对账：

```text
untagged native H2D
= native request Host-hit bytes
+ layer handoff consumer bytes
+ exact ACK-matched staged native demand bytes
```

随后逐个核对暂存批次的所有成员、attempt/session/epoch、真实后续
prefill 和完成回执；取消必须有明确取消日志。不能把所有 native H2D
当作预取，也不能给同一份拷贝重复计入原生 Host 命中和暂存恢复。
ACK和后续服务通过仍不能直接证明隐藏了多少传输时间。

`mechanism_review.py` 继续复用现有 JOIN grant 和 sibling 实际服务
算子，保留逻辑资格、真实释放、reserved extent 及未知服务身份的区别。
`known_guard_review.py` 仅在声明的 engine pair 与其余全部共同条件相同
时复用原来的四个工具/EOS反例分类器，不修改生产 guard。

## 执行次序

所有命令只使用CPU；在本轮实际完整结束并退出后执行：

1. `complete_review.py`：完整工作、清理、三个系统指标及趋势/分布图。
2. `audit_demand_prefill_pipeline.py`：精确 ACK、完整字节和后续服务。
3. 现有 `audit_causal_jct_paths.py`：本轮 Full、Preadmission、ThunderAgent；
   再运行 `audit_request_service_interleaving.py` 分解成员及交错区间。
4. 现有 `audit_sealed_wait_prefetch.py`、`audit_restore_wait_cohorts.py`、
   `audit_eos_trigger_lifecycle.py`：分别保留完整预取、采样等待和最终RETURN。
5. `known_guard_review.py`、`mechanism_review.py`、`final_index.py`：核对
   原生身份与完整输入，复用已验证的阶段/批次形态算子，封存原字节归档。

历史24.981494秒仍属于旧分组版的关键工具后继。新结果使用自己的完整
关键链，若链改变则明确报告，不筛选相同链或删除正常长尾。历史新增
独占控制CPU、普通数学长序列greedy等价性仍未证明。

本次只准备CPU审计入口，没有注册另一个GPU候选、baseline或消融。
明确、可重复的双指标优势及所有baseline比较目标保持未完成。
