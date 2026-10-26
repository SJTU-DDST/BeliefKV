# Decode Batch 与 ThunderAgent 等待超时核查

日期：2026-10-26。保留 v230 自然 native、v232 Full/ThunderAgent 和
刚完成的 v233 Full 全部正常长尾。全部执行 3674 请求、
145565966 输入 token、549671 输出 token，seed21、18 个不同项目、
running/decode24 和原有 Host/Device 容量不变。

## ThunderAgent 是否有老化

作者源码固定在 `ThunderAgent-org/ThunderAgent` 的
`7ddc8610270e56d3b109eed8796b3a4360fc67c9`。
本地 `ThunderAgent/scheduler/router.py` SHA256 为
`87f946f62a4e8c81fe7939aa6a2a16bcf1ba554bd5b6c110f166a943a7bde752`，
与已有作者忠实性证据一致。

作者常规恢复顺序是多轮 REASONING、新 program、ACTING；
各类内按上下文大小选择，不随等待时长持续提高优先级。
但 `_wait_for_resume(..., timeout=1800.0)` 在等待 30 分钟后，
会不顾逻辑容量强制恢复。这是饥饿兜底，不能表述成完全没有老化。

我们的 `ThunderAgentMigration.aging_s=1800` 使用相同时间阈值，
但仍要求安全的非 running owner 容量交接；它不是作者的无条件超量恢复。
论文 baseline 的 adapter 单独复现作者超时，不使用 Full 的安全切换。
v232 Full 和 v233 Full 的 `starvation_handover` 均未触发，
v232 ThunderAgent 的 `public_timeout_force_resumed` 也未触发。
因此关闭该规则不能消除这些完整实验中已经发生的迁移。
本轮保留此兜底，没有增加更短的周期轮转或等待时间提升策略。

源码：

- `/home/longhao/experiment/ops/paper_baselines_20261024/thunderagent/ThunderAgent/scheduler/router.py:807`
- 同文件 `:934` 的超时恢复。
- `beliefkv/policy/thunder_agent_migration.py`
- `beliefkv/runtime/paper_baseline.py`

## 完整工作量的批次统计

| 指标 | v230 native | v232 Full | v232 ThunderAgent | v233 Full |
| --- | ---: | ---: | ---: | ---: |
| 平均完整 workflow JCT，秒 | 1750.365 | 1316.524 | 1339.764 | 1324.729 |
| 全程输出吞吐，token/s | 261.680 | 261.508 | 262.225 | 272.709 |
| decode 平均真实 batch | 9.085 | 5.035 | 4.932 | 5.657 |
| 按 worker 区间长度加权的 batch | 10.452 | 6.013 | 6.057 | 7.331 |
| 最大观察到的 decode batch | 20 | 11 | 11 | 15 |
| decode worker 区间累计，秒 | 1542.558 | 1655.770 | 1652.569 | 1564.505 |
| decode 输出 / worker 区间，token/s | 353.955 | 329.754 | 330.393 | 348.990 |
| prefill worker 区间累计，秒 | 523.425 | 374.866 | 373.677 | 381.252 |
| decode 请求步平均上下文，token | 55733 | 55730 | 55726 | 55732 |
| batch1–2 的 decode worker 区间，秒 | 29.896 | 158.656 | 238.920 | 193.400 |
| 配置推断的额外 graph 行 / 真实行 | 18.20% | 20.53% | 17.95% | 18.18% |

实际 decode 输出均为 545997 token；其余输出由 prefill 首 token
产生。批次包含零输出的结束步骤，不能把 `batch_size` 直接当生成量。
worker 区间包含运行时与依赖等待，不是纯 CUDA kernel 时间。
各 phase 区间可能受流水线与交错影响，不能直接相加、相减来归因 JCT。

v233 相对 ThunderAgent 的完整 JCT 低 1.122%，全程吞吐高 3.998%；
相对 v232 Full 的 JCT 高 0.623%，全程吞吐高 4.283%。
仍未达到 JCT 和吞吐均至少改善 10% 且重复验证的门槛。
不启动其他 baseline，不把一个候选的单次改善称为可重复收益。

## 可以确认的限制

1. 24 的运行上限没有被 Full/ThunderAgent 的实际 decode 打满。
   仅提高该上限没有直接证据支持。准入资格、可用物理容量、
   工具/JOIN 阻塞与依赖宽度应分别核算。

2. v233 通过已备份阻塞 owner 的预算出借，观察到更宽的 batch、
   更少的 decode forward 和约 91.26 秒的 decode worker 区间减少。
   这是支持继续研究实际批次宽度的单次比较；并未证明可以任意加并发。
   v233 实际 H2D 为 445.022GB，高于 v232 Full 的 341.792GB，
   也高于 ThunderAgent 的 216.496GB，必须与 JCT 一起评价。

3. v232 Full 的 72685 个 decode 样本与已提交、尚未完成且在前一
   新鲜快照中没有 active 资格的其他请求同时存在；这些区间约
   1336.060 秒。v233 相应为 56724 个、1227.596 秒。
   这些是诊断共现，不能证明额外请求已有物理空间或可立即执行。
   快照最多保持 1500ms；native 和 ThunderAgent 没有同等 agent
   身份快照，其资格分类保留 unknown。

4. v233 仍有 336.909 秒 decode worker 区间没有其他已提交未完成的
   请求，平均 batch 约 2.002。不能为填满这些批次凭空制造工作。
   是否与全局 DAG 宽度、准入顺序或正常收尾有关，需要结合完整依赖。

5. 所有 decode 都观测到 graph replay。当前单设备、非 speculative
   配置使用 `1,2,4,8,12,16,24` 等档位；引擎按不小于真实 batch 的
   档位运行，因而 3→4、5–7→8、9–11→12。上表只计额外配置行，
   不是额外 kernel 时间或 18–21% 可获得的加速。三个参考 arm
   都有类似 padding，不能将通用图优化的全部收益归给 predictive。

## 下一步设计顺序

先保持 agent 连续性，在自然阻塞、Host ACK、RETURN 和完成等已有事件
处检查有效计算宽度。只有真实 pending 目标和安全容量同时存在时，
才从已备份、非 running、非 pending 的阻塞 owner 出借预算。
现有 v233 每秒至多准入一个、至多八个 donor 检查的约束仍保留；
下一版应先测事件至准入的空档，再决定是否增加事件驱动检查。
不要通过周期轮转 ready agent 填 batch。

其次，针对相同上下文和相同实际 batch，验证补充常见 graph 档位
`3,5,6,7,9,10,11` 的收益及图内存开销。若有益，将其作为共同引擎
配置用于相应比较双方，并固定相同 KV 池容量。旧结果保留，
不直接把旧 ThunderAgent 与单方新图配置称为策略对比。
先做有界微基准，不需要为此重采 native 工作轨迹。

同时检查长 prefill 期间已有 decode 的推进情况及批次恢复时机。
如要验证 prefill/decode 交错，需要先确认当前 HiCache、session、
原生锁和预取依赖兼容；尚未因此启用 mixed chunk。

实际执行宽度的选择应使用近期 batch/上下文/计算成本，
结合 JOIN 关键依赖和完整容量检查。不能为了平均 batch 更大，
让刚解锁的 parent 持续等待，也不能为同优先级预取驱逐 ready agent。
验收仍同时包含完整 JCT、全程吞吐、H2D/D2H、旧输入重算和控制开销。

## 验证与证据

新增离线工具 `scripts/audit_decode_batch_efficiency.py`。
完整输入封存、原生请求身份及 token 计数核验、逐 batch/资格分类、
graph 配置与老化计数保存在：

- `experiments/reports/v234_decode_batch_complete_20261026.json`
- `experiments/reports/v234_decode_batch_with_v233_complete_20261026.json`

新增审计及相关等待、传输、吞吐审计回归共 66 项通过，
报告为 `experiments/reports/v234_decode_batch_audit_cpu_retry_20261026.xml`。
没有修改 v233 application/engine 或论文 baseline。
v233 原始请求/token/停止条件核验、双新鲜 idle 快照与进程清理完成，
旧输入前缀丢失代理为零、遥测零丢弃/失败。
这些不替代尚待完成的 GPU 数值等价性验证或重复性能比较。

## v235 完整更新与下一候选

v235 的真实平均 decode batch 为 6.418，worker 加权 batch 为 8.369，
decode worker 区间累计为 1518.026 秒，均好于 ThunderAgent 的
4.932、6.057、1652.569 秒。decode 产出 / worker 区间为 359.676 token/s，
而完整运行输出吞吐为 280.631 token/s；二者分母不同，不应混用。
所有请求/输出均核对，普通长尾未删除。

但是平均 JCT 为 1344.922 秒，比 ThunderAgent 的 1339.764 秒慢
0.385%，全程吞吐只高 7.019%，尚未通过两个指标均改善至少 10%
且重复验证的要求。实际 H2D 也从 ThunderAgent 的 216.496GB 增至
647.027GB。不能只因 decode 更宽便继续扩大所有并发。

v235 还有 1192.749 秒 decode worker 区间与快照中的 inactive pending
请求共存；这仍不是这些请求当时已有物理空间的证明。
没有其他已提交请求的 decode 区间为 324.115 秒、平均 batch 2.618，
其中不能仅凭当前服务队列推断是否有尚未由依赖解锁的工作。
相同事件边界下，排队缩短与交错执行跨度增加同时出现，需完整评价。

因此新增独立的 workflow 首次入口候选，保留已展开 workflow 的
agent 连续性与 JOIN 召回；已有至少八个 active、pending、非阻塞
agent 时暂缓新 workflow 的首次资格，后续 child/JOIN 不受此门槛。
这既不增加请求上限，也不轮转驻留 ready agent。
完整 JCT 包含入口等待，候选可能降低有效宽度或拉长首次服务；
必须用不变的来源检验，不能预先认定有益。
设计见 `docs/design/workflow_entry_decode_candidate_20261026_zh.md`。

graph 档位仍相同，v235 的配置推断额外行约 16.16%。
若下一步尝试补齐小 batch 档位，必须先做独立有界微基准，
并在相应双方使用共同设置、相同 KV 容量；不能直接与旧 baseline
比较后将通用引擎收益全部算作 predictive 调度收益。
