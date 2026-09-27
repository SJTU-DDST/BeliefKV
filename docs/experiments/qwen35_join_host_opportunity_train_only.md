# Qwen3.5 JOIN parent 的 Host-backed 机会：训练集只读审计

数据来自 `qwen35_cold_tool_overlapped_128root_train_20260927_v1`。
这不是独立测试，也不是预测动作上线资格。
以该批次的 `intent_workloads` 为输入，使用
`scripts/audit_join_parent_first_service.py --host-backed-upper-bound`
复现；报告位于同目录的 `join_host_backed_window_train_only.json`。

105 个可配对的自然完整 JOIN 中，parent LLM 提交时的观测如下：

| 观测 | JOIN 组数 |
| --- | ---: |
| FULL Host token 命中 | 0 |
| Mamba Host slot 命中 | 3 |
| 任意 Host 命中 | 3 |
| FULL Device 前缀命中 | 101 |
| 含未缓存 prompt token | 105 |

3 个 Host 命中组从终态通知至 parent 首次 GPU 服务均超过 2 秒，
中位约 28.4 秒。这包括排队、准入和恢复等待，不能当作可节省的 H2D
时延。454,618 个未缓存 prompt token 包含新增输入，不能直接算作
驱逐后重算。Device 前缀命中也不能证明 parent 拥有的全部 FULL
页都驻留在 HBM；FULL token 与 Mamba slot 是不同物理单位。

本次可选审计使用首次 GPU **服务区间起点**，而非样本结束时间；
既有密封实验审计的默认输出保持原口径，不修改已冻结的测试报告。
提交时未命中 Host 不能证明更早的 JOIN 通知时刻没有 Host 副本；
同样，提交时 Host 命中不能证明通知时它已经存在。

**下一门禁**：在实际 safe point 上，将 JOIN/READY parent 和候选
执行 beneficiary 绑定 context/session epoch；获取 ancestor-closed
FULL/Mamba 的 Host/Device 驻留、共享 ownership、锁定/可回收容量、
allocator 余量和竞争传输。按同一身份记录真实 D2H/H2D 的 enqueue、
ACK、准入以及首次 KV 消费，再与相同到达流的 reactive 对照，
计入 HBM 字节时间、victim miss 和其他 workflow 的 JCT。
当前结果只是机会的**上界筛选**，不证明 H2D 物理可执行或有收益。
中高压下还需检查 next-agent handoff 与 PREPARE/Host 保留机会，
不能只以 JOIN parent H2D 为收益来源。
