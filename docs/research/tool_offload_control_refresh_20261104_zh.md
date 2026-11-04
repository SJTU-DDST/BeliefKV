# 工具卸载控制路径的剩余优化

2026-11-04，Asia/Shanghai。此前列为待完成的 hint 刷新退避、donor
前置筛查和 settled Host 副本快速路径现已实现，并在独立冻结代码上完成
CPU 检查。GPU 实验仍按用户要求暂停，没有注册新的实验或重启旧实验。

实现提交：

- `b1365e7a perf: reduce advisory tool offload control work`
- `7f23a893 perf: reuse unchanged tool release plans within a check`

最终 CPU checkout：

```text
/home/longhao/experiment/.worktrees/beliefkv-offload-plan-reuse-20261104
```

保留 original73、Host400、20 初始加 mean70 到达及 fixed18 协议，
本次没有运行这些工作量。任务、token、依赖、工具时间、预测器 artifact、
工作量排序目标及 native 引擎均未修改。原 GPU 服务 `6f1c648b` 的冻结
目录和用户中断记录不变。

## 同一次等待的预测刷新

`_accept_tool_wait` 继续拒绝过期、身份不匹配或已返回工具的结果。
有效新 hint 仍立即替换旧预测；只有唤醒控制的方式改变。

- 新等待、请求身份或 invocation revision 变化、预测来源变化、旧 hint
  过期及新 hint 的剩余有效期不足 200 ms 时，立即清零检查 deadline。
- 同一等待的例行刷新保留已有 deadline，且不会把它延后。
  历史异常长的 deadline 会收紧到当前时间加 200 ms。
- 如果两条检查路径本来都已到期，无需再次设置相同的立即唤醒状态。
- 新请求、工具返回、JOIN 满足、完成、传输 ACK 和容量释放原有的
  `rearm` 路径保持不变，例行刷新合并不会覆盖这些立即唤醒。

因此，普通刷新带来的新卸载资格最多等待现有的 200 ms 重试点，
不扩大到 1、2 或 5 秒工作集周期。这里存在最多一个短重试周期的取舍，
不宣称与旧的每次刷新立即检查完全等价。新增检查验证：短预测更新为
可卸载预测后，在原 deadline 到达时实际释放 KV 并准入消费者，
反复刷新不会把它一直推迟。

新增运行计数：

```text
tool_offload_hint_rearms
tool_offload_hint_already_due
tool_offload_hint_refreshes_coalesced
```

## donor 前置筛查

当前 Full 的 ThunderAgent reference 工具替换路径，先保留原逻辑容量
筛查，再检查原有至多 8 个 donor 的旋转窗口。若没有符合当前工具
预测、恢复保护和实际阻塞条件的 donor，直接结束本轮，不读取完整
consumer 前缀。

第一次正向资格结果只在同一同步检查内复用一次，避免后续紧接着再次
执行相同预检查；不缓存失败到下一轮，也不改变 consumer 或 donor
顺序。consumer 前缀读取后仍检查真实阻塞状态与额度，实际备份和释放
继续验证当前工具状态、保护、请求、锁、Host 副本和 native 容量。
新增竞态检查覆盖了预检查之后、consumer 观察期间发生工具返回及
donor 重新变为 pending 的情况。

新增运行计数：

```text
tool_event_replacement_donor_prefilter_checks
tool_event_replacement_no_eligible_donor
```

## settled Host 副本快速路径

在同一 reference 替换入口，先从已有压力索引读取至多 16 个节点，
按 private leaf 到 ancestor 的顺序生成只读释放计划。只有计划覆盖
本次完整缺口时，才跳过 shadow 重建；实际物理释放与准入仍经过原
`_evict_backed` 和 `_grant` 的新鲜检查。计划本身没有物理容量授权。

索引缺失、节点身份过期或已知副本不足时，保持原 shadow 刷新。
同一次刷新若确实登记了新节点或更新了节点身份，重新规划。
若刷新只读取 native 状态，既没有更新压力索引，也没有新语义事件，
复用刚刚得到的同调用计划，避免回退路径扫描两遍相同的节点。
这里没有跨事件、跨请求或按时间保留的物理计划缓存。

`parent_pressure_index_updates` 记录 `_remember_parent_pressure_node` 的
登记操作，只用于区分同次 capture 是否更新了索引，不表示 KV 传输或
物理容量变化。索引变化时仍可能需要两次有界的只读扫描；游标只按原
路径推进一次，实际释放也保留原每次至多 16 节点的限制。

新增运行计数：

```text
parent_pressure_index_updates
tool_event_replacement_shadow_refresh_skips
tool_event_replacement_shadow_refresh_fallbacks
tool_event_replacement_release_plan_reuses
```

该快速路径针对 `ReferenceToolReplacement`，没有更改其他维护型卸载
路径、legacy hybrid 路径、JOIN/EOS 预取、Host 空间管理或传输实现。

## 局部 CPU 成本

最终 `refresh_and_shadow_benchmark_r3.json` 使用实际 native CPU radix /
allocator fixture，baseline replacement 类从冻结 `84c53fb0` 加载，
其他 runtime 与安全路径共用当前代码。这个 baseline 已含此前的
同次需求复用，不是原 GPU 服务 `6f1c648b`。每种情况交替 9 个 block，
每个 block 30 次；构造、清理、断言和调用计数在计时之外。
成功情形的实际释放、逻辑准入和 node cursor 一致。

| 单次检查线程 CPU 中位数 / 微秒 | baseline | 本次 | 变化 |
| --- | ---: | ---: | ---: |
| 单 donor 逻辑容量不可能放下 | 16.62 | 16.70 | 增加 0.52% |
| donor 预测不支持卸载 | 84.46 | 26.53 | 减少 68.59% |
| donor 受执行资格保护 | 90.19 | 31.22 | 减少 65.38% |
| 已有 Host 副本，但节点锁住 | 193.30 | 197.94 | 增加 2.40% |
| donor 尚无 Host 副本 | 375.96 | 380.98 | 增加 1.34% |
| 完整 settled 副本，实际替换成功 | 611.58 | 542.42 | 减少 11.31% |

前两种 donor 拒绝情形，10 次尝试的 consumer reentry 调用从 10 次
降为 0。成功替换情形的 shadow 刷新从 10 次降为 0。回退路径仍有
约 4.6--5.0 微秒附加成本，不能把上述改动称为每个分支都正收益。
早期 r1/r2 的回退开销更高，先后去掉重复 donor 资格检查及未变化计划
的第二次扫描；原微基准没有覆盖或删除。

另外用同一 candidate replacement 隔离比较 hint 唤醒方式。
以下是合成的控制节奏诊断，不代表原 workload 的工具分布或 hint
频率；每次包含 32 次同一等待的预测刷新，均没有物理动作：

| hint 间隔 | 原实际检查数 | 本次实际检查数 | 原整段 CPU / 微秒 | 本次整段 CPU / 微秒 |
| --- | ---: | ---: | ---: | ---: |
| 50 ms | 32 | 8 | 1049.77 | 452.32 |
| 150 ms | 32 | 16 | 1049.70 | 669.28 |
| 1000 ms | 32 | 32 | 1044.26 | 1059.20 |

稀疏刷新时不减少尝试数，局部整段 CPU 增加约 1.43%。
没有按真实运行中的分支占比加权，不能组合成整轮 CPU 降幅，更不能
乘以前轮 118--132 秒控制 CPU 或直接推导 JCT 改善。GPU 关键路径
暴露量及新增最多 200 ms 等待的实际影响仍待后续匹配实验检验。

## CPU 检查及证据边界

最终冻结 `7f23a893` 上 974 项检查全部通过，没有失败、错误或跳过，
保留此前 481 项检查的全部 testcase 身份。覆盖普通卸载、备份 ACK、
JOIN/EOS、消费者变化、工具提前返回、共享前缀、节点分裂、锁及 DMA
安全、Host 容量和即时补位。4 项真实 tokenizer EOS 检查使用本地
Qwen3-Coder tokenizer，未加载模型执行 GPU。

原生 radix 和 allocator 使用实际 CPU 实现；GPU、copy/event 为
stand-in。这不是新模型正确性、GPU 迁移或性能资格。

开发及冻结证据均保留在原实验的 CPU 证据目录：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  synchronous_control_audit_20261103_r1/
    refresh_and_shadow_cpu_r1.xml
    refresh_and_shadow_cpu_r2.xml
    refresh_and_shadow_cpu_r3.xml
    refresh_and_shadow_cpu_r4.xml
    refresh_and_shadow_frozen_cpu_r1.xml
    refresh_and_shadow_frozen_cpu_r2.xml
    refresh_and_shadow_frozen_cpu_r3.xml
    refresh_and_shadow_benchmark_r1.json
    refresh_and_shadow_benchmark_r2.json
    refresh_and_shadow_benchmark_r3.json
    refresh_and_shadow_cpu_qualification_r1.json
```

最终 qualification 将 974 项通过身份、原有 481 项保留身份、干净冻结
commit、最终微基准、实际 native CPU 来源、旧测试的第三方依赖和本地
tokenizer 文件哈希绑定，并再次确认 GPU `STOP` 标记存在。

早期 expanded r2 有 4 项未指定本地 tokenizer 的 skip；冻结 r1 有一项
硬编码 `third_party/sglang-v0.5.20` 路径缺失。没有删测试或修改断言：
显式提供本地 tokenizer，并将独立 checkout 的忽略依赖目录链接到
原有第三方源码。最终 r3 全部通过，原失败、skip 和较早微基准保留。

实际服务顺序的离线审计此前已完成，见
`selected_workflow_service_order_20261103_zh.md`。本次没有据此增加在线
prefill 排序或改动 active agent 连续性；已选集合中的首次服务排序
机会较少，仍不能把该审计当成完整 JCT 的收益上界。

## 后续联合验证

用户已重新授权 GPU 实验，并明确要求合并已经完成的优化，避免每项小
修改单独运行 GPU。本轮因此使用最新已提交实现，同时启用已经完成
CPU 部署验证的条件树进度预测器、无成本分母组合目标和控制路径优化。
前述 `7f23a893` 的 CPU 证据保留，不再将服务回退到旧预测器实现以隔离
单项收益。联合结果不能解释成仅修改检查间隔或仅改善预测精度的收益。

后台工具卸载与替换的重试间隔通过
`BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS=400` 配置；默认仍为 200 ms。
新请求、工具返回和备份 ACK 保留即时唤醒。例行预测刷新不延后已经
设置的 deadline；新卸载资格至多等一个配置的重试周期。
成本判断改用独立的 `control_cost_margin_ms=400`，保留旧默认
`2 * 200 ms` 的余量，不因降低检查频率把收益门槛加倍。
运行快照同时记录实际重试间隔和成本余量。

采用的条件树导出为
`r5_remaining_calls_cpu_r1/conditional_trees_r1/qualified_export/model.json`，
只将原冻结组合成本 profile 移植到该模型，保留其已经验证的主干、
起始头、条件树和支持范围。连续权重及 Qwen 语义头仍属于离线研究，
没有将它们表述为本轮已经部署的优化。

原 73 项目、20 初始加 mean70 到达、Host400、805198 Device tokens、
running/decode24、prefill2048/mixed off、工具时间和固定 18 测量成员
不变。复用已有完整 Full、两轮 ratio 和 Thunder acting-decay 对照，
不重跑基线；测量组自然结束后停止背景，统一核对完整 JCT、共同窗口
吞吐、实际服务顺序、重算及线程 CPU。新的联合尝试保留独立注册，
不恢复此前用户中断的部分运行。

联合实现已提交为 `d5d12f27`。同一联合版本通过 2318 项 CPU 检查，
无失败、错误或跳过，完整保留原 1346 项及刷新路径的 974 项检查身份。
实际 registrar 的构造器及 native 来源校验均通过。首份 CPU 聚合报告
因驱动先修改了测试进程中的 operator 全局选择而有两项编排检查失败；
改为独立解释器执行后全部通过，原报告保存在
`control400_serving_frozen_r1/bootstrap_scope_failure_r1/`，未启动额外 GPU。

本轮实际注册为
`workflow_original73_host400_control_refresh_20261104_r1`，端口 19002；
实际服务与 GPU 监督器使用同一提交。服务进程的环境已核对为 400 ms、
`.75` 卸载概率门槛和新的条件树模型。衍生模型仅继承原收益目标及成本
profile，SHA256 为
`fe07eabc1c2ee72230efdb2bff190276ffc372d45dfaeae41228d97a90bfc3d6`。
监督器及服务均为 nice0；结果观察器和等待回放的停止观察器 bootstrap
均为 nice19、CUDA hidden。启动核对时仍在初始化模型/Host 池，不能
把它写成已经完成回放或已经获得 JCT 收益。

启动与六项完成后 CPU 分析的证据位于
`r5_host400_control_optimized_r1/control_refresh_r3/`。该命名仍属于原实验
的后续尝试，没有为 CPU 检查、分析或每个小修改另开主实验编号。

## 实际回放与早期服务核对

随后已进入 `running_full`，独立确认监督器、服务、回放客户端均实际
存活且为 nice0；固定 18 成员结束观察器及结果观察器均实际存活且为
nice19。`running_verified_r1.json` 绑定封闭 JSONL 前缀、实际进程身份、
运行快照与 native 容量 census。GPU 唯一计算进程为本轮 scheduler。

运行快照确认重试间隔 400 ms、成本余量 400 ms，物理控制没有被禁用。
条件树模型 SHA 与配置一致，后台预测器无错误、已完成 421 次推断，
没有让调度线程同步等待推断。实际容量为 805198 Device tokens、
4069011 Host tokens / 400000057344 bytes，与声明和既有对照一致。
这些事实证明联合版本已执行，不代表性能收益。

同一冻结实现的 `early_service_r1.json` 对独立早期前缀重放 232 个
组合入口中的 25 次搜索，发现 5 次准入集合改变、5 个新增 agent
episode。5 个均具有实际 grant、压力处理后保留和精确请求服务证据，
其中 4 个在后续控制或已记录干预之前有有用完成；另一个不作此声明。
没有新增 free-dispatch episode。201 个元数据不足入口、6 个无竞争
入口及 2 次实际 soft-deadline 回退分别保留。

这是早期全运行前缀，不是固定共同窗口，也不是完整 workflow JCT。
不能将 5 次新服务解释为 5 条 workflow 完成、条件树独立收益或
oracle 上界。当前继续同一轮 GPU 运行，等待固定 18 成员正常结束，
由已经启动的观察器执行停止、排空、实际退出核对及六项完整 CPU 分析。

## 相同已结束时间窗口的工具检查成本

`closed_60_300s_tool_control_r1.json` 比较同一回放到达时钟下第 60--300
秒的封闭窗口。该区间位于既定吞吐窗口内，但只是其中的早期 240 秒，
不是全程统计。用边界两侧实际累计快照给出上下界，没有按时间插值，
也没有把当前部分运行累计值与旧运行的全程累计值相比。

| 运行 | 非空工具替换检查次数 | 检查线程 CPU / 秒 | 每次检查 CPU / 毫秒 |
| --- | ---: | ---: | ---: |
| 本轮联合版本 | 1290 | 3.331503 | 2.582561 |
| 非排序 Full | 1583--1595 | 10.337280--10.407325 | 6.481053--6.574431 |
| ratio 首轮 | 1571 | 7.463623 | 4.750874 |
| ratio 确认轮 | 1662 | 10.084359 | 6.067605 |

本轮该窗口检查次数比两轮 ratio 少约 18%--22%，线程 CPU 少约
55%--67%。400 ms 限制的是后台重试；工具、请求和 ACK 等真实事件仍
立即唤醒，所以不能期待总检查次数减半。运行中也已观察到例行预测
刷新被合并，而真实事件继续触发替换检查。

以上是联合修改后的实测观察。不同调度顺序会改变该窗口中的实际工作量、
候选及 KV 状态，平均每次检查也不是匹配同一分支的组件微基准；不能
把这些百分比全部归因于检查间隔、直接推算完整 JCT，或代替原定共同
窗口的完整控制成本统计。原日志未改写，比较绑定四份注册与各自独立
封闭前缀。本轮 GPU、测量停止和完整结果观察器继续运行。
