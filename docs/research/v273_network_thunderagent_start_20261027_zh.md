# V273 ThunderAgent 启动记录

用户已明确释放 GPU 预约，并要求先运行新 trace 上的 ThunderAgent，
再修复 predictive 的容量记账与 ALL-JOIN 准入。正在运行的对照组冻结，
后续代码修改只进入新的候选版本。

## 实验身份

- 注册：`/home/longhao/experiment/ops/v273_locobench_network_thunderagent_20261027.registration.json`
- 运行目录：`experiments/raw/locobench_empirical_network_thunderagent_20261027_v273`
- 唯一 arm：`thunderagent`，端口 18651。
- 应用：`29396b2f53a0b897d4d704fd65fd17859c84e1a6`。
- 原生引擎 tree：`11b84f7c31986782be3fbe699bae2574ef092389`。
- 模型：Qwen3-Coder-30B-A3B-Instruct；running/decode24；种子 21。
- Device：805198 token slots；Host：200000077824 bytes。
- 每个串行 invocation 独立作为 program，Full agent admission 未启用。
- 原生 free-page H2D fence 与逐请求流式指标均开启。
- 本次采用公开实现的默认策略，尚未启用可选 acting-token decay。

## 共享工作量与计时范围

沿用完整 V230：18 个不同项目、3674 次请求、145565966 个输入 token、
549671 个输出 token、72 次 JOIN，其中 20 次双 child，没有嵌套。
原始请求、token、依赖、停止规则与工具结果保持一致。

只有 931 个 execute 的等待时长使用 V269 固定采样表：

`experiments/reports/v269_v230_empirical_network_timing_cpu_20261027.json`

SHA256：`fa75164726e37277772d181f3601f6eaf234d3e55d8503710bdc0fc966cca5be`。

这是公开网络工具计时分布下的合成敏感性负载，不是新的自然 shell
执行轨迹。采样由固定调用身份和 seed21 决定，不依赖调度顺序；
未来结束时间只供 replay 使用，不交给在线策略。后续 Full 必须使用
同一采样表和流式指标开关，不能用旧的自然工具时间结果当性能对照。

## 已验证与未验证

V273c 共享 CPU 资格为两次执行的显式合并：2304 个唯一用例全部通过，
另有四个 subtest。第一次两项 Docker 用例因遗漏镜像环境变量失败；
保留原报告，指定实际 tools-only 镜像后的精确重试通过。未覆盖原始
失败记录，也不将合并报告描述为一次完整通过。

2026-10-27 14:25:13 Asia/Shanghai 的独立记录
`experiments/reports/v274_network_thunderagent_live_gpu_20261027.json`
验证了真实 GPU 所有权、冻结源码、隔离的 ThunderAgent 策略与
275 条已封存的精确请求回执。随后再次核对 supervisor、server、
scheduler 与 driver 均存活。启动和早期回执不等于完整工作量、
最终正确性或性能验收。

## 后续修复边界

继续按 invocation 管理 agent，分别处理：

1. 已被实际换出 HBM 的阻塞 parent 仍占完整逻辑预算的问题；
   复用现有有 Host 证明的借出路径，核查真实共享 extent，证据不足
   时保守计费，不按角色或相似 prompt 直接折扣。
2. ALL-JOIN 的必要 child 在 agent 准入阶段被普通续接排后的问题；
   只使用当前已观察阻塞依赖，保留 parent 优先、容量限制、正常
   agent 连续性及原生物理安全条件，不给 child 永久保护。

V275 的旧预算重建仅约 109 秒与 V260 采样值一致；其余区间不能
复原 streaming-growth 更新。该子集的阻塞占比不可外推至整轮，
更不能用来证明物理共享、可回收独占页或预计 JCT 收益。
