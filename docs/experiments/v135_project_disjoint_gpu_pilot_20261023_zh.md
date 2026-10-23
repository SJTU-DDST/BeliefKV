# V135 独立项目 GPU 验证与缓存策略

时间均为北京时间。用户于 2026-10-23 明确授权立即使用空闲 GPU；
此前 2026-10-23 20:00 至 2026-10-24 06:00 的一次性限制已被本次
授权替代。23:20:30 核验旧 guard 的 PID/start_ticks 后停止，仅移除
与该预约完全匹配的 pause marker；保留旧预约与解除原因。
23:20:37，已经登记的 V135 watcher 通过资源检查并启动 native 源采集。

运行登记文件：
`/home/longhao/experiment/ops/v135_project_disjoint_source_20261024.registration.json`。
解除记录：
`/home/longhao/experiment/ops/v135_user_gpu_release_20261023.json`。
目录名保留原排队日期 20261024；实际启动时间为上述 2026-10-23。
本轮于同日 23:58:30 中止，相关进程和容器退出，GPU 回到
17MiB / 0%。截止分析生成于 2026-10-24 00:01:56。

## 本轮配置与验收

- 16 个 root 对应 16 个不同 LoCoBench-Agent 原始项目，同时到达。
- running/decode 上限 8；服务端、采样和 Python seed 均为 21。
- 上下文 262144 tokens，95% 时触发压缩；模型自然决定委派、嵌套、
  多轮和是否唤醒旧 handle，不强制复用或固定 DAG。
- FULL HBM 为 1797968 tokens / 36,822,405,120 bytes；
  Mamba HBM 为 513 slots / 33,096,007,680 bytes。
- Host FULL 为 160,000,020,480 bytes，Host Mamba 为 40,050,032,640 bytes。
- 冻结 app commit `16c953bd5584a9d0cd2c8402c86bbcd9c4ef55d1`；
  引擎 canonical tree `cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8`。
- CPU app 资格为 5355 tests 及 2 subtests，引擎为 657 cases。

本轮首先采集自然轨迹和精确 token/因果依赖，检查真实 started resume、
JOIN 数、嵌套/多轮、Host 迁移、有效状态损失及重算。
初始前缀并集 41.884GB FULL 只是容量代理，不能替代运行时迁移证据。
本轮 retention 为 off，不是 selective 的性能实验。对照必须在此后
使用同一源轨迹、容量、停止条件和工作量，保留正常 JCT 长尾。

## 截止结果

截止时 15/16 workflow 自然结束且 JCT eligible，累计 1848 次请求提交、
1847 次完成、20 次 spawn、16 次 JOIN satisfied。5 个 root 有多轮
委派，最多 4 轮；嵌套及真实同 handle 复用均为 0。
因此本次不能验收选择性保留，不能作为完整重放源；未接着启动保留矩阵。

已完成 H2D 为 132 批 / 255.833GB，其中 FULL 238.576GB；
D2H 为 3111 批 / 203.230GB，其中 FULL 70.975GB。
16 个 JOIN 后继中 7 个注册时有 Host FULL 命中，合计 20.778GB。
注册命中和实际 ACK 字节为不同计数，均不能换算成已测预取收益。

最后一个 `python_system_networking_hard_027` workflow 的逐工具核查
推翻了先前仅凭持续生成判断其正常推进的结论。它有 79 次连续
`edit_file` validation error，跨度约 1264.859 秒 / 21.08 分钟。
解码校验过的晚期真实输入
`beliefkv:01a1697a-01a5-7fd0-b530-e16ab2dc934d` 显示：
搜索与替换文本在换行标准化后相同，backend 拒绝无效编辑，文件未修改。
连续失败数来自事件序列；错误全文来自这一个已核对输入，不假称逐一
解码证明全部 79 次是完全相同的参数或错误内容。

本轮 `tool_circuit_breaker_enabled=false`、
`loop_guard.enforce_semantic_guard=false`，因此该类错误没有触发语义
中止；仍在生成也不代表存在有效任务进展。4 小时 wall-clock 与
2048 graph-step 硬兜底不足以避免本次 GPU 浪费。
停止是对不合格源采集的行政中断，最后一个 workflow 按删失保留；
不计算删去它之后的“完整平均 JCT”，不宣称任何吞吐或 JCT 提升。

最终封闭前缀报告：
`experiments/reports/v135_project_disjoint_pilot_cutoff_20261024.json`。
停止原因和身份核验：
`/home/longhao/experiment/ops/v135_pilot_stop_noop_loop_20261023.json`。
状态为 `capture_interrupted`，driver exit 143。没有通过完整 token/
因果源或 GPU replay 资格验证，普通工程性能目标继续 active。

23:33:35 的独立封闭前缀报告为
`experiments/reports/v135_pilot_live_20261023_233335.json`：
当时 4 个 JOIN 后继均有 Host 命中，注册时合计 12.074GB FULL 前缀；
已完成 H2D 的实际 FULL ACK 字节为 231.928GB。
前者是请求注册时的区间计数，后者是实际重复传输量，两者不能混用，
也都不等于预取收益或独立工作集。
9 个恢复依赖采样的总等待 P50 为 0.105ms、最大 15.768ms；
样本量小，不覆盖所有恢复、准入或 CPU 阻塞，不能作为 JCT 上界。

另解码 3 个 JOIN 后继的真实输入，校验 token hash 后确认均包含 task
的 `agent_id` 参数、复用说明及返回 handle，输入分别为 185749、
124134、125465 tokens。这三例没有遗漏向 parent 反馈 handle：
`beliefkv:01a16963-f99d-7fc1-b43c-0bb0da2ed7f4`、
`beliefkv:01a16964-d346-7ce0-b588-5ad0ae0cd0c3`、
`beliefkv:01a16967-50a3-7ea3-82ac-23e6748406ca`。
这只是这三个输入的可见性核查，不是所有调用正确性或模型选择原因的证明。

20 个已返回 subagent 最后一次完整输入加输出共 1006059 tokens，
对应 20.604GB FULL；各取一份必要 Mamba 状态再加 1.288GB，
合计逻辑体积代理约 21.892GB，小于默认 32GiB / 34.360GB Host
保留预算。该代理不是实际独占节点、同时驻留量或 Host 淘汰证据；
它提示本轮除缺少真实复用外，等预算选择压力也需单独验证。
不能仅凭项目去共享或大 H2D 量宣称选择性保留已有收益。

## Native 如何回收 subagent KV

Native 使用 `NATIVE_POLICY_BASELINE=1`、
`BELIEFKV_SUBAGENT_RETENTION=off` 和原生 session radix cache。
这里 off 表示不安装自定义休眠 subagent 保留控制器，不表示 RETURN
之后删除 KV。可复用 subagent 返回携带 `context_retired=False`，
因此会话仍可复用；显式 close、压缩换代或 workflow 结束才退休会话。
物理 KV 缺失时，逻辑 handle 和 transcript 仍可用于重算。

原生缓存把可回收节点放入统一竞争范围，不按 subagent 返回触发
drop-all。当前 FULL 在 session 模式下的键为
`(是否有会话引用, 引用数, last_access_time)`；Mamba 使用原生组件 LRU
及引用分区，真实请求锁和传输锁仍必须遵守。
因此准确描述是带会话引用规则的原生缓存淘汰，不是纯粹全局 LRU。
关闭会话只释放引用，不能等同于立即释放全部物理 KV。

后续 `subagent_lru` 是额外的 agent 级等预算保留基线，与 native
节点/组件级 LRU 不同。所有组使用相同复用 API、prompt 和私有历史，
不得通过让 native 在 RETURN 时强制丢弃状态来制造优势。

相关实现：
`beliefkv/runtime/reusable_subagents.py::return_attributes`、
`beliefkv/runtime/deepagents_adapter.py::_retire_native_radix_sessions`、
`beliefkv/runtime/subagent_retention.py::from_environment`，
以及冻结引擎的 `FullComponent._session_ref_eviction_strategy`。

## Selective 冷启动策略

当前没有足够数据时不声称能够可靠识别哪些 subagent 会被模型再次使用。
每次控制器初始化使用新在线估计器，冷启动以 budgeted MRU 回退：
在预算内保留最近返回的对象，超预算先放弃较旧对象的物理 KV；
逻辑会话和历史继续存在。

默认 Host 预算 32GiB，额外 HBM 预算 2GiB。只有已复用过且返回仍有
未完成事项的对象，可能取得不超过一秒的软 HBM 保留；关键就绪需求
可撤销保护。该规则不是对所有休眠 subagent 保留 HBM。

估计器至少取得 16 个有效标签，且正负标签各至少 2 个，才使用学习排序。
特征只在 RETURN/委派边界提取：角色、未完成事项、是否实现任务、
真实历史复用次数，以及新委派与原任务范围的关联。
排序考虑复用概率、预期节省的 FULL 重算、缺失备份及恢复代价，
并按独占大小归一化。当前吞吐/带宽参数是显式成本先验，并非已测
JCT 收益或准确率。

正例只在同 handle 的新 invocation 实际 LLM_SUBMIT 后确认。
显式 close 或正常 workflow 结束可形成负例；取消、guard 或未完成
episode 按删失处理。不会借用当前对照轨迹未来的复用结果作预测。
如果后续引入历史先验，应先独立采集并按项目划分训练/验证，
冻结后让所有相应对照使用一致训练条件，另报完全冷启动结果。

本轮原生采集可提供复用发生率及特征证据，不能直接证明 selective
比 LRU 有效；这一点需要后续同工作量 GPU 对照。
本轮零个复用正例，不能拿它训练一个“已有效”的复用先验。
