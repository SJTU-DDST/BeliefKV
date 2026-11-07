# 83 项目持续到达 GPU 对照启动

2026-11-06，America/Los_Angeles。用户明确要求“进行GPU实验”后，解除
2026-11-04 的 GPU 暂停。旧暂停文件按原字节归档，释放记录绑定其 SHA256。
当前只安排本轮持续压力诊断及匹配 Oracle，不启动旧的已取消重复实验。

## 工作与资源

| 项目 | 本轮声明 |
| --- | --- |
| 来源 | 83 个不同原项目，复用七份完整原始采集 |
| 到达 | 20 个初始 workflow，平均 60 秒泊松到达 |
| 测量 | 原固定 18 成员，保留完整 JCT，全部自然完成后停止背景 |
| 模型 | Qwen3-Coder-30B-A3B-Instruct，BF16，完整 attention |
| HBM | 805198 FULL token，79154282496 字节 |
| Host | 4069011 FULL token，400000057344 字节 |
| 原生执行 | 相同 2048-token chunk、精确 decode graph、running/decode24 |
| NUMA | 相同分配策略，实测页面比例只作诊断 |
| Full / Oracle | 相同 2 秒统一控制、400ms 工具检查，仅切换工作量排序 |
| 保留 | JOIN/EOS 预取、预测工具卸载、prepare_host、连续性和 sibling |
| 关闭 | 工具预取、逐层 handoff、组合成本分母 |

本轮是增加背景人口与持续供给的诊断，原 73 项目、20+70 秒的已完成结果
继续独立保留。CPU 来源和配置依据见
[来源绑定](background83_source_binding_20261106_zh.md)。

服务冻结代码为 `631911fb`，沿用 8188 项与 23 个子测试的完整 CPU 证据。
分析冻结为 `6db3aa39`，含最新结果读器修复。未因本次启动重跑完整 CPU
回归或独立 400GB CUDA 组件；原生引擎保持 `434c35dd6621`。

## 已通过的 GPU 检查

在端口 19016 上执行了 24 个源绑定短请求：

- 24 个请求通过原始 token、session 和原生停止边界检查。
- 原生 position sampling 与真实 GPU 进程检查通过。
- 采样成本覆盖 batch 1、8、24，保留原 sampler，并在其后执行固定 token 回放。
- 实际 Device/Host 容量与上表完全一致。

资格服务完成后，已注册非排序 Full；正式服务初始化与实际重放分别记录状态，
当前不能用注册或启动证明完整性能收益。

## 实际 Full 与接续进程

Full 已实际进入 `running_full`。独立 CPU 读器在
2026-11-06 21:15:57 America/Los_Angeles 完成检查：

- 745 个已完成请求、55527 个输出 token 与原始包和精确输出哈希一致。
- 实际 Device/Host 容量与表中预期值一致。
- GPU 唯一计算进程为注册 server 的 scheduler 272413，服务进程 nice0。
- 实际工作量排序与未来工作决策关闭，保留统一 2 秒控制和既有恢复机制。
- 固定 cohort 观察器和条件 Oracle 协调器都实际存活，前者 nice19。

证据为 `gpu_pair_20261106_r2/live_full_verified_r2.json`。
它只验证已封闭前缀和当时存活状态，不建立完整 JCT、平均吞吐或收益。

初次独立读器将 GPU worker 的 `/proc/.../environ` 缺失字段判为配置关闭，
在发布证明前失败。原读器及 `live_full_reader_failure_r1.json` 保留。
父 server 的实际初始环境包含正确开关；worker 的初始环境区域已无法提供
该字段，沿用已有原生执行观察器对这类可见性缺失的处理。新读器分别记录
父启动配置、源绑定继承与 worker 字段不可见，不将其写成直接观察到 hook
安装。没有修改或重启 GPU 实验，也未改写原始 token/遥测。

## 启动前的路径绑定失败

首次 entry 在 GPU 分配前被拒绝：原生 CPU 证据将 canonical patch 路径绑定到
旧冻结目录。新执行目录中的补丁字节、原生 tree、commit 和全部其他状态字段
逐项一致；只重新绑定路径，没有改动原生实现、放宽验证或重跑组件。

`gpu_pair_20261106_r1` 保留失败输出和原 operator；有效启动使用
`gpu_pair_20261106_r2`，含新的 `native_sources.json` 与路径比较证据。
两次 entry 的 operator 都按当时哈希保留。

## 执行入口

```text
ops/workflow_original83_host400_mean60_position_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_full_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_full_cohort_20261106_r1.registration.json
ops/workflow_original83_host400_mean60_matched_oracle_20261106_r1.plan.json
ops/run_background83_matched_oracle_20261106.py
```

原始 GPU 结果位于：

```text
experiments/raw/workflow_original83_host400_mean60_position_20261106_r1/
experiments/raw/workflow_original83_host400_mean60_full_20261106_r1/
```

Oracle 条件接续已在实际 Full 和固定 cohort 观察器存活后注册。它仅在测量成员
全部完成、原始回执、fresh drain、真实退出、负载供给及可改变集合的竞争
审计通过后执行。复用本轮短 GPU 检查，重新核对资源和新暂停；没有自动扩展
到参数扫描、旧重复实验或其他 baseline。

验收仍是完整测量组平均 JCT、固定共同窗口吞吐，以及选择到实际服务的传递。
单对结果不建立重复性或完成当前 goal。
