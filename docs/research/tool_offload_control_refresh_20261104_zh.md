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
