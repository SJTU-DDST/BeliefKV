# 最后一次新负载 Oracle 验证与后续优化边界

## 当前结论与执行顺序

最后一次新负载 Oracle 对比已完成，平均完整 JCT 回退0.157%，未达到事先冻结的5%改善门槛，暂停工作量排序方向。返回原始56、初始12条及平均70秒到达的非排序 Full；该负载已有相对 ThunderAgent acting-decay 的10.430% JCT改善和3.262%共同窗口吞吐提升。不能把 Full 的收益归因于 Oracle，也不能断言已知工作量必然改善非抢占、容量受限的 agent 调度。

按用户最新要求：

1. 只在新采集轨迹组成的 original82 上再做一组非排序 Full 与容量规划 v6 Oracle 对比。
2. 若固定测量组平均完整 JCT 改善不足5%，或共同时间窗口输出吞吐低于对照98%，暂停工作量排序方向，返回 original56、初始12条、平均70秒到达的已完成最佳匹配负载。不再继续为 Oracle 搜索任务、到达参数或策略组合。
3. 在最终选定负载上补 native SGLang 的同 token 回放对照。自然采集时的到达方式与工具时间不同，不能替代这个对照。
4. 逐层 handoff 和其他优化先审计实际机会；只有机会足够才增加 GPU 实验。

5%和98%是此次停止搜索的工程判据，不是统计显著性，也不是对所有负载的保证。Oracle 的组合成本函数也不是全局最优 JCT 上界。

## 已冻结的新负载

来源：`experiments/reports/remaining_original_projects_20261030/final_oracle_original82_source_r1/`。
到达及测量：同目录上一级的 `final_oracle_original82_traffic_r1/`。

- 100个完整原项目组成候选库；保留82个不同项目，包含新采集19项目中的11个。
- 完整人口13,124次请求、1,644,343个输出 token；模型工作量 P80/P20 为3.017。
- 初始12条、平均70秒持续到达，arrival seed35，静态平衡后冻结 permutation194。
- 固定12条预热、18条测量、52条背景；测量组含4个新采集项目，模型工作量 P80/P20 为4.681。
- 工具 overlay 继续使用原公开分类池与 seed21；与旧69重叠的2,298项工具时长逐项相同。其他工具仍用自然时间。
- 测量任务全部自然完成后才停止背景；保留所有测量长尾，不等待所有背景完成。共同墙钟窗口统计所有实际输出。
- 58项目、P80/P20约4.04的组合仅是CPU候选记录，不排队运行。

新19项目的完整资格审计位于 `remaining_node_original19_finish_r2/`。R1观察器错误地期待自然采集不生成的 `owned_process_cleanup.json`，原失败记录保留；R2核对封存的真实进程身份已退出、owned-run为空、实际遥测排空及完整 token/因果证据，没有重新采集或改写原始数据。

## 最佳已完成回退负载

original56、初始12条、平均70秒的同负载对比：

| 指标 | 非排序 Full | ThunderAgent acting-decay |
| --- | ---: | ---: |
| 固定测量组平均完整 JCT / 秒 | 1052.995 | 1175.614 |
| 共同窗口输出 token/s | 301.892 | 292.355 |

JCT降低10.430%，共同窗口吞吐提高3.262%。18条中17条改善，一条回退约4.393秒。保留该负载的背景覆盖与有用前缀丢失审计限制，不能把单次配对称为正式稳态结论。

## 最后一次Oracle的完整结论

`final82_oracle_and_selected_native_r1/oracle_comparison.json`独立核验两臂均完成固定18条、3,093次精确请求和422,130个输出token；两次新鲜空闲遥测、真实owned进程退出均通过。82条全部到达，各79条自然结束，另外3条非测量任务按固定测量组完成规则停止。

| 指标 | 非排序Full | Oracle v6 |
| --- | ---: | ---: |
| 平均完整JCT / 秒 | 998.478450 | 1000.047191 |
| P50完整JCT / 秒 | 461.012528 | 463.947236 |
| P95完整JCT / 秒 | 2927.154629 | 3045.212881 |
| 共同窗口输出token/s | 283.760981 | 283.482442 |

平均JCT变化为回退0.157113%，共同窗口输出下降0.098160%；10条改善、8条回退。吞吐通过98%非劣门槛，但JCT未通过5%改善门槛；两臂背景覆盖等资格限制仍保留，`formal_workload_accepted=false`。

`complete_oracle_choices.json`使用完整封存记录重放6,878条组合记录、418次实际搜索和72次集合改变，识别24次新选入agent episode。22次周期新选入均有真实授予、资格保留及后续精确服务，21次在下一控制或已记录干预前完成有用批次；但仅2次属于测量workflow，其余20次属于非测量任务。两次free-dispatch episode未在该审计中建立服务对应。早期“13次搜索、0个新成员”仅是保留的实时前缀，不能代替最终结论。

因此实现确实改变了一部分准入并传递到服务，完整测量均值仍未改善。此结果不证明所有短任务调度均无收益，也不是全局最优JCT上界；按此次明确停止判据，暂不继续Oracle GPU搜索或训练工作量预测器。配对完整JCT及共同窗口decode/全部输出趋势图已生成并检查，位于`oracle_figures/`。

## 回退负载的native启动与监控修复

原顺序协调器保留在`stopped_preserve_final_comparisons`。native最初在GPU分配之前遇到工作树命名碰撞：位置检查从run-root最后一段`r1`派生目录，与已完成Oracle位置检查重名。原失败入口、stderr和原工作树全部保留；恢复只给native位置检查的scratch run-root增加`_native56`，所有任务、token、到达、资源、native配置与测量成员不变。

恢复入口已重新完成24请求token/session/native-stop检查及1/8/24采样，并启动正式`native_matched`。固定18条观察器已挂载，完整测量任务结束后再停止非测量背景。正式注册是`ops/workflow_final_selected_original56_nonranking_native_20261031_r1.registration.json`；没有重跑Full、Thunder或Oracle。

首个CPU汇总followup在formal状态仍为`waiting_idle_gpu`时，将未填入的进程句柄交给只接受字典的存活检查，报`NoneType`错误。这不在服务进程或测量路径内，native随后正常启动；原失败状态和脚本保留。R2只接管该实际运行，安全处理缺失句柄并避免终态分支覆盖结果处理函数；不启动或重试GPU任务。最终native性能以完整18条结果及实际退出核验为准。

## 逐层 handoff 的当前机会

只读审计：`handoff_service_scale_complete_controls_r1/`，对应完整已退出的 original56 和 original69 Full。

- original56：619次选中消费者的容量不足观察；专用`predicted_tool_offload_backup`标记记录119次脏尾备份，合计14,926 token，P50为71、P90为364 token。该标记不覆盖事件替换路径，不能称为全部预测式备份。另有450次 settled 备份副本驱逐事件，不能与需要新复制的备份混为一谈。
- 119次备份的脏尾字节数/消费者恢复预算，P50仅0.227%。这说明消费者恢复可能很大，而 donor 新增复制通常很小。
- 全记录 D2H stream 合计80.369秒，H2D为198.868秒，测量首到达至最后测量完成跨度4,360.159秒。两方向服务量最小值约为跨度1.84%。
- original69相同服务量比例约1.47%；专用标记中的50次脏尾备份共5,945 token，P50同为71 token，同样不是全部预测式备份。

上述数字包含背景复制，且复制已能与其他计算重叠。它们是服务量尺度，不是可消除 JCT 或暴露等待的上界；排队传播也不能由此直接求出。采样的逐层等待不得按采样间隔放大。

R1没有沿原生`tagged_child_commits`关联备份命令，不能把未匹配视为零复制。补充只读审计`handoff_tagged_copies_complete_controls_r2/`保留R1并改用原生子命令回执：

- original56中25次备份能同时对应同context/epoch/消费者的后续释放，原生D2H stream合计13.212毫秒；备份事件到ACK合计636.226毫秒，ACK到观察到的释放合计980.554毫秒。
- original69中相同严格链路6次，D2H stream合计2.921毫秒。
- 其余备份缺少这一严格的后续释放对应，不能将它们视为没有传输、无效备份或可消除交接等待。

目前没有足够机会证据启动逐层handoff。ACK与事件路径也应先核对实际服务与批次覆盖，不能直接把记录时间差算作可消除JCT。

补充`handoff_all_tagged_backup_copies_r3/`不要求存在后续同消费者释放，逐一核对专用预测标记对应的全部备份命令：

- original56的119次全部有原生tagged D2H回执，实际子命令共1,467,285,504字节；按包含它们的原生复制去重，stream合计55.672毫秒，submit到ACK合计3.247秒。
- original69的50次也全部匹配，共584,417,280字节；对应stream合计22.838毫秒。
- 这些原生复制可能合并了其他子命令，服务量口径保守地包含整次复制。它们不等于GPU暴露等待，也不是JCT上界。

这补齐了R2未能关联后续释放的备份，并未将那部分认定为零复制。当前`prepare_host`已使大量donor成为Host-backed副本，额外的脏尾复制很小；继续暂停逐层handoff，不为放大交接机会而关闭备份或强行制造迁移。

### 备份路径清点与口径修正

`handoff_prepare_route_inventory_r4/`改为清点全部`prepare_native_issued`命令，并关联原生`tagged_child_commits`。完整original56/69及当时original82的独立实时前缀均没有缺失原生回执的已发命令。R1--R3原始报告保留，不能将它们的专用标记样本外推为全部工具卸载。

| 路径与口径 | original56完整记录 | original69完整记录 |
| --- | ---: | ---: |
| 预测备份命令计数 | 170 = 119专用标记 + 51事件替换 | 94 = 50专用标记 + 44事件替换 |
| 全部`tool_wait`备份命令 | 1,429 | 1,039 |
| 其中主动工具备份计数 | 1,259 | 945 |
| `tool_wait`对应子命令字节 / GB | 14.152 | 10.182 |
| 包含这些子命令的原生D2H stream / ms | 592.729 | 417.835 |
| `join_prepare`命令 | 149 | 148 |
| `join_prepare`对应D2H stream / ms | 120.384 | 120.699 |

`tool_wait`包含主动备份，是预测备份的保守超集；不同路径可能合并到同一次原生复制，表内stream不能直接跨路径相加。表格也没有覆盖原生自动换出和idle备份，不替代全记录80.369秒D2H的口径。上述服务量不是GPU暴露等待、可消除JCT或强制A先于B的穷尽清点。

original82的R4仅是运行中前缀：202次预测备份计数，1,691次`tool_wait`、235次`join_prepare`，对应stream分别722.337和211.816毫秒。虽然随后Full已完成，这份封存前缀仍不能改称最终总量。修正遗漏后，目前仍未建立足够大的逐层handoff机会，保持关闭。

`final82_complete_control_opportunities_r1/backup_routes.json`随后独立验证完整测量、实际退出及全部最终备份回执，得到相同计数与stream总量；这是新的完整记录证明，不改变R4实时前缀的原有口径。202次预测备份由128次专用标记与74次事件替换构成，全部工具备份中另有1,489次主动prepare计数。

## Native 对照入口

native 对照显式声明为 singleton `native_matched`，清除 Full 的 agent准入、预测卸载、备份、预取及 Oracle 信息。保留公共数据平面、计时和精确 token 回放，以同一容量、到达、工具时长和测量组比较。

复用固定测量任务完成后的停止、精确请求回执、两次新鲜空闲遥测与真实进程退出验证。对照程序不把测量组身份交给调度策略。结果应称为带公共测量扩展的 native SGLang 回放，不声称未经修改的 upstream。

CPU入口检查R1发现停止入口遗漏显式native声明校验，原有拒绝未声明native的测试正确失败；修复后R2通过6,574项测试及23项子测试，保留R1失败。继续核对真实registration发现声明保存在已经hash绑定的experiment configuration中，停止入口也支持从该不可变文件验证声明；缺失绑定、文件改变、错误arm与非布尔声明仍然拒绝。R3通过6,580项测试及23项子测试，保留全部R2身份与2,393个源码输入。项目测试与任务内容未改动。

## 实际执行状态

新original82 Full使用提交`9072232d`的独立冻结快照，已完成本轮24请求的精确token/session/native-stop检查及1/8/24采样开销检查，正式对照已经启动。固定18条测量任务的观察器已实际挂载；不等待全部82条任务完成。

准备中的顺序协调器只允许在Full完整测量结束并验证实际退出后，使用同次对照校准v6并启动最后一次Oracle；依据已冻结门槛选定负载，然后补native对照。每次GPU启动都要重新检查预约、真实资源和所有输入，不因前一轮失败自动重试。

顺序协调器已实际启动，计划与状态在`ops/final_oracle_native_20261031_r1.{plan,state}.json`，使用独立干净快照`a013dbd7`。运行中的Full仍用原冻结快照；两个快照的服务策略相同，差异在native停止声明的分析入口。最佳旧56的native配置也已通过真实CPU入口检查，保存于`final_native_original56_cpu_entry_r1/`，没有因此提前启动GPU。

本轮运维脚本按原始字节归档到`final_oracle_operator_archive_r1/`；脚本及状态属于这组实验，未为每次观察分配新的GPU实验版本。

### 新负载Full完整测量结果

original82非排序Full已完成固定18条测量任务，独立`completion.json`及`verify_measured_execution`确认3,093次精确请求、422,130个输出token、两次新鲜空闲遥测和实际owned进程退出。

- 平均完整JCT为998.478450秒，P50为461.012528秒，P95为2,927.154629秒，最大3,960.730333秒。
- 共同墙钟窗口输出为283.760981 token/s。
- 82条全部到达，79条自然结束，3条非测量任务按预定测量组停止规则截尾。
- 最后背景到达5,012.012056秒，最后测量完成5,598.891382秒，仍有586.879327秒背景未覆盖尾部。保留该限制，不能称为合格稳态。
- supervisor的`stopped_for_investigation`与driver的`-2`源自固定测量组完成后的主动背景中断；应结合独立完成证书解释，不构成重新运行GPU对照的理由。

最后一轮Oracle根据这次完整Full的实际成本校准：`capacity_v6`、全池容量规划、`full_batch` decode成本、最多6/8候选及0.5毫秒搜索软预算。实际构造器验证记录于`final82_completed_control_oracle_r1/entry.json`，成本身份为`f17c17d7cb849fad40a9fb53a4032869498cfe836a5e18fe868c596c6beda69a`。启动资格阶段及正式运行必须分别核对，不能把协调器上一阶段保留的18条完成数视为Oracle进度。

Oracle已通过24请求位置/session/native-stop与1/8/24采样检查并进入正式回放。`final82_completed_control_oracle_r1/live_oracle_r1.json`独立核对379个精确原token回执、唯一实际GPU scheduler、实际进程祖先、nice0服务进程、nice19固定测量组观察器、相同原生容量与160条使用`planned_pool_geometry`的选择记录。JOIN/EOS、预测工具卸载、prepare保持开启，工具预取与逐层handoff保持关闭。

早期`live_choices_r1.json`重放323条组合选择记录：13次实际搜索、2次所选集合变化、0个原规则之外的新选入agent。其余260条是空reference等未进入搜索的元数据回退，50条没有竞争对象。它们不是323次有效竞争，也不是完整测量结论；必须等待完整18条JCT再执行已冻结的方向停止判据。

更长的`live_choices_r2.json`封存910条选择记录，仍是13次搜索、2次集合变化、0个原规则之外的新选入agent；719条未进入搜索，178条没有竞争对象。两次变化发生在相邻的periodic/free-dispatch边界，分别将3成员reference缩为1成员、2成员reference缩为1成员，不应解释为两次独立的新短任务替换。该前缀记录的metadata与selection线程CPU合计32.658毫秒，中位8.787微秒/条，不包括整个控制路径和日志写入，不能据此解释完整JCT变化。

对照`final82_live_control_r1/periodic.json`完整覆盖共同吞吐窗口：该窗口只有4次跨workflow逻辑容量竞争，整份较长控制前缀有93次，其中89次在共同窗口之后。它们是重复的逻辑容量观察，不是物理可执行组合数。不能仅用Oracle早期没有新成员来断言后半程也没有机会，更不能在测量长尾完成前提前终止本轮。

### 已完整覆盖的共同吞吐窗口

`final82_oracle_live_common_r3/`独立封存Oracle的较长原生和事件前缀，完整覆盖55.692771至1,638.954850秒的固定窗口。`closed_common_window.json`复用既有输出核算函数，校验相同token/引擎/容量/到达及组合对照契约，并逐项复现已完成Full的cohort输出统计：

| 完整共同窗口指标 | 非排序Full | Oracle v6 |
| --- | ---: | ---: |
| 全部workflow实际输出token | 449,268 | 448,827 |
| 全部输出token/s | 283.760981 | 283.482442 |
| Decode输出token/s | 281.501089 | 281.233919 |
| Decode worker时间加权声明宽度 | 6.049861 | 6.104596 |

窗口长1,583.262079秒，包含背景任务和prefill首token，按真实原生批次完成时间归入窗口。Oracle全部输出吞吐变化为-0.098160%，通过冻结的98%非劣门槛。尚未获得完整Oracle平均JCT，不能据此宣布整轮通过，也不对已完成子集计算可替代完整均值的结果。表内声明宽度不是配置上限24、纯kernel效率或全运行平均宽度。

Oracle的R3周期审计在共同窗口记录782次控制、248次存在ready-paused、5次跨workflow逻辑容量竞争、268次授予且全部保留资格。更长前缀共有321次ready授予，321次都有后续精确服务，285次在下一控制或已记录干预前完成有用批次，1次观察窗口仍未关闭；这些是授予记录及其有界服务证明，不是321个独立agent或独占JCT收益。

同一组较长前缀的组合审计为1,123条记录、13次实际搜索、2次集合变化、0个原规则之外的新成员。周期审计与组合审计各自封存独立前缀，不是原子全局快照；仍需保留后半程和全部测量长尾。

### 剩余测量长尾的实际进度

`final82_oracle_tail_progress_r1/`在2026-11-01 07:47 UTC独立核对实际存活的watcher、server、driver、固定测量组观察器和顺序协调器，并封存事件、回执及周期准入前缀。事件前缀中14/18完成，余下四条的精确请求完成数为：

| 原项目 | 已完成请求 / 完整请求数 | 距最近完成回执 / 秒 |
| --- | ---: | ---: |
| `python_desktop_development_expert_021` | 67 / 369 | 16.499 |
| `python_fintech_trading_medium_066` | 225 / 403 | 11.222 |
| `python_mobile_game_medium_096` | 250 / 355 | 3.637 |
| `python_system_networking_medium_063` | 149 / 229 | 9.970 |

这些是观察时点的请求计数，不能当作工作完成比例或剩余JCT预测。桌面开发root在最近周期中ready且保持active，完整逻辑预算248,335 token；其余观察到的三个JOIN parent均inactive，移动游戏child保持active，网络项目child在该周期从inactive恢复为active。交易项目child在该周期无ready模型请求，不能仅凭此认定停滞。

最近120秒中，桌面/交易各有一次ready-paused且单独逻辑可容纳的观察，也各有一次恢复；网络项目有7次此类观察、6次恢复。它们是重复周期样本和逻辑额度，不证明全部时刻物理可容纳。这个前缀显示四条任务仍有近期进展，未提供JOIN parent占完整active额度阻塞child的证据；最终验收继续等待全部测量任务完成。

## 其他优化的机会审计

`measured_prefill_opportunity_complete_controls_r1/`按固定18条测量任务重建实际最后完成依赖链，逐项复现完整JCT。保留公共背景批次的干扰，但只对测量workflow做等权均值：

| 实际关键路径分量 / 秒 | original56 Full | original69 Full |
| --- | ---: | ---: |
| 完整JCT | 1052.995 | 813.056 |
| 模型请求提交到首次worker启动 | 119.533 | 46.629 |
| 上述等待中没有被记录worker区间覆盖的部分 | 0.156 | 0.382 |
| 首次worker启动到请求完成 | 608.279 | 454.580 |
| 其中自身worker区间并集 | 408.800 | 338.504 |
| 其中自身worker区间以外 | 199.479 | 116.076 |
| 首次decode后与prefill worker区间相交 | 189.484 | 116.774 |

最后一行是prefill穿插的暴露量，不是可消除JCT，也不能与其他行再相加。original56全记录12,445个prefill批次，P90约341毫秒、P99约744毫秒；有4,522批超过100毫秒。记录到的decode按worker时间加权平均宽度为10.27，不是配置上限24或纯kernel效率。

候选方向按以下次序继续核对：

1. **限制连续prefill对驻留decode的占用。**检查已经可执行、KV驻留的decode是否被多个chunk连续推迟；有驻留decode时在prefill间插入有界decode进度，没有decode时继续prefill。可以先利用原生已有的prefill/decode节奏能力建立对照，不声称通用引擎调参是agent调度创新。早完成工具前请求可能把后续外部等待与prefill重叠，但需实测，不能从189秒直接扣除。
2. **给已驻留的短续接留出同批输入预算。**若原生chunk owner先用完本轮输入预算，小增量的已就绪续接仍可能等待；先核对这种事件是否实际频繁存在，再考虑把完整chunk与短续接装进同一原生prefill批。仍保留原生请求行、KV增长及锁检查，不新增普通恢复lease或抢占active agent。
3. **ACK后的释放与准入。**现有PREPARE_HOST ACK已经rearm预测卸载，不存在完全没有唤醒的简单缺口；已确认样本的时间尺度比prefill干扰小，优先级较低。只有发现实际批次可推进却重复等控制周期，才修改链路。

缩短chunk、插入decode或调整组批都可能延迟新请求、增加提交与计算开销。只有通过局部机会审计后才提交一个有界GPU候选；验收仍是完整测量JCT、共同窗口输出吞吐和有用前缀重算，不能用TBT改善替代JCT与吞吐。

### Prefill连续占用的局部模型

`prefill_decode_burst_opportunity_r1/`额外要求关键路径请求在连续prefill前已经decode、在紧接的decode批次仍然存在、token进度没有变化，且未在该prefill段重算；再用紧接批次的固定worker成本估计每个prefill后插入一步decode：

- original56有1,252段多批prefill，其中1,115段能观察到这样的测量关键路径请求；局部模型中80个不同请求有提前返回机会，单次局部提前量P90约31.8秒。
- original69对应1,014段、67个不同请求。
- 模型使用未来输出长度只为判断局部机会，实际候选不读取这些长度。重复请求、批次改变与并行JOIN意味着不能累加这些局部提前量作为完整JCT预测。
- 每次额外decode仍需原生KV增长准入；局部模型不授予物理容量。也没有把decode时间视作免费：模型记录被推迟的prefill服务量。

据此准备一个有界、通用引擎调优候选：原生`prefill_decode_interval=1`，chunk仍2048、mixed仍关闭，agent与KV策略均保持不变。只在显式声明时传递该CLI参数，历史配置保持默认字典与命令行为。CPU用例执行固定native源码中的`get_next_batch_to_run`、间隔判定和重新arm方法，覆盖已有chunk owner、非空/空decode及默认关闭行为；空decode最多跳过一次选批，必须通过实际GPU验证其墙钟影响。

候选准备在`prefill_decode_cadence_original56_prepare_r1/`。先完成最后Oracle与选定native比较，不批量排队新的调参。后续按同任务、工具、到达、容量和测量组验证；cadence比较入口拒绝额外策略或资源变化，暂定3%平均JCT改善、共同窗口输出不低于98%作为进一步复验的信号，不声称单次显著性或agent调度创新。

候选的`prefill_decode_cadence_bound_cpu_r1/`完整绑定通过6,620项测试及23项子测试，保留此前6,580个literal身份与2,393个旧源码输入，新绑定共2,395个源码输入。初轮聚焦检查中3项配置fixture仍写8请求而调用24请求入口，被原容量一致性校验拒绝；保留失败XML，修正fixture的共同running=24后聚焦347项通过，完整检查也通过。没有修改项目测试或放宽容量校验。

### 进一步核对后的决策：暂不单独运行cadence

`cadence_external_overlap_opportunity_r1/`进一步核对同invocation串行后继与原chunk结束边界。非终态请求早返回后，短工具结束并不意味着其下一次prefill就能通过尚在执行的chunk owner：

- original56的81次局部早返回观察中，78次能关联同invocation的下一个关键路径请求，3次跨invocation/JOIN保留未知。
- 考虑chunk前沿推迟后，78次的局部后继就绪提前代理合计约44.323秒，远小于仅看请求早返回得到的约523.048秒。两者都不是全局净JCT收益，不能除以workflow数后直接宣称优化效果。
- original69对应58次串行后继约22.404秒；另有一次最后模型请求的局部机会约37.742秒，8次跨invocation/JOIN仍未知。
- 所有竞争workflow还需承担prefill被推迟的成本。因此目前不能仅凭189秒prefill穿插暴露或P90局部提前量认定单独cadence会有明显JCT收益。

据此保留已通过CPU资格的显式候选与实现，**暂不排队GPU cadence实验**。当前运行与已冻结后续Full/Oracle/native都保持原cadence，后续应先验证“短续接被chunk owner挡住”的实际机会。

一个更完整的待验证设计是：在长chunk仍有保证进度的同时，为**已经有正常agent资格、实际KV就绪且增量输入很短**的续接，在同一原生prefill批中保留其真实输入量；然后让已经驻留的decode有界推进。只在同一现有优先级范围内选取，不改变JOIN保护、active连续性、逻辑额度和原生KV增长检查。

例如原本chunk独占2,048个输入token，若实际可执行续接只需50个输入token，则尝试1,998+50的原生同批装入，而非只插入decode后仍让后继请求等待整个chunk结束。初步实现边界应是最多检查8个已就绪候选、总短续接输入不超过本轮预算的1/8；按真实需求和page对齐预留，不固定浪费256个token。未知驻留、在途H2D、锁、请求行或实际容量不支持时保持原路径，不新增普通预取或lease。

上述仅为设计边界，还没有完整实现或GPU收益。现有历史记录无法始终证明请求在等待期间的完整物理驻留，不能拿“首次服务时前缀已命中”倒推整个等待过程都热。必须先补足可核验机会，再决定是否做模型GPU验证。

### 短续接被连续chunk挡住的定量核对

`short_continuation_chunk_opportunity_r1/`只读核对完整测量关键路径上的同invocation续接：首次原生prefill的输入增量不超过256 token，且该批就能覆盖完整剩余prompt。长chunk段限定为同一请求连续至少两批singleton prefill、累计至少4,096输入token。工具等待与请求提交之前的时间不计入等待重叠。

| 观察 | original56 | original82 |
| --- | ---: | ---: |
| 同owner长chunk段 | 275 | 319 |
| 关键路径短续接与这些chunk重叠 | 92 | 73 |
| 其中chunk期间有active/ready周期准入记录 | 32 | 29 |
| 全部短续接的等权workflow平均等待重叠 / 秒 | 41.427 | 34.622 |
| 限定为存在上述逻辑资格证据的请求 / 秒 | 25.387 | 19.095 |

最后一行保留的是“该请求在chunk内至少有一次逻辑资格证据”对应的整体重叠，不能称为整个等待区间都已ready，更不能称为物理可执行时间。原生首次服务几何不证明等待期间的HBM驻留；多请求prefill的实际成本、长任务推迟、后续decode和工具依赖变化仍未扣除。

该机会比“平均142秒与prefill穿插”更具体，但仍只支持保留有界短续接共批设计，尚不足以登记新的GPU收益实验。后续实现必须在当前原生选批边界证明真实完整前缀、资格、请求行及增长容量，用实际短输入从长chunk中分出预算，并保留长chunk进度和JOIN保护。不能将25.387秒直接从1,052.995秒JCT扣除后宣称优化。

源码时序核对进一步确认：`NativeReactiveTelemetry.on_launch()`在首次GPU服务时才填入`cached_tokens_device`、`cached_tokens_host`和checkpoint等属性，然后将`llm_submit.ts_ms`设置为先前保存的提交时间。该行不是提交时的缓存快照。`native_ingress_timing_ms`保留真实入口时刻，但同一行的缓存字段仍属于首次服务观察；即使时间戳早于长chunk，也不能据此消除物理驻留的不确定性。

### 共批候选与相关工作的边界

2026-11-01核对原始资料：

- Sarathi-Serve，arXiv:2403.02310v3（2024-06-17），第4.2节及Algorithm 3已经采用有token预算的prefill/decode共批，先装入进行中的decode，再推进chunk和新请求。通用chunk切分或插入decode不是我们的新机制。
- SGLang官方RFC #45416，2026-10-20提出PPD：在PD分离场景下，让保存前一轮prompt和输出前缀的decode worker本地处理短增量prefill。它是提案，不是本地冻结`434c35dd`引擎已经具有此功能的证据，也不能将它的PD传输收益套用到单GPU实验。

本候选应按工程优化验收：是否在既有agent资格、真实物理前缀及增长容量内，避免长chunk延迟短续接；是否通过提前完成使后续工具或JOIN关键路径与其他计算重叠。必须同时支付新增控制、多成员prefill形状以及长chunk推迟的实际成本。仅改善TBT、首次服务或合并批次次数，不足以证明完整workflow JCT和吞吐收益。

本地冻结引擎的实际server-info配置为prefill `breakable`、decode `full`。`PrefillCudaGraphRunner.can_replay_locally()`中的请求槽位上限只对Full prefill后端生效，不能把初始化时的`_capture_req_slots=1`当作当前双请求共批必然失去图回放的证据。多请求形状能否实际回放及其CPU/GPU成本仍需单独验证；当前未修改图后端或启动共批GPU候选。

### 摘要前后旧KV失效范围的机会核对

桌面开发workflow的一段较长完整请求间隔对应内部`context-summary:000016`。有界实时尾部实际观察到摘要invocation连续decode，root inactive而摘要invocation active。该观察区分完整请求回执间隔与实际无服务，不据此估算剩余JCT。

考虑过的另一个候选是在摘要开始时，根据运行时已经确定的上下文改写计划，提前停止备份或释放将失效的私有后缀。它需要在摘要前就能证明保留前缀，保留共享页和原生DMA/锁检查；离线知道下一条prompt的LCP不能直接作为线上淘汰权限。

`compaction_prefix_geometry_complete_controls_r1/`复用实际回放的source loader、`CapturedTokenCorpus`及原生前缀比较函数，对已退出的Full记录中每次`context_compact`后的caller请求与其上一个已完成请求比较精确token：

| 观察到的摘要后caller输入对 | original56 | original82 |
| --- | ---: | ---: |
| 完整测量记录内已观察并精确比较的输入对 | 17 | 24 |
| 其中属于固定测量组的输入对 | 0 | 24 |
| 摘要前输入token中位数 | 248,436 | 248,646 |
| 仍保留的旧输入前缀比例P50 | 96.770% | 96.688% |
| 仍保留的旧输入前缀比例最小值 | 96.533% | 96.510% |
| 被改写的旧输入后缀token中位数 | 8,025 | 8,235 |

original82的24对均来自桌面开发workflow。摘要没有使整段约24.8万token旧上下文失效；大部分旧前缀仍可复用。表内长度是逻辑token几何，不是独占HBM/Host页数、实际复制字节或可消除JCT，也不包含旧输出尾部的物理页核算。original56的17对属于被保留到测量截止点的背景执行记录，不能称为所有背景均已完成。

另外逐一核对original82这24对的`next_prompt_after_common_prefix_tokens`与实际冻结Oracle的`requests[request_id].input_increment`，24对全部一致。工作量清单使用`WorkflowProgress.completed()`比较实际token，不直接将新session的序列化base缺失当作全部新增输入；没有发现把这类约24.8万token后继统一计为全新输入的缺口。

现有adapter已在摘要后的`context_compact`声明`old_kv_disposition=release_ownership`。本次几何证据不足以支持新增提前失效控制路径，尤其不支持整段caller提前丢弃。保持现有运行，不排队这项GPU候选。

初次CPU分析入口把JSON身份列表直接传入要求tuple身份的校验器，被`verify_capture`拒绝。失败代码与错误保存在`compaction_prefix_geometry_cpu_entry_failure_r1/`；修正为调用已有source loader后两组分析通过。原token、已有校验器、项目源码与运行中的GPU实验均未修改。
