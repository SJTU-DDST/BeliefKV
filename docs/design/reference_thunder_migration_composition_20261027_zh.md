# 使用参考 ThunderAgent 准入的 FULL 工程候选

该候选用于检验现有提前迁移机制能否在强 ThunderAgent 准入核心上
产生完整工作收益。它不是新的调度算法，也不把借出或召回改名为贡献。
旧 retained FULL、V378 和全部 paper baseline 保持原样。

## 已有完整运行给出的依据

V391 读取已完成 V345/V364/V378 的实际 CUDA 依赖等待采样，
每个批次只计一次，不按请求或采样间隔扩张。

| 实际采样 | V345 ThunderAgent | V364 FULL | V378 FULL |
| --- | ---: | ---: | ---: |
| 采样批次数 | 18 | 41 | 40 |
| 总依赖等待/ms | 2662.835 | 4009.957 | 4363.473 |
| 首个观测 FULL 层等待/ms | 31.907 | 103.163 | 172.859 |
| 后续 FULL 层等待/ms | 2630.928 | 3906.794 | 4190.615 |

V345 采样中约 98.80% 的层等待发生在首个观测 FULL 层之后。
16 个样本为工具后继、1 个为 JOIN 后继、1 个为混合批次。这证明
“首层就绪”仍可伴随后续层的实际等待；不能据此推算全程可节省
时间、JOIN 与工具的完整占比或平均 JCT 的理论上限。三组的采样
批次和请求不同，总和不是严格匹配的恢复性能对照。

V392 沿每个实际最后完成依赖路径，重建全部 18 个 workflow 的 JCT：

| 平均路径区间/秒 | V345 ThunderAgent | V364 FULL | V378 FULL |
| --- | ---: | ---: | ---: |
| 提交到首次 native forward | 393.951 | 269.534 | 281.658 |
| 首次 forward 到 native 请求完成 | 877.326 | 945.602 | 936.984 |
| 工具完成边上的外部等待 | 377.265 | 377.270 | 377.202 |
| 完整 JCT | 1697.584 | 1641.531 | 1644.824 |

首次执行后的区间包含 prefill、decode、其他请求交错和运行时等待，
不能作为纯计算或传输时间。各臂实现的阻塞路径也可能不同。
这些结果支持减少额外工作集策略、独立检验迁移机制的收益，
没有证明某个新增选择规则是性能变化的唯一原因。

## 组合方式

显式 `BELIEFKV_AGENT_ADMISSION=thunderagent_reference` 使用
`ReferenceThunderAdmission`。其 policy 类型直接是
`ThunderActingDecayWorkingSet`；原生容量绑定、五秒 tick、
已完成请求处理、实际生成长度更新和 abort 方法直接继承
`ThunderAgentDecayAdapter`，没有重写算法。保留一秒半衰期、
未衰减压力检查、每 agent 100 token buffer 和原有超时兜底。
paper adapter 的隔离检查和默认实例化路径保持不变。

FULL 只增加当前 agent 身份索引和已有迁移接口：

- 注册和 native requeue 更新 context/session/generation/attempt 身份；
  terminal 事件及关闭释放索引。
- JOIN/EOS 预取只允许当前已经 active 的同一 owner，不能改变准入
  集合、暂停其他 agent 或建立新的逻辑预算预留。
- 工具预取额外要求当前实际 WAIT_TOOL、仍有未完成工具、没有已提交
  模型请求且 epoch 完全相同。已有空闲/真实阻塞 Host-backed 冷容量
  和原生锁、DMA、分配检查仍由原迁移路径负责。
- SPAWN 增量备份、真实阻塞后的压力卸载、动态代价门槛下的预测长
  工具卸载、prepare_host、resident-first 和有界短工具连续性继续
  使用既有接口。物理卸载不能自行授予逻辑准入资格。
- 普通 frontier restore 关闭；不加入 sibling 特殊准入、JOIN 借出
  召回、fitting 或额外工作集压力计划。该候选先检验最小组合，
  不是已经实现全部研究目标的最终配置。

该模式只资格化 session-bound FULL-only 路径，拒绝当前依赖旧
agent policy 内部字段的 sessionless 扩展。迁移本身仍不允许普通
工具预取驱逐 ready/排队 agent。原生页分配及 ACK 保持物理权限。

## CPU 验证及边界

V393c 的 27 项针对性检查通过，覆盖真实运行时构造、native prefill
过滤、工具/JOIN 生命周期、过期身份、native requeue、控制臂隔离及
实际状态声明。四个固定种子各执行 240 步同事件序列，逐步对比参考
adapter 的完整 program 字段、衰减历史、容量和强制恢复状态；
每步额外发起预取权限检查，确认不修改工作集。

V394 在一次执行中通过 3030 项共享检查及 4 个子测试，无失败或跳过，
保留原有全部 3003 个 case 身份。V395 封存源码摘要、配置、运行命令、
继承方法身份及 FULL/default ThunderAgent/工具衰减 ThunderAgent
三个实际构造器。原 V393/V393b 缺少测试 event socket 和 JOIN 成员
创建事件的失败报告保留；修复只涉及新测试夹具。

同事件序列下的准入核心等价不保证真实执行时序相同：迁移与 FULL
请求排序仍可能改变请求返回、后继提交、计算干扰和真实工作集变化。
共享 native CPU 测试含 copy/event/Host-index 替身。GPU 正确性、
控制成本及完整 JCT/吞吐尚未验证，不能从 CPU 通过推导性能。

配置为 `configs/migration/predictive_reference_thunder_20261027.json`。
后续只运行该 FULL，复用完整 V345 工具衰减对照；保持 V230 全部
3674 请求、原生停止、18 个不同项目、V311 工具时间表、容量、
原生树、running/decode24、流式计时和种子21，保留所有正常长尾。
完整目标及其他 baseline 的启动门槛保持不变。

## 首次 GPU 组合故障与修复

V396b 在第一次进入 demand-cost 工具卸载路径时因接口不兼容停止：
旧卸载器直接读取 `.policy.agents/.in_flight`，参考核心使用
`.programs`。V397 封存 99 个精确完成请求，核验原始失败日志及
实际进程退出。保留完整失败，不用这段前缀计算性能，也不续跑部分轨迹。

原 CPU 检查分别验证了准入和物理卸载，遗漏了两者组合，这是实际
覆盖缺口。修复为参考准入提供明确的 `tool_offload_protected` 检查，
以当前身份和是否已有模型请求判断保护；实际在途预取、服务 lease、
Host 备份与原生锁仍由原卸载路径检查。准入资格只表示可执行或
可提前恢复，不等于已经持有物理恢复锁。

参考核心下，受益请求必须已经取得正常资格；未准入请求直接退出
该次卸载判断，不能调用旧 retained 预算借出逻辑。旧 retained
分支行为保持原样。V398b 的 109 项针对性检查通过，新增五种组合
使用真实 FULL-only native tree/allocator 与 copy/event 替身，验证
实际释放32槽、Host副本保留、未准入受益者拒绝、pending/恢复中/
身份不明 donor 拒绝，以及整个 reference program 状态不变。
V398 首次夹具沿用 legacy execution recovery 的失败也保留。
V399 随后一次通过 3035 项共享检查及4个子测试，无失败或跳过，
保留 V394 全部3030个case身份并新增5个；源文件、三个实际构造器
与 V397 原始 GPU 故障均绑定到资格报告。完整 GPU 重试仍待验证。

## 证据

- `experiments/reports/v391_v345_v364_v378_restore_wait_cohorts_20261027.json`。
- `experiments/reports/v392_v345_v364_v378_complete_jct_paths_20261027.json`。
- `experiments/reports/v393c_reference_thunder_admission_targeted_cpu_20261027.xml`。
- `experiments/reports/v394_reference_thunder_shared_cpu_20261027.xml`。
- `experiments/reports/v395_reference_thunder_cpu_evidence_20261027.json`，
  及同名 `.sources.json`。
- `experiments/reports/v397_reference_thunder_offload_integration_failure_20261027.json`。
- `experiments/reports/v398b_reference_thunder_offload_targeted_cpu_20261027.xml`。
- `experiments/reports/v399_reference_thunder_offload_cpu_evidence_20261027.json`，
  及同名 `.sources.json`。
- [上一轮完整结果与贡献边界](../research/v378_working_set_switching_results_20261027_zh.md)。
