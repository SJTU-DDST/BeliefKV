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
