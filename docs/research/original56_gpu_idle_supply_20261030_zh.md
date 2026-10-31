# Full56 的 GPU 零值采样与供给核对

## 结论与边界

当前零利用率主要对应阶段性可执行供给不足：部分 workflow 已完成，
余下 agent 在等待工具或 JOIN，同时新的 workflow 还没有到达。
它不是当前证据中的主要准入停顿。固定窗口的 JCT 和吞吐统计仍有效，
但本轮未形成足够的恢复竞争，不适合作为短任务优先收益的主验证负载。

本次只运行 nice19、CUDA 隐藏的 CPU 审计，未修改正在运行的 Full、
到达表、在线调度或外部 GPU 采样器。当前仍遵循固定18项测量全部完成
后取消非测量工作、排空并验证退出的协议，没有启动后续 GPU 实验。

## 对齐后的数据

GPU 原始数据来自已经运行的 `nvidia-smi` 200ms 采样器，而不是新建监控：

`experiments/shadow/p6_control_plane_semantic_delta/20260831T155240Z/workloads/gpu_samples.csv`

采样文件时间按 Asia/Shanghai 解释，通过原生审计的 wall/monotonic
时钟差对齐到重放时钟。读取有界完整行后缀并保留字节位置与哈希；
状态、事件和 worker 日志分别封存完整前缀，不宣称原子快照。

固定共同窗口为530.928631--2368.047777秒，长度1837.119146秒，
采样覆盖率100%，准入状态新鲜度不超过1.5秒。

| 项目 | 结果 |
| --- | ---: |
| 时间加权 GPU 利用率 | 88.5487% |
| 零值采样折算时间 | 182.138秒，9.9143% |
| 零值时没有任何客户端模型请求 pending | 161.122秒，占零值折算时间88.46% |
| 零值时客户端 ready 且 native active | 19.415秒 |
| 零值时客户端 ready 且 native paused | 1.201秒 |
| 零值时客户端 ready，native 成员资格未观察到 | 0.400秒 |
| 时间加权在途 workflow 数 | 6.432 |
| 时间加权 application-ready agent 数 | 3.305 |
| 存在 ready-paused 的时间比例 | 3.3232% |
| 修正后独立供给审计的 ready-paused 快照 | 59/1794，3.2887% |
| 共同窗口采样的跨 workflow 可装入组合竞争 | 0 |

“可装入组合竞争”保留原审计的逻辑下界口径：多个不同 workflow 的
paused-ready 候选各自放得下，但合计不能全部放入。它不是所有在线
准入决策的完备审计，也不代表测量尾部不存在其他竞争。

最长连续零值采样段为2054.976--2077.592秒，跨度22.616秒。
其中点2066.384秒只有3个在途 workflow、3个工具阻塞 agent、
2个 JOIN 阻塞 parent，客户端没有待执行模型请求，也没有 ready-paused。
这几个 agent 可以属于相同 workflow，不能把它们相加当作 workflow 数。

该段落在1826.635--2106.207秒、279.572秒的无新到达区间中。
1910.929--2090.929秒的三个60秒分桶平均只有3个在途 workflow，
GPU 均值依次51.74%、45.61%、41.05%。现有工作不足以覆盖工具阻塞。

零值采样折算不是精确 CUDA 空闲时间。NVML 的内部采样窗口可能滞后于
对应时刻的应用事件，worker interval 也不是 kernel 执行时间。
剩余约21秒有待执行模型工作的零值观测，尚不能全部归因于 CPU 控制、
恢复或准入。不能把它们直接算作可消除的性能损失。

## KV 容量证据

截至3167.258秒的独立60秒定期采样中，下一次可复用旧前缀联合量峰值
为1,948,389 token，约占 HBM+Host 合并上限2,839,704 token 的68.6124%。
它是已观察定期样本峰值，不是连续时间峰值，也不代表每份 KV 都能在
任意时刻移动到任意层级。同期供给审计读取的5528次原生前缀探针中，
旧前缀丢失重算代理计数为0，尚不是本轮最终结果。

共同窗口逻辑 active 预算平均73.51%。有新鲜物理采样时，allocator
resident 平均约98.02%，物理采样覆盖94.94%的窗口。这个物理值包含可
驱逐缓存和在途分配，不能解释成98%的 HBM 都被有效可执行工作占用；
更不能据此认定工具阻塞区间是 HBM 容量导致 GPU 停工。

## 审计修正

发现 `beliefkv/experiments/sustained_supply.py` 用
`(invocation_id, join_id)` 清理 JOIN，但实际 `join_satisfied` 的
`invocation_id` 为 null，parent 身份只在 `join_wait` 中出现。
旧读法会遗留阻塞标记、低估返回后的 ready parent。

已改为用 `(workflow_id, join_id)` 保存及清理等待，并增加无 invocation
完成、超时及不同 workflow 同名 JOIN 的回归覆盖。该错误位于离线
审计，不在冻结 GPU 的在线策略中。旧的 ready/competition 数值需要
用修正读者重新核对，不能据此断言旧轮所有准入竞争都不足；旧轮 JCT、
GPU 采样和 KV 原始记录不受这处读者修正影响。

首次本次观察 `idle_supply_original56_r1` 保留并标记 SUPERSEDED。
`r2` 修正 JOIN；最终 `r3` 进一步明确不新鲜状态的统计覆盖率。
它们是同一 GPU 实验的 CPU 审计观察，不是三轮 GPU 实验。
冻结的测量结束观察器仍可能输出旧读者的供给文件，后续验收须使用
修正读者重新生成并绑定报告，不能直接沿用该旧供给结果。

相关测试命令：

```bash
python -m pytest -q tests/test_workflow_idle_supply.py tests/test_sustained_supply.py tests/test_measured_cohort_stop.py
```

实际在 nice19、CUDA隐藏、OMP/OpenBLAS各1线程环境通过43项。

## 后续到达调整

保留本轮作为校准，不删除零利用率区间，不按完成顺序重选测量组，
也不通过提前停止背景任务人为改善测量组 JCT。

下一轮优先尝试将泊松平均间隔从90秒缩到60秒，保留8项初始到达、
seed35、排列seed54、56个完整原任务及12/18/26分组。保持相同种子
使变化来自到达强度，不靠筛选一个有利种子消除空档。该参数是下一次
供给标定的候选，尚未运行或宣称稳态成立；若有用 KV 丢失或积压持续
增长，再适度回调，不能为了竞争机会无限加压。

供给验收关注 ready-paused 持续存在、反复出现真实跨 workflow 容量
竞争、选择能够影响实际服务，并同时检查有用前缀丢失和控制成本。
无需为了填空档继续补采任务或修改工具时长。低变异到达可以作为单独
敏感性分析，但不得截断指数间隔后继续宣称原始泊松到达。

完整18项 JCT 仍保留到自然完成；共同吞吐窗口仍由固定到达序号决定。
只有在新负载供给合格后，才值得开始匹配的全局 oracle 对比。

## 证据入口

- GPU/供给完整对齐：
  `experiments/reports/remaining_original_projects_20261030/idle_supply_original56_r3/report.json`
- 同目录 `samples.csv`、`gpu_and_supply.png`、`gpu_and_supply.pdf`。
- 独立供给重算：
  `experiments/reports/remaining_original_projects_20261030/idle_supply_original56_corrected_supply_r1.json`
- 有用 KV 前缀定期采样：
  `experiments/reports/remaining_original_projects_20261030/idle_supply_original56_live_demand_r1.json`
- 复用入口：`scripts/audit_workflow_idle_supply.py`。
