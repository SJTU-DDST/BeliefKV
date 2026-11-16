# Child 首次 KV 物化背压：实现与 CPU 验证

用户要求保持 subagent 原任务不变，在有效 KV 接近容量上限时，
通过系统准入延后新增 child 的状态。现已实现独立、默认关闭的候选；
尚未启动本候选的 GPU 实验，未声明 JCT 或重算改善。

## 设计目的与执行顺序

本设计的核心是让 agent runtime 感知 HBM 与 Host 的有效 KV 容量压力，
约束新 subagent 首次 KV 物化，避免盲目扩展已物化工作集，
把随后仍要使用的 JOIN-paused、tool-paused 等状态挤出有效缓存后再重算。
它应在正常高压运行中产生价值；完全无工作时的防死锁出口只是异常兜底，
不是策略设计的主要场景，也不能代替吞吐与重算验收。
此前进度说明过分强调防死锁，不能据此改变本设计的目标。

按用户最新指示，先保持原队列执行，验证已经排队的修复后 Oracle，
再进行本设计的匹配 GPU 实验。
当前原队列为
`workflow_original133_host400_mean60_ready_capacity_oracle_20261115_r1`，
使用冻结 `d6d9aa1e`、133 原项目、20 初始、400 GB Host、平均 60 秒到达、
固定 18 测量成员。监督器 PID 3546293、start_ticks 79393944
已重新核对实际存活，状态为 `waiting_resources_full`。
本轮不替换其注册、冻结服务源码、到达表或容量配置。
后述 200 GB／55 秒四组配置仅为 Oracle 之后的新设计候选准备，
尚未注册或启动，不插入当前 Oracle 验证之前。

## 实现入口

- 候选工作树：
  `/home/longhao/experiment/.worktrees/beliefkv-subagent-materialization-pressure-20261116`。
- 实现提交：`469be6eaefa20433b00fc54c4f09efa55e8da06a`，
  `perf: defer first child KV materialization under memory pressure`。
- 最终提交：`440a69171727f0e5f6566313c12176b67b4310c0`，
  `fix: clear stale pause marks before child materialization waits`。
- 基础：已通过 CPU 验证的严格剩余工作量优先候选 `b07de6a5`。
- 设计文档：
  [完整设计](../../../.worktrees/beliefkv-subagent-materialization-pressure-20261116/docs/design/subagent_materialization_pressure_20261116_zh.md)。
- 实现：候选中的 `beliefkv/runtime/subagent_materialization.py`。
- 配置：`BELIEFKV_SUBAGENT_MATERIALIZATION=pressure_v1`；默认 `off`。

SPAWN、JOIN、客户端请求登记和 workflow 原到达时间保持原样。
仅在新 child 首次 native prefill 前等待，延迟计入完整 JCT。
已经开始 prefill 的请求保留连续性，已有状态的续接和恢复不重新限流。
后到的真实 SPAWN/CALL 会补登记尚未开始的首请求。

容量使用真实空闲 Host、可回收 Host cache、真实空闲 HBM，
扣除已有需求预留以及初始 prefill 尚未分配的后缀。
有引用的 Host-only 状态、DMA/锁定状态、JOIN 服务保护和未归入树的 Host 占用
不提供可回收额度。共享树节点只计一次，实际回收和准入保留原生权限。
候选顺序复用 R5/Oracle 的剩余 workflow 工作量，不另加老化或请求成本密度。

限制覆盖登记、周期恢复、免费补位、sibling、工具替换候选及超时强制恢复。
没有已有可执行工作时提供单分支进展例外，允许沿实际嵌套 JOIN/CALL 推进，
避免 parent 等 child、child 等内存的循环。例外和容量缺口单独计数。

## CPU 证据

证据根位于候选工作树下：
`experiments/reports/subagent_materialization_20261116/`。

- `bound_cpu_r2/evidence.json`：实际通过 9,267 个唯一用例及 23 个 subtest，
  保留全部 9,219 个前驱身份，新增 48 个；失败、错误、跳过均为零。
- 最终完整执行 233.20 秒；21 条警告保留在 `bound_cpu_r2/pytest.log`。
- 2,783 份源码、365 项继承外部输入及本轮 5 项配置/模型/到达/测量绑定检查通过，
  前后源码、原生引擎和输入保持一致。两组输入绑定存在重叠，不能相加当唯一数量。
- `control` 和 `pressure` 两臂都通过生产环境 preflight、实际 runtime 构造和 cache attach。
  挂载构造使用容量替身，不声称执行了 GPU 或完整原生 allocator。
- 新用例另外执行真实原生 CPU 树上的拆分、备份 ACK、卸载和计量，
  并覆盖实际 native 候选过滤、后缀预留、重试、取消、窗口退避和嵌套 ALL-JOIN。
- 保留开发用例 `focused_cpu_r2.xml` 的两次失败：
  容量准备 fixture 缺少已有 FULL-only/NUMA 声明；补齐 fixture 后
  `focused_cpu_r3.xml` 的 47 个用例通过，随后新增 JOIN 用例纳入上述完整验证。
- 初版 `bound_cpu_r1` 也完整通过，但最终复核补充了迟到 SPAWN 遗留待暂停标记的断言。
  `pause_mark_before.xml` 使用原 `469be6ea` 登记方法实际复现失败；
  `440a6917` 清除未分配 KV 请求的旧标记，最终以 `bound_cpu_r2` 为准。

`census_cpu_r3.json` 的标量节点替身微基准中，空闲容量快速检查中位约 3.2--3.4 us，
1,024/8,192 节点完整普查分别约 0.396/3.078 ms。
该微基准绑定 `469be6ea`；原始 Git blob 哈希验证通过，
最终暂停标记修复没有改变容量观察类或普查函数的 AST，未为无关改动重复测量。
这不是整个控制器、真实 allocator、传输或 GPU JCT 的成本。
持续高压下完整普查采用现有 2 秒控制周期兜底，
普通标量检查为 400 ms，真实完成/ACK 提前唤醒；不在每个 decode tick 普查。
超出 8,192 节点或不可观测时显式回退原调度，不把未知占用记成零。

## 匹配实验准备

`host200_prepared_r3/{control,pressure}.json` 保留：

- 133 个原项目、20 初始 workflow、平均 60 秒到达、同一固定 18 条测量成员。
- 相同模型、HBM、运行/decode 上限、工具、输入输出、依赖和剩余工作量预测器。
- 两臂均使用 200 GB Host，沿用现有 NUMA 放置诊断，不增加平均分布要求。
- 两臂仅切换首次物化策略。不同 Host 容量的历史结果不作为匹配控制。

初始 20 个 root 的已知输入合计 1,609,748 token，按当前 FULL KV 单位约 158.245 GB。
200 GB Host 与现有 HBM 共 2,839,704 个槽位。
这只是初始输入的容量参照，不是同时驻留、无重算或吞吐预测。
依据为原始 `workload_assessment.json`，SHA256
`39df3e5dabc56ed5914f6938295c6fa155ab79aa0febdd8f04df6be3aba91fd4`，
及不变到达表 SHA256
`faed8d3737785a32641d0293ecb90eb199d5bcc3ee4480ba60c89b5e1f5f1695`。

GPU 验收需同时看完整 JCT、共同墙钟吞吐、有用前缀丢失、实际额外 prefill、
首次物化等待、JOIN 推进、进展例外及累计控制 CPU。
新 root 和已开始 agent 的后续增长仍可能施加压力，本候选不保证任意负载零重算。
只有减少重算的收益超过新增排队和控制成本，才值得启用。
本轮没有修改当前排队服务的冻结源码或 GPU 占用策略。

## 后续：联合选择 Host 容量与到达间隔

最新准备提交为候选工作树的 `ad044fa5`，
`perf: prepare evidence-based child KV pressure workloads`。
首次物化服务实现仍为前述 `440a6917`；本轮增加离线分析、到达配置和对照准备，
没有改服务运行时，也没有启动本候选的 GPU 实验。
原 `host200_prepared_r3` 的 60 秒间隔两臂保留作历史准备；
待原队列 Oracle 验证后，本设计的首轮候选采用 `host200_mean55_prepared_r2`。

采用现有完整 133 原项目、20 初始、固定 18 测量成员，
选择 **Host 200 GB、平均 55 秒持续泊松到达** 作为第一轮压力候选，
不为各个容量单独跑 GPU。模型、HBM、运行/decode 24、prefill 2048、
原任务、输入输出、依赖、已配置工具时长、workflow 顺序和随机抽样流保持一致。
测量成员仍按同一到达序号选取，保留完整 JCT，全部测量结束后停止背景。

容量依据来自已完成 Host 400 GB、60 秒间隔的封存轨迹。
每 60 秒采样的“仍被后继复用的唯一旧前缀”峰值为 3,259,387 token，
约 320.41 GB；包含待输入的需求峰值约 347.12 GB。固定旧服务轨迹时：

| 候选 Host | 可复用旧 KV 超出 HBM＋Host 的时间比例 | 峰值需求缺口 |
|---|---:|---:|
| 160 GB | 45.56% | 81.26 GB |
| 200 GB | 18.64% | 41.26 GB |
| 224 GB | 3.11% | 17.26 GB |
| 240 GB | 1.04% | 1.26 GB |
| 256 GB | 0% | 0 GB |

200 GB 接近公共窗口旧 KV 需求的 P80，低于约 223.28 GB 的 P95 Host 需求；
可形成重复压力，同时避免首轮直接压到 160 GB 的更大缺口。
这是逻辑前缀并集与假设容量的离线对照，不是实际 Host-only 占用，
也不是新压力策略下的重算、物理分配或 JCT 预测。

55 秒使到达强度提高 9.09%。固定已测完整 JCT 的覆盖筛查中，
末次背景到达约为 6,194.72 秒，最慢测量完成约为 5,812.82 秒，
剩余约 381.90 秒；50 秒会留下约 151.76 秒的未覆盖尾部。
当前有限 133 条背景流的静态覆盖下界约为 51.42 秒，
不能直接压到 40--45 秒并宣称仍具持续尾部压力。
若新运行变慢，55 秒也可能覆盖不足，应按实际结果报告。

完整源有 20,895 个请求、2,781,537 个输出 token。
55 秒对应后续任务的名义新增输出约 371.52 token/s，
而旧运行公共窗口输出为 342.21 token/s；约 1.086 的比值表明它可能是有限过载诊断，
不能直接称作稳态服务，也不能假设推迟物化会消除长期超载。

### 已有续接供给

新增分析按精确 source request/workflow/invocation/context/epoch 区分首次请求与续接，
不依靠重试可能改变的 `steps`。一次扫描核对完整 journal 的
912,192,675 字节及既有封存 SHA-256；保留 compact 结果供后续复用。

- 完整测量生存期：2,829 次控制观测，平均 ready-paused 3.897 个，
  其中真正续接 2.821 个，首次请求 1.076 个。
- 2,463 次有续接候选；1,939 次同时存在至少两个不同 workflow 的续接。
- 公共窗口：658 次控制观测，平均续接 2.190 个，389 次为多 workflow 续接并存。
- 请求身份全部对齐，未知为零。首次请求包括 root 和 child，不能全部当成被限流 child。

这些续接已有先前模型调用，不再受 child 首次物化限制。
这支持保持当前任务组合继续试验，但它是离散控制观测，
不是时间加权状态、真实容量竞争、物理可恢复证书，或新配置下的供给保证。
前述按时间加权的全测量生存期 active-ready/ready-paused 为 16.47/3.38，
两种口径不能混用。原进展出口只防止停滞，不能保证 decode 批宽。

### 准备和验收

`host200_mean55_prepared_r2` 包含 `control.json`、`pressure.json`、
`thunderagent_acting_decay.json`、`native_matched.json`。
前两组只差本设计开关；后两组明确隔离 Full 策略，使用相同到达、工具、资源和测量成员。
NUMA 继续只诊断，不新增均匀分配要求。

Full＋背压相对同配置 Full 控制必须同时满足：
实际执行的旧前缀重算减少，公共墙钟窗口 decode 与总输出吞吐不下降，
完整 18 条平均 JCT 不变差。
分别报告已物化 ready-paused、放行/held 首次请求、tool/JOIN-paused、
native decode 宽度、held 期间新增空闲、控制 CPU 和进展例外。
不能用 held child 冒充恢复积压，或用 decode worker 内部速率代替墙钟吞吐。
若减少重算却缩小执行集合、降低吞吐，仍为负结果。

新增配置和诊断相关 CPU 回归 **178 项通过**，16 条警告保留，
包含全部原物化测试、到达和 Host 几何、四组配置隔离及续接身份校验。
此前完整 `bound_cpu_r2` 服务资格保留；它不是 55 秒新协议的 GPU 资格。
最新产物均位于候选的 `experiments/reports/subagent_materialization_20261116/`：

- `workload_pressure_screen_r3/screen.json`：容量和到达联合筛查及输入绑定。
- `workload_pressure_screen_r3/figures/capacity_and_supply.{png,pdf}`：已查看的容量与状态曲线。
- `continuation_supply_r1/{supply.json,decisions.jsonl.gz}`：精确续接供给统计与 compact 观测。
- `host200_mean55_prepared_r2/preparation.json`：四组配置及验收合同。
- `protocol_cpu_r3.xml`：178 项相关 CPU 回归。
