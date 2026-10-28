# 原生需求恢复流水线的正式 FULL 启动

2026-10-28。新的 FULL 已进入正式请求执行，尚无完整 JCT 或吞吐结果。
应用冻结为 `01510ed528c2`，native tree 为 `729aa0caa7a1`。
保留 V230/V311 完整工作、全部正常尾部和完整 V345 工具衰减
ThunderAgent 对照，不启动额外 native、其他 baseline 或消融。

## 资格与数值边界

`formal_entry_r1_cpu` 完整执行通过 4100 项及六个子测试，保留此前
4096 个身份并新增四个。manifest 包含正式配置，实际构造器、安装状态
及 probe 状态有检查。准备重放的 base `fa5cb52a` 加累计补丁
`c9b3ae50` 重建相同 native tree，92 项 native CPU 检查通过；
native 注册 sources 仅将补丁路径重绑定到冻结应用，字节与树不变。

当前冻结应用的四项真实 DMA 组件通过。其新的确定性模型验证完成
七个请求：24583 token/2416607232 bytes 真实恢复、精确 ACK、
后续实际 prefill、三个在暂存后启动并在 ACK 前完成的 resident decode
batch 均通过；A 的 512 token 和 B 的八个 token 与独立冷参考相同，
最大 logprob 差均为零。实际 server 确认为确定性 FA3/pytorch。
这是有明确数学配置的 KV I/O 数值资格，不是正式性能配置。

普通 FA3/flashinfer 的独立续接参考仍不满足长序列 greedy 相同门槛：
pipeline 运行 A 首个 token 分歧为155，关闭 pipeline 的相同场景
为210。两侧 B 的八个 token 均相同、最大 logprob 差均为
0.000630020746；独立初始历史差为零。所有九个请求正文除真实 session
命名空间外相同，runtime AST 的差别仅为新增 probe 状态发布字段。
这些结果和原始失败均保留，普通后端长序列 greedy 等价性明确未证明。

独立 `review_current_models.py` 与 `current_model_review.json` 记录上述不同证据的边界，核对
当前 CPU/source/native、实际参数、精确 ACK/服务、逐请求输入和实际
owned 退出。它只授予继续做 position 校准的资格，不授予完整性能
收益或普通后端全 token 数值等价。完整固定-token replay 的系统时间
与独立 greedy 数值检查分开报告。

## Position 校准

注册为 `ops/demand_prefill_pipeline_position_20261028_r1.registration.json`。
24 个精确 token/session 请求全部通过，24 个均按原生 stop 结束。
位置分派的每步 wall 增量均值为：

| 实际 batch | 增量/ms |
| --- | ---: |
| 1 | 0.009065 |
| 8 | 0.003786 |
| 24 | 0.009628 |

它保留原生采样再进行位置分派，不是纯 GPU kernel 成本或 JCT 收益。
校准 watcher967144 已实际退出，server 清理完成；正式 server 在其
结束后启动，未将 transient GPU 空闲当作模型资格完成。

## 实际 FULL

正式注册为 `ops/demand_prefill_pipeline_20261028_r1.registration.json`，
端口18693，执行树为 `beliefkv-causal-01510ed528c2`。使用
`predictive_demand_prefill_pipeline_20261028.json`，仅相对原配置切换
准确补丁、开启已提交 native demand 的流水线并显式关闭 progress probe。
没有开启工具预取、普通执行前沿恢复、恢复 TTL 或新的 agent 资格。
JOIN/EOS、预测工具卸载、prepare_host、agent 连续性、sibling 共同
准入和8/4 layer handoff 保持原配置。

完整工作为18个不同项目、18 workflow、3674请求、145565966输入、
549671输出 token、72 JOIN，Device805198/Host200GB、running/decode24、
seed21、原始 stopping、流式测量及全部正常尾部不变。V311 工具时间
provenance 与完整 V345 一致，quality_exclusions 为空。

`live_start.py/.json` 已实际核对当前 nice0 watcher969993、server970533、
driver971947 和 sole GPU scheduler970873；安装的 pipeline 为 true，
progress probe、shared profiling、工具预取及优先窗口、普通 frontier
restore 均关闭。实际 server 参数为原来的非确定性 FA3/flashinfer，
确定性数值 fixture 的参数没有带入正式性能实验。

启动审计封存了170个精确完成回执，与完整 V345 逐请求工作匹配。
第一次审计因当时尚无完整回执而拒绝，保留 `live_start_r1.log`；
后续成功记录为 `live_start.json` 和 `live_start_r2.log`。170只是
完整3674请求的前缀，不能据此报告完整正确性、JCT 或吞吐收益。

## 后续验收

完整结束后需要两次新鲜 idle 快照、实际 owned 清理、完整原始停止/
token 工作、独立 staged-demand/Host-hit/handoff 字节对账及服务身份核对。
继续比较完整 workflow JCT、全程输出吞吐和随时间的趋势，保留所有正常
长尾及负结果。把恢复等待挪出 prefill worker 不能单独算收益。

完整 V345 的引擎为 `11b84f7c3198`，与本候选不同，报告及图中需披露。
当前没有明确、可重复的双指标性能收益，整体目标保持未完成；
本轮不自动排队其他 baseline、消融或后续 GPU 候选。
