# 将工具预测接入 ACTING 压力选择

## 当前结论

完整独立验证已结束，保留原 2 秒、mixed 关闭的 Full，不推广本候选。
新候选相对匹配 ThunderAgent acting-decay 的 JCT/全程输出吞吐改善
3.5722%/2.8159%，但相对原 Full 的 JCT 变慢 0.7563%、吞吐仅提高
0.4910%，没有达到双指标改进要求。全部正常尾部和负结果保留，
尚未达到明显、可重复优于所有 baseline 的整体目标。

## 要解决的组合缺口

保留 Full 已有工具剩余时间预测及实际 demand-cost 卸载，但参考工作集在
预算不足时仍先暂停上下文最小的 ACTING program。ACTING 表示最近一次
模型调用已完成，不代表其工具快返回，也不代表其 KV 已经迁出 HBM。
预测此前没有参与这一步选择。暂停小上下文可能同时暂停短工具 owner，
却仍需继续暂停另一个大 owner 才能补足逻辑缺口。

完整 2 秒 Full 的初步采样关联看到 545 次非终态 active 集合退出，
419 次最近发出的事件为工具开始，266 次随后 5 秒内又提交模型请求。
这里存在采样窗口和事件发出/投递差异；不能认定全部由周期 pressure
造成，更不能把这些计数当成物理驱逐、可避免迁移或可消除 JCT。
此证据用于提出可检验候选，实际执行将记录明确的压力选择身份。

## 候选规则

默认关闭的 `AGENT_PRESSURE_PAUSE_POLICY=predicted_acting` 仅用于 Full 的
reference agent core，并保留原 2 秒控制、1 秒 acting-decay 半衰期、
完整原始逻辑预算、恢复顺序、reasoning 标记以及 1800 秒 force-resume。

当且仅当原策略已经恢复候选、原始逻辑预算变为负数，并且存在多个
ACTING owner 可选时：

1. 沿原来的上下文升序检查，优先选择有当前有效长工具预测的 owner。
2. 必须仍真正等待单个工具、没有提交模型请求，context/session/epoch
   与 hint 一致，预测来源匹配，在线尾部样本支持有效，且没有预取服务保护。
3. 预测继续阻塞至少一个完整控制周期，加上该 owner 完整上下文可能需要
   的 H2D 恢复时间。沿用 75% survival 门槛和已有近期 H2D P90；
   缺测量时采用声明过的 8GB/s 冷启动估计，缺 KV 单位几何则不偏好。
4. 没有合格预测时，按原来的最小上下文暂停；不因为预测不确定而卡住
   容量回收。只有一个 ACTING 候选时不查询预测。

这改变已有的强制工作集缩减选择，没有增加定时器、独立工作集切换协议、
新归还关系、工具返回的额外优先权或新的物理驱逐授权。不要包装为新的
迁移原语或独立创新。每个 owner 每次完整控制最多查询一次，且只在原有
pressure 分支中查询；不逐 decode 扫描 KV 路径。

与已有模块的关系：

- JOIN/EOS 预取、prepare_host、sibling 共同推进和 native demand pipeline 保留。
- 逻辑暂停不等于 Device 页释放；原生容量、锁、Host 完整性和 DMA ACK 继续
  决定物理动作。物理工具 offloader 仍独立检查实际消费者及完整动态成本，
  不使用这里的逻辑选择替代其 D2H/H2D 成本门槛。
- 工具预取、普通 frontier 预取、物理逐层 handoff、mixed chunk 均关闭。
- 未推广的 workflow-size 模型仍关闭。该单项候选暂时拒绝与其同时启用，
  避免把两个未获益的实验改动混在一起；这不是新增的 baseline 限制。
- ThunderAgent 和 native 控制保持原 pressure，公开参考策略文件不改动。

## 可验证行为

在一个 1000 token 逻辑预算的 CPU 例子中，两个 ACTING owner 上下文分别
为 200、400，新的等待者为 500；每 owner 原 buffer 为 100。衰减允许恢复
等待者后，原始预算不足 400：参考策略先暂停小 owner，仍不够，再暂停大
owner。候选有大 owner 的可靠长工具预测时，只暂停大 owner，保留小 owner
连续性。运行时版本同时保留实际输出增长，不把纯策略 fixture 的数字
混同于原生 request 的完整上下文长度。

CPU 用例覆盖没有预测时多种种子的参考生命周期一致性、无压力零查询、
仍在 reasoning 的请求只标记而不提前释放、真实 runtime WAIT_TOOL/
session 身份、过期与短预测、未知几何、恢复过慢、在线尾部样本不足、
服务保护、不同预测源，以及控制臂隔离。原生缓存测试需要原生源码环境；
copy/event stand-in 通过不等于 GPU、正常传输时序或完整性能通过。

每次真正暂停记录 `agent_predicted_acting_pressure`，包含当前所选 owner、
同一局部状态的参考首选、上下文大小、前后原始逻辑余额及预测证据。
它不是完整参考策略的纵向反事实。验收需核对后续实际服务、工具返回、
原生迁移与 JCT/吞吐，不以暂停次数减少代替性能收益。

## 实验边界

声明从原 mixed-off/2 秒 Full 派生，只改变 pressure 选择开关。
继续使用相同 18 项目、3674 请求、145565966 输入/549671 输出 token、
10 条初始加 8 条 mean120 泊松到达、V311 分类工具时长、Device805198/
Host200GB、running/decode24、chunk2048 和 seed21，保留所有正常尾部。

配置与 CPU 证据位于
`experiments/reports/predicted_acting_pressure_20261029/`。
第一次 CPU 准备缺少真实 launcher 的字段别名转换，未启动 GPU；
独立 config_r2 已完成 Full/控制的实际构造检查。`cpu_r1/evidence.json`
记录完整源绑定 CPU 回归：5141 项和 6 个子测试通过，无失败、错误或跳过，
保留上一轮全部 5066 个测试身份，2027 个源绑定输入和原生源码在执行期间
保持不变。纯控制生命周期使用 4 个种子、每个 400 步与原参考状态逐步一致；
实际 runtime 压力例子也已验证不同的受害者选择，没有发起物理复制。

保留 focused_r1/r2/r3 的原始失败，包括缺 WORKFLOW_START 的测试构造、
手动 CDF 未标注离散经验类型、测试默认工具预取开关与正式配置不同，
以及新参数验证分支先使用未初始化 expected 的缺陷；最后一项已修复。
focused_r4 为 123 通过/8 原生环境跳过，完整 cpu_r1 已在原生环境补齐，
不能把较早定向结果的跳过算成新完整检查通过。

源绑定 CPU 与构造预检仍不构成新 GPU 或性能结果，不推广候选。
下一步冻结提交，重查 GPU 预约/资源、当前工作量和 native patch 后，
进行新源码的 position/session/native-stop 和 sampler 资格，再只运行
这一项 Full。复用已有匹配 ThunderAgent 和原保留 Full，不重采 native。

## 已启动的独立验证

运行时代码冻结于 `46d5b37a`。独立 position 检查已完成 8 个上下文、
每个 3 轮共 24 条 exact token/session/native-stop 请求，24 条均为原生停止；
新 sampler 的 1/8/24 测量通过。该小样本未连接 Full policy receiver，
因此只验证共享数据路径，不宣称已验证工具预测压力选择的 GPU 效果。

正式 Full 已注册于
`ops/predicted_acting_pressure_20261029_r1.registration.json`，端口 18901。
实际 watcher/server/driver 和唯一 owned GPU scheduler 已核对为 nice0；
运行源码及原生树继续保持冻结。控制沿用完整 mixed-off ThunderAgent 和
原 2 秒 Full，不额外采集 native 性能基线，也不更改本轮到达或工作量。

`complete_review.py` 是独立的纯 CPU 完成审查入口，其 12 个定向测试通过。
它等待全部原始工作完成、fresh drain 和实际 owned exit，随后生成配对
decode 时间线/分布，并核查 demand、JOIN 预取、sibling、EOS 与明确的
pressure 日志。零次有效改变及完整负结果也须保留；局部 victim 变化不
构成完整反事实，更不能直接折算为物理迁移或 JCT 节省。

开发数据已包含两批额外 native 采集，当前 34/22/18 的整体执行划分足够
继续验证。遵循用户允许的跨独立执行项目/任务重叠，不为严格项目隔离
继续消耗 GPU；不拆散同一执行的快照，也不将当前开发评估称为盲测泛化。

纯 CPU 完成评估器已从 `8686451a` 独立 checkout 启动，
PID 1828218/start ticks 749833184，实际 nice19、CUDA 隐藏，状态为等待完整
Full 与实际退出。它不会启动、重试 GPU 或修改冻结运行源。

新增 `pressure_outcomes.py` 以原生 request、context epoch、session 及
同机 monotonic 时钟绑定暂停与真实单工具返回，保留未完成边界及工具已返回
的反例，按工具执行去重重复暂停。相关 20 个测试通过。封存的首批前缀中，
125 次预测暂停有 121 次完整绑定、108 次实际剩余等待达到已记录门槛，
4 次后续边界未知，未观察到工具已返回才暂停。该选择样本的约 89.3%
不能推广为整体预测准确率或吞吐收益；全部未来时间只用于离线核查。

## 完整结果与不推广决定

完整审查核对 18/18 个不同项目 workflow、3,674/3,674 个 exact 请求、
145,565,966 输入及 549,671 输出 token、72 个 JOIN 和 92 个 child；
原生停止条件、正常尾部、fresh idle drain 及实际 owned exit 全部满足。
纯 CPU 评估器正常结束，没有后续 GPU 任务。三幅 decode 时间线、分布
和相同 batch/context 几何比较图均已打开检查。

| 方法 | 平均 JCT / 秒 | Makespan / 秒 | 全程输出 token/s |
| --- | ---: | ---: | ---: |
| 保留的 ThunderAgent acting-decay | 1504.072304 | 2305.899187 | 238.375989 |
| 原 2 秒、mixed-off Full | 1439.457324 | 2253.756840 | 243.890996 |
| 预测 ACTING 压力选择 Full | 1450.343294 | 2242.744765 | 245.088522 |

本轮的 423 次必需 ACTING 暂停中，238 次选择使用有效预测，111 次选择
不同于同一局部状态的参考首选。最终 238 次均绑定到不同的实际单工具
执行及后继请求，没有未绑定边界；210 次实际剩余等待达到已记录门槛，
约 88.24%，28 次未达到，没有工具已返回才暂停的样本。这是经过策略
选择后的生存门槛核查，不是全体工具预测准确率或独立校准集。

对照原 Full 的最终 native ACK 计数，单位为十进制 GB：

| 物理统计 | 原 Full | 本轮 Full | 变化 |
| --- | ---: | ---: | ---: |
| H2D ACK | 2659.224748 | 2850.771763 | +7.2031% |
| D2H ACK | 939.598381 | 988.092039 | +5.1611% |
| Host 驱逐字节 | 672.722977 | 733.486449 | +9.0325% |

两臂各 3,564 条同 session 旧输入复用探针的重算代理 token 总数均为 0。
这不是逐层 kernel 工作测量。逻辑选择改变并没有保证更少的物理迁移，
而“实际仍会等工具”也没有直接证明替换原受害者更有利于完整 workflow。
这些是完整配对的描述性结果，不能把其中一个统计单独归因为 JCT 差异。

完整关键依赖路径分解也已通过相同请求工作量和 JCT 边界重建检查：
提交到首次原生服务的路径均值从 170.931159 秒增加到 181.517392 秒，
约增加 10.586233 秒；首次服务到原生完成从 842.433232 秒增加到
842.837363 秒，约增加 0.404132 秒。两臂可以实现不同的最后完成依赖路径，
这些区间包含排队、准入、恢复和交错执行，不能直接作为控制 CPU 成本
或单一模块的因果归因。下一步应核对选择与后续准入/恢复的组合代价。

需求恢复核查记录 512 个 staging episode、721 个成员，全部有后续实际
原生服务和完成；未标记 H2D 字节完整对账、未解释字节为 0。
预取仅来自 JOIN ticket：1,411 个命令全部确认复用，约 218.61GB，
工具预取和物理 handoff 命令均为 0。Sibling 记录 1,055 条全部核实，
其中 21 个多成员实际批次；55 条 EOS 触发请求全部找到最终请求边界。
这些正确性证据不改变上述不推广决定。

完整入口：

- `experiments/reports/predicted_acting_pressure_20261029/complete_review_r1/review.json`
- `complete_review_r1/pair/`：完整配对指标、60 秒时间桶、PNG/PDF 图表。
- `complete_review_r1/{native_demand,prefetch,siblings,eos}.json`：机制审查。
- `complete_tool_outcomes_r1.json`：请求/session/工具返回的完整绑定。
- `complete_causal_paths_r1.json`：相同工作量下的完整依赖路径重建。

下一步若继续此方向，需要评估一次逻辑暂停所引发的实际迁移、恢复及
后续服务结果；不能只用增加训练快照或提高门槛命中率代替调度收益。
当前已有独立执行数据足够继续开发，不再为项目严格隔离补采 native。
