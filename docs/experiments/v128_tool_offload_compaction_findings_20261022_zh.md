# V128 工具卸载、压缩与 JOIN 恢复核查

日期：2026-10-22，北京时间。

V128 的 no_background_prepare 已完成，退出码 0，36/36 workflow
系统测量有效且无 guard 排除。完整平均 JCT 为 1903.854281s，外部
模型请求 8403 次，原生请求 8404 次；额外一次是客户端明确标记的内部
压缩请求。23:51:33 开始 no_tool_duration_model，随后仍为
no_child_reclaim、Full、native_matched。已取消的三项消融不恢复。

本次只读分析使用 main 的离线工具；冻结应用、引擎、任务、策略及种子
没有修改。本组尚不能单独证明任一模块的净收益。

## 预测工具卸载确实运行，但不是预测后立即全部驱逐

`NativeAdmissionRuntime._long_tool_wait` 对仍在 WAIT_TOOL 的真实上下文，
判断未来 1000ms 内完成概率是否不超过 10%；没有 release CDF 时使用
剩余时长 P10 至少 1000ms。关键 parent 存在真实容量需求时，其他安全的
工具等待 owner 另有不依赖时长预测的 donor 路径。

`dispatch_tool_prepare` 先确认 FULL 压力，选择缺少 Host 副本的增量，
并在可估计时检查传输往返成本与剩余等待。异步 D2H 的 ACK 只证明备份
完成，不等于已释放 HBM。实际回收由原生 allocator 短缺路径或关键 parent
容量回收触发，并继续检查锁、引用、Host 副本及在途 DMA。

完整 no_background_prepare 的机会记录与 PREPARE_HOST ACK 核查：

| 备份选择原因 | 选择轮次 | ACK 命令 | FULL 字节 |
| --- | ---: | ---: | ---: |
| predicted_long_tool_wait | 173 | 173 | 1626849280 |
| critical_parent_tool_wait | 13 | 13 | 28651520 |
| waiting_parent | 68 | 68 | 446095360 |

每条选择在本组恰对应一条命令；173 个不同 context/epoch 不等于 173 个
独立 agent，也不是预测正确率的分母。工具等待期间另记录 243 次节点
回收、103 个 context/epoch，累计释放 FULL 2663034880 字节及 Mamba
3090677760 字节。它们包含预测和紧急容量路径，不全归因于预测；
累计回收量也不是同时获得的空闲容量或不重复的物理页集合。

JOIN 等待期间记录 129 次节点回收，累计 FULL 3918929920 字节及 Mamba
2060451840 字节。因此关闭后台备份没有关闭全部备份/回收机制，也不能
解释为 parent 状态一直没有卸载。no_tool_duration_model 取消工具模型
及依赖模型的工具预取，保留紧急 donor；该消融不能把预测卸载和工具
预取的净收益单独分解。

源数据为 V128/no_background_prepare 下的
`opportunities/admission_opportunities.jsonl` 和
`server/physical_action_ack.jsonl`。原生回收安全条件另见冻结引擎
`UnifiedRadixCache._evict_backed_join_parent`。

## 上下文压缩的直接开销

V127/no_join_prefetch 完整组有 3 个内部压缩请求，集中于
`locobench__python_mobile_game_hard_060_multi_session_development_expert_01`。
客户端 summary 模型调用分别为 12.392318、7.128598、7.075086s，合计
26.596002s，约占该 workflow 完整 JCT 3933.202782s 的 0.676%。
这个 workflow 另有 guard 和语义未完成排除，不把它当自然完成样本。

最新 V128/no_background_prepare 只有 1 次内部压缩，发生于
`locobench__python_game_simulation_medium_033_cross_file_refactoring_expert_01`，
客户端模型调用 12.306399s，约占该 workflow JCT 3367.005358s 的 0.366%。
原生输入 137710 token、输出 1239 token，与客户端 request_id 对齐。

压缩调用会阻塞所属 agent 的后继请求，同时其他 workflow 仍可运行。
上述比例是观测到的直接模型调用墙钟占比，不是关闭压缩后的反事实 JCT
收益。后续前缀变化、重算、摘要质量以及与其他请求的资源竞争仍需分别
核查；不能把所有后续 prefill 都算成压缩引起的额外开销。

## 前缀观察与完整 JOIN 结果

此前 34 个正 FULL 计划、31 个提前复用、约 1.633GB，来自 V2856
独立封存的运行前缀，不是完整组结果。

V2860 对最新完整组重新封存和审计：

- Harness 有 75 个 JOIN，控制器审计关联到 74 个已完成等待；未关联项
  不算零需求。
- 50 个等待在完成前有正 FULL 计划；38 个完成前提交并确认下一请求
  复用，FULL 为 2784768000 字节；另 12 个已有启动计划却未在完成前提交。
- JOIN 命令总 FULL 传输 7460556800 字节，正身份见证确认复用
  7416238080 字节，包含完成后恢复。全部载荷 10358067200 字节还包含
  Mamba，不能都称为 FULL。
- 完成前正计划 FULL 的 P50 为 16906240 字节，但最大的两次计划约
  4.031GB 和 3.522GB。该组同时存在小私有增量和大恢复需求。
- 大计划出现 FULL 容量不足和 enqueue_causal_changed；后者说明提交前
  重新排空事件并检查时，旧计划的因果身份不再有效，不是传输已经成功。
  这些原因可能重叠，不能把全部遗漏都解释为 HBM 命中。

完整组 JOIN 后继尚有约 8.627GB 原生 FULL Host-hit 逻辑跨度；它不是
逐页物理 DMA。约 8.131GB 集中在两个大计划对应的后继，提示继续核查
大计划容量与从 EOS 到 confirmed/client request 的交接。不能据该逻辑量
直接给出可提前传输字节或 oracle 收益。

V2857 已确认，同任务集的 36 roots 来自 9 个项目，每项目 4 个 root
共享同一仓库文本。child 使用独立提示词，不代表同项目其他 root 不再
需要该物理前缀；parent 私有冷后缀与仍有活跃 owner 的共享祖先应分别
判断。共享解释部分恢复目标偏小，但不能替代逐个大 JOIN 的容量诊断。

详细证据：

- `experiments/reports/v2860_v128_no_background_complete_lifecycle_20261022.json.gz`
- `experiments/reports/v2860_v128_no_background_complete_lifecycle_20261022_summary.json`
- `experiments/reports/v2857_v127_initial_root_prefix_sharing_20261022.json`

V2860 保留各 JSONL 完整前缀字节数及 SHA-256，不改写冻结组自带报告。
完整 JCT、guard、普通长尾和逐任务工作量仍按现行比较口径保留；等待
同版 Full/native 与其余消融后再判断模块净收益。
