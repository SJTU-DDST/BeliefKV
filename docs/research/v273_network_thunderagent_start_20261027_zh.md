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

## 退出诊断与重试边界

V273 在 2026-10-27 14:52 Asia/Shanghai 因客户端 `httpx.ReadTimeout`
退出，保留 2430 / 3674 条精确完成回执。没有完整平均 JCT 或吞吐
结果可用于比较。监督器已完成所有自有进程清理；旧冻结代码、
原始退出状态和所有部分结果均保留。独立诊断：

`experiments/reports/v283_v273_timeout_diagnosis_20261027.json`。

3 个 root 的首个请求一直排队。最后一次调度容量快照记录最长等待
1799.648 秒，而驱动默认读超时为 1800 秒；作者 router 本身恰好在
1800 秒才强制恢复暂停 program，适配器每五秒检查一次。客户端在
下一次检查前取消了请求，当时其他请求仍正常执行。

修复只将后续所有 arm 的共同客户端等待期限显式设为 3600 秒，
不缩短作者的 1800 秒策略、不省略长排队、不删除 workflow，也不
修改工具时间。旧注册继续使用原 1800 秒默认值，避免改变历史执行
含义。需要独立注册并完整重试 ThunderAgent，再执行匹配的 Full；
不能从这 2430 条回执处继续生成拼接性能结果。
