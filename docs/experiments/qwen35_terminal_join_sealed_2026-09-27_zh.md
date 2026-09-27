# Qwen3.5 工具与终态 JOIN 未见项目密封评估（2026-09-27）

## 冻结口径与运行

`configs/migration/qwen35_terminal_join_sealed_2026-09-27/` 冻结
Matplotlib 与 scikit-learn 各 8 个 `test_id` root；32 个训练项目
root 只提供并发负载，不作为该批模型拟合标签。测试 manifest
SHA-256 为
`df7e70c37c5d636a637738597cbbbbc6e0dab0a9177c97c8503f1f290c2dec61`，
provenance SHA-256 为
`68a272907bd9088173e37fd19c4ff8844df2d285c30fbb8b40e2e63c89aff8de`。
真实运行源提交为 `5c38ddc8d405db46a602ea66974bf30e59948ce5`，
输出位于 `experiments/raw/qwen35_terminal_join_sealed_20260927_v1/`。
服务保持 Qwen3.5-35B-A3B BF16、SGLang 0.5.20、
running=48、Host 120 GB/70:30/NUMA1。predictive 物理动作未启用。

16 个测试 root 全部形成终态结果文件，其中 15 个 completed、
1 个 incomplete（`matplotlib__matplotlib-22865`）。后者三个
child 都自然 RETURN 且 JOIN 满足，但 root 没有合格的后续
parent reentry；不能算成具有可配对首次服务的自然预取候选，
也不能冒充自然完成的端到端 workflow。没有 runner_error。

原启动器在**实验已结束后**调用
`scripts/audit_join_parent_first_service.py` 时因直接执行
文件路径缺少仓库根 `sys.path` 而退出；GPU 采集和冻结模型
未因此改变。只对审计器及两份密封评分脚本补了导入路径
bootstrap，回归验证从仓库外直接执行后，按原计划的
训练源、manifest 与阈值补跑审计与两项评分。
这属于后处理入口修复，不是看到测试误差后重新选参；
该报告的评分代码提交应与运行源提交分别保存。

## 冻结结果

- **终态 JOIN 时间**：审计得到 16 个自然 JOIN，15 个
  parent 请求与首次服务可配对，15/15 有触发前负载快照。
  固定的 `heavy_queue` 门禁选中 5 组（Matplotlib 1、
  scikit-learn 4）。仅在这 5 组上，训练项目
  task-balanced ETA 约 225.8 ms 的 JOIN 绝对误差 P50
  为 **37.6 ms**；通知即 JOIN 的配对基线为
  **188.2 ms**。按 task bootstrap 的平均绝对误差
  改善约 160.3 ms、95% 区间 [125.3, 198.8] ms，
  5/5 在 500 ms 内。这里的“显著”只对这 5 个
  有条件选中的任务及该配对基线成立；只有两个未见
  项目且 Matplotlib 仅一例，不是完整 JOIN 或跨
  压力精度的证明。
- **JOIN 物理窗口**：选中 5/5 个通知到 parent 首次
  GPU 服务超过 2 秒，中位约 6.09 秒。但训练
  `heavy_queue` 服务窗口 P10 先验约 8.65 秒
  **高估全部 5 组**，不能当作安全的 `latest-start`
  或 H2D 预算。所有 15 个可配对 JOIN 的通知到
  parent 首次服务中位约 645 ms；等待包含请求提交、
  admission/排队等，未归因到 parent 的 H2D。
- **工具存活窗口**：608 个首次冷输入调用中
  500 个实际至少 600 ms。预冻结的 calls 权重、
  `p>=0.8` 门槛选中 363 个，353 个为真
  （precision 97.2%，对 500 个真窗口的 recall
  70.6%）；Matplotlib 为 211/213，
  scikit-learn 为 142/150。始终报长的
  基线 precision 为 500/608（82.2%），
  但覆盖 100%；必须一起报告漏掉的 147 个真窗口。
- **工具时间 ETA**：在事后已知为长调用的同一
  500 个事件上，calls 权重 ETA 的绝对误差
  P50 约 369 ms，全局训练 task 先验约
  402 ms；按 workflow/task 聚类的配对改善
  95% 区间 [-95, 238] ms，**不支持**
  声称工具时刻已显著改善。预列的 workflows
  权重此批误差约 289 ms，对 calls 的配对
  改善约 80 ms、区间 [51, 128] ms；
  但该模式在训练项目留一时为约 484 ms，
  明显劣于 calls 的约 348 ms。不能事后用
  测试结果选择 workflows 权重再称其为密封成功。

服务窗口、工具时长与首次服务均为**响应式运行**
的事后标签。该批没有请求级预测 H2D 容量/ACK/
实际消费，也没有配对的 P5/P6 GPU 吞吐对照；
两份评分产物均为 `not_action_eligible`。现有证据
支持“终态提示 + 压力门禁”在有限子集改进 JOIN
点预测，以及工具长窗口分类在两个项目上有效；
**不满足**工具返回 ETA、完整 JOIN 覆盖和预测式
物理动作的最终验收。后续只能在训练项目内
继续探索新特征/条件时钟，重新冻结并采集新的
独立项目验收；不得反复用本次测试集回选阈值。

## 复核产物

位于上述 run 目录：

| 文件 | SHA-256 |
| --- | --- |
| `test_parent_first_service.json` | `9f08785ba8a8811cc863e6a6ce25be6e4ae81be8a688d786ad83d2bc5a1dc284` |
| `project_disjoint_service_gate.json` | `2dab64444d3509fe7a8a452b4f55948ad10a6f8b83b35a0678df1e8e955dd2ae` |
| `project_disjoint_tool_gate.json` | `2ae14f5ff6c2e4459c5cc2c63914150ad430f86ffbf8245f7f3859415e1df5ab` |
