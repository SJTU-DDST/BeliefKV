# 已空闲完整预算的 continuation 准入

EOS 修复 FULL 的完整平均 JCT 与全程输出吞吐只比 V345 改善
2.98785% 和 1.74522%，没有达到研究目标。新增候选针对实际已提交、
完整逻辑预算已能容纳的请求等待，不增加借出、召回、lease 或新的
普通预取权限，也不把这项工程补齐作为独立研究贡献。

## 完整轨迹中的机会

`scripts/audit_reference_capacity_waits.py` 对同一完整运行的 506 个
排队至少一秒的工具后继，使用原生请求的精确输入长度，加上参考核心
已声明的 100-token buffer，与同一资格快照的完整未折扣预算比较。
快照最多持有 1500ms，缺失部分保持未知。

| 采样分类 | 累计请求等待 / 秒 |
| --- | ---: |
| active | 241.582003 |
| inactive，完整逻辑预算已可容纳 | 813.650830 |
| inactive，完整逻辑预算不足 | 2510.258787 |
| 未知 | 8.989317 |

282 个后继至少出现一次完整逻辑预算可容纳的采样区间。请求等待存在
并行重叠，813.65 秒不能除以 workflow 数或从 JCT 中直接扣掉；
快照也不是精确状态转换或一次新准入必然获益的证明。

审计没有把 `prefetch_residency_headroom` 当作同时刻的物理准入证据。
该字段缓存上次同步规划的空间观察，没有独立的观察年龄；不能用它
跳过原生分配、增长预留、锁或 DMA 检查。另有 39 个快照带暂停标记，
38 个的 raw 预算已经不超限；此处只记录观察，不再单独为减少少量
标记安排 GPU 实验。

报告为
`experiments/reports/reference_tool_eos_guard_20261027/complete_capacity_waits.json`。
原始完整比较、正常长尾、负结果及已完成 V345 对照均保留。

## 候选实现

默认关闭的 `AGENT_FREE_CONTINUATION_ADMISSION=1` 仅适用于 Full 的
`thunderagent_reference` 组合。独立的
`ReferenceFreeContinuationAdmission` 子类复用原核心
`ThunderActingDecayWorkingSet`、周期 tick 和迁移路径：

- 实际请求注册、非空原生完成回调，以及相关 runtime 事件后尝试一次
  额外准入；空 decode 完成/事件回调直接返回，不逐 token 扫描。
- 只考虑已有 agent 的已提交 continuation，不主动授予首个请求资格，
  不根据未来工具结束时刻、后继 trace 或最终输出长度做选择。
- 校验当前 request、invocation、context/epoch、可见身份及实际 ready
  状态；仍等待工具、子代理或 JOIN 的对象不能借此执行。
- 先检查至多 24 个已知 JOIN parent，保留其正常优先级。超过这个窗口
  时本轮不增加普通准入，继续使用原调度路径。
- 再检查至多八个普通候选，包含本次实际提交的对象；每次事件最多
  增加一个资格。没有新增队列、计时器或 agent 驱逐规则。
- 复用已有完整预算授权 helper：授权前刷新实际流式输出，重新核对
  完整未折扣预算，只使用当前已经空出的容量。暂停标记、工具衰减或
  预计释放不能提前变成容量；不暂停任何 ready peer。
- 新资格不发起物理预取，不产生 1500ms 恢复预留，不改变工具预取
  权限。随后仍经过正常 agent 驻留策略与原生请求准入/需求恢复。

原始 ReferenceThunderAdmission 继续继承精确参考调度方法；两个
ThunderAgent arm 及其他 controls 的配置转换将新开关清零。
SPAWN/JOIN、EOS 修复、工具预测、prepare_host、原生容量及工作量均
保持原声明。候选配置为
`configs/migration/predictive_reference_free_continuation_20261027.json`。

## CPU 验证与验收

定向集成报告 `reference_free_continuation_20261027/targeted_integration_cpu.xml`
通过 419 项，无失败或跳过，包括新旧模式、真实 runtime 注册/完成
回调、完整预算重检、标记不提供容量、流式增长、身份/生命周期拒绝、
JOIN 优先、候选窗口、单次授权 journal、控制组隔离及原有原生
cache/allocator 资格测试。原生数据传输仍使用 CPU copy/event 替身。

开发时两轮夹具没有正确建立参考队列的前置状态：第一轮未清除周期
tick 门槛，后续还忽略了参考核心对第一轮 ACTING program 的排序；
另一个夹具把 active_tool_calls 误当作 set。对应失败报告和中间报告
保留，不将它们写成历史性能根因。

此时完整共享回归、源绑定及实际三种构造器检查仍待完成，没有登记
GPU 实验。通过后只运行新的完整 Full，复用 V345 的相同请求、token、
依赖、V311 工具时间、engine、容量、流式协议和种子。完整 JCT、全程
吞吐、资格等待、迁移/重算、真实授权消费及控制开销共同决定是否保留
此候选。将等待从 inactive 移至 active 不构成性能改善。

## 完整共享资格

同目录 `shared_cpu.xml` 在一次执行中通过 3291 项及四个子测试，
无失败或跳过，保留前一轮全部 3247 项身份并增加 44 项。新增部分还
覆盖普通 continuation 授权日志：仅允许一个当前请求，JOIN identity
可以为空，原生 context/epoch、session、时钟和实际服务证据保持原有
严格检查，不将它当作尚未提交的 parent 后继。

`shared_cpu.sources.json` 和 `shared_cpu.evidence.json` 绑定了测试、
候选配置及代码摘要。三种实际 runtime 构造器均通过，Full 新开关为
真，原始和工具衰减版 ThunderAgent 均为假。原始 ReferenceThunderAdmission
的参考方法继承关系保持不变，新子类只扩展声明的事件入口。

本轮配置与上一轮 sibling 配置的 common_environment 仅相差
`AGENT_FREE_CONTINUATION_ADMISSION` 及其 BELIEFKV 前缀形式，源轨迹、
工具 overlay、控制复用及执行顺序完全相同。native 11b84f7c3198
的 726 项源码绑定证据也已重新核对。此时仍未登记 GPU 实验，
共享 CPU 成功不构成 GPU 或完整性能证明。

## 实际运行

前一轮实际 owned 退出、最新预约、空闲 GPU、端口和源码检查通过后，
登记 `ops/reference_free_continuation_20261027_r1.registration.json`。
冻结应用为 `1f1678b266e15bc0427a0f98b683bc6b098ec026`，native 树仍为
11b84f7c3198，端口 18664，socket 为 `/tmp/bkv-causal-18664.sock`。
只运行本轮 Full，继续复用完整 V345，没有重新运行 native 或对照。

同目录 `live_start.py/.json` 独立核对实际 watcher318424/start731387723、
server318870/start731390336、GPU scheduler319195/start731391750、
driver320163/start731405400 均为 live/nice0；GPU PID 与 NVML 一致。
13 项工作量、工具时间、容量、引擎、流式及种子字段与完整 V345 相同，
五个相关 runtime/core 文件与 3291 项资格源摘要一致，实际新策略
开关、JOIN 扩展与物理控制均已核对。

启动证据封存 164 个精确完成请求。随后独立
`collecting_grants_after_start.json` 观察到一个新 continuation 授权，
完整预算 8967 tokens，与相同当前请求/context/session 的实际原生
服务匹配；此前尚未服务，授权到原生 run_batch 观察入口为 18.183ms。
这不是 CUDA kernel 开始时间、隐藏传输时间或最终 JCT 改善证明；
两个审计封存的前缀也不是同一原子快照。

CPU 绘图等待器320361/start731406698保持 nice19、隐藏 CUDA，显式
使用 `ops/cpu_plotting_20261027/bin/python`，仅在完整成功且实际 owned
退出后绘制共同 60 秒分桶图，失败则停止，不启动 GPU。状态为
`ops/reference_free_continuation_20261027_r1.plots.state.json`。没有后续
GPU 实验排队，冻结代码、配置和外部输入保持不变。

## 新增回调的 CPU 代价

同目录 `admission_cpu_cost.py/.json` 对照运行中冻结源码的摘要，使用
33 个 program、33 个 pending 记录和真实异步日志 writer，单独测量
新增 Python 路径。每档预热 50 次、采样 500 次，计时使用 thread CPU：

| 路径 | 中位数 / 微秒 | P99 / 微秒 | 最大值 / 微秒 |
| --- | ---: | ---: | ---: |
| 新普通 continuation 授权 | 91.443 | 118.244 | 926.538 |
| 完整预算不足 | 32.153 | 51.417 | 93.067 |
| 流式增长重检后拒绝 | 40.826 | 44.602 | 56.470 |
| 空 decode 完成回调 | 0.506 | 0.564 | 0.722 |
| 空 runtime 事件回调 | 0.376 | 0.423 | 0.522 |

空回调的候选扫描计数保持不变。普通窗口上限为八个；这份测量没有
覆盖 24 个已知 JOIN parent 的窗口，不包含异步 writer 线程、整个
控制器、真实 KV/DMA、模型执行或 JCT。夹具为了重复测量而重置资格
或缓存长度，不能当作在线 workload。首次脚本把 writer 的分类计数
字典误当成总数，校验失败；首次 550 条完整日志及该失败单独保留，
修正后的记录在 `cpu_journal_verified/`。

后续 `collecting_pipeline_with_live_grants.json` 封存 1189 个精确完成
请求；reference 的 per-agent 预算时间占比继续保持未知。较晚的
`collecting_grants_with_completed_service.json` 记录 54 次授权，其中
52 次来自新 continuation 路径、两次来自原 sibling 路径；其中 50 个
continuation 和两个 sibling 授权关联已绑定完成后的原生服务，另两个
continuation 仍缺完成或 session 证明。
独立封存的前缀不能当作跨文件原子快照，也不能与完整 V345 的平均
吞吐或 JCT 直接比较。

## 完整结果审计入口

同目录 `complete_review.py` 只在完整 FULL 和已完成 V345 的共同图表
生成、实际进程退出及两次新鲜空闲遥测确认后运行。它核对全部请求、
token、原始停止条件、完整 JCT、相同工具时间和容量，并绑定完整的
迁移、JCT 路径、采样恢复等待、工具后继、资格/容量等待、预取复用、
授权消费及 EOS 审计。新增普通授权和 sibling 授权分开计数。

旧输入缺失另用现有原生探针与最终状态交叉核对：只统计同会话已经
服务过的共同输入，不把新增输入或 Host 恢复算成重算。控制组的
3564 个有历史输入请求合计 140952652 个共同 token，缺失探针为零；
这不等价于测量所有 kernel 重算，也不是本轮 FULL 的结果。

完整性预检查用真实控制组文件确认了上述计数一致，并拒绝截短文件
及其他 arm 的路径。`complete_audit_helpers_cpu.xml` 一次通过 300 项
现有审计/helper 测试，无失败或跳过。此时本轮实际 FULL 仍在运行，
完整审计入口尚未执行，性能、重复比较和全部 baseline 目标均未完成。
