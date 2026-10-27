# Reference FULL 的 ALL-JOIN 共同准入

本改动补齐当前 reference-core FULL 的一个既有系统要求：在实际阻塞的
ALL-JOIN 中，允许已经提交模型请求的多个 child 使用现有空闲逻辑预算
共同获得 agent 执行资格。它不改变 ThunderAgent paper arm，不作为新的
调度算法贡献，也没有实现逐层容量交接。

配置为 `configs/migration/predictive_reference_join_siblings_20261027.json`。
它保留 free-JOIN 配置的全部工作量、V311 工具时间、容量、种子和引擎，
只额外开启 `AGENT_COADMIT_JOIN_CHILDREN` 及其 `BELIEFKV_` 别名。
parent 的 free-JOIN 授权仍使用原有显式开关；两个开关可以分别启用。

## 共同准入规则

只在请求注册、JOIN 创建/等待和成员终态事件处理时检查，不新增 decode
逐步扫描或定时器。一次检查最多处理一个 2--24 成员的已观察 ALL-JOIN；
超过此界限继续走原有 reference 路径。

- parent 必须确实处于该 JOIN 的 WAIT_JOIN，属于同一 workflow，仍是
  未释放的 waiter，没有独立工具或 foreground child 阻塞。
- 只考虑当前已提交、pending、inactive、未标记暂停的 ready child。
  request、visible key、workflow、context 和 epoch 必须仍一致。
- 组内当前符合条件的 inactive child 必须整体放得下才授权，不能先接纳
  部分成员后占据剩余成员所需空间。已经 active 的成员保持原状。
- 使用完整、未衰减的上下文加每个 program 原有 100-token buffer；
  不使用 ACTING 折扣容量、未来输出长度或实际物理共享的未经证明折扣。
- 没有提交请求的 sibling 不阻挡 ready 成员，不预留未知上下文，不等待
  全员到齐。ANY、推断 JOIN、后台 SPAWN、已经满足/释放的 JOIN 不授权。
- 不暂停其他 ready agent，不借出/召回预算，不增加恢复 TTL。
  获得资格后仍遵循原 reference 的增长、压力、完成和退休规则。

逻辑共同准入不保证同一个 GPU batch 开始，也不保证两个 child 同时结束。
真实 native 物理容量、Host checkpoint、锁和 DMA 仍决定实际恢复/准入。
CPU 原生组件检查专门验证逻辑允许两个 child、物理仅够恢复一个时，
第二个恢复仍被拒绝，Host 副本和 ACK 所有权保持正确。

## 授权前的增长记账修复

实现过程中发现 free-JOIN 和新 sibling 授权共用的额外接纳路径可能使用
上一个参考控制轮次的 program 大小，漏掉正在生成的 token：

1. 三成员夹具的总容量 1202 tokens；第一个 active child 最后记录 302，
   两个 inactive child 各需要 400。第一个又生成 18 tokens 后，真实
   剩余 782，旧额外授权仍把两个都接纳。
2. parent 恢复夹具中，一个小 owner 退休后缓存剩余 304，parent 需要
   302。另一个活跃请求又生成 18 tokens 后真实剩余 284，旧路径仍授权。

两个失败报告分别保存在 `streaming_budget_before_fix.xml` 和
`parent_streaming_budget_before_fix.xml`。冻结 3a23fe10 的 GPU 实验仍使用
旧路径，不能宣称本修复已经在该实验中运行。

当前额外授权在容量检查前调用继承的 `_refresh_streaming_progress()`，
从当前 visible native 请求的实际 input/output 长度更新参考预算。
没有每 token 扫描，没有更改 reference tick、完成或原有直接准入方法。

新增 `agent_join_free_capacity_admitted`、`agent_join_siblings_coadmitted`
日志仅在真实新授权成功时写入异步 opportunity writer，包含 JOIN、
完整当前请求身份、实际上下文、所需完整预算、授权前后剩余量及时间。
它们是逻辑授权证据，不能直接当作物理分配、复制或节省的 H2D。

## 验证

`experiments/reports/reference_join_siblings_20261027/` 保留原始失败、
定向和共享检查。修复及身份日志后的定向检查为 86 项全部通过。
一次共享执行通过 3174 项及 4 个子测试，无失败/跳过，保留前次
3143 项全部身份并新增 31 项。实际 FULL、默认 ThunderAgent、工具衰减
ThunderAgent 构造器均检查隔离；原生 tree/allocator 测试使用明确的
copy/event/Host-index CPU 替身，不能推及 GPU 数值或性能正确性。

`admission_cpu_cost.py/.json` 使用 33 个 program、32 个实际 pending
记录的合成长度夹具和真实异步日志，每条路径预热 50 次、采样 500 次：

| 路径 | thread CPU 中位数/微秒 | P99/微秒 |
| --- | ---: | ---: |
| parent 已有资格 | 2.710 | 3.009 |
| parent 新授权 | 78.703 | 124.958 |
| parent 完整预算不足 | 36.832 | 45.293 |
| 两个 sibling 成组授权 | 68.328 | 93.760 |
| sibling 完整预算不足 | 38.998 | 45.858 |

这是事件回调局部 CPU 成本，不含后台 writer 线程，不是完整控制开销、
GPU 干扰或 JCT 收益。旧 claim-only 8.393 微秒夹具没有这些 pending
记录及新刷新/日志路径，不能继续作为当前实现的开销结论。

CPU 验证时尚未登记该候选的 GPU 实验。完整 free-JOIN 实验只带来 2.5869% 的
平均 JCT 改善和 1.4620% 的全程输出吞吐改善，且持续服务间隔变差；
应保留负面结果。后续若验证本候选，继续使用完整 V345 工具衰减控制，
不得重采一条获胜轨迹、删去普通长尾或启动其他 baseline/消融。

## 实际运行

后续已核对完整 free-JOIN 和 V345 的真实进程退出、预约、空闲 GPU、
端口及 socket，登记 `ops/reference_join_siblings_20261027_r1.registration.json`，
只执行 FULL，继续复用完整 V345。冻结应用为
`525c7cc66e1f80a0c35f1ecf98f080939441e943`，native 树仍为 11b84f7c3198，
端口 18662，socket 为 `/tmp/bkv-causal-18662.sock`。

实际启动的 watcher255889/start ticks730446159、server256436/
730448880、GPU scheduler256898/730450297、driver257923/730463855
均独立确认 live/nice0，实际 GPU PID 与 NVML 一致。真实 runtime policy
为 `full_with_reference_thunderagent_join_and_sibling_admission`，共同准入
及授权前 streaming 刷新均生效，物理控制健康。

`live_start.py/.json` 核对与完整 V345 相同的 13 项工作量、工具时间、
容量、引擎、流式和种子字段。这个最早快照尚无完成回执，不能作为
完成请求正确性、授权消费、完整 JCT 或吞吐的证据。

随后独立的 `collecting_pipeline_after_start.json` 封存并验证 138 个完成
请求与完整 V345 的 token、hash 和原生停止一致。其 reference 预算只有
聚合信息，逐 agent 阻塞 token-time 仍为 unknown；没有用 program 数或
全程均值补齐。该前缀也不能用于与完整对照比较性能。

CPU 绘图等待器256660/start ticks730449328为 nice19，隐藏 CUDA，
只在完整成功且 owned 退出后生成同口径 60 秒曲线、时间加权分布及
geometry 图。它不启动 GPU 工作，状态为
`ops/reference_join_siblings_20261027_r1.plots.state.json`。本轮源、配置
和外部输入保持冻结，没有再排其他 GPU 实验。
