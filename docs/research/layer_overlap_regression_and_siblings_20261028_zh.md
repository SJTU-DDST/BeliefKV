# 逐层 Overlap 回退分析与 Child 共同准入

2026-10-28。逐层 D2H/H2D overlap 继续启用，工具预取及其优先级窗口关闭。
当前配置为 `configs/migration/predictive_no_tool_prefetch_20261028.json`。
保留 JOIN/EOS 预取、工具时长预测卸载、prepare_host 和 agent 连续资格。
本次修复没有启动或排队新的 GPU 实验。

## 完整性能

三组均完成相同的 3674 请求、145565966 输入 token、549671 输出 token、
18 个不同项目 workflow 和 72 次 JOIN；保留全部正常长尾及原生停止边界。
完整退出及两次新空闲快照已核对。

| 方案 | 平均 JCT / 秒 | 全程输出 token/s |
| --- | ---: | ---: |
| 上一轮在线预测刷新 FULL | 1639.208934 | 236.448501 |
| 本轮 75% 门槛、Host 管理、逐层交接联合 FULL | 1646.588432 | 235.272956 |
| V345 ThunderAgent acting decay | 1697.583645 | 233.371801 |

本轮比上一轮 JCT 增加 7.379498 秒，即 0.45019%，输出率下降 0.49717%。
相对 V345 仍改善 3.00399% / 0.81465%，未达到明显且可重复的优势要求。
固定输出量下，全程输出率由 makespan 决定，不是独立的 decode 内核加速证据。

本轮同时改变工具卸载门槛、Host 策略与 native 引擎，不能据此判定逐层
overlap 的独立收益为负。联合引擎为 `0d407f981c51`，上一轮及 V345
为 `11b84f7c3198`；保留该差异。

## 已定位并修复的错误

`NativeAdmissionRuntime.dispatch_idle_prepare()` 将
`HostDemandCapacity.optional_batch()` 的结果与原始 512MiB 上限直接比较。
前者按完整 KV 页向下取整，而本模型一个 token 的 FULL KV 为 98304 字节：

```text
512 MiB                         = 536870912 bytes
floor(512 MiB / 98304) * 98304   = 536838144 bytes
差额                            =     32768 bytes
```

即使 Host 空间充足，旧判断仍认为预算不足。本轮最后记录的 10114 次检查
全部被这个门槛拒绝，idle prepare 实际发起零批；这里不包括另一路已经
发起 69 批的 SPAWN 备份，不能说所有 prepare_host 都失效。
上一轮实际 idle 备份为 65 批、76 个节点、16951 token。

修复将两个比较量统一到完整 KV 页，同时保留 2GiB/实际需求预留，不允许
可选备份消耗正常卸载的预留空间，也没有修改 native DMA、锁和冷 owner 条件。
四个真实 native CPU tree/allocator 边界案例覆盖整数预算、1 字节余量、
32768 字节余量和确实不足一页；修复前后报告分别保存。
首份失败夹具只更新了单池 geometry、漏掉 group geometry，单独保留；
修正夹具后的失败报告有两项预期失败、两项通过。

该错误可使冷 KV 到实际需求时才备份，是明确的实现缺陷；其 JCT 影响尚未
隔离，不能把 7.38 秒全部归于它，也不能提前声称修复后必然恢复性能。

## Overlap 的实际覆盖和开销

完整原生 ACK 与消费者后续服务审计核对到 13 次交接，共恢复 1090 token、
107151360 字节，占本轮全部 H2D 的约 0.004%。消费者恢复长度为
40--284 token，中位数 67 token，约 6.59MB；没有覆盖典型的 GiB 级大恢复。

当前入口要求已有逻辑资格的实际选中消费者，以及没有 Host 备份、满足独占
和完整路径条件的工具阻塞 donor。已有完整备份的 donor 优先走原来的
直接 demote；路径向上遇到已备份前缀就停止。因此多次命中的只是小尾段。
这是一项覆盖限制，不能通过允许 ready donor、越过锁或虚构容量来扩大。

native `L2TransferEngine.submit_layer_handoff()` 每次建立 48 个 donor
和 48 个 consumer 层事件，逐层提交 48 次 D2H 和 48 次 H2D 后才返回
调度器。小传输也承担这一固定提交成本。13 次实际 native-submit 到
应用日志的时间总计 62.4265ms，中位数 4.8201ms；它还包含 native 发布
及应用检查，不能全部视为 CUDA event 创建成本。
对应 H2D stream 区间合计 119.2532ms，包含流依赖。
CPU run_batch 进入早于最终 ACK，不足以证明实际计算内核与每层拷贝重叠。

这些数据说明当前实现尚未充分发挥大恢复流水线的作用，但上述几十毫秒
不能解释整个平均 JCT 回退。当前没有完整历史 scheduler CPU profiling，
不能用估算补成已测控制开销。后续应先覆盖有真实容量需求的大恢复，
再验证提交成本与逐层依赖；事件复用或提交方式变更需另行验证生命周期。

## 回退发生在哪里

按 workflow 等权的完整关键路径分解：

| 区间 / 秒 | 上一轮 | 本轮 | 增量 |
| --- | ---: | ---: | ---: |
| 请求提交至首次 native launch | 341.459840 | 342.079429 | +0.619589 |
| 首次 launch 至 native 完成 | 871.701936 | 878.452362 | +6.750427 |
| 上项中请求所属 decode worker 区间并集 | 665.742370 | 672.110384 | +6.368014 |
| 上项中请求所属 prefill worker 区间并集 | 28.008507 | 28.446048 | +0.437542 |
| 上项中请求所属 worker 区间之外 | 177.951059 | 177.895930 | -0.055129 |

工具结束事件路径仅增加约 0.042 秒，提交事件路径反而减少约 0.066 秒。
回退主要出现在首次服务后的 worker 区间，不能简单解释为工具等待变长
或者所有请求重新排队。worker 区间含传输等待、调度交错和运行时开销，
不是纯计算内核时间。

实际 D2H 比上一轮增加 149525594112 字节，H2D 增加 27935047680 字节。
各方向 stream 区间也增加，但它们与计算相交，不能直接从 JCT 相减。
平均 decode batch 为 5.895 对 5.789，单个 worker 区间均值为
16.952ms 对 16.536ms；更宽 batch 并未转换成更高的 worker 输出率。
目前支持“迁移与执行形状变差”的观察，尚不能区分具体干扰、控制成本和
单次运行波动的贡献。

本轮工具预取实际有 106 个完成的 H2D 节点命令、8717991936 字节，其中
74 个命令、3161456640 字节有 version-2 首次服务复用证据，32 个为否。
上一轮为 130 个、5956239360 字节，全部首次复用检查为否。
因此不能沿用早期实验的“本轮工具预取复制为零”说法，也不能将实际复用
等同于净 JCT 收益。按照用户要求，后续关闭工具预取；工具卸载预测仍开启。

## 同组 Child 如何执行

`ReferenceThunderAdmission._coadmit_join()` 在请求注册、真实 JOIN
创建/等待、成员返回等事件上检查已观察的 2--24 成员 ALL-JOIN。
parent 必须实际阻塞在该 JOIN；只收集已经提交、身份仍有效、ready、
inactive 且未被标记暂停的成员。

授权前刷新当前 native 输出长度，每个成员使用完整上下文加原有 100-token
buffer；当前合格成员整体放得下才授予逻辑执行资格。已经 active 的 sibling
保持原有资格，不等待尚未提交或仍在执行工具的成员，不暂停其他 ready agent。
实际 prefill、H2D、native 容量和后续压力处理仍按原有路径决定。

这只是补齐同组成员的准入机会。`order_ready`、`selected`、`admitted`
没有同步组批实现，也不预测 child 剩余工作量，不能保证两个 child 同时
开始、持续并行或同时返回。

本轮 95 次 sibling 授权中，93 次新增一个成员，2 次新增两个；
97 个新增 child 请求均绑定到实际后续 native 服务。两次新增两个成员的
first-service entry spread 均为零。其余事件可能已有另一个 active、
工具执行中或已完成的 sibling，不能据此断言全轮只有两次 child 并行。

历史 free-JOIN FULL 到 sibling FULL 的平均 JCT 从 1653.668345 降至
1643.381962 秒，约改善 0.62%，输出率提高约 0.98%；但同期还修复了
streaming 预算记账，且仅有单轮结果，不能视为独立消融。
当前判断是低成本、条件性的辅助收益，尚无证据支持它是主要加速点。
局部双成员授权 CPU 中位数 68.328 微秒来自合成夹具，不代表全控制器成本。

## 验证与证据

- 定向检查 176 项通过。
- 一次共享 CPU 回归 3481 项及 4 个子测试通过，无失败/跳过，
  保留前次 3477 项身份并新增四个取整案例。
- 实际三个构造器检查通过：FULL 的 overlap 开启、工具预取关闭、
  预测卸载开启；ThunderAgent 控制不继承 FULL 的管理逻辑。
- 新修复没有新的 GPU 性能结果。之前逐层交接的组件/模型正确性证据保留。

主要报告位于 `experiments/reports/host_demand_layer_handoff_20261028/`：
`complete_core_review.json`、`joint_regression_audit.py/.json`、
`complete_jct_paths.json`、`complete_service_interleaving.json`、
`complete_grant_service.json`、`implementation_review.py/.json`。
后一算子保留各完整报告和工具命令 JSONL 的 SHA256，输出紧凑统计。

新配置和修复的 CPU 来源绑定为
`experiments/reports/tool_prefetch_disabled_20261028/idle_alignment_shared_cpu.*`；
修复前失败和定向成功报告保留在同目录。
本轮三张诊断图在
`experiments/figures/host_demand_layer_handoff_20261028_r1/`。
继续复用完整 V345；全局明确且可重复优势目标尚未完成。
