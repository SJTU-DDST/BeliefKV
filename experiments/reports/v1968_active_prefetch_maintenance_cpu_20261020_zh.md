# 非空恢复回执维护和首次服务降本

日期：2026-10-20（北京时间）。BeliefKV 基线为 `4d389de`，引擎继续使用
V88。GPU 实验仍按用户要求暂停，没有启动模型服务或新的工作负载。

## 实际轨迹依据

读取最新完整 V85d predictive 的原始 admission_opportunities.jsonl，
SHA256 `05d80418cf724afb0a3327f39d85d00fcfdace47072c1103c14d7ed60009f1ab`。
共 172 条租约注册，全部来自 JOIN；172 条均找到对应释放，没有未配对
释放或遗留租约。同一个 context 同时最多 42 条回执，全局最多也为 42。

因此非空回执维护并非只需考虑一条记录。本轮采用 1、8、42 条的控制
场景，并额外检查两个 context。两个 42 条 context 共 84 条回执的场景
超过本轮实际全局高水位，只是扩展压力控制。注册后的计数直方图不代表
持续时间分布、scheduler 调用频率或逐次维护人口，不能据此加权推算
实际累计 CPU，更不能推导 JCT。

## 修改

- summary 和池驻留检查使用直接循环，减少逐回执临时生成器；
  判断顺序、summary 依赖和逐池缺失处理保留。
- context 已进入下一 epoch 的交接状态时，省去另一份 JOIN 提交证明
  的重复查询。该分支原本已有独立的交接依据，native session generation
  仍必须验证；没有新的恢复许可。
- 本次扫描中，相邻回执共享同一个不可变 `PrefillCandidateKey` 对象时，
  复用已经找到的后继请求匹配。使用一个局部引用，不创建集合和哈希。
  不同身份对象照常匹配，不跨扫描保存结果；逐回执的有效性检查照常执行。
- 没有可见请求或当前 context 没有可见候选时，不进入空匹配生成器。
- 释放回执时，仅在 `service_window_expired` 且来源为 `tool_wait` 时
  读取工具 survival 历史。正常首次服务、JOIN 过期及其他释放不再查询
  之后不会使用的历史。

没有更改触发阈值、迁移计划、容量预算、保护期限、优先级、工作负载、
模型委派、原生 ACK 或 GPU 调度。新 epoch 不能替代 session generation；
同 context 不能替代请求身份；已找到 consumer 不能替代节点驻留证明。

## 迭代记录

V1965 的首版使用本次扫描内的 key 集合。大回执组已经改善，但两个
context 各一条回执时，后继匹配的 P50 在关闭/开启诊断下分别增加
1.145/1.423us，约 3.7%/4.4%。保留这个退化，不按大计划的好结果提交。

V1966 改为单个身份引用。关闭诊断的对应场景改善，开启诊断的一组仍
多 0.438us/1.3%。其余首次服务路径尚未修改，计时有正负波动。
随后发现每次释放都会查询工具 survival 历史，即使是 JOIN 首次服务。
V1967 只在真正需要该信息的释放分支查询，最终以 V1967 为准。

V1965、V1966 的原始数据和各自 runtime patch 均保留。用独立临时 git
index，从 `4d389de` 应用对应 patch，重建 runtime SHA256，与两份
原始报告逐字节一致。没有让中间版报告只指向最终源码。

## CPU 对照

使用 `benchmark_active_prefetch_maintenance_cpu.py`。基线的完整
event_channel、observer、physical 和 runtime 均冻结并重新绑定依赖，
没有把旧方法放入新 runtime 的 globals。所有受测源码 SHA256 保留；
最终测量结束后再次确认源码未变。

96 组覆盖 1/2 个恢复 context、每 context 1/8/42 条回执，以及：

1. 等待期间的到期扫描。
2. 已 READY、尚未提交的扫描。
3. 下一 epoch 的 session 交接。
4. 后继请求已可见的扫描。
5. 首次 GPU 服务回调，消费回执并释放最深锁。
6. 维护期限未到的快速返回。
7. scheduler_step、post-forward、batch completion 的 runtime 控制循环。

每组包含诊断 writer 关闭/开启；控制循环另覆盖 profiling 开关。
每个 context 仅最深回执持有一份锁，其余保存逐节点消费记录；仅最深
节点携带必要 Mamba，未构造逐页迁移多份 Mamba 的策略。
每组 151 个样本、20 次预热，新旧测量顺序交替。延后场景每样本 100 次
调用，其余每样本一次；构造、状态重置、比较和 trace 读取在计时外。
固定时钟使用普通函数，不在计时内保留 mock 调用记录。

所有场景的完整租约字段、visible 身份、semantic revision、计数、逐条
日志、pending、保护期限、释放回执与锁计数相同。没有产生新物理命令。
后继可见、READY 和首次服务每次均在计时外重置，避免重复测已经完成的
一次性变化。

节点、session 和锁字段采用 CPU 替身，锁回执检查拒绝重复释放。
这不是实际 allocator 或 DMA；writer 只追加到列表，不含 JSON/文件 I/O。
runtime 循环不含完整原生 get_next_batch_to_run、模型或后台预测计算。

## 最终计时

以下为单 context、关闭诊断和 profiling 的线程 CPU P50，单位 us：

| 回执数 | 路径 | 基线 | 最终 | 降幅 |
| --- | --- | ---: | ---: | ---: |
| 8 | 等待扫描 | 34.473 | 25.638 | 25.6% |
| 8 | runtime 控制循环 | 47.626 | 38.792 | 18.5% |
| 42 | 等待扫描 | 119.981 | 83.609 | 30.3% |
| 42 | READY 扫描 | 130.113 | 90.696 | 30.3% |
| 42 | epoch 交接 | 149.138 | 88.720 | 40.5% |
| 42 | 后继可见扫描 | 334.691 | 271.175 | 19.0% |
| 42 | 首次服务回调 | 46.489 | 38.473 | 17.2% |
| 42 | runtime 控制循环 | 138.004 | 98.945 | 28.3% |

最终 96 组中 91 组 P50 更低。84 组非延后场景全部改善，其中 24 组
runtime 控制循环改善 2.5%–30.2%。12 组未到期场景基本不变，5 组
略慢约 0.001–0.009us，最大相对回退 1.6%，保留原始分布，不称为该
快速返回路径的加速。均值、P50、P90、P99 和壁钟均在原始 JSON 中。

计时外另行计数，单 context/42 回执：

| 路径与检查 | 旧版次数 | 最终次数 |
| --- | ---: | ---: |
| 等待扫描：原生节点检查 | 42 | 42 |
| epoch 交接：原生节点检查 | 42 | 42 |
| epoch 交接：额外 JOIN 证明查询 | 42 | 0 |
| 首次服务：工具 survival 查询 | 42 | 0 |
| 首次服务：被消费回执 | 42 | 42 |
| 首次服务：最深原生锁释放 | 1 | 1 |

计数包装均在性能计时之后的独立检查中运行，没有放大消除查询的收益。
节点检查保留，证明降低成本不依赖省掉逐节点验证。

## 回归及当前状态

最终主仓 2058 项 CPU 回归通过，无 skip；引擎 lifecycle、scheduler
hook 和 shadow dispatch 共 87 项通过，另有 41 个 subtest。
新增 8 项检查覆盖 FULL/Mamba 丢失、节点代际变化、同 context 中原请求
不能成为自己的后继、跨扫描重新匹配、扫描中途过期、epoch 交接跳过
冗余 JOIN 查询但仍拒绝错误 session generation。
原有 summary、工具等待超时生存证据、原生引用、ACK、部分恢复、
首次服务及 CPU radix/allocator 检查继续通过。

早期的 182/160/8 项检查和中间版 2058 项回归与最终集合有重叠，不相加。
最后一次代码修改后重新完成最终 2058 项回归；计时与测试分开运行。
全部显式禁用可见 GPU。

引擎和补丁未修改，仍为 V88：
`1db6d8846499beba8f963db9ba8abc7a832ecda8`，
完整 staging patch SHA256 为
`2cbb0b4699e000ffc28bd80f6ee70732fb13b4950e40aa303b9afbc7d5a418ef`。
24-root、16+8/900s、running8、256K、Host80:20、动态嵌套委派及前端
对照配置保持原值。V85d 状态仍为 paused_by_user，
automatic_resume_allowed=false，真实暂停标记保留，未重启 GPU 实验。

四份关键文档已同步更新。本轮解决的是非空维护和释放中的重复控制，
仍需继续测量实际 PREPARE、恢复规划、有效预取转化和准入成本。
没有新的在线预取量、GPU 利用率、吞吐或 JCT 结果，最终相对 native 的
性能目标尚未验收。

## 原始记录

- [V1967 最终对照](v1967_active_prefetch_maintenance_cpu_20261020.json)，
  SHA256 `5f5583cb7fc03a0784dc8d7ccf0d9545e6c77b98edeb4b8a6574bcc156ce8d16`。
- [V1966 对照](v1966_active_prefetch_maintenance_cpu_20261020.json)及
  [对应 runtime patch](v1966_active_prefetch_maintenance_candidate.patch)，
  JSON SHA256 `1e723f12ac9edd3de6210aeb3cdc0f9a770ffdc334f1d42a511fd14e9d4a9461`。
- [V1965 对照](v1965_active_prefetch_maintenance_cpu_20261020.json)及
  [对应 runtime patch](v1965_active_prefetch_maintenance_candidate.patch)，
  JSON SHA256 `9d2c1daa4b9a3751ebeb40bb56aaf4f8fb46d79682cd313cf71902f79a541f7a`。
