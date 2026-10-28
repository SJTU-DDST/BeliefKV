# Sibling service/capacity 候选的 GPU 验证

当前候选使用 `predictive_sibling_service_capacity_20261028.json`：
将 ALL-JOIN sibling 的共同准入落实到原生 prefill 选批，统一普通工具
卸载和逐层 handoff 的消费者输入预留，并将 JOIN 逻辑额度交接关联到
实际物理恢复。完整 CPU 资格为 3580 项及 4 个 subtest，原生相关
检查 92 项，三个实际 runtime 构造器隔离检查通过。

本次冻结应用 `100ec17e07ce0d6381df460d4784fbcb98b59019` 位于
`/home/longhao/experiment/.worktrees/beliefkv-sibling-capacity-gpu-100ec17e`。
再次逐文件验证原 CPU manifest 的 1795 个绑定文件均未变化。
用户未提交的研究文档不包含在该冻结 checkout 中。

## 原生组件

2026-10-28 21:57 Asia/Shanghai，GPU 组件 R1 通过：

- 干净原生 commit `873970a45a250045e823dcc1134a48d70de49ec2`，
  tree `cdd58a4acbb5038207d3a5020c54d7cbcef983b9`；
- 17 层真实 GPU/Host pool，D2H/H2D 使用 8/4 分组；
- donor 的全部 Host K/V、consumer 的全部 Device K/V、
  受保护 resident 的原始 K/V 均验证；
- donor 旧 forward reader 的数据未被覆盖，提前返回仍受 ACK 保护；
- 真实空闲容量在提交后保持 4 slots，最终 ACK 后才增至 20；
- 带有限延迟的独立夹具验证 consumer 首层可在 donor 最终 ACK 前读取。

正常夹具未观察到最后一项时序，不能隐去该结果；有限延迟仅用于字节
与依赖正确性验证，不用于推断真实 workload 的隐藏传输时间或性能收益。
组件进程 700216 已实际退出，随后 GPU 上没有计算进程。

## 模型数值

同一应用和原生引擎的模型 R1 执行 7 个无强制输出请求。它使用
49152 Device-token、8GB Host 的独立夹具，保留完整 Qwen3-Coder 模型，
不将这些小容量作为正式实验配置。

真实 `before_native_admission` 路径完成一次 8/4 handoff，恢复 12296
token，保留 8 个输入增量及 24 个增长预留；逻辑准入未被该物理操作修改。
consumer 输出与独立参考逐 token 一致，后续 donor 恢复也逐 token 一致，
两者最大 logprob 差均为 0。donor 恢复确有 12295 token 的 Host 命中，
不是只有 resident 命中的空验证。

独立 `gpu_model_review.py/.json` 再次核对组件字节、实际 handoff、
模型输出、配置 SHA、CPU 绑定、原生状态及实际 owned cleanup。
重放准备 checkout 的 canonical patch 路径只重绑定到冻结应用中的
同字节 patch，tree 保持完全一致。

这些证据资格仅覆盖物理字节、原生 handoff 和模型数值，尚未证明：

- 新 sibling 偏好在完整动态 workload 中实际带来共同服务；
- JOIN 物理计划在完整 workload 中减少了恢复等待或总迁移；
- 完整 JCT、吞吐及控制开销收益。

## 下一阶段

独立 position/session/原始停止检查登记为：
`ops/sibling_service_capacity_position_20261028_r1.registration.json`。
它通过后才登记单臂 FULL：
`ops/sibling_service_capacity_20261028_r1.registration.json`。

正式比较仍使用 V230 完整 18 个不同项目、3674 请求、145565966
输入及 549671 输出 token、72 JOIN、V311 工具时间、Device805198/
Host200GB、running/decode24 和 seed21。工具预取及其优先级窗口、
普通 execution-frontier 恢复保持关闭；JOIN/EOS、prepare_host、
75% 长工具卸载、agent 连续性和 8/4 handoff 保留。
比较复用完整 V345 工具衰减 ThunderAgent，明确其 common 原生树
与本候选不同，不将组合比较解释为单项消融。

当前整体性能目标仍未满足。保留全部正常长尾、旧负结果及原工作量，
仅在完整 JCT 和吞吐有明确且可重复收益之后推进其他 baseline。

证据位于 `experiments/reports/sibling_service_capacity_20261028/`：
`gpu_components_r1.json`、`model_r1/report.json`、
`model_r1/owned_process_cleanup.json`、`gpu_model_review.py/.json`、
`native_registration_sources.json`。完整 position/FULL 结果单独审计，
不由这些小夹具推断。
