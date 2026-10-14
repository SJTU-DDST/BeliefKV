# BeliefKV 文档导航

更新日期：2026-10-14

用户临时将服务器留给其他人至2026-10-14 21:30 CST；V58未
派发管理器暂停，实验与性能基准延后。到时仅恢复资源等待，
GPU仍忙则继续等待。单次记录见v542，每日停实验规则保持撤销。

开发分支新增单FULL叶路径顺序复用，减少重复映射与排序；
v543封存暂停前448项通过检查。v541完整原版等价及CPU成本
测量尚未执行，候选未部署，也没有新性能结论。V58冻结保持。

V57已于2026-10-14 18:47:37 CST自然结束全部后处理：
156 completed、零incomplete，冻结0fd9d13/3c13ad50/
572109fe。v535保留历史native对照：平均配对JCT约-5.78%、
完成吞吐约+7.26%，但实际输出工作少11.73%、token速率低5.93%，
独立收益未验收。JOIN/tool提前且下一请求复用FULL为1.587/
10.690GB，JOIN最后ACK到服务P90仍为19.201s。

新GDN候选v533/v534已通过437原生测试+53子检查和68启动检查，
消除普通prefill mask.nonzero和四份checkpoint元数据回读；
CPU约多20–37us，GPU/JCT效果待验证。v536/v537已登记V58冻结
9a54233与58份合格源码；2026-10-14 19:02登记时等待器1528612
等待其他用户GPU任务释放容量，尚未创建实验目录或派发workflow。
资源可用后冷启动
先predictive、再同引擎native。保持既有PrefillAdder，新延迟凑批、
最小槽延迟、mixed chunk、间隔调整及工具180s/输出/截止预算不变。
后续仍查v523定位的物理闭包/恢复计划控制成本、首次服务长尾及
有用FULL预取消费；资格与登记不等于最终性能验收。
实际状态见`experiments/reports/v58_resource_wait_gdn_prefill_20261014.json`。

V56完整同版本两侧及后处理已于16:01:22自然结束。v515保留
两侧155 completed/1 incomplete；共同完成任务平均JCT约
-7.44%、完成吞吐约+2.35%，伴随实际输出工作少8.31%与token
速率低6.16%，未证明独立迁移收益。V56 native只作V57开发参考。
v516/v517的最新600–900s窗口仍显示发射后周转和控制成本：
短单请求发射到结果P50为56.896/50.291ms，版本及需求不同。

v530/v531更新V57第二波3600–3900s：短prefill周转P50为
60.271/58.199ms，decode服务量336229/373544；batch数量接近
不代表工作量相同。v528将旧V49 server日志无损归档，8份文件
完整比较后6.289GB→175.401MB；V50–V57原始证据继续直接可读。

已部署候选按GraphDelta维护等待成员并复用受影响集合，减少
无关事件造成的全context扫描。v507在156份V56前缀上得到
769010次完整候选等价，回放线程CPU约少30.2%；原生绑定与查询
频率为代理，不能称JCT或GPU收益。v509最终841项通过，v510
绑定58份源码，完整重建变慢与初次空绑定无效回放均保留。
质量、公平性、重算与最终重复对照仍待验。

以下保留历史证据；旧等待器与运行状态以顶部实际状态为准。

最新冷候选的服务缓存按新增/淘汰证据方向失效，避免D2H到达却
反复重算未变的H2D统计。v491保持7395个V56查询完整等价，
重算4656→4372，局部查询/ACK CPU约降低2.6%；v493最终803项
通过，v494绑定56份源码与不变的原生400+29资格。
v495/v496已登记最新V57冻结d58e982/等待launcher2985446，
旧等待和docs-sync已在派发前核验终止；合并设备槽及缩小payload
时机修复，不增加独立实验或热改V56。此结果不代表短prefill、
GPU/JCT或最终吞吐验收；通用算子框架与全面零拷贝暂不推进。

最新v497–v499补齐同版本第二波3600–3900s：短prefill完整
发射到结果P50为55.051/58.199ms，返回后间隔6.017/4.419ms；
decode服务量低4.77%，prefill需求与轨迹不同。57/65个样本和
12个稀疏交集组不证明新候选或最终收益；后续仍查选择/维护和
服务消费。下文旧V57等待状态以v495/v496替换记录为准。

用户已于2026-10-14撤销每日00:00–06:00停实验规则。23:59自动
停止cron已移除，旧06:00自动恢复登记已关闭；新实验与运行中的
实验均不再受这一时段限制。下文旧预约及截止记录仅作历史凭证。

当前运行V56，冻结代码661ed45、patch b8e7942c、引擎树c916d585。
2026-10-14 10:33:21启动，GPU监控起点10:36:01.117，顺序为
predictive→同版本native；v451登记、v453核验50份源码与服务。
predictive最终155 completed、1 incomplete，全部156条终态齐全；
客户端duration9325.121s、29个workflow多轮spawn，HTML已生成。
同版本native于13:18:38完成启动核验，保持冻结代码与配置。
该incomplete是root最后8192-token length截断，child先前图预算事件
另计。V55已于10:20:55完成后处理，最终154
completed、2 incomplete，HTML齐全；两例root输出截断保留。

V56尚未证明短prefill或端到端改善。v455的600–900s中，短单请求
run_batch墙钟P50为42.163ms，V55同为predictive的参考是40.815ms；
15个匹配小组多为1–2个样本。v456对旧V29 native的decode服务量
为368701/380722，prefill需求也不同，仍只是开发诊断。
最新v469/v470的3600–3900s中，decode服务量对历史参考约+0.56%，
短单请求run_batch墙钟P50为46.018ms，V55 predictive为46.756ms；
匹配小组稀疏，尚无稳定改善。最新self-wall16.520s/300s，最大
是control_drain，不能沿用首波tool_prefetch最大的成本排序。
v471/v472封存的确认提前FULL复用为JOIN69事件/1.502GB、
工具171事件/10.881GB；JOIN/工具长尾分别存在入队后等待、
预取过早及客户端空档，需分开处理。
v459保留894个active-stack样本与85个读取错误，32个主线程样本
位于prefill逐请求Mamba槽号.item；采样不能换算CPU或可删JCT。

隔离候选v463将虚拟槽号留在设备上组装，并异步上传mask/长度，
不变更物理翻译、状态边界或stream barrier。400项native测试及
29项子检查通过；CPU中位少约1–7us，但48请求lazy用例均值变慢。
v466/v467已导出并资格绑定3c13ad50/572109fe，尚未部署且不宣称
GPU收益。V56继续冻结，native自动只读观测已登记，
待补齐同引擎、同配置的完整相位与NUMA记录。既有PrefillAdder保留，
新延迟凑批、mixed chunk和间隔调整仍关闭。
v486/v487已在派发前保留替换旧设备槽等待登记；新候选d7980a4，
launcher1922608等待V56全部后处理，尚无V57 run目录。
新增修复补齐“回收后payload缩小、因耗时支持缺失而停止”的断点；
同key/checkpoint的缩小payload可用原较大计划的当前耗时预算，
仍重查最新预测和全部原生物理条件。781项检查通过，成本回放
涉及16 JOIN/5工具等待，不能计作已执行动作。v482封存的最终
提前FULL复用仍为1.502GB/10.881GB。新版于V56全部收尾后冷观察，
当前native参考与原等待checkout均保持。
同版本v488的短prefill run_batch P50已为42.163/42.150ms，
但发射到结果处理结束仍为55.989/50.291ms；主要差异窗口位于
run返回后，后续优先查这一周转成本，尚未证明端到端收益。
v468的本地Host容量
审计不支持整worker强制NUMA绑定，本轮继续preferred。

以下为先前阶段的证据与背景，不覆盖以上当前运行状态。
v433–v436完成61,229笔传输的因果时序评估。
全面改用分类型历史会降低整体支持率，因此只增加原估计不支持时
可用的近期FULL-only H2D补充历史，最多32条、年龄不超过300s。
真实runtime回放保持60,778个已有支持的估计完全一致，为18个
独立工具等待的22条拒绝记录补上支持；793项检查通过。这不证明
新增动作、child RETURN精度或吞吐收益。V438/V439随后记录2个
root输出截断incomplete，封存时153个completed、1个仍未终态。
此前c319339登记被v449保留替换，详见状态和执行计划。

当前主线为Qwen3.5-35B-A3B / SGLang 0.5.20，同一
`beliefkv-next` 环境。此前V55于2026-10-14 07:11:24恢复服务，
使用冻结156-workflow、108+48两波、running48和Host200GB/80:20。
CPU绑定NUMA1，Host内存优先NUMA1并可回退；已修复上游自动NUMA
绑定覆盖外层策略导致的启动OOM，失败原件独立保留。
当前重点是真实短prefill周转成本、提前FULL恢复的消费及平均JCT。
既有PrefillAdder保留，新延迟凑批/mixed chunk/间隔调整仍未启用。
v398记录源码/引擎/容量/HTTP服务核验；启动成功不代表性能达标。
旧V29 native用于开发诊断，正式验收需同版本、同配置多轮对照，
包括新NUMA策略与实际页落点。实验按实际资源可用性执行，
不再设夜间停机时段，其他用户作业不受影响。
旧Qwen3/0.5.2rc1、canary和旧池配置不作为当前默认。

下一冷候选还纳入单请求Mamba槽映射优化：v423验证CPU分配
结果一致及局部3–7us减负，v426绑定新b1921753/c9d1fbd引擎，
363项native检查及29项子检查通过，实际GPU收益尚未验证。
V56将先predictive、再补一轮同版本native完整相位参考；
V55源码继续冻结，新延迟凑批保持关闭。v419无损归档72份
旧server/opportunity trace，20.278→0.636GB，恢复索引见
experiments/archive/README.md；当前与历史V29参考仍直接可读。

最新开发候选修复JOIN票据沿用旧工作终点：v408/v409关联16个
独立JOIN、55次时机拒绝，其中49次已有更新且有效的预测。
v412记录950项检查与2项子检查、43份源码绑定，尚待V55退出后
冷测；CPU模拟约44us新增成本保留，不能写成已实现性能收益。
v413补齐2100–2400s完整窗口；历史native仅作开发参考，短
prefill差距与最终JCT/吞吐问题仍未解决。当前实现/计划/注意
事项已同步更新，不用新报告覆盖运行中的V55配置或未知终态。

本页是项目文档的统一入口。文档状态分为：

- **当前**：可以作为代码实现、实验计划和论文表述的依据；
- **参考**：仍有技术价值，但不是当前实现契约；
- **历史**：只用于追溯决策，不得覆盖当前文档；
- **实验凭证**：记录当时的代码和结果，不随当前设计修改。

## 1. 当前权威文档

| 文档 | 用途 |
| --- | --- |
| [当前系统设计](beliefkv_design.md) | 算法、状态机、预测质量边界和未来可选方案 |
| [当前架构状态](architecture_status_zh.md) | 已实现、已验证、未验证和阻塞项 |
| [当前执行计划](implementation_plan.md) | 唯一有效的近期实施顺序 |
| [实验注意事项](experiment_operating_notes_zh.md) | 配置、冻结、统计口径及清理约束 |
| [JointPlan 图解](beliefkv_jointplan_visual_zh.md) | 目标架构参考，不代表新版迁移全部完成 |
| [Architecture](architecture.md) | 英文简要入口 |

发生冲突时按上表顺序解释语义，但“当前实现是否完成”以架构状态页和代码为准。

## 2. 环境与运行

| 文档 | 状态 |
| --- | --- |
| [安装与环境](setup.md) | 当前 |
| [Runtime 接入](runtime_integration_zh.md) | 新版入口与旧接口参考分开，具体符号以代码为准 |
| [服务器迁移](migration_guide_zh.md) | 新版要点与历史迁移记录分开 |
| [README](../README.md) | 项目入口、命令和目录 |

冻结实验参数应从对应 `configs/` profile 读取，不从历史报告复制。

## 3. 研究背景

| 文档 | 状态 |
| --- | --- |
| [相关工作比较](related_work_comparison_2026-07-21_zh.md) | 持续更新 |
| [动态 Agent Workflow 分析](beliefkv_dynamic_agent_workflow_considerations_2026-07-20_zh.md) | 参考 |
| [HiCache/Theta KVPool 启发](hicache_theta_kvpool_implications_2026-07-18_zh.md) | 参考 |
| [ClawTrace 数据分析](clawtrace_dataset_analysis_2026-07-13.md) | 参考 |

## 4. 实验凭证

[`docs/experiments/`](experiments/README_zh.md) 中每个文件对应一次实验、修复或
characterization。它们遵循：

- 保留原始日期和当时结论；
- 后续发现测量错误时，在原报告中明确撤回或降级；
- 不因当前算法变化而重写历史数据；
- 不单独作为“当前系统已经支持某能力”的依据。

当前最相关的报告：

| 报告 | 结论 |
| --- | --- |
| [v5 84-root结果](experiments/joint_tool_join_h2d_v5_84root_zh.md) | 17个预测H2D；JOIN11个复用，工具6个被再回收，完成吞吐-8.95% |
| [v4工具/JOIN恢复](experiments/joint_tool_join_h2d_v4_zh.md) | 六个JOIN H2D及吞吐负结果；补有GPU利用率根因复核 |
| [84-root与对照要求](experiments/pressure84_and_fair_comparison_2026-10-05_zh.md) | 当前单pair；正式多轮平均，回放不是前置要求 |

以下为旧Qwen3/0.5.2rc1 checkpoint参考，不能覆盖新版状态：

| 报告 | 当时结论 |
| --- | --- |
| [PREFETCH recall 与 funded path](experiments/beliefkv_p6_prefetch_recall_enablement_2026-09-16_zh.md) | v6 校准、在线假阳性修复与最新 bounded gate |
| [Natural PREPARE no-opportunity](experiments/beliefkv_p6_natural_prepare_canary_no_opportunity_2026-09-12_zh.md) | 高 HBM 不等于存在 beneficiary |
| [PREPARE_HOST mechanism gate](experiments/beliefkv_p6_prepare_host_mechanism_gate_2026-09-12_zh.md) | 单笔真实 D2H 机制闭环通过 |
| [Direct predictive pipeline](experiments/beliefkv_p6_direct_pipeline_high_pressure_2026-09-11_zh.md) | 出现 timely-positive，但当时物理动作关闭 |
| [Future-growth top-4](experiments/beliefkv_p6_future_growth_top4_shadow64_2026-09-10_zh.md) | beneficiary/value 路径首次产生正收益 |
| [Performance/transfer gate](experiments/beliefkv_extreme_performance_p3_p4_gpu_gate_2026-08-26_zh.md) | 控制面与 bundle transfer 基线 |

## 5. 历史归档

- `docs/archive/snapshots/`：重写前的权威设计、状态、图解和计划全文；
- `docs/archive/plans/`：已经完成、暂停或被替代的执行路线；
- `docs/archive/research/`：已被当前设计取代的早期讨论。

归档文档不得作为当前实现状态。索引见
[archive/README_zh.md](archive/README_zh.md)。

## 6. 已归档路线

以下路线不在当前关键路径：

- 2026-07 HiCache Joint Control P0-P8；
- 2026-08 H200 BF16 R5 恢复计划；
- Perfect-Future Oracle v2；
- GPU-first Oracle；
- Performance-first 独立优化阶段；
- 2026-08-11 P6 旧执行计划；
- morphology 独立策略。

相关代码可以继续用于测试或诊断，但不能据此扩展当前论文主张。

## 7. 文档维护规则

1. 按设计/状态/执行分工修改对应权威页，并同步检查其它当前入口。
2. 模型、框架或实验配置变化时，同步维护环境、runtime及迁移说明。
3. 已实现、GPU已验证、净收益已证明三类事实必须分开。
4. 新实验记录对应报告；旧实验的新诊断追加到原报告并注明日期，
   不能仅新增一份报告而不更新已有状态。
5. 完成或被替代的计划移动到 `docs/archive/plans/`。
6. 不再向状态页追加按日期排列的完整实验报告。
7. 文档中的“已实现”“已验证”“已产生收益”必须分开表述。
