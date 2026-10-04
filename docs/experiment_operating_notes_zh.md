# BeliefKV 实验注意事项

更新日期：2026-10-04。本文是当前实验的执行约束，不是新增 agent
guard、终态门禁或模型动作授权。启动前同时阅读
`docs/implementation_plan.md`；旧诊断脚本和历史计划不能覆盖当前约定。

## 场景与配置

2026-10-04 用户明确批准将下一轮改为 **64-root**、加强多轮
spawn prompt、启用选择性 JOIN parent PREPARE_HOST，并验证
JOIN 前 H2D 的端到端收益。下文 36-root 是之前的约定，不再是
本轮启动参数；不得自行改成 128 或重叠 64+64。

场景目标是存在真实可迁移状态与可用 HBM
空间、且有用 KV 丢弃后重算较少的负载。server running=48 是
请求执行上限，不是 48 个 root，也不是 36 个 root 的替代配置。

不得因预测 H2D 为零自行增加到 64、128 或重叠 64+64。先区分
checkpoint 不存在、session 引用丢失、Host 无副本、目标仍在 GPU、
缺物理空间、预测太早/太晚及传输发射故障。数据仍在 GPU 时，
不发 H2D 是正确行为，不得人为驱逐或清空 HBM 制造事件。
调整并发、到达方式、Host 大小或池比例须先说明并取得用户确认。
用户已批准的 64-root 变更见
`docs/experiments/join_prepare_h2d_64root_2026-10-04_zh.md`。

2026-10-01 的 64+64 仅为高压机制诊断。两池满、频繁 Host 驱逐
和预取后再次淘汰不符合当前主场景，不能将该批次的 ACK 或
完成速率当作 36-root 策略收益。

reactive/predictive 对照必须使用相同 task 集合、到达方式、
模型/采样参数、runtime prompt、通知、收尾优先级、deadline、
物理池和原生缓存规则。预测推理和预测动作是实验变量。
两侧重新冷启动，禁止将不同源码版本或不同配置结果拼成对照。
没有有效迁移机会时先报告原因，不重复同配置来积累无效 ACK。

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
