# 当前 Host400 工作流服务瓶颈

2026-11-03。基于 original73、20 初始＋mean70、400 GB Host、
805198 Device FULL token、running/decode24、chunk2048、mixed off 的
最近完整对照。分析保留全部固定 18 条 workflow 的正常尾部、
2991 条请求和 362157 个输出；非测量任务按原协议右删失。
本次未修改运行中代码、模型、工作量或启动 GPU 实验。

## 最新关键路径分解

复用归档的 `audit_measured_prefill_opportunity_20261031.py::inspect`，
重新验证完整测量、原始请求身份、所有原生 forward、完成证书、
fresh idle drain 与实际 owned-process 退出。
按每个 workflow 实际最后完成的依赖链重建 JCT，再作 workflow 等权平均。

| 互斥关键路径分量 / s | 非排序 Full | 首轮 R5 64 | 确认 R5 64 |
| --- | ---: | ---: | ---: |
| 请求提交到首次 worker 启动 | 154.269 | 158.619 | 167.460 |
| 请求自身 worker 区间并集 | 477.993 | 473.949 | 477.000 |
| 首次启动后、自身 worker 之外的区间 | 179.713 | 162.270 | 176.055 |
| 工具完成边界对应的外部等待 | 225.267 | 227.160 | 228.936 |
| 其他事件、请求派发和回调路径 | 44.288 | 44.073 | 44.109 |
| 完整平均 JCT | 1081.531 | 1066.071 | 1093.560 |

非排序 Full 的对应占比为 14.26%、44.20%、16.62%、20.83%、4.09%。
首次启动到原生请求完成合计 657.706 秒，占 60.81%；
当前已经不能沿用旧负载“大部分 JCT 在首次排队”的结论。

首次 decode 后与 prefill worker 区间相交的暴露分别为
174.759、157.145、170.573 秒。这些是上表内部的重叠诊断，不能额外相加，
也不是可全部删除的等待。Worker 区间包含 CPU 提交、数据依赖和模型工作，
不能将 477.993 秒当作纯 GPU kernel 时间。
非排序 Full 的首次排队区间全部被记录 worker 区间覆盖；
这既不证明纯 GPU 一直忙，也不证明这些请求当时物理可执行。

确认轮的背景尾部覆盖失败继续保留：最后测量完成超过最后计划到达
87.177219 秒。首次 64 的改善没有在确认轮复现；本次描述性分解没有
把两轮差异因果归为某个调度函数。

## 服务资源与控制成本

非排序 Full 的固定共同窗口为 1583.262079 秒，包含背景请求。
已有完整 token 成本审计记录：

- Prefill worker 合计 593.916688 秒，占两类 worker 合计约 37.50%。
- Decode worker 合计 989.737911 秒，占约 62.50%。
- Decode worker 时间加权有效宽度 14.0576，声明宽度 14.1503，上限 24。
- 精确 decode graph 已启用，图行数与真实行数相同，没有旧稀疏桶填充。
- 同窗口已计时周期控制线程 CPU 为 2.663021 秒，选择部分为 1.303735 秒。
  这些不是全部控制路径，不能据此排除前缀扫描、事件、日志或 GPU 依赖开销；
  也不能直接扣除为 JCT 收益。

既有真实模型组件 profile 中，decode attention 占 kernel duration 合计
约 52.67%，一个 65,917-token 冷 prefill 中约 66.36%。
这是组件形态证据，不是本轮完整 GPU profile。
逻辑 KV 读取量曾估得 3.459 TB/s，但没有实测 DRAM counter；
不能据此宣称已经达到硬件带宽上界。

相较继续微调组合枚举，长上下文 attention、prefill 开始次序和
prefill/decode 的实际推进方式具有更直接的成本证据。
不过“prefill 占比高”不等于它是无用工作，单纯缩小 chunk 或频繁插入
decode 可能只是重新分配等待并损害必要输入处理。

## 共享前缀计费的额外筛查

源码中逻辑准入仍按各 agent 完整 context 加 buffer 求和，原生 radix
缓存允许共享前缀。因此检查了是否存在明显的重复逻辑收费机会。

对首轮 64 的 530 个完整候选窗口，从已验证的原始 token corpus 重建
候选当前输入，按原生 `extra_key/cache_salt` 命名空间求精确 token 前缀并集。
不以相同角色、相同 workflow 或文本相似推断可共享。

| 候选之间的共享筛查 | 数值 |
| --- | ---: |
| 前缀去重节省 token 的 P50 | 549.5 |
| 节省比例 P50 | 0.2084% |
| 节省比例 P90 | 0.4363% |
| 节省至少 4096 token 的窗口 | 21 / 530 |
| 只改候选逻辑计费后新增可规划组合的窗口 | 3 / 530 |

该筛查没有纳入候选与已有 active 的共享，没有证明即时物理页、锁和增长
可行性，也不证明未来所有请求的共享都少。但目前不足以把“共享去重准入”
作为主要优化候选；不能仅凭原理正确就宣称存在大容量收益。

## 建议的方向

### 实际计算准入与工作流选择保持一致

现有新策略主要决定 paused 谁恢复为 active。其效果还要经过原生等待队列、
长 chunk owner 和 prefill 优先路径，逻辑入选不等于立即获得模型推进。
冻结原生 `get_next_batch_to_run` 在存在可执行 prefill 时优先运行它，
`get_new_batch_prefill` 又先为已有 chunk owner 分配本轮输入预算。

一个具体候选是复用现有的工作量概率，把收益优先传递到新的实际
prefill 消费者选择；仅在真实存在竞争且不违反 JOIN、老化和物理准入时
改变次序。不新增预测器或每 token 全表扫描，不撤销已有 active 资格。
必须分别统计“逻辑激活”和“首次原生 prefill/decode 服务”是否改变。

若进一步确认同一个低优先 workflow 的 chunk owner 持续挡住更高优先
ready 请求，才考虑在 chunk 边界轮换多个未完成 prefill。部分前缀继续
保留，但这会新增请求行、KV 增长和 unfinished-cache 生命周期管理，
不能把它描述为只改一行队列排序。

### 按后续依赖推进安排 decode 服务

旧“每个 prefill 后只插一步 decode”或“只让短续接早拿首 token”的
局部检查没有建立足够的完整 JCT 机会，不能重新包装为已验证正方向。

可另行筛查一个更窄的候选：对已经观察到的最终报告阶段、能解锁真实
ALL-JOIN 的最后分支，或未来新增的因果可见工具调用输出阶段，检查
一个有界 decode 小段能否使模型请求真正结束，从而触发工具或 parent，
并使外部执行与其他 prefill 重叠。仍让原 running decode batch 共同推进，
不能读取录制轨迹的未来工具、RETURN 或输出长度作在线优先级。

该方向需要付出其他 prefill 被推迟的成本，还需要观察后继请求能否及时
进入实际服务；不能把 token 更平滑或首 token 更早当作完整 JCT 收益。
它应先作机会筛查，不直接启动 chunk/步数参数扫描。

### 降低长上下文 KV 的实际读取与存储成本

若允许独立改变执行配置，低精度 KV 是比扩大预取字节更直接的候选：
可能同时减少每个 decode 所读的 KV 字节并增加可容纳的工作集。
SGLang 官方提供量化 KV 配置，但本地 FULL/HiCache、FA3、模型与迁移
组合仍须单独验证，不能把官方单项支持等同于完整路径已经可用。

这是通用引擎方向，应同样提供给各 baseline。固定 token 强制回放不能
验证量化后自然生成的质量；需要另行验证数值和实际任务质量，也不能
承诺 KV 字节减少一半就获得两倍吞吐。

## 优先级与边界

先独立验证已提出的纯分子准入，避免混入新的计算编排。
若仍没有稳定改善，优先筛查实际 prefill 入口的工作量优先覆盖，
随后再看依赖推进型 decode 小段；数据表示优化作为独立执行方向。

当前没有证据支持继续投入逐层 handoff、扩大预取量、单纯缩短 tick、
增加共享计费复杂度或提高预测模型容量，就能取得明显、稳定的吞吐提升。
这不是系统最优性的证明，也不是所有可用优化的收益上界。

在保持 BF16、完整 attention、单卡、现有非抢占资格与相同工作量的
约束下，暂未找到可以诚实承诺两位数额外提升的简单机制。

## 证据

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  r5_host400_horizon64_r1/bottleneck_review_20261103_r1/
    nonranking_full_critical_path.json
    first64_critical_path.json
    confirmation64_critical_path.json
    candidate_prefix_sharing.json
```

前三份重新核对完成证书、全部测量请求和封闭原生日志；最后一份沿用
已封存的同一候选窗口，不将规划可行当作原生物理授权。
全部新增分析以 nice19、CUDA hidden、OMP/OpenBLAS/MKL 单线程执行。
没有为描述性分析新增与实现镜像的测试或重复 GPU 组件资格。

相关已有证据：
`host400_thunder_complete_and_learned_R5_20261102_zh.md`、
`workflow_cost_denominator_audit_20261103_zh.md`、
`useful_token_execution_cost_20261101_zh.md`、
`short_continuation_native_and_downstream_20261101_zh.md`。
