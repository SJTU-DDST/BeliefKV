# 新到达负载下的 ThunderAgent 对照

2026-10-29。用户已取消没有已核验作者代码的 Agentix 复现，并明确要求
先启动新 workload 下的 ThunderAgent，与我们的优化并行。
本页记录这项新授权，替代先等待 Agentix 正式完成的顺序。
既有 Agentix 和 TokenCake 的代码、模型检查及失败结果全部保留。

## 不变的完整工作

采用现有 V230/V311：18 个不同原项目、3674 个请求、145565966 个输入
token、549671 个输出 token、92 个 child 和 72 个 JOIN。
保留原始依赖、完整请求和全部正常尾部。工具计时仍是公开 TraceLab
分类样本替换 931 次 execute 的耗时，其他工具保持原轨迹时间，
没有给在线政策提供未来的精确工具返回时刻。

模型为 Qwen3-Coder-30B-A3B-Instruct，原生共同树 11b84，
Device805198 token、Host200GB、running/decode24、prefill4096、
mixed 关闭、全部 seed21。只运行工具衰减 ThunderAgent，
不混入 predictive 的 JOIN 优先、预取、备份或卸载策略。

## 新到达契约

10 个 workflow 在 t=0 到达；剩余 8 个按独立 RNG 的泊松过程到达，
平均间隔 60 秒。保持原 workflow ID 排序，不按任务大小或历史输赢
挑选初始组。seed21 的最后到达时刻为 431.715498 秒。
这是一份有限加压轨迹，不宣称长期稳态容量。

版本 1 的纯泊松 manifest 保持原有字节及语义。新混合方式使用版本 2，
标为 warm_start_poisson，冻结初始数量、种子、速率及完整到达表，
验证时重新生成并逐项比对。完整 JCT 包含从计划到达到完成的全部
入口延迟、准入等待和正常工具阻塞。

CPU 首轮 58 项到达相关检查通过；完整共同 causal/paper 回归仍需
在干净冻结源码上执行。原共同模型及位置采样证明只在对应模块、
采样器和原生树精确相同的条件下复用，由现有启动入口重新校验。
改变到达不改变模型输入或采样逻辑，不为此重采 native。
旧突发 ThunderAgent 的 JCT 不能作为这次新到达的匹配对照结果。

## 执行边界

准备、CPU、注册入口位于
`experiments/reports/warm_start_thunder_20261029/`。
开始 GPU 前检查实际旧进程退出、当前预约、可用资源及全新事件 socket。
冻结对照源码后，predictive 的 CPU 优化可以继续；新增 GPU 验证顺序执行，
不能修改运行中的冻结源码或与该对照争用 GPU。
当前只有已生成配置，尚未宣称正式运行、完整结果或性能收益。

## 实际启动

上述最后一段保留准备时点的含义。现已从干净 `33a34b61c4b6` 冻结副本
启动正式 `thunderagent_acting_decay`，端口 18851。
完整 CPU 检查通过 883 项，无失败、跳过或丢失旧 726 项身份；原生树
和源码在检查前后不变。保留第一次 live 读取尚无完整回执的等待状态，
没有将空前缀作为开始服务的证明。

2026-10-29 19:08:17（Asia/Shanghai）的独立 `live_start.json` 核验
watcher1270225、server1270700、driver1272091、GPU scheduler1271085
的实际身份、所有权、nice0 和唯一 GPU 进程。前 172 个完成请求的输入、
输出、摘要及原生停止条件与完整源相同，已实际投递 15 个 workflow，
其中 10 个 offset 为零，其余时刻与冻结表一致。运行中的前缀不计算
完整 JCT 或全程吞吐。

注册入口：
`/home/longhao/experiment/ops/warm_start_thunder_20261029_r1.registration.json`。
运行目录：
`experiments/raw/warm_start_thunder_20261029_r1/thunderagent_acting_decay/`。
CPU、注册与 live 证据保存在本页同名报告目录。后续 live 观测使用新名字，
不覆盖原始启动证据、不增加主实验版本号。

predictive 开发使用主应用和独立 native 工作树；冻结对照不包含这些改动。
下一步对比仍需相同到达表和完整工作，旧突发实验只作历史参考。

`live_all_arrived.json` 后续核验 18 个实际到达、1014 个精确完成回执；
`live_after_cpu.json` 在并行 CPU 回归结束后再次核验同一冻结来源和唯一
GPU 所有权，完成回执增至 1776/3674。原始 live_start 不覆盖。
这是同一次实验的进度证据，不是三次 GPU 实验，也不构成完整结果。
并行 CPU 候选见 `../design/layer_handoff_free_extent_20261029_zh.md`，
GPU/模型验证和性能验收仍待进行。
