# 有界并行委派与工作量差异小样本

本次落实用户对旧负载的三项修正：撤销拟议的 child 2/4 提醒，
保留共同 4/8 软预算；扩大实际交付范围的差异；重新生成 seed22 到达表。
新采集须等当前对比及其 CPU 收尾实际退出。

## 当前对比的完整结果

`burst_oracle_review_r1/review.json` 已确认两臂完成相同的 18 个 workflow、
4,246 个请求、607,248 个输出及原生停止，fresh idle drain 与所属进程退出
均通过；CPU reviewer 也已实际退出。三张完整 decode PNG 已检查。

| 指标 | 保留 Full | 模型与工具 oracle |
| --- | ---: | ---: |
| 平均 JCT | 1444.0200s | 1445.4067s |
| makespan | 2149.9878s | 2143.9853s |
| 全程输出吞吐 | 282.4425 token/s | 283.2333 token/s |
| 时间加权 decode batch | 7.9830 | 8.0168 |

JCT 回退 0.096%，全程吞吐增加 0.280%，没有明显净收益；oracle 不推广。
1,067 次控制中仅 4 次有符合定义的同类别、跨 workflow、ready-paused
短长容量竞争，其中 3 次改变局部集合。此计数不是物理容量证明或因果收益。
保持原 Full 的排序、工具/普通预取、物理 handoff、mixed chunk 关闭。

## 旧轨迹为什么不适合 sibling 验证

`scope18_child_shape_r1.json` 保留完整源的独立离线审计：

| 项目 | 完整源结果 |
| --- | ---: |
| JOIN | 97 |
| 单 child / 双 child JOIN | 78 / 19 |
| 多 child JOIN 比例 | 19.59% |
| 嵌套 JOIN | 0 |
| child 模型调用中位数 / P90 / 最大值 | 29 / 44.5 / 61 |
| child 输出中位数 / P90 | 2976.5 / 8579 token |
| child 创建到返回中位数 / P90 | 107.71s / 286.00s |
| 多请求重叠 / 有正进展共同 decode 的 JOIN | 19 / 19 |

4/8 提醒实际发生，但不保证 child 就在 8 次内返回。问题还包括 assignment
范围过大、角色倾向全面调查，以及原子任务维护 todo。新协议通过这些入口
改善任务范围，保留自然工具调用、完整报告、必要嵌套 JOIN 和真实终态。
创建到返回时间含排队和工具，不能直接视为模型工作量。

`audit_parallel_workstreams.py` 将客户请求重叠与真实共同 decode 分开：
后者要求 native sample 中不同 sibling 的 decode `token_delta > 0`，
身份与完整原生请求一致，且处于显式 parent JOIN_WAIT 区间。
不以 prefill、零增量残留或两个相同 agent 的记录充当共同服务。

## 新的六项目采集声明

`parallel_pilot6_declaration.json` 固定使用旧十八项目声明的上下文 band 1、4，
各取 investigation、implementation、multi-stage 一个，合计六个不同项目。
选择不读取新运行时长或调度胜负。

| 范围 | 两个项目的实际交付物 |
| --- | --- |
| 窄诊断 | GatherProof 平台差异的单条代码路径诊断；PhySage 网络同步症状的单项证据诊断 |
| 单项改动 | PalettePulse 邮件 helper 提取及调用方适配；MediVerse 版本模型和创建服务第一里程碑 |
| 完整多阶段 | LedgerLink 计划支付全部原要求；InsightLedger shadow inference 全部原要求 |

这些任务明确记录为 derived task，保留原 instance、scenario、公开问题与范围
指纹。前四个交付物不宣称完成原始全任务；后两条保留完整原问题。
实际请求的交付物放在 problem statement 最前面，受限语义输入可保留它。
没有把“预计短/中/长”分类提示给调度器，也未暴露参考答案。
项目原源码、测试、镜像、配置、工作目录和上下文不改；工具真实时间保留。

新 `native_in_graph_parallel_workstreams` 协议保留 automatic 首次 tool choice
和至少一次真实双 child root JOIN 的原终态要求。提示模型在同一回复中提交
2–4 个独立小 checkpoint，父节点负责整合与后续分解；单个不可拆的问题仍
允许 singleton 并如实统计。不同 editor 要有不相交写入范围。
child 不再获得 todo middleware，root 保留 todo。报告提示简明证据和明确
PARTIAL/BLOCKED 交接，不新增模型调用或输出硬上限、不强制轮数和 DAG。

采集配置为 `workflow_parallel_pilot6_native_20261030.json`：
6 roots、24 running/decode、2048 chunk、mixed 关闭、Qwen3-Coder、seed21、
200GB Host、child4/8 与 root8/16；native 臂关闭 BeliefKV 准入。
这轮只诊断任务形状，六项目本身不证明正式十八项目的 HBM 压力或性能。

完整采集后再检查多 child 比例、模型调用分布、自然终态、工作量 P90/P10、
嵌套与并行服务。声明中的 60%、中位数12/P90为20、工作量5–10倍均只是
诊断目标，不是 runtime 截断、删 trace 的规则，也不保证调度收益。
不自动扩大十八项目、不排队 baseline 或新的到达实验。

## 到达表重新生成

用户要求重生成 seed22 分布后，固定新 seed23，仅生成一次，保留 r1。
没有根据 GPU 结果挑随机种子，也没有裁剪或压缩长间隔。

| 到达项 | 原 r1 / seed22 | 新 r2 / seed23 |
| --- | ---: | ---: |
| 初始 workflow | 5 | 5 |
| 后续间隔数 | 13 | 13 |
| 分布参数平均间隔 | 132.72s | 132.72s |
| 实现的最大间隔 | 875.05s | 393.93s |
| 最后提交时间 | 2485.85s | 1579.22s |

路径为 `experiments/reports/workflow_mix_20261029/main_arrivals_r2/`，
任务排列 seed21 与间隔 seed23 独立。这仍然是旧 population 的标定，
尚未运行 GPU，也未证明持续 HBM 压力。新交付范围改变后，应从对应完整
control 重标定速率，并保留 seed23 的声明，不直接搬用旧132.72s参数。

## 实施验证

`parallel_pilot_cpu_r1/evidence.json` 实际通过 5,266 项测试及 12 个子测试，
保留此前全部 5,243 个测试身份，无失败、错误或跳过；2,072 个绑定输入和
原生 engine 在测试期间不变。真实嵌套 harness 检查新协议的 root/child
工具区别、自动首选、独立 session、JOIN 归属和自然返回。
六项目的 15 项原始源码、上下文和工具环境外部输入也已重新核对。
这不是新源的 GPU 或任务形状验收；启动前仍需冻结及 fresh sampler 检查。

首次 `workflow_parallel_pilot6_source_20261030_r1` 已通过 fresh sampler 1/8/24
和 native BF16 检查，但 shell launcher 的独立 fanout 白名单漏加新协议，
在创建模型 server 目录前以退出码2拒绝启动。`startup_failure.json` 保留完整
状态和 launch log 指纹，确认所有所属进程退出；没有自然轨迹可用于比较。
已补齐白名单，并通过真实 shell 入口的无 GPU native/Full 两项回归检查。
后续使用显式 r2 身份重新冻结与资格，不复用或覆盖失败目录。
修复后的 `parallel_pilot_cpu_r2` 通过 5,268 项及 12 个子测试，保留 r1 全部
5,266 个测试身份，无失败、错误或跳过；2,072 个绑定输入和 native engine
在检查期间不变。
