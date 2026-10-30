# 原始任务筛选与上下文混合

用户于 2026-10-30 明确修正准备方向：不能通过缩小交付物把原题改成短任务，
应直接从 LoCoBench-Agent 中选择较简单的完整原题。简单题可以来自短上下文
项目；混合负载仍须包含长上下文项目。上下文长度与实际执行工作量分别统计。

`focused_review_pilot6_20261030` 在 GPU 启动前撤回，其配置、准备记录与 CPU
证据保留，不能作为后续实验入口。此前已完成的派生任务采集同样保留历史身份，
不能改名为原始任务结果。新的原题候选需要重新采集匹配的自然轨迹。

筛选边界：

1. 保留原始 scenario ID、完整 `title`、`description`、`task_prompt`，
   以及固定 archive 与 scenario 的 SHA-256。使用既有 `scenario_prompt`
   渲染，不删减阶段、要求或验收条件，不额外注入源码答案或未来轨迹。
2. 扩展到短上下文项目，不把历史长上下文下限当作新候选的门槛。保留长上下文
   原题，使工作量与 KV 成本都有变化；不以增加某个 baseline 的重算或输赢筛选。
3. 每个 workflow 来自不同原始项目，包括后续 warmup 与背景流量。
4. 优先核对原生代码理解、架构理解与有界调查任务，再与完整实现、重构或多阶段
   任务混合。数据集难度只是筛选线索；自然采集后才能确认真实工作量档位。
5. 项目源码和测试不在准备阶段修复。工具路径、执行环境等 runtime 问题可以修复，
   root 与 child 使用一致的真实工具环境。
6. 保留 4/8 child、8/16 root 软预算、模型自主委派、真实并行 JOIN 与自然返回，
   不强制固定轮数或 DAG。原题不要求编辑时可以保持只读；实现任务允许正常编辑。

工具侧独立修复：`DockerWorkspaceBackend.glob` 原先没有把
`/workspace/...` 绝对通配符映射回实际挂载根目录，会错误返回空结果。七项 CPU
回归在修复前失败，修复后的完整 CPU 运行通过 5629 项测试和 12 项 subtests。
这不代表任何新的负载形态、迁移压力或性能收益已经验证。

## 第一组原题候选

完整 Python 原始目录含 800 条 scenario。新筛选不再要求项目满足旧的长上下文
下限；下面六个项目均保留原题完整文本，且源码、测试和完整上下文已经与归档
逐项核对。两个分析题来自原生代码理解和架构理解类别，并非重写调查题。

| 原项目 | 原题 | 完整上下文 token | 筛选假设 |
| --- | --- | ---: | --- |
| `python_ml_nlp_easy_053` | Configuration-Driven Model Selection Analysis in ChirpPulse | 30,631 | 简单代码理解 |
| `python_mobile_utility_medium_023` | Explain the Offline Synchronization Mechanism | 91,651 | 简单架构说明 |
| `python_web_blog_easy_040` | Implement a Site-Wide Maintenance Mode Feature | 35,046 | 完整三阶段实现 |
| `python_api_microservice_medium_044` | Implement User-Based Rate Limiting for API Endpoints | 50,948 | 完整功能实现 |
| `python_web_social_hard_001` | Implement Course Waitlist with Automated Notifications | 119,129 | 完整多阶段开发 |
| `python_system_automation_hard_062` | Implement a Dynamic Correlation Engine for Intelligent Alerting | 118,586 | 完整多阶段开发 |

该选择包含长上下文分析题与短上下文实现题，减少任务范围与 KV 成本完全绑定的
问题，但不宣称已经获得短、中、长实际工作量分布。所有原题阶段都保留，原题要求
输出文件时也允许写入；root 和 child 保持正常编辑能力。未启用撤回候选中的
child 强制只读或 400 行读取 profile。

准备入口为 `scripts/prepare_original_task_mix.py`，清单为
`configs/workloads/locobench_original_task_mix6_coder_20261030.json`。自然采集前还须
取得新鲜的 root/child 实际工具检查、匹配源码 CPU 证据和空闲资源检查。
先用六项目检查形态，不自动扩大人口或启动正式策略对比。

最初准备沿用了 36 项目池的 Qwen3.5 长度统计，虽原题和源码均完整，仍不适合
判断当前模型容量。该次准备与工具证据原样保留；`preparation_r2` 对六个完整
上下文全部使用当前固定 Qwen3-Coder tokenizer 重算，表中均为修正后的数值。
真实工具检查只验证 runtime、共享工作区和正常退出码，不要求项目测试通过，
不把 agent 应解决的测试或业务错误提前修复。

修正后六项目的 live root/child 工具检查全部通过；`bound_cpu_r1` 的完整执行
通过 5644 项测试和 12 项 subtests，保留此前 5629 个 literal test ID，
2121 个源码输入和 native 源在执行期间未变，无失败、错误或跳过。
这是当前准备版本的 CPU 证据，不是自然工作量、KV 压力或性能收益的证据。

## 自然采集启动证据

六原题采集已从固定应用 `b7ac1491` 启动，native tree 仍为
`11b84f7c31986782be3fbe699bae2574ef092389`，端口 18910。注册文件为
`/home/longhao/experiment/ops/workflow_original_task_mix6_source_20261030_r1.registration.json`，
原始记录位于 `experiments/raw/workflow_original_task_mix6_source_20261030_original6r1/`。

启动前 reservation 与资源检查通过，fresh sampler 的 1/8/24 batch 和 native
BF16 检查通过。独立 `gpu_entry_r1/live_start.json` 已观察到六个不同项目的
workflow_start 语义边界与 26 条完整输出，核实实际 2048 prefill chunk、
mixed 关闭、running 24、seed 21、native admission 关闭、4/8 child、
8/16 root 和真实并行工具协议，以及实际进程 nice0 与 GPU 独占。
该时点的启动证据不是完整轨迹验收。

CPU reviewer 从同一固定 checkout 以 nice19、`CUDA_VISIBLE_DEVICES=99999`
实际等待完整源、fresh drain 与 owned process 退出，记录在
`finish_r1.registration.json` 和 `finish_r1/state.json`。没有注册 Full、
ThunderAgent 或扩大人口的后继 GPU 任务。采集完成后须审查全部原题自然返回、
真实工作量异构、child 调用长度、多成员 JOIN 和实际共享 decode。

后续用户允许复用已有完整原题 trace，只补采缺失任务档位或不同项目。
改变并发或到达表不再要求整组自然重采。当前模型工作门槛为P80/P20至少3、
同时报告4倍档，P90/P10仅作参考；
长短分档另外纳入真实工具关键路径，不能用工具时间绕过模型倍率要求。
详见`docs/research/reused_original_workflows_20261030_zh.md`。

本次六原题采集与CPU reviewer已完整退出：970请求、145,508输出token，
六workflow自然返回，fresh drain及owned exit通过。模型工作P90/P10=3.666，
当时未达到P90/P10的5倍门槛；新P80/P20为3.250，达到3档、未达到4档。
child调用P50/P90=14/36、最大105，22个JOIN中14个单成员，
root并行轮次中位数1，仍不是合格的最终负载。保留全部结果，优先复用可用部分，
只补缺失档位和不同项目，不触发整组重采或自动GPU比较。
