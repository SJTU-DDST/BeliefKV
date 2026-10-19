# 常态轮询计数与空维护降本

日期：2026-10-20（北京时间）。基线 `a9b42a2`。
GPU 验证最早在北京时间 06:00 启动；本轮只用 CPU。

## 依据与修改

最新完整 V85d predictive 的
`opportunities/admission_opportunities.jsonl` SHA256 为
`05d80418cf724afb0a3327f39d85d00fcfdace47072c1103c14d7ed60009f1ab`。
其最终 runtime state 中的四类诊断计数如下：

| 计数 | 旧轮递增次数 |
| --- | ---: |
| physical_planning_after_forward | 1811270 |
| prediction_maintenance_deferred | 1790449 |
| tool_prefetch_scan_deferred | 189619 |
| prefetch_lease_maintenance_deferred | 6392 |

这四类计数只描述进入轮询或等待原定期限，没有参与动作选择、所有权、
容量、失效、ACK 或首次消费判定。现在删除这些逐轮 Counter 读写。
它们不是 GPU forward 或 batch completion 次数，不能将下表微秒数乘以
它们来推算在线收益。未来轨迹中计数缺失也不代表不存在等待。

实际维护扫描、工具候选扫描、迁移、失败、ACK 和服务消费继续记录。
详细 profiling 开启时仍覆盖 scheduler、post-forward、batch-completed
三个顶层区间；没有增加新的观测开关。

另外删除空任务的调用和分配：

- scheduler 在事件交付和已到期终态回收后，没有预测库存时不再调用空维护。
- 批次回调没有报告阶段时不调用空报告观察；没有租约时不调用空租约刷新。
- pending 服务分组延后到确有一个尚未观察首次服务的传输才创建。

所有库存检查都在本次调用的原位置读取，前序 handler 创建的新工作仍然
可见。没有跨调用缓存空状态，没有移动时钟、延长期限或减少原始 batch
的完成检查；ACK 前服务证据和完成 ID 返回继续保留。

## CPU 对照

V1987 冻结基线 event_channel、observer、physical、runtime，并绑定当前
源码 SHA256。采用真实空 Unix socket；24 synthetic workflow、8 running
是候选规模之一，其他规模为 12/8 和 156/48。

48 组常态循环包含 scheduler_step、dispatch_after_forward 和完整
on_batch_completed，覆盖：

- 0/16 条未到期物理命令；
- 0/8 条工具预测；
- 维护未到期/每次强制到期；
- 详细 profiling 关闭/开启。

PREPARE 已开启，但其原期限在未来，未计量实际备份。该轮 V85d 实际
sleep_on_idle=false，本次不包含 idle-sleeper fd 路径。

每组 151 个样本，20 个预热样本，每个样本 50 次调用；新旧次序交替。
构建、状态比较和历史轨迹读取在计时外。2090 项检查结束后，才执行
最终 V1987 计时，没有并行运行测试。V1985 是初次检查，不是最终结果。

单位为线程 CPU 微秒，以下均为 24-root/8-running、无在途命令、
关闭 profiling 的 P50：

| 场景 | 基线 | 候选 | 降幅 |
| --- | ---: | ---: | ---: |
| 无预测库存，正常期限 | 4.37252 | 3.35112 | 23.36% |
| 8 条工具预测，维护未到期 | 4.83138 | 3.78960 | 21.56% |
| 8 条工具预测，强制维护到期 | 7.59694 | 6.80786 | 10.39% |

48 组循环的 P50 全部更低，降幅 4.4%–23.4%。可见请求、原始 running
batch 引用、context token、语义版本、预测库存、物理预留/预算和实际
动作计数相同。只从一致性判定中排除明确删除的四种诊断计数和既有的
维护周期诊断；原始两侧诊断值仍留在 JSON 中。

额外 36 组物理登记校验验证输入接受/拒绝及完整 pending 状态一致。
这不是本轮修改的收益路径。两个空 socket 控制也没有代码变化：
关闭 profiling 的 P50 0.51228→0.50584us，开启时
0.71218→0.71306us。保留这组 0.00088us 的回退，均不归因为本轮优化。

这是局部常态 runtime 控制基准，没有完整原生 scheduler、模型、真实
预测 worker、活跃恢复首次消费、DMA、CUDA 或 writer I/O。它不能证明
预取覆盖增加、累计控制成本降低多少，或相对 native 的吞吐/JCT 提升。
这些仍由 06:00 后新实验验收。

## 正确性与启动安排

原 2090 项 CPU 检查全部通过，无 skip。既有检查现直接核对未到期时
不扫描、不发布动作和不移动期限，替代对已删除诊断计数的依赖；空库存
检查同时禁止调用相应空 helper。保留事件立即唤醒、前序 handler 跨期限、
ACK 处理、部分恢复、session/generation、原生锁及首次服务检查。

V88 引擎未修改，树仍为
`1db6d8846499beba8f963db9ba8abc7a832ecda8`，
完整补丁 SHA256 仍为
`2cbb0b4699e000ffc28bd80f6ee70732fb13b4950e40aa303b9afbc7d5a418ef`。

提交在 main 完成；随后向原等待进程发布新的 CPU 资格及不可变执行目录，
保留旧目录。最早 06:00 后核对 GPU 空闲，运行约定的 24-root、
16+8/900s、running8、256K、Host80:20 动态嵌套候选，先 predictive，
后 native。不接续中断的 V85d native，不恢复每日停机规则。

## 记录

- [V1987 最终对照](v1987_recurring_poll_control_cpu_20261020.json)。
- [V1986 最终 CPU 检查](v1986_recurring_poll_cpu_checks_20261020.xml)。
- [V1985 初次检查](v1985_recurring_poll_control_cpu_20261020.json)。
