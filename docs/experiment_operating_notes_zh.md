# BeliefKV 实验注意事项

更新日期：2026-10-06。本文是当前实验的执行约束，不是新增 agent
guard、终态门禁或模型动作授权。启动前同时阅读
`docs/implementation_plan.md`；旧诊断脚本和历史计划不能覆盖当前约定。

## 场景与配置

2026-10-05 用户进一步批准下一轮 **84-root 单波到达**，
server running=48、Host 200 GB/NUMA node 1及池比例保持不变。
此前64-root配置仅作历史对照；不得自动扩到128或重叠64+64。
当前pair属于live压力探索，不因固定seed就声明轨迹相同。
开发阶段当前只做一对84-root；正式阶段再多轮配对取平均和报告
方差。固定需求GPU回放不作为主线或前置要求，见
`docs/experiments/pressure84_and_fair_comparison_2026-10-05_zh.md`。
v5已结束，没有新GPU实验队列。84-root仍是待修复评估的候选，
不能仅凭脚本默认值自动重跑或当作已冻结的理想正式负载。

场景目标是存在真实可迁移状态与可用 HBM
空间、且有用 KV 丢弃后重算较少的负载。server running=48 是
请求执行上限，不是 root 数，也不代替 root 并发配置。

不得因预测 H2D 为零继续增加已授权的并发或改变到达方式。先区分
checkpoint 不存在、session 引用丢失、Host 无副本、目标仍在 GPU、
缺物理空间、预测太早/太晚及传输发射故障。数据仍在 GPU 时，
不发 H2D 是正确行为，不得人为驱逐或清空 HBM 制造事件。
调整并发、到达方式、Host 大小或池比例须先说明并取得用户确认。
历史上2026-10-04从36-root改为64-root，同时加强多轮spawn
prompt并启用选择性JOIN parent PREPARE_HOST，见
`docs/experiments/join_prepare_h2d_64root_2026-10-04_zh.md`。
该64-root配置不是当前默认。

2026-10-01 的 64+64 仅为高压机制诊断。两池满、频繁 Host 驱逐
和预取后再次淘汰不符合当前主场景，不能将该批次的 ACK 或
完成速率当作当前策略收益。

reactive/predictive 对照必须使用相同 task 集合、到达方式、
模型/采样参数、runtime prompt、通知、收尾优先级、deadline、
物理池和原生缓存规则。预测推理和预测动作是实验变量。
两侧重新冷启动，禁止将不同源码版本或不同配置结果拼成对照。
没有有效迁移机会时先报告原因，不重复同配置来积累无效 ACK。

原始吞吐/JCT必须和实际工作量差异一起报告。固定seed甚至
temperature=0都不是逐token、工具结果或workflow路径相同的证明。
不要筛掉轨迹分歧任务，也不要把总耗时除以token数作为“公平”
JCT。正式live重复按run/pair统计，不把同轮共享资源的workflow
当作独立整轮吞吐重复；确定性内核变更必须两側共同验证。

## 长程任务预算

3600 秒 workflow deadline 于 2026-09-28 02:30:12 的 `f3700f4`
引入，当时 `run_qwen35_native_regime_probe.sh` 默认只有 8 root，
用于小规模原生缓存诊断。后续扩成完整 workload 时未同步调整，
是实验配置遗漏，不是模型、SGLang 或长程任务本身的一小时限制。
2026-10-01 20:09:11 的 `4ee4199` 恢复 14400 秒默认值。

当前完整实验显式使用 14400 秒（4 小时），可通过
`ACTIVATION_WALL_CLOCK_SECONDS` 覆盖。任务超过一小时可以正常，
不能按这一时长判定循环或错误。四小时仍是人为预算，达到该
预算要记为截断，不能视为自然完成或证明任务不可完成。

workflow deadline 是 root 与全部 descendants 共用的绝对墙钟
预算，排队、工具、总结及多轮执行均计入；不是每次 LLM 调用的
600 秒 timeout，也不是 sandbox 单条命令 timeout。
graph limit=2048，允许提前 32 步 FINALIZE，不得回退为 512。

workflow deadline、模型请求timeout和工具命令timeout是三个
边界。v4的django-16938两次全量测试各达到约600秒、返回137/
Killed，不能归为GPU等待或把它们当成工具自然完成。检查默认
工具上限及实际命令、超时反馈，不为了改善makespan而统一缩短
所有正常长工具。实验期间冻结配置，修复须后续同时用于两侧。
`python ... | tail ...` 的上游失败可能被末端退出0掩盖，
不能仅凭该退出码或“Command succeeded”判定测试通过。

分类错误必须检查 deadline audit，不能只看异常类。
64+64 批次的 14 个错误中，11 个在模型提交前报
`ActivationDeadlineExceeded`；django-10554 和 xarray-6721
分别只剩约 688 ms、154 ms 时提交请求，随后 deadline abort，
表现为 `APITimeoutError`。13 个均属 deadline 相关截断；
另一个 django-11149 是缺失 tool-call ID 的协议校验错误。
不能把前两条仅按异常名字归为独立网络故障。

## Runtime 与模型边界

完整实验使用 `--native-reactive-guard-profile`，不启用重复工具、
无进展或 soft budget 的语义干预，不添加短 canary 或 child
取消门禁。保留允许的 graph hard limit 收尾；显式的预算到期
和真实执行故障单独记录，不掩盖为模型自然 RETURN。

自然语言 child 回复是有效终态，不要求结构化 completion。
自然语言与真正空响应必须区分，尤其不能把拒绝工具后的空白
响应补成成功结果。空响应的 parsing/serving 问题不能直接归咎
于模型。工具 ID 可在响应边界规范化，但保留工具名/参数，
未知工具收到普通错误反馈，不执行虚构工具、不终止整个 workflow。

任务路径须与 sandbox `/workspace` 的实际仓库根一致，不能将
host checkout 路径提供给模型。模型重复读文件时先核对路径、
返回内容和 prompt，不能直接加强 guard。

允许 root 动态多轮 spawn，积极提示而不固定每轮 child 数。
不要为制造 JOIN 或迁移而伪造轮次。child 通知和有限收尾优先级
必须与 predictive 解耦，并在两侧启用，不用预测得分决定 child
是否可以 RETURN。

保持当前阶段 head、encoder 和阈值稳定，只独立优化条件剩余
工作 head。模型输出阶段置信度和剩余工作，不学习离线 trace
不可识别的预取净收益；Host 副本、容量、时机和收益由 runtime
判断，不修改 eligibility 元数据来绕过物理安全检查。

## 物理证据与统计

恢复目标必须是 parent 下一输入可复用的安全 checkpoint，
不能使用生成输出末端；FULL 前缀和 Mamba 状态须分别验证。
短 decode 的 finished insertion 为空时，不得覆盖已存在的
真实 prefill anchor。没有真实状态时不能补造引用。

原生 D2H 留下的有效 Host 副本也可以 predictive H2D，不要求
先消费 PREPARE_HOST。PREPARE 仅备份可能被卸载的有效状态，
不对全部 agent 无差别备份。预测 H2D 重试预算绑定同一 JOIN
与 parent context/epoch、session generation；阶段刷新或
request attempt 改变不能清零预算，也不能长期 pin 预取数据。

分别报告候选、native issue、ACK、首次服务、实际 FULL/Mamba
复用、censor 与未验证状态。ACK 不是收益，Host 副本存在也不是
数据已被消费。未验证 Mamba 不全部记为浪费，Host eviction
不全部记为有用 KV 丢失，uncached input 不全部记为重算。
块归因 probe 溢出时不能以观测子集推断全量重算率。

usage 与物理占用分开：SGLang 的 FULL/Mamba usage 会扣除
可驱逐缓存，物理 free-list 接近零时 usage 仍可能很低。
不能用日志 usage 推断池未填满，也不能用填满推断热 KV 满池。
2026-10-03 的 36-root 观测到 FULL usage 峰值 33.34%、
而 FULL/Mamba 物理占用曾接近/达到 100%。

冷启动时不能要求本轮先发生三次 H2D 才初始化其预测服务估计，
然后通过增加并发绕过这一依赖。缺实测服务模型时先核对已有
证据与初始化，不把被服务样本保护挡住的动作算成预测头失败；
有效输入目标仍驻留 GPU 时，即使估计就绪也不应发 H2D。

native EOS、child RETURN、JOIN satisfied、parent 首次 GPU
服务是不同边界。ACK 到首次服务不是 child RETURN 预测误差；
EOS 之后收到的快照不能声称生成结束前预测准确。
剩余工作头只用因果有效的已生成 token/正文/通知，
不混入未来排队等待；RETURN 时间与实际动作提前量分别评估。

内部 summarization 必须继承真实调用 agent 的 callback 祖先链
和 invocation scope，不能重新构建仅含 handler 的 callback 列表
使 child summary 回落到 root。检查 JOIN 未满足时 parent 是否
仍 WAIT_JOIN，不能用错误唤醒的 READY 状态解释为“无恢复目标”。
foreground 调用和 JOIN 是可同时存在的依赖，完成其一不能绕过
另一个。此项是因果/遥测正确性，不是额外 agent guard。
旧的机会采样只用于回放诊断，实际传输仍重新验证身份、副本及
当前物理容量；采样消失不自动判定为 Host 数据被驱逐。

亚秒级窗口必须同时检查正文采样间隔、推理队列年龄和 decode
进度回溯量。100 ms 正文快照须由已送达的新内容触发，不补造
结尾，也不改变 agent 工具/返回路径。只有双方 Linux boot、
time namespace 与 CLOCK_MONOTONIC 的域标识匹配，才直接使用
正文时刻之前的已完成 GPU 服务；未知时钟和旧 trace 仍保守
回溯 100 ms。不要为了增加短窗口命中而回填旧的时钟域证明。
短 token 区间概率与 RETURN 墙钟误差分别报告，无触发不算收益。

GPU利用率低时，先区分无请求/工具长尾、有请求但发射不足、
batch/context变化和实际kernel繁忙。NVML利用率是采样期间
kernel-active占比，不是SM occupancy。`gpu_service_sample`
记录scheduler/worker墙钟interval，包含非kernel开销，不得
累计后当作GPU kernel时间。完整makespan保留长尾，同时报告
completion curve及非空服务阶段；诊断分段不能替代正式指标。

工具等待模型必须在启动计划和运行时状态中明确记录为已加载，
不能仅因存在 tool_wait 代码就声称启用。事件时间预测与 legacy
admission 的动作资格分离，不修改旧产物的 eligibility 标志。
并行工具不能在首个 TOOL_END 时唤醒 agent；使用稳定 tool_run_id
跟踪，时间动作依然绑定真实等待、session/generation/epoch。

预测 load_queue 不能等后续 prefill 才启动并把延迟算成预测误差。
提前 flush 必须保留原生 stream fence 与 layer producer event，
不能抢原生 prefill 的 consumer index。服务估计须区分池形态、
相近大小、enqueue 排队与 submit-to-ACK，不能线性放大固定开销。
窗口现为 1000 ms，但必须仍按真实 submit 到 RETURN/TOOL_END
审计；未来未生成的 token、first GPU service 均不能替代返回标签。

v5的工具路径证实，同一target可同时被P50倒计时判为即将恢复，
又被条件CDF判为长等待victim，导致ACK后再次pressure parking。
后续须统一时间/驻留口径与短租约，而非增加独立模型门禁。
EOS后、RETURN前的提前H2D是合法机制证据，但不等于语义头
在生成结束前已经预测准确。Host归因overflow限制重算结论，
不应被扩展为拒收整轮逐请求/child事件数据的额外门禁。

## 版本与文件

启动前提交代码，持久化实际 commit、源码 patch、模型/工作头、
manifest 的 SHA256，以及 root/到达、deadline、guard profile、
NUMA 和实际容量配置。检查最终命令和有效环境，不只看脚本默认值。
同一实验内冻结 prompt、runtime 和模型，修复后另建目录，不能
改写历史配置或回填已结束结果。

每轮结束后只删除正常完成、sandbox 已清理、patch 已归档且
artifact 无错误的旧 workspace。保留 trace、patch、配置、模型
以及失败现场。检查磁盘余量，不以扩大并发或删除在用数据解决
磁盘不足。GPU 实验进行中持续区分系统故障与正常长任务，
真实系统故障及时停止并修复，不以吞掉异常“提高完成率”。

## 最近清理记录

2026-10-06删除24个已被后续导出替代的JSONL及373个已完成、
sandbox已清理且patch已归档的旧workspace，回收约83.7 GiB，
磁盘可用空间由约25 GiB增至108 GiB。逐项清单见
`experiments/analysis/old_artifact_cleanup_20261006_result.json`。
旧v9/native-transfer导出目录只保留manifest及清理标记，不再是
完整dataset；不能仅凭manifest存在就重新选为训练源。

保留当前模型引用的 `dataset_failed_repeat_v10`、所有原始
trace/result/patch、失败及近期workspace、v3/v4/v5证据、模型、
processed标签、环境依赖和Docker镜像。在用模型文件哈希及443个
运行源码文件指纹均未变化。
清理使用低I/O优先级，发生于v5 predictive侧的01:01:59至
01:02:21（Asia/Shanghai）；它是已记录的背景I/O事件，不改写
原始性能指标，不据此新增数据资格门禁。
