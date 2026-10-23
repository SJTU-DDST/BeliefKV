# V136：恢复一次性 subagent，24 root / batch 24

用户要求暂缓 subagent 复用与选择性 KV 保留，先验证原有的 JOIN、
工具期卸载/预取、Host 增量准备及调度机制。此次将 root 数由 16
增至 24，将服务端 running/decode 上限由 8 增至 24；实际 batch
大小仍取决于就绪请求和原生容量准入。全部 root 同时到达。

使用旧版 `native_in_graph_nested_2to4` 系统提示词和委派中间件。
该版本在初始委派中提示 2–4 个 child，鼓励分阶段多轮委派，并允许
coordinator 再创建叶子 child；模型生成实际工具调用。没有固定重放
DAG，也不加入任何强制复用。完整项目源码只提供给 root，child
通过具体任务描述、选定片段和工具获取其所需信息。

`BELIEFKV_SUBAGENT_RETENTION=off`，不使用稳定可复用 handle API。
predictive 延续安全退休 child 私有物理 KV 的机制；共享前缀和真实
请求/传输锁继续遵守原生所有权检查。native 不额外感知 RETURN
并删除物理 KV，继续采用原生会话引用及缓存淘汰规则。

## 项目和压力依据

新清单是
`configs/workloads/locobench_agent_long_24root_16project_env_20261024.json`，
SHA256 为
`1204acfa790bb85f17e9de026282be5bdbf07ad374296e59750db6fa419dcd6a`。
它按既有 seed 21 顺序，从已固定环境的 16 个项目选取 24 个不同
原始 LoCoBench-Agent 任务，每项目最多两个任务，未根据运行结果选取。
这是 24 个任务、16 个项目，不能称为 24 个独立长前缀。
16 项目的固定镜像、源码 commit、context 与来源证据继续绑定；
启动前检查了 51 个输入绑定。

V135 截止时 Host FULL 高水位为 67.837/160.000 GB，约 42.4%；
Host Mamba 则达到 40.050/40.050 GB，且有组件淘汰。
FULL 的同 session 已服务公共前缀丢失代理为 60894/166104655 tokens，
约 0.0367%，不能等同于全部模型重算率。旧版的 token_usage 和
resident_pressure 会扣除可驱逐缓存，不能用其 48% 活跃期中位数
认定总驻留量只有 48%。V3181 保留原始数字并增加了明确的语义更正。

已检查的数据中，源码语法通过且长度处于 95K–196608 tokens 的项目
只有 16 个；100K 以上为 12 个，50K 以上为 30 个。若要求 32 个
不同项目，在现有 196608 上界内需要降低长度门槛至约 42K tokens。
另有 4 个语法通过、源码约 204K–237K 的项目，尚未完成当前固定
环境与完整输入检查；即使加入它们，也只有 20 个 95K 以上项目。
不能通过加入较短项目后仍把它描述成 32 个独立的 100K 长项目。
理想化的 32 份独立 100K FULL 前缀为 65.536 GB，大于 36.822 GB
FULL HBM 池，小于 160 GB Host FULL 池；这只证明会需要分层存放，
不能单独证明大量重算，也不能保证无重算或 predictive 优势增加。
因此暂不直接扩大到 32，先检查 24/batch24 下真实迁移和重算证据。

## 固定设置与验证

FULL HBM 保持 1797968 tokens，Mamba HBM 保持 513 slots；
Host 保持 200GB、FULL:Mamba 为 80:20，write_back。
上下文 262144 tokens，95% 压缩，root 输出上限 16384，seed 21。
普通 execution-frontier restore 和短工具排队连续性保持关闭。
Full 计划验证 EOS-only JOIN 预取、Host 增量备份、工具时长预测/
压力卸载，以及 workflow/resident-first/stage 调度。
工具预取采用实际 ACK 起算的 1500ms 优先窗口。

在原有每秒一次安全点采样中加入 FULL/Mamba 的 allocator 空闲、
可驱逐、不可驱逐及总分配单位。只读取原生标量计数，不遍历 radix
树，不增加逐 token 扫描或 GPU 同步。无法读取或不一致时报告
不可观测，不将其记为零。该遥测对 native 和 Full 相同。

先采集完整自然 native 源轨迹，保留精确输入输出 token、工具依赖、
正常长尾及完成资格。按用户最新要求，将这次自然 native 采集的
完整 JCT、工作量及全程输出吞吐作为 native 参考，不再额外全量
重放 native。源通过完整性及实际 GPU 数据路径校验后，仅重放
Full、JOIN-only 和必要消融，容量、batch、token、停止条件一致。
报表显式标记自然采集参考，并将历史重放校准单列；不宣称当前新源
已重新完成 native 重放校准。不按 Full 收益、固定 JOIN 数或正常
长尾筛选源轨迹。
CPU 资格及注册/运行状态另存报告和 ops 登记；未完成的采集不能
报告为完整 JCT 或收益。

## 实际启动与初期观测

北京时间 2026-10-24 00:34:50 前后，V136 已通过资源检查并启动，
冻结 app commit `79bb1bf24cd574ef52c78b7d9d4d57d22da62f4a`，
引擎 tree `cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8`。
登记文件为
`/home/longhao/experiment/ops/v136_oneshot_24root_running24_source_20261024.registration.json`。
实际 server_info 确认 running=24、context=262144、seed=21，
decode CUDA graph 包含 batch24；实际四个池与参考 census 完全一致。
CPU 最终资格 V3184/V3185 保留原 5355 项，合计 5379 项及 2 项
subtests 全部通过；此前失败的 V3182/V3183 保留，原因是容量测试
fixture 未补全新观测使用的字段。没有把失败资格用于启动。

约 00:39 的封闭事件前缀已有 24 个 workflow_start、48 次 spawn、
24 个 JOIN create、412 个 LLM submit；尚非全程完成统计，
不能将 JOIN create 当成 satisfied 或提前恢复复用。GPU 的一次
利用率采样为 98%，不能视作全程平均。

00:40:05.998 的实际安全点采样：

| 池 | 使用情况 |
| --- | --- |
| FULL HBM | 1795200/1797968 tokens 已分配，99.85%；其中 1390978 可驱逐 |
| Mamba HBM | 511/513 slots 已分配，99.61%；其中 419 可驱逐 |
| Host FULL | 25.273/160.000 GB，15.80% |
| Host Mamba | 22.923/40.050 GB，57.23% |

相邻状态快照记录 FULL H2D ACK 173938 tokens，约 3.562GB，
Host 尚无淘汰。已服务公共前缀丢失代理为 14703/5047636 tokens，
约 0.291%；它不包含所有混合状态重算，不能替代实际重算率。
遥测 failed/dropped 均为零，writer_error 为 null。
这些都是启动阶段证据，不能外推为稳态、全程平均或 32 root 的结果；
它们已经证明当前 24/24 能制造真实 HBM 占用和迁移，不需要为制造
压力立即再增加 root。

## 后续扩容与对照规则

若 24 root 在主要 JOIN/工具等待阶段仍缺少有用 Host-only 目标、
持续迁移或 Host/HBM 容量压力，则直接升级至 32，必要时再到 36。
默认保持 running/decode24、固定容量及 seed21，记录真实项目数，
优先纳入更多独立且通过环境验证的项目。新增项目不足时，不把同
项目的不同任务当作独立长前缀。同步检查有用 Host 状态损失和重算，
避免只增加排队与反复迁移。

改变 root 数后重新采集对应配置的自然轨迹/native 参考，再执行
Full 与必要消融；省去的是额外全量 native 重放，不跨不同工作量
复用旧 JCT。`run_causal_comparison.py` 默认只选择 Full、JOIN-only，
报表带上自然 native 参考和相对变化，精确 token/因果/停止校验保留。

已核对 V133b 的原始重放校准记录：
`experiments/raw/qwen35_locobench_long_36root_running8_reviewed_position_20261023_v133b/native_matched/source_replay_calibration.json`。
native 重放/自然源的平均 JCT 比值为 0.9921719，约低 0.783%；
全程耗时比值为 0.9935081，约低 0.649%，校准 accepted=true。
这支持按用户要求省去重复全量 native 重放，但仍是旧 running8
配置的历史证据，不将其写成本轮 batch24 新测得的误差界。
报告保留未经误差修正的原始相对变化，引用的源 summary 和 native
启动策略均绑定 hash；非 native 策略采集的轨迹不能充当此参考。

后续流程的最终 CPU 资格为 V3188/V3189：保留原 5379 项，合计
5388 项及 2 项 subtests 全部通过，无失败或跳过；新增覆盖不重放
native 的真实 supervisor 流程、源策略校验、原始 JCT 长尾及报表
相对值。V3187 是来源策略加严前的中间报告，不用于最终源码资格。
这些修改已用于下一轮操作流程；正在运行的 V136 仍使用自己的
冻结源码与原登记文件，未中断或更换采集代码。

## 自然 Native 采集完成

北京时间 2026-10-24 01:05:11，V136 正常退出，24/24 workflow
自然结束且 JCT eligible；3951 个请求均完成，精确输出 958791
tokens，输入 264598879 tokens。全程耗时 1646.240 秒。
guard 干预、修复协议完成及请求错误均为零；独立任务正确性仍未评分。
精确 token 和因果图检查通过，源的 GPU 重放验证尚待执行。

共 74 次 spawn，43 次 JOIN satisfied，其中 root 委派 41 轮、
嵌套 JOIN 2 轮、嵌套 spawn 4 次；9 个 root 有多轮委派，
最多 5 轮。全部 root 的初始委派已发生，不强制复用或固定后续 DAG。

Host FULL 高水位为 103.608/160GB，Host Mamba 满池。
FULL H2D ACK 共 62846824 tokens，即 1287.103GB，含重复传输；
FULL D2H 为 112.884GB。同 session 已服务公共前缀丢失代理为
136945/257548954 tokens，约 0.0532%，不覆盖全部混合状态重算。
这些观测支持先保持 24 root 验证原有机制；它们不是预取净收益。

接续操作检查发现旧 `run_position_replay_calibration.py` 硬编码
36 root、balanced_join/80 JOIN、全量 native replay，并且 GPU
校验和对照未统一显式绑定源的静态设备池。现接入本轮预声明配置，
保留完整自然源与实际进程退出检查，移除本场景的旧 JOIN 数门槛。
GPU 校验和正式对照均使用 FULL1797968 / Mamba513，并沿用实际
Host census。采样组件校准覆盖 batch1、8、24；随后只启动 Full
和 JOIN-only。Full 的冷增量准备、工具预取1500ms优先窗口均从
同一配置读取，避免仅修改说明文档却仍执行旧环境。

最终接续代码的 CPU 资格为 V3191/V3192：保留原 5388 项，
共 5412 项及 2 项 subtests 全部通过，无失败或跳过。定向检查
214 项通过，覆盖实际 CLI 参数解析、24/32/36 完整人口、每组固定
池大小、配置策略以及 batch24 采样校准。
自然 native 参考均值 JCT 为 1164.156 秒，全程输出吞吐
582.413 tokens/s，原始统计保存在
`experiments/reports/v3193_v136_native_completed_20261024.json`；
不能将其当成 predictive 已取得的效果。

## GPU 校验及注册修复

V137 的 24 请求 GPU 校验通过，24 个请求的精确 token、会话续接及
原生 stop 均得到验证。实际 sampler 的 GPU 校准完成，覆盖1/8/24。
登记文件：
`/home/longhao/experiment/ops/v137_oneshot_batch24_position_check_20261024.registration.json`。
微校验不是全量 native 重放或完整 JCT 结果。

正式注册在使用尚未输出到自然 summary 的
`workload_manifest_sha256` 字段时失败，未启动 Full；
保留 V137 的 `stopped_for_investigation` 和原始日志。
本轮 summary 没有该字段，但 GPU 证据绑定的 V136 源登记文件、
其实际冻结清单以及 CPU 资格中的清单 hash 均为
`1204acfa790bb85f17e9de026282be5bdbf07ad374296e59750db6fa419dcd6a`。
修复后沿这条来源链严格核对任务、root 数、batch、profile、
源目录及资格 hash，同时若 summary 确实有 hash 则要求一致。
这避免用缺失的可选字段拒绝有效源，也继续拒绝换源或任务被修改。

V3198/V3199 保留原5412项，合计5419项及2项subtests全部通过；
V3197定向164项通过，并在V136实际数据上验证清单绑定。
V138重新注册时复用有效V137 GPU校验及采样校准，未重跑全量native。

## Native 恢复机会

V3194 全程审计将 43 个 JOIN 后继全部关联到首次 GPU 服务，
其中 42 个注册时有逻辑 Host FULL 命中，总计 96.591GB；
这是注册命中量，不能与实际迁移 ACK 字节或预取收益混用。
从 native 队列注册到首次服务 P50 为529.8ms，
API 收到到首次服务 P50 为922.0ms，包含排队、准入及前端成本。

V3196 的1377批H2D共1.501TB，CUDA stream累计46.448秒，
submit-to-ACK累计94.188秒。这些是组件计时，不是完整可隐藏等待。
87个依赖等待采样中，逐层等待P50为0.117ms、P90为3.810ms，
最大34.383ms；采样不能覆盖全部容量、准入及混合状态前置成本。
因此Full必须与同重放JOIN-only比较，不能仅靠迁移字节声称增量收益。

## V138 启动配置覆盖修复

V138 在加载完模型后的配置检查阶段停止，尚未执行正式重放：
实际 `idle_prepare_policy=all`，预声明要求 `cold`。
旧参考同时保存普通变量及 `BELIEFKV_` 变量，旧的带前缀值覆盖了
新配置中的普通变量。失败状态、退出和原始日志保留，不计性能。

配置覆盖现在先移除对应的旧别名，再应用新声明；同一声明内普通
变量与带前缀变量冲突时拒绝启动。新增的 CPU 预检会实际构造
NativeAdmissionRuntime 并核对策略字段，再允许加载 GPU 模型。
用 V138 的真实参考测试得到 Full=`cold/workflow/speculative/complete/
tool-prefetch1500ms`，JOIN-only=`cold/join_priority/工具预取关闭`。
V3202/V3203 保留5419项，5423项及2项subtests全部通过；
V3201定向168项通过。下一轮V139继续使用V136/V137证据。

## V139 正式对照已启动

北京时间 2026-10-24 01:45:21 注册 V139，冻结 app commit
`b92a1c242f1eba0694847dba1ef6e99655657b26` 和 engine tree
`cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8`，复用 V137 的有效
GPU 微校验及采样校准。登记文件：
`/home/longhao/experiment/ops/v139_oneshot_batch24_controls_20261024.registration.json`。
实际状态文件为同目录的
`v139_oneshot_batch24_controls_20261024.registration.state.json`。
顺序仅为 `full`、`join_priority_only`；native 使用 V136 自然采集参考，
没有再安排完整 native 重放。

01:49 核查时，watcher、server、scheduler 和 replay driver 的实际
PID/启动身份与登记一致；scheduler PID 900376 拥有实际 GPU context，
请求回执与服务事件持续推进。运行时实际确认
`idle_prepare_policy=cold`、`prefill_scheduling_policy=workflow`、
`join_prefetch_trigger=observed_eos`、
`tool_restore_mode=speculative` 和 `tool_prefetch_priority_ms=1500`。
普通 execution-frontier restore、短工具续接和 subagent retention 关闭。
running24、context262144、seed21 及四个池容量均与 V136 完全一致。
首批 7 个已完成请求的精确 token 检查通过；这是运行前缀证据，
不能视作完整工作量校验或性能收益。

维持用户最新规则：先完成 24 root/batch24 的这组对照。若主要
JOIN/工具等待阶段有用 Host-only 目标、迁移或 Host/HBM 压力不足，
则后续增加至 32，必要时 36；扩大 root 后采集对应自然源参考，
继续省略额外完整 native 重放。V136 已观测到明显恢复需求，
因此本轮不为追求迁移总字节而中途扩大并发。完整 JCT 保留正常长尾，
待 Full 与同重放 JOIN-only 均完成后再判断系统收益。

## V139 封存前缀与后续消融

V3204 在 01:53:17--01:53:18 封存独立、带 hash 的完整 JSONL 前缀。
其中 7 个 JOIN 在完成前出现正 FULL 计划，全部提前提交并由首次服务
确认复用，共 8.783GB FULL；另有 1.389GB 在完成后、客户端提交前
恢复，单列为完成后的恢复，不能并入提前量。该前缀没有这些 JOIN
后继的残余原生 FULL Host 命中；这不代表全程无遗漏。

V3205 使用同一前缀核对备份：489 个普通后台批次平均 21.8MB，
P50 为 6.55MB；25 个 spawn 批次平均 1.687GB，P50 为 2.104GB。
所有批次均有对应 ACK，没有触及字节或节点数上限。仍有 6 次容量
检查看到未备份冷页，当时最近 Host 样本仍有约 90--100GB FULL
余量；这些检查可以重复访问同一节点，不能相加为遗漏 KV 总量。
后续 ACK 累计已超过原先 10GB 迁移目标，当前无需扩大 root 数。
这仍然不是完整运行的提前复用量或系统收益结论。

V3206/V3208 对齐短 prefill 的分阶段采样。Full 前缀有 36 个短单请求
样本，完整自然 native 有 112 个；512-token 工作分桶仅匹配 14/21
样本，64-token 分桶仅匹配 3/4，16-token 分桶尚无共同样本。
宽分桶下 run-batch wall 比值为 0.958、thread CPU 比值为 1.024；
post-launch physical 的 Full 均值约 0.74ms，native 约 0.003ms。
尚未重现过去明显的 prefill 回撤，但相邻 batch 与服务混合不同，
严格匹配覆盖不足，不能据此宣布 prefill 或 JCT 优化成立。

为分离调度收益和 KV 管理增量，当前重放入口补齐已有的
`scheduler_only` 和 `kv_only`。前者保留 Full 的 workflow、
resident-first、阶段及 JOIN parent 排序，关闭 prepare、预取、
工具预测和生命周期回收；后者保留 KV 路径并使用原生请求顺序。
后续先接 scheduler-only，仍使用 V136 精确 token 和依赖，不另跑
完整 native。V139 继续使用自己的冻结代码，默认顺序不变。

V3209 定向 308 项检查通过。V3212/V3213 保留此前 5423 项，
合计 5435 项及 2 项 subtests 通过；V3210 初跑遗漏显式引擎和
tokenizer 路径的 78 项，已在 V3211 使用冻结引擎及实际 tokenizer
重新执行并通过，全程隐藏 CUDA。初跑另含一个不在原资格集合内的
可选 Docker progress 测试，其跳过记录保留，没有计入新资格。
V3214 又用 V139 真实配置构造 CPU runtime，确认 scheduler-only
为 `workflow/cold/tool-off`，kv-only 为 `native/cold/tool1500ms`。
这些是执行路径和配置资格，GPU 性能仍待后续实测。

V3215 在 02:09 左右再次封存运行前缀，已有 40 个完成边界明确的
JOIN，全部在完成前有正 FULL 计划；31 个提前提交并确认复用
24.281GB FULL，另 9 个尚无完成前提交记录，存在容量预算限制。
工具等待中有 34 个完成前正 FULL 计划、15 个提前提交，13 个
确认提前复用，共 2.699GB FULL。后续重点检查容量机会转化和工具
恢复后的驻留长尾，继续保持 24 root。该组 workflow 尚未全部完成，
仍不能用这些 episode 或迁移字节宣布 JCT/吞吐收益。

## V139 Full 完成与 V140 排队

V139 Full 正常退出，24/24 workflow、3951 个请求完成，输入
264598879、输出 958791 tokens 与 V136 一致。精确 token、原生停止、
HTTP session 生命周期及因果执行校验均通过，没有质量排除；
保留全部正常长尾。独立任务正确性仍未评分。完整结果及原始证据 hash
保存在 V3219：

| 指标 | V136 自然 native | V139 Full | 变化 |
| --- | ---: | ---: | ---: |
| 平均 workflow JCT | 1164.156s | 1144.890s | -1.655% |
| 全程耗时 | 1646.240s | 1621.194s | -1.521% |
| 全程输出吞吐 | 582.413 tokens/s | 591.410 tokens/s | +1.545% |

17 个 workflow 更快、7 个更慢。这是同一工作量的 Full 重放与对应
自然 native 参考的单次观测，没有新增 batch24 的 native 重放误差
校准；旧校准继续单列。小幅差异尚不能证明自定义 KV 管理增量，
更不能提前认定胜过 JOIN-only 或仅调度。

V3217 全程审计有 43 个完成前正 FULL 计划的 JOIN，33 个提前提交并
确认复用 24.409GB FULL；工具等待有 13 个确认提前复用，共 2.699GB。
10 个 JOIN 没有完成前提交记录，其中 9 个有完成前容量限制证据。
V3218 的 FULL 拒绝样本显示，原生预留后 FULL 预算确实为零；
本轮没有“仍有正 FULL 预算但首个完整 extent 过大”的拒绝证据。
工具恢复最后 ACK 到首次服务的 P90 约 27.8s，说明其机会与驻留
仍需继续逐事件核查，不能仅凭 ACK 字节认定成功。

V3220 普通后台备份共 1456 批、31.221GB，平均每批 21.44MB，
仅 2 批达到字节上限；spawn 备份共 32 批、42.692GB。
容量检查仍有未备份节点拒绝，不能通过单纯提高批次上限解决，
还需核对节点的真实冷状态和可释放条件。V3221 保留全程 prefill
采样，供与随后完成的 JOIN-only、scheduler-only 继续对齐。

Full 的进程清理回执确认无遗留进程后，V139 已启动 JOIN-only。
V140 scheduler-only 已注册并有实际存活的等待进程，依赖 V139 两组
均完成及 GPU 进程退出，不会在中间释放窗口抢跑：
`/home/longhao/experiment/ops/v140_oneshot_batch24_scheduler_only_20261024.registration.json`。
冻结 app 为 `b560d23c50423ad29f3e681094389093f75c5ef4`，其 runtime
与 Full 相同，新增的是消融入口与配置校验。容量、batch24、seed21、
token/依赖继续对齐 V136，没有再排完整 native。

V140 首次注册传入未暂存测试文件的冻结引擎，严格源码指纹检查拒绝；
没有启动 GPU 或生成成功登记。V3216 记录在独立 checkout 中用同一
canonical patch 重建原资格的文件状态后，源码状态 hash
`81229ec53afbc62865671c7730ff1b80df881b82dad65cc85a2c04529dfd4e87`
完全一致，引擎 tree 仍为 `cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8`。
未修改正在服务的引擎或原开发引擎，未放宽校验。随后注册成功。
