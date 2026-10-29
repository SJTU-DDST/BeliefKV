# Workflow 分档实验结果与 Mixed Chunk 验证

## 当前结论

保留原 2 秒控制周期 Full。剩余工作量分档及 500ms 补位组合未取得完整
JCT、全程输出吞吐同时改善，暂不推广，也不因此启动其他 baseline。
下一步将原 Full 与 ThunderAgent acting-decay 同时开启原生 mixed chunk；
这是共同的 serving 参数验证，不将其包装为 predictive 调度创新。

## 数据与开发验证

用户允许在开发阶段放宽项目、任务隔离，并在不足时增加 native 采集。
已完成两批独立 native 执行：8 条不同任务及 6 条匹配当前执行提示的任务。
现有划分为 34 条训练、22 条校准、18 条开发评估 workflow。
其中训练含历史 V174 的 24 条、八任务采集中的 4 条及匹配采集的 6 条；
校准含历史 V171 的 18 条及八任务采集的另外 4 条。

此划分允许同项目、同任务的不同完整执行，不能声称未见项目泛化。
同次执行的快照不拆散到训练与评估，原始事件文件哈希标识执行。
当前评估已经参与多轮开发决策，因此是开发评估，后续正式结论需要新的
独立执行。此时不再为满足严格项目隔离而额外消耗 GPU 收集数据。

## 完整结果

三个结果均保留相同的 18 个不同项目、3,674 请求、145,565,966 输入 token、
549,671 输出 token、原生停止条件及全部正常长尾。到达为 10 条初始加
8 条均值 120 秒的泊松到达，种子 21；chunk 2048，mixed 关闭，
running/decode 上限 24，Device 805,198 token、Host 200GB。

| 方法 | 平均 JCT / 秒 | Makespan / 秒 | 全程输出 token/s |
| --- | ---: | ---: | ---: |
| ThunderAgent acting-decay | 1504.072304 | 2305.899187 | 238.375989 |
| 原 2 秒 Full | 1439.457324 | 2253.756840 | 243.890996 |
| Workflow 分档加补位 Full | 1432.302431 | 2274.218978 | 241.696602 |

新组合相对原 Full 的 JCT 改善 0.4971%，吞吐下降 0.8997%。
这是不同配置各一次执行，不是重复试验或严格模块归因。
完整结果、fresh drain、实际 owned exit 和图表已核对；保留负结果。

结果入口：

- `experiments/reports/workflow_work_training_20261029/workflow_dispatch_r1/review_worker_r1/`
- `candidate_vs_original.json`：与原 Full 的完整比较。
- `complete_dispatch_service.json`：159 次 grant 对应 159 个不同请求尝试，
  全部找到 grant 之后的实际原生服务及完成；P50 150.15ms，P90 707.62ms，
  最大 17.04 秒。这些不是独占因果效果或可消除 JCT。
- `complete_periodic_selection.json`：1,132 次完整控制中仅 19 次局部集合变化。

## Prefill 与 Decode 间隔证据

新的只读审计识别同一逻辑请求两次实际正 decode 进展之间的 prefill，
并排除该请求已在中间 prefill/mixed 批次得到服务的情况。
只取 worker 区间并集，保留前后无 decode 边界的覆盖计数。

| 方法 | 有间隔的 prefill 段数 | 总 prefill worker 秒 | 位于上述间隔的 prefill worker 秒 |
| --- | ---: | ---: | ---: |
| ThunderAgent | 2688 | 434.144858 | 349.784501 |
| 原 Full | 2854 | 386.068754 | 305.164732 |
| 新组合 Full | 2869 | 384.886960 | 304.991335 |

最长 decode 边界间隔约 20 秒。两个首服务等待约 17 秒的请求，其窗口
分别包含约 15.4–15.6 秒 prefill worker 区间；不能据此称其一直驻留、
一直已准入、纯等待 KV 或存在同等可消除 JCT。D2H 的 submit→ACK 包络也
不是暴露拷贝时延。Mixed chunk 可能增加算力、容量及控制成本，需要实测。

## 新配对的边界

`configs/experiments/mixed_chunk_20261029/` 从原 2 秒 Full 和当前完整
ThunderAgent 配置派生，仅将共同 `ENABLE_MIXED_CHUNK` 从 0 改为 1。
原 Full 不开启 workflow 分档或 500ms 补位；ThunderAgent 仍采用作者的
5 秒完整控制及 1 秒 acting 衰减半衰期。工具预取、普通 frontier 预取、
逐层物理 handoff 保持关闭；JOIN/EOS、预测工具卸载、prepare_host、
sibling 偏好及 demand pipeline 保留原有各方法边界。

双方分别进行新源绑定的 24 请求 token/session/native-stop GPU 检查、
实际 1/8/24 sampler 测量，然后顺序执行完整 ThunderAgent、Full。
Mixed 小样本先等待两次新鲜 idle telemetry，再清理 owned 进程。
检查必须观察到 prefill 批次内 decode 成员产生正输出，并使所有模式的
输出序号和每条 exact/native-sampled 回执总数守恒；正式注册重算并封存
这些 journal，不能只靠开关或旧 mixed-off smoke 放行。

新的排队入口复用已验证的顺序控制及完整比较 reader，失败即停止，
不自动重跑或继续下一臂。每次 GPU 启动重查预约、实际资源和前驱退出；
不重采 native 性能基线，不改变工作量、工具时间或正常尾部。
本文件记录验证计划，尚无 mixed GPU 或性能通过结论。

## 当前 CPU 验证

`experiments/reports/mixed_chunk_20261029/cpu_r1/evidence.json` 记录一次实际
源绑定验证：5,064 个测试及 6 个子测试通过，无失败、跳过或错误；保留
上一轮全部 5,008 个测试身份，绑定 2,016 个输入文件，原生源码保持不变。
独立启动前检查同时确认原 Full、ThunderAgent 和新组合均完成相同工作，
且 owned 进程实际退出，两个原生树及 patch 字节可严格重绑定。
这些 CPU 和历史完整性检查不构成新 mixed GPU 或性能结论。

## R1 小样本失败与记账修复

冻结 `db974231` 的 R1 检查完成全部 24 个 exact/session/native-stop 请求，
实际观察到 286 个含 decode 成员的 prefill 批次及其中 422 个 decode 输出。
GPU 检查仍然失败：24 条已完成请求的在途重叠描述缺少输出起止序号，
其中 8 条位于 prefill、16 条位于 decode。原有 telemetry 在 `_completed`
处提前跳过，因此只留下默认 delta=0，没有写入终态输出序号。

独立只读 `failure_review_r1.json` 将这些描述绑定到先前的同请求原生完成，
已记录输出总量与实际回执相同。这个诊断不补写原日志、不改变原失败状态，
也不把旧小样本升级为通过。原队列、position watcher、server 及 GPU 进程均
实际退出，fresh drain 有效，正式 ThunderAgent 和 Full 均未启动。

修复将已完成判断移到输出观察之后，继续读取实际 `output_ids` 数量，
为零新增输出写入准确序号，仍只产生一次完成事件；异常新增输出也不会被
跳过或伪造成零。两个原代码回归测试先实际失败，修复后相关 265 项通过。
源绑定完整 CPU 及新 R2 GPU 检查仍待执行，失败 R1 保留。
独立重试使用显式 `--attempt 2` 及全新路径，不重启原有失败执行。

修复后 `cpu_r2/evidence.json` 已实际通过 5,066 个测试及 6 个子测试，
保留 R1 全部 5,064 个测试身份，无失败、错误、跳过；2,016 个源绑定输入及
原生源码在执行期间未变化。双方采用相同修复后的 telemetry；该次 CPU
通过仍不等于 R2 GPU、完整性能或总体目标通过。
