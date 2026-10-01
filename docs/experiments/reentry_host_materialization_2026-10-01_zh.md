# 安全输入状态与原生 Host 副本补齐

## 定位

v5 的 JOIN opportunity 有 3550 条 `session_has_no_cached_leaves`，
2138 条输入 checkpoint 不存在或已驻留，118 条 session anchor
快照拒绝。前一项不能当作 workload 没有缓存机会。

原生 prefill 已通过 `cache_unfinished_req` 捐出输入状态，但若 decode
没有跨过下一次追踪间隔，结束时有效 cache length 为零，插入结果
指向 root，覆盖之前的 request last_node；随后 session 注册直接
跳过 root。这丢失的是可复用输入的引用，不是物理数据本来不存在。

另两项缺口：输入末端对齐时，prefill 只追踪整个输入末端，未必
保留换行合并前的安全 checkpoint；write-back 的 Mamba 内部节点
压力驱逐会直接释放 device 状态，live session 也未必留下 Host 副本。

## 修改

Tagged prefill 用原生 intermediate-state tracker 追踪
`floor((input_tokens - 1) / checkpoint_grid) * checkpoint_grid`，
仅在该边界位于本批 extend 内且对齐时覆盖原追踪点。不新增 buffer，
不改变模型生成预算，未 tagged 请求沿用原行为。

结束插入得到空 root、而请求仍有真实 protected input 时，保留
此前的输入 anchor 再正常注册 session；没有真实 protected input
时不补造引用。

原生 Mamba allocation 压力驱逐前，查看至多四个实际 LRU 候选。
只对 unlocked、live-session、未备份的内部状态先做原生 D2H，
同步 ACK 后由原驱逐逻辑释放 device 状态。Host 不足不淘汰其他
有用数据来强行备份，保留原压力回退。不是所有 agent 都 PREPARE，
也不人为清空 HBM。Reactive 与 predictive 共用该原生规则。

逐请求 result 新增实际 session Mamba anchor 的安全前缀、device/
Host 存在性；Host 采样新增压力保留 attempt/ACK/decline 计数。
这些不是 enqueue 即收益，仍需 H2D ACK 和首次服务的复用证据。

## 保持阶段头稳定的工作头更新

复用原冻结 encoder 和阶段头，只更新 conditional work。比较
直接拟合剩余 token 与拟合总长度后减 observed output token；
模型形式只由 Astropy selector workflow 选择，Sphinx 不参与选择。
总长度形式在扣已生成量、加校准偏置后再非负截断，避免末段残余
预测存在不可消除的正数底座。

组合产物为
`experiments/models/child_semantic_work_frozen_phase_20261001_v1/`。
阶段参数、校准温度与阈值保持原值，工作头单独保存并校验 SHA256，
两个头共用一次文本编码。分类输出不因工作头更新改变。

校准集末段 token MAE：remaining 127.31、total 94.50，选择 total。
同快照隔离项目的 35 个自然 RETURN：旧工作头末段中位绝对误差
158.23，新头 110.00；P90 为 249.25 / 202.87。阶段分数保持原值，
不引入前轮滚动阶段头的额外误报。仍不是亚秒墙钟精度，也不是
新密封验证或预取收益证明。

## GPU 验证边界

先用新版代码和组合头完成一次单侧 native predictive 压力验证，
不紧跟没有有效机会的 reactive 重跑。保持 running=48、BF16、
同 NUMA 200 GB Host、HBM Mamba/FULL=0.9、Host 按 device 字节分配，
通知与收尾优先级不变、guard profile 不变，PREPARE_HOST 关闭。
Host 副本来自原生实际压力驱逐，不靠手工逐出或短 canary 制造。

本轮使用训练 manifest 首 64 个 root，目的是在修复后观察真实
压力与恢复机会，不作为可直接与旧 36-root 比较的吞吐实验。
系统故障需停止；正常情况下区分“状态存在”“Host 有副本”
“可发射 H2D”“实际 ACK”“首次复用”，不以 ACK 数取代收益。

## 首 64-root 单侧诊断

`qwen35_reentry_source_64root_20261001_v1` 使用提交 `d6c5474`，
64 个初始 root result 均登记了安全 Mamba 输入状态，旧的初始
session 空引用问题消失。62 completed、2 incomplete，无物理账本
或模型 worker 故障；两个 incomplete 的 root 终态为空，不是
guard 强制终止，失败现场保留，不能算完整自然 JOIN 训练标签。

本批预测 H2D 仍为零，原生 H2D service sample 也未建立。初始
JOIN 窗口的输入状态都在 GPU，没有可恢复 Host 目标；Host FULL
后期满池、Mamba 后期约 90% 使用主要发生在 JOIN 后的长工具阶段。
因此保留压力 attempt/ACK 都为零不是写入故障，而是原生实际
LRU 压力没有选中这些待 JOIN 的输入状态。

该批验证了状态和引用修复，但不能声称解决了有用预测 H2D，
不能因 Host 后期占用高而继续重复同配置或补无意义 baseline。

下一轮用同一 128-task manifest 的两波 64+64，第二波在 60 秒
到达并与第一波重叠，保持服务端 running=48 和其余池配置不变。
目的只是在 child 尚未 RETURN 的窗口内形成真实迁移压力；
不是人为逐出缓存、不是 throughput 对照。若只能形成极端
Host 丢弃或仍无待 JOIN 的 Host 目标，也须照实判定不适用。

## 两波 64+64 机制验证结果

`qwen35_reentry_source_64plus64_20261001_v1` 使用提交 `566175d`，
第二波在首波开始后 60 秒到达，同一 server、同一冷启动池。
3694.26 秒结束，114 completed、14 error；18514 条 native 请求
缓存观测、18265 次客户端 LLM 调用。客户端调用与原生请求统计
边界不同，不将差值当作请求丢失。GPU 平均利用率 81.38%，
worker 与物理账本未禁用，遥测 dropped/failed 均为零。

预测 H2D 从零恢复为 46 个原生完成 ACK、2635571200 字节，
涉及 27 个 parent context/epoch。45 个 action 有首次服务记录，
1 个在首次服务前被 censor：

| 证据 | 结果 |
| --- | --- |
| FULL 首次前缀复用 | 14 个 action，145817600 字节 |
| FULL 首次未复用 | 25 个 action，167034880 字节 |
| FULL 首次状态未知 | 6 个 action；这些 action 的 FULL 传输字节为零 |
| Mamba 同 node/device value 的单请求 COW 与 forward 完成 | 9 个 action，579502080 字节 |
| Mamba 首次服务尚无上述完整证明 | 1674117120 字节；不能全部记为浪费 |

各恢复目标的采样前缀与实际 parent 输入、cache namespace 对得上。
未复用仍可能来自恢复后再次驱逐、Hybrid 匹配截断或替代加载；
不能把“输入前缀正确”直接等同于“这次传输被消费”。
本轮 Host 来源是原生 FULL 叶 write-back。新增内部 Mamba
压力备份分支 attempt/ACK 仍为零，不能宣称该分支已经 GPU 验证。

46 次 tagged H2D 的 submit-to-ACK 中位数为 60.37 ms；
45 个首次服务记录的 ACK 到服务中位数为 10.49 秒，最大
202.10 秒。这一时间包括继续生成、JOIN 与准入排队，不是
child RETURN 预测误差，也不是同等长度的传输隐藏收益。
累计提前驻留上界约 42.96 GB·s；数据若中途驱逐，真实驻留成本
更低，不能将该上界作为精确占用积分。

47 次 latest-start 决策中，31 次早于 child 的 native EOS，
16 次位于 EOS 或之后。只有 9 次决策的估计剩余时间为正。
这分别是决策时间与生成终点的比较，不是 46 次 ACK 都在
RETURN 前完成的证明，更不是 JOIN 毫秒精度已经达到。

FULL Host 峰值 105.36 GB，Mamba Host 峰值 94.65 GB，两池均满。
累计驱逐 FULL 237.27 GB、Mamba 412.54 GB。全请求未缓存输入
17302514 / 397516629 token，约 4.35%；包含新输入，不能全部
视作 eviction 后重算。块级 FULL 重访观测到 2478 次 device hit、
4 次 Host hit、61 次重算，但存在 9076397 次有界前缀 probe
溢出，不能用这个子集声称全局重算仅有 61 次。

因此本轮只用于证明真实 Host 来源、H2D、ACK 与部分首次复用
已接通。没有同配置 reactive 对照，且 Host churn、长驻留和
错误较多，不将 128-root 直接定为低重算主场景，也不报告吞吐
提升。不继续重复已确认 JOIN 期间无 Host 目标的单波 64-root。

## 本轮后的执行修复

原阶段和 ticket 分别限两次 H2D，但语义阶段 2 秒过期后会重建，
重新从零计数。django-14631 同一 root context/epoch 发出了
6 次 H2D，导致重复搬运。现将预算绑定到 JOIN、workflow、
parent invocation/context/epoch、session/generation，跨阶段
失效重建、通知刷新及 confirmed reentry 共用。只在真实 native
issue 成功后计数，失败发射不消耗预算；request/attempt 更新
不会重置同一身份，新的 epoch/generation 独立计数。
预算清理随 JOIN timeout 或 workflow end，不在 JOIN satisfied
时提前重置以绕过 confirmed reentry 的上限。
不取消 workflow、不修改阶段分数、不长期 pin HBM。

django-11149 的模型输出带有 name=unknown、id=null 的工具调用，
ToolMessage 校验直接终止了 workflow。现于模型响应进入工具
节点前补齐缺失/空白/重复 ID，并记录规范化审计。保持原工具名
和参数；unknown 仍收到普通工具错误反馈，不执行虚构命令，
也不增加模型调用或强制终态格式。空响应重试后的结果使用
相同规范化路径。

11 个错误由脚本写死的 workflow 3600 秒 deadline 触发，另有
2 个 APITimeoutError。现恢复现有 14400 秒默认预算，可用
`ACTIVATION_WALL_CLOCK_SECONDS` 显式覆盖；2048 graph step 与
32-step 收尾保留不变。该修改不回填本轮结果或掩盖两条 HTTP
超时。本轮 14 个错误都保留原始现场，不作为完整自然轨迹拟合。

后续 probe 和 A/B 脚本默认使用 frozen-phase 组合产物，A/B
计划记录实际传入产物与 SHA256，避免脚本隐式退回旧工作头。

## 工作头与观测口径

本轮 139 个自然 RETURN 请求的首次阈值快照中位 token 绝对误差
104.78；133 个具有 EOS 前快照的请求，其最后 EOS 前快照的
中位绝对误差 81.57、中位有符号误差 +35.16 token。
这是开发负载在线诊断，不是独立封存集评估。

原“最后接收快照”多数已看到零剩余工作，中位绝对误差 54.11
不能用于声称生成结束前的预测精度。审计新增独立
`last_pre_native_result_snapshot`，只统计真正早于原生 EOS 的
快照。继续固定阶段头、encoder 和阈值，剩余工作头单独改进；
模型不拟合动作净收益，是否传输仍由 runtime 判断。

原始数据目录内持久化 `mechanism_summary.json`、
`online_forecast_audit.json` 与 `workspace_cleanup.json`。
已删除 114 个 completed、sandbox 清理完成且 patch 归档的
workspace，保留 14 个错误 workspace 和全部 trace/patch。

执行、传输、输入 checkpoint、遥测、组合模型、协议规范化和
统计脚本相关回归：337 passed、1 skipped。两条实验脚本
`bash -n` 与 staging patch reverse-check 均通过。
这些检查覆盖本轮后的代码修复；新增重复预算与 ID 规范化尚未
在另一轮 GPU workload 中验证，不冒称已改变本轮的浪费计数。
