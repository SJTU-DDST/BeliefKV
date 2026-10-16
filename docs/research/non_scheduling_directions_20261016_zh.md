# 动态多 Agent 的系统层研究筛选与最新 Trace 证据

更新：2026-10-16 13:35 PDT。研究筛选记录，尚未选定新论文贡献。

## 1. 本次调整

最新用户约束：重点是 serving/runtime 系统，目标投稿系统领域会议。当前目标是
在相同 agent 任务和行为下，通过系统执行、物理缓存/状态和请求处理取得收益。
SPAWN/JOIN、工具开始/结束及会话续接可以提供系统信息，但不能靠改变模型决策、
摘要内容、输出上限或有效任务量证明系统性能。应用层工程修复应匹配放进各对照臂。

V1133 的 qwen_prefix 摘要原型保留在独立 worktree，默认关闭。它对 68 个历史
root 首次摘要证明了实际输入 token 前缀和 checkpoint 位置条件，不证明物理驻留、
cache hit、任务质量或 GPU/JCT 收益。262 项 CPU 检查通过、1 项 Docker 检查未运行。
这个原型降为工程对照，暂不安排 GPU；下文摘要格式/证据反馈方向是此前的候选分析，
不再是已选择的论文主线。当前也不承诺另一套未经证实的三机制。

撤下“动态执行准入、完整状态预算、父子容量交接”作为既定研究主线的地位。
FULL 有余量而 Mamba 缺槽首先是池配置/容量问题，不能把它包装成新研究问题。
“减少无法立即消费的状态”未被证明是具有足够规模的独立问题，不继续据此扩展控制器。
既有实现、CPU 验证和研究草稿保留为历史候选。

V74 在等待 V73 时已取消，未启动任何 GPU 实验；冻结计划、候选、引擎和 helper
均保留。V73 两侧现已结束且进程退出，源码、引擎和原始结果继续冻结。取消凭据：
`experiments/reports/v74_cancelled_before_gpu_20261016.json`。

总目标仍是匹配资源、有效任务和质量下优于 native 的平均 workflow JCT 与有效
完成吞吐。V75 已开始批量 write-back 工程验证，核心论文机制尚未选定。

研究范围进一步明确：harness 提供已有的 SPAWN/JOIN、工具和会话身份事件，
主实现及待验证机制放在 serving/runtime 的执行、数据布局、缓存和传输路径。
新设计必须指出实际少做了哪些计算、搬运或同步，以及多 agent 工作流信息
为何比普通请求信息更有用；普通工程修复与 workflow 信息的额外收益分别验收。

## 2. 数据与口径

主证据是完整 V73 predictive 的 156 个 workflow，154 completed、2 incomplete；
任务正确率尚未独立评分。两个 incomplete 保留，不加 token 上限或重标成功。
V1125 同时读取的 native 前缀在当时仅有 101 个终态，不能用该旧前缀总量与完整
predictive 宣称性能差异。V1134 的新系统审计已覆盖 native 完整服务事件。
最终两侧各 154 completed/2 incomplete；native/predictive 客户端总时长
7125.628/8201.434 秒，全部 156 项原始 workflow duration 均值为
1905.792/1912.261 秒。前者是完整运行时长，后者不是仅成功任务的 JCT，也不是
质量验收；两侧任务正确率尚未独立评分。本轮不能宣布系统取得稳定收益。

- `experiments/reports/v1125_v73_predictive_agent_work_20261016.json`
- `experiments/reports/v1125_v73_native_agent_work_prefix_20261016.json`
- `experiments/reports/v1122_v73_predictive_context_summary_20261016.json`
- `experiments/reports/v1125_redundancy_auditor_cpu_20261016.xml`

只读审计器为开发分支 `scripts/audit_agent_work_redundancy.py`。每个输入保存实际
读取字节数及 SHA256，活动 JSONL 只读观察长度内的完整行。8 项 CPU 检查覆盖
并发工具身份、完成前不可命中、工作区 epoch、部分行、摘要归因和 batch geometry。
不新增 GPU 探针、不改变客户端、模型、工具或引擎。

## 3. 最值得关注的实际成本

| 项目 | 完整 V73 predictive 观察 | 含义与限制 |
| --- | --- | --- |
| 内部摘要 | 123 个 workflow、693 次；请求墙钟 P50/P90 为 27.88/46.49 秒 | 包含排队、生成和传输，693 次时长之和不是端到端收益 |
| 摘要输入计算 | 14,933,804 个 native prefill extend token，占 33.58% | 已由实际 batch geometry 核对，不只是提示词字符数 |
| 摘要后首个正式请求 | 6,570,836 个 extend token，占 14.78% | 其中有必要的新输入；不能全称为无用重算 |
| 摘要与后继请求合计 | 21,504,640 / 44,470,352 个 extend token，即 48.36% | 是成本集中区，不是可实现的加速百分比 |
| 摘要输出 | 640,523 / 8,156,973 token，即 7.85% | 已关闭 thinking 的专用摘要请求仍有这部分生成 |
| root 同结果重复 | 92 个 workflow 中 2,432 次，运行时观察到相同 workspace epoch | 不是全环境等价证明，不能据此直接安全缓存 shell |
| 发起这些重复的模型请求 | 2,418 次、398,377 输出 token，占全部输出 4.88% | 工具结果缓存本身省不掉这些已经发生的生成 |
| 同 agent 相同参数重复 | 12,793 次，涉及 152 个 workflow | 期间可能修改状态，包含写工具，不是缓存命中数 |
| 不同 agent 相同参数 | 2,407 次，所涉工具墙钟合计约 307.60 秒 | 有些是通知或写工具，且并行重叠，不构成较大工具缓存收益的证明 |

实际 prefill 核对覆盖 40,688 个批次，所有保留样本都有已知 extend geometry。
mixed prefill 若含 decode 行，其一 token 也按原生 geometry 记录；不按 token 比例
分配 GPU 时间。两类摘要相关 token 总量与登记处 uncached token 核算吻合。

当前 native 前缀也观察到摘要占已记录 extend token 的约 29.72%，摘要后首请求
另占约 12.65%。这说明上下文成本不是只有 predictive 新控制器才存在的现象；
它不是两侧完整性能或质量对比。前缀有 3,587 个 token 尚不能匹配已封存客户端
身份，保留 unknown，不能填零或强行归因。

## 4. 系统侧最新测量及已有能力

V1134 新审计器只读完整行、保存实际字节数和 SHA256，不新增服务探针或 GPU 任务。
7 项 CPU 检查覆盖身份去重、未知/非法采时、两级缓存核算、重叠区间、完整行截断、
小对齐差距与首次/续接请求分组。结果：

- `experiments/reports/v1134_v73_native_system_costs_prefix_20261016.json`
- `experiments/reports/v1134_v73_predictive_system_costs_20261016.json`
- `experiments/reports/v1134_native_system_costs_cpu_20261016.xml`

native 报告名中的 prefix 指固定文件观察范围；本次已在 native 退出后核验它覆盖
全部已写服务事件，不能与 V1125 的未完成客户端前缀混淆。审计器位于开发 worktree
`scripts/audit_native_system_costs.py`，不改变 V73 的源码、引擎或 helper。

| 系统观察 | native | predictive | 解释 |
| --- | ---: | ---: | --- |
| 实际出现原生首次服务事件的请求 | 37,612 | 40,186 | 未服务/未进入该事件的请求不在分母 |
| 对话转换 P50/P90 | 51.39/75.71ms | 53.42/78.74ms | 包含模板处理及其内部 tokenization，不是独占 CPU |
| 对话转换区间累计 | 1937.44s | 2158.30s | 与 GPU/其他请求可重叠，不能从运行时长扣除 |
| HTTP 收到至原生入队 P50 | 84.54ms | 87.75ms | 不含原生排队至服务，不能称 H2D 等待 |
| 完整输入 token P50 | 20,943 | 21,009.5 | 前端处理的整段输入 |
| 原生 uncached 输入 token P50 | 379 | 376 | 与完整输入的两个中位数不能逐请求相减 |
| FULL 匹配超出实际两级复用边界的正差额 | 2,991,043 token | 3,233,014 token | 占 uncached token 7.26%/7.27%，不是提速百分比 |
| 正差额 P50/P90 | 36/60 token | 35/60 token | 大部分是小差距，不能全部解释成大块状态丢失 |
| 差额至少 512 token | 775 请求/2,422,421 token | 797 请求/2,619,981 token | 大差距须单独核查来源和实际计算时间 |
| 续接请求中差额至少 512 token | 347 请求/1,271,924 token | 399 请求/1,534,106 token | 首次出现与后续出现按 context 分列，含 compaction 后续接 |

FULL 匹配与可复用混合状态边界不同是引擎确有的行为：
`unified_cache/components/mamba.py::finalize_match_result_in_tree_core`
从 FULL 最深匹配计算新 branching checkpoint 目标，而实际请求从具有必要状态的
有效边界续算。目标位置不等于已有可恢复状态；checkpoint 对齐、历史改写、数据
驻留和缓存保护均须检查，不能只由差额判定是 Mamba 驱逐或策略缺陷。
Marconi [R6] 已研究混合模型精确状态命中、checkpoint admission 与按计算节省/
内存占用决定 eviction；“混合缓存有不同边界”或“增加 checkpoint”不单列为新意。

前端全量处理是具体系统对象，但单独优化的端到端价值尚未成立。native 转换
累计平均每个 workflow 约 12.42 秒，相对全部原始 workflow duration 之和约
0.65%；这是工作量的量级比较，不是全局收益上界，因为请求相互排队和并行。
同样，1937 秒也不能解释成 GPU 被它阻塞了 1937 秒。须进一步测它是否真正限制
请求供给、关键串行续接或与 GPU 并发时的 CPU 路径，才决定投入。

本地冻结引擎已具备两种容易混淆的能力：

1. 当前应用的 top-level `session_id` 配合 `enable_session_radix_cache` 提供 radix
   引用生命周期与 eviction 偏好；`handle_generate_request` 仍按本次完整输入
   构造普通请求，没有自动拼接历史。
2. 原生 `Session.create_req` 已有追加、分叉和 replace/offset 路径；streaming
   session 可在连续轮次保留 KV/状态。追加路径和当前 radix session 不是同一种
   接口，但“支持有状态会话”本身已存在，不能当作本项目创新。

此外，TokTier [R7] 已有保持参考 token IDs 一致的 session 增量 tokenization，
Delta Tokenizer [R8] 已有精确增量、CPU chunk 并行和在 SGLang 的评估。
要比较的是它们在实际 Qwen chat template、历史改写、分叉与当前负载中的适用性，
不能因为主干没有直接启用便声称此前未解决。未经匹配测试，不断言它们无法支持
某种动态 workflow。前端增量化目前作为性能候选/工程对照，未选为论文贡献。

### 4.1 V1135：大差距主要发生在首次/压缩后输入

完整服务记录与客户端身份、压缩事件逐请求连接，全部 37,612/40,186 个原生
请求均匹配到客户端身份。实际 batch geometry 分别核算出 41,220,157/
44,470,352 个 extend token，与输入计数一致；FULL 差额对应的 extend 位置
分别为 2,991,043/3,233,014 token。没有按 token 比例分配 GPU 时间。

| 至少 512 token 的差额来源 | native 请求/差额 token | predictive 请求/差额 token |
| --- | ---: | ---: |
| root 首次请求 | 110 / 417,725 | 106 / 409,688 |
| child 首次请求 | 318 / 732,772 | 292 / 676,187 |
| root 压缩后首请求 | 76 / 440,734 | 111 / 646,919 |
| child 压缩后首请求 | 242 / 793,912 | 259 / 853,903 |
| root 普通续接 | 10 / 15,706 | 11 / 9,717 |
| child 普通续接 | 19 / 21,572 | 18 / 23,567 |

native 775 个大差额中，746 个发生在首次或压缩后输入；普通续接仅 29 个、
37,278 token。不能据此把长期 agent 状态频繁丢失当作主要瓶颈。首次/压缩后
仍可能有公共前缀和缺失 recurrent checkpoint，应先与 Marconi 类 admission
比较，不能由差额直接判定可以跳过计算。

原生同 session 的旧输入前缀缺失代理合计 1,161,267 token，不是逐层实际 FLOPs。
Host FULL 驱逐后被请求归因为重算的仅 5,527 token；这是已观测 HMAC 精确前缀
匹配的驱逐记录范围，不是所有形式重算的总量。缺旧输入探针时保持 unknown，
首次/新 session 的样本不填零。

### 4.2 V1135/V1136：逐叶 write-back 的工程候选

native 实际配置是 `hicache_write_policy=write_back`。当前 FULL 驱逐路径对
每个未备份叶节点执行 backup、阻塞式 writing_check、再 demote。controller
已有 CacheOperation.merge_ops，但这里每次 write 立即 start_writing 并等待 ACK。

| native D2H | FULL-only | FULL+Mamba |
| --- | ---: | ---: |
| payload 批次 | 105,872 | 19,286 |
| 每批节点数 | 全部 1 | 全部 1 |
| FULL token P50 | 172 | 127 |
| 总字节，十进制 | 1.122 TB | 1.399 TB |
| transfer-stream 区间累计 | 305.407s | 79.468s |
| submit-to-ACK 累计 | 474.456s | 196.258s |

合计 125,158 批，FULL-only 中位量约 3.52 MB，不是 agent 整段大小。
两类时长均不能从 JCT 直接扣除；尚无逐次 scheduler 阻塞或 GPU 暴露空闲的
完整计时。同锚点反复 D2H 的节点有 7,021 个，但节点可 split、锚点又可覆盖
其他页，不能把重复锚点字节全认作相同数据或把未关联 H2D 的备份全认作浪费。

V1135 证据：

- `experiments/reports/v1135_v73_native_hybrid_reuse_20261016.json`
- `experiments/reports/v1135_v73_predictive_hybrid_reuse_20261016.json`
- `experiments/reports/v1135_native_hybrid_reuse_cpu_20261016.xml`：14 项 CPU 检查。

独立候选 `.worktrees/sglang-v1136-batched-writeback-20261016` 从 V73 完整引擎树
`791dd37f412514eaab6dbfeb08d18b18a6fd97d3` 建立 baseline snapshot
`d65ad382f4862b27dce55d67b9465ce002f96212`，仅改 3 个生产文件。
V73 原工作区/index 树及注册源码未更改。候选仅批量提交当前 FULL 分配需要的
连续原生设备驱逐节点：

1. 沿叶向上；若其他原生 heap victim 排在父节点之前，结束批次。多个设备 child、
   活跃/Host 锁及在途传输也结束链，不跨共享分支回收。
2. 按实际 FULL 缺口及节点预算止步。批内可使用原生 Host 回收；已排入节点沿用
   原生 pending 保护，不以“必须有空闲 Host”阻止高压下的有效合并。
3. 保留必要 Mamba 和原生 controller 合并；整批 ACK 后再释放。部分成功或后续
   入队异常会先完成已入队部分，未完成 DMA 不计可用容量。
4. 将原生游标推进至最后实际释放的节点。未知 planner、SWA/其他组件、buffer
   mode 保持原路径；Host 分配失败继续既有回收/失败处理。

`SGLANG_HICACHE_WRITE_BACK_BATCH_NODES` 默认 1，保留单节点路径；CPU 夹具用
32，尚未为 GPU 选值。开关与 predictive 解耦。设备 FULL victim 顺序受约束，
Host 回收结果与 pending 占用仍可能变化，须测命中、重算和完成率，不能只看批次数。

候选 90 项 CPU 测试及 27 个 subtest 通过。真实 CPU cache/tree 接线使用原生
CacheOperation 合并和模拟 DMA：相同 3 节点由 3 次提交合为 1 次，释放容量、
顺序、Host 索引对应数据及 ACK 前源页一致；另验证原生 pending 回收保护。
这些不验证真实 GPU DMA、PCIe 时延、吞吐或质量。最初未固定 PYTHONPATH 的
5 项失败来自旧安装包，原记录保留；最终显式固定并断言候选 Python import 根。

- `experiments/reports/v1136_batched_writeback_candidate_20261016.json`
- `experiments/reports/v1136_batched_writeback_cpu_20261016.xml`
- `experiments/reports/v1136_batched_writeback_installed_engine_failure_20261016.xml`

这是共用路径的工程候选，**不是已成立的论文创新**。批处理/同步合并有已有方法，
本地 controller 也已有合并 API。后续先测独立 GPU 效果，把有效共用修复匹配
放进 native/新策略，再判断动态 workflow 信息的额外系统收益。未登记新 GPU；
研究问题、新颖性、质量和端到端验收仍开放。

### 4.3 V75：同配置 predictive/native 实机验证已启动

V1137/V1138 在新的应用/引擎中增加低频批量计数，包括实际上限、尝试次数、
无连续原生节点、计划节点数、入队失败及成功合并。它们仅随既有每秒 Host
采样和状态文件发布，没有逐 decode 日志。应用测试 140 项、引擎 90 项及
27 个 subtest 通过，绑定记录 V1139；先前 V1136 原型和 CPU 记录保留。

V75 于 2026-10-16 08:15:55 PDT 冷启动，08:18:46 已核验真实 predictive GPU
服务、源码/引擎、实际 batch=32、相同到达表与物理池容量。应用 commit
`2ad32d097dae169ff437ecb9f66098a2dc6ffea4`，引擎完整树
`53020ce3efef3ed362e9af0db77147b39070f064`，二者及已登记新 helper 冻结。

156 个任务和顺序、Poisson 均值15秒/root、seed21、running48、context131072、
output8192、工具180s、activation14400s、Host200GB80:20、HBM Mamba/FULL0.9、
prefill4096 均沿用 V73；模型、prompt、harness、预测头和策略参数不改变。
predictive 后 native，两侧都 cap=32，开发仅一轮，不扫率/改顺序或重复。
Host 继续 preferred NUMA；无 qwen_prefix、V74 或新的 guard/canary。

- 资格：`experiments/reports/v1139_batched_writeback_gpu_candidate_20261016.json`
- 登记/启动：V1140/V1141；实际 predictive 启动核验：V1142；placement：V1144。
- 状态：`ops/v75_batched_writeback_20261016.state.json`
- 结果目录：`experiments/raw/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback`

早期已有 D2H，但 FULL 合并的 attempts=0。这只能证明当时没有进入此合并路径，
不能把所有 D2H 当作 FULL 分配触发，也不能称候选已合并或发生传输失败。
继续按真实回收来源、实际容量和批次证据判断；active token usage 也不等于
allocator free list。吞吐、JCT、重算、Host 回收与公平性仍未验收。

V1149 随后的密封片段已确认真实合并：394 次尝试中成功 202 批、865 个节点，
共回收 359,210 个 FULL token；192 次无连续原生节点，入队失败和部分入队均为 0。
同一读取片段的传输日志有 202 个不含预测标记的多节点 D2H ACK、合计 865 个
节点，与上述内部合并数一致；这些多节点 payload 不再仅是 CPU 模拟或开关状态。
完整行范围、SHA256、状态快照、首批 ACK 和当时实际存活 PID 身份保存在
`experiments/reports/v1149_v75_early_gpu_coalescing_20261016.json`。
这是机制执行证据，不能据此把所有字节算成有用、推导净吞吐或质量/JCT 改善。

V73 两侧质量输入各 156 项已导出，原始两项 incomplete 均保留；输入核对
任务集合、patch 和来源哈希，尚未执行官方评测，也未启动额外评测容器：

- `experiments/reports/v1146_v73_native_quality_inputs_20261016`
- `experiments/reports/v1147_v73_predictive_quality_inputs_20261016`
- `experiments/reports/v1148_v73_quality_inputs_basis_20261016.json`

### 4.4 V75：已封闭窗口的系统路径核对

V1150/V1154 复用既有 batch-geometry 分析器，读取 V75 predictive 与历史
V73 native；V1151 保留 V73 predictive 的相同窗口。原点为各臂 GPU monitor
首行，均按 America/Los_Angeles 解析，已覆盖 `[600,900)` 与 `[900,1200)` 秒。
这不是相同模型轨迹的隔离消融；V75 native 尚未运行，历史 native 缺少共用合并
改进，不能用其宣称最终收益。

| 600–900s 已观测项目 | V75 predictive | V73 predictive | V73 native |
| --- | ---: | ---: | ---: |
| 未带预测标记的 FULL-only D2H 批次/节点 | 985/2,937 | 3,884/3,884 | 3,009/3,009 |
| 上述数据量 | 25.994 GB | 29.542 GB | 36.900 GB |
| 全部 FULL-only D2H transfer-stream 累计 | 8.645s | 9.692s | 10.826s |
| 已埋点 Python self 墙钟累计 | 17.024s | 19.513s | 未采集同口径 |
| 实际 decode 请求步数 | 354,358 | 373,210 | 376,582 |
| 同会话旧输入缺失代理 token | 70,083 | 80,665 | 70,283 |

stream 行包括带预测标记的 FULL-only D2H，不能与上一行未标记 bytes 混成带宽。
低频快照分别记录成功合并 509 批/2,464 节点与 536 批/2,910 节点，入队失败及
部分入队均为 0；第一个窗口实际未标记多节点 ACK 为 507 批。快照与 ACK
选窗边界不同，不要求逐窗口相等。轨迹和流量不同，批次数下降不是隔离提速。

满 48 请求、相近上下文的 decode worker 均值比例 V75/V73 native 在两个窗口为
0.993–1.022 与 0.986–0.994；单请求且 actual extend<512 的短 prefill 分桶比例为
1.19–2.14 与 0.76–1.18。分桶仍有不同实际 token 与邻接 batch，不能将第一个
窗口推广成全程固定差距，或把第二个窗口当成因果加速。
V1152/V1153/V1158 配对现有 prefill phase 样本；第二窗口 V75/native 的短
prefill 主线程 run_batch CPU 均值为 39.57/33.23ms（79/73 样本），CUDA event
包围区间为 31.33/25.92ms。event 包含发射间隙、依赖和可能的并发工作，不是
纯 kernel 时间。所配样本未记录层恢复等待，不证明全路径等待为零，也不能将
差额直接归给某个函数。V75 第二窗口 Python self 累计仍为 22.191 秒。

Host FULL 占用采样 P50 为 20.44%/37.43%，Mamba 满池；FULL Host 回收建堆
增量为 0。Mamba 触发 Host 回收分别释放 125,879/148,876 个 FULL token。
该现象先归池配置和原生回收诊断，不恢复完整状态预算研究主张。旧输入缺失
代理不是逐层重算；缺少 FULL reaccess 归因保持 unknown，不能填成总重算为零。

补充只读分析器为开发 worktree 的 `scripts/audit_transfer_control_window.py`：
复用完整行封存和 pool 字节核对，记录输入/代码 SHA256、窗口及快照滞后。
3 项 CPU 检查覆盖乱序快照、边界、覆盖不足/计数重置和两种重算口径。
V1155/V1156 为第一窗口传输/Host/重算报告，V1157 为第二窗口，均在
`experiments/reports/`。未修改 V75 应用、引擎、helper 或 GPU 探针。
合并真实发生，净收益仍未成立；接下来先核对短请求执行组合中的 CPU 发射、
状态复制/恢复依赖和控制成本，再选择可证明已有方法差异的系统机制。

### 4.5 V76：无预取、保留备份与工作流运行时的独立候选

按最新讨论，下一轮先隔离预取及其专用预测/观测成本。候选
`.worktrees/beliefkv-v1160-no-prefetch-20261016` 已冻结为 `b2adf481`，从 V75 的
`2ad32d0` 派生；物理引擎与 canonical patch 均未改变，仍为树 `53020ce3`、
批量 write-back 上限 32。当前 GPU 仍运行 V75，不热改或中断它的对照。

| 路径 | V76 reactive 配置 |
| --- | --- |
| JOIN/tool 完成前的预测 H2D | 关闭 |
| child 内容预测 worker、正文内容上送 | 关闭 |
| H2D 机会枚举和诊断扫描 | 关闭 |
| 工具时长模型 | 保留，供长等待 PREPARE/卸载判断 |
| PREPARE_HOST、冷页候选、终态 child 回收 | 保留 |
| 因果/驻留优先和有界收尾/parent 优先 | 保留 |
| 原生需求 H2D、引用保护和逐层恢复依赖 | 保留 |

已关闭的预取不再调用 JOIN/tool 预取分发器；仍有在途 ticket 时保留原处理入口。
PREPARE 的选择/ACK/消费归因和运行时状态继续记录。新的
`BELIEFKV_H2D_OPPORTUNITY_SAMPLING=0` 只去掉枚举，不关闭整个观测通道。
原生恢复使用原生引用和锁，已有 HBM 驻留准入不依赖预取租约；没有额外构造
一套未验证的需求恢复租约。关闭不能用 `PREFETCH_LEAD_MS=0` 代替。

原 reactive 启动会继续上送正文，候选已修正。实际启动环境检查将 GPU launcher
替换成环境捕获进程，执行真实 shell/env 配置，验证 reactive 保留工具模型/
PREPARE/驻留而移除语义模型，predictive 保持原配置，native 清除继承的语义配置；
没有启动模型或 GPU canary。340 项回归及 3 项新增环境检查通过，去重 343 项。
最初新增用例字段名拼写失败保留在 V1160 XML，不混作成功；资格为 V1164。

V76 于 09:09 PDT 已登记并启动等待进程 `3035460/start_ticks633722974`。
等待 V75 supervisor/driver 的实际身份退出、完整 native 后处理结束且资源可用，
再自动启动单臂 reactive；不追加新 native。使用同 156 项任务/顺序、Poisson
均值15秒/root、seed21 和到达表摘要 `00e1d626…`，其余模型/应用/预算保持匹配。
参考的是 V75 中同样启用 batch=32 的 native；单次历史对比仍不是正式配对重复。

- 资格：`experiments/reports/v1164_no_prefetch_candidate_20261016.json`
- 登记：`experiments/reports/v1165_v76_no_prefetch_registration_20261016.json`
- 权威状态：`ops/v76_no_prefetch_20261016.state.json`
- 结果：`experiments/raw/qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch`

V1163/V1167/V1168 另封存 V75 的 2400–2700s 窗口：满48 decode 分桶的 worker
均值比例 V75/V73 native 为 0.981–1.003，实际 decode 请求步数为
358,173/333,265；短 prefill 分桶比例仍为 1.009–1.514。Python self 累计
23.347s，其中 H2D 机会扫描 2.391s。V75/native 短 prefill phase 样本主线程
selection CPU 均值为 16.28/28.99ms，run_batch 为 39.15/34.00ms；说明不能
将全部差额归给新增准入。传输/回收量与任务轨迹不同，不能由该窗口隔离合并、
终态回收或预取的因果收益。V76 尚无 GPU 服务、吞吐/JCT 或质量结果。
该候选是选择系统主机制所需的工程消融，不因“去掉预取”就宣称论文创新成立。

### 4.6 V75：终态回收与普通 checkpoint 失效必须分开

V1171 在 09:37:56 PDT 完成只读前缀审计，覆盖机会日志的前 957,692,155 字节
和 156 份客户端完整行前缀；各输入均记录实际读取长度与 SHA256。分析器为
`scripts/audit_terminal_cache_lifetime.py`，复用既有物理释放和 parent 关联核算。
V1172 的 87 项 CPU 检查包含原有生命周期用例，以及新作用域、时钟、epoch/
session 精确匹配、节点身份复用和活动文件截断检查。没有增加 GPU 探针或改动
运行中的源码。V1170 是首次审计，普通 epoch 样本的名称仍有歧义；其诊断样本
结论由 V1171/schema2 替代，原报告保留。

| 明确作用域 | 有回收记录的 context/session 数 | 累计设备 FULL 释放 | 累计设备 Mamba 释放 | 首次正释放距调度端终态观察 P50/P90 |
| --- | ---: | ---: | ---: | ---: |
| 真实 dynamic subagent | 330 | 12.887 GB | 60.976 GB | 0.573/0.987 ms |
| runtime_internal，主要是上下文摘要 | 506 | 236.873 GB | 128.134 GB | 0.759/1.104 ms |
| 已结束 workflow 的 root | 125 | 4.097 GB | 20.991 GB | 0.760/1.440 ms |

以上是原生 allocator 实际释放的累计量，不是同时占用、避免的 D2H、parent
独占容量或 JCT 收益；没有把 Mamba slot 与 FULL token 相加。该前缀未记录正
释放 retry，节点 `(id, creation_time)` 无重复释放。最后记录的 pending 数不是
完整实时 backlog：没有释放的 retry 可以不写日志。观察时刻是调度端墙钟，
没有减去客户端 monotonic 事件时间，也不包含 RETURN 消息在到达调度端前的延迟。

两个需要固化的口径修正：

1. `terminal_child_reclaim_calls` 包括内部摘要，不能直接称为返回 child 数。
   本次非 root 的 FULL 释放大部分来自内部摘要。只看汇总计数会误判动态 fork/
   join 生命周期优化的覆盖和来源。
2. `_terminal(key)` 也会在 context epoch 改变时成立，因此
   `terminal_context_cache_sample` 不一定对应 invocation RETURN。35,751 条
   dynamic-subagent 角色样本包含 22,692 个旧 checkpoint key，却只涉及 345 个
   invocation。与真实终态回收匹配同 workflow/invocation/context/epoch/session
   的仅 348 条、159 个 checkpoint，其中 189 条发生在调度端终态观察后。
   普通旧 checkpoint 仍可包含后续会复用的共享前缀，不能称为“已结束 agent
   的无用 KV”。匹配的终态样本也有共享祖先，仍不能全部按可回收字节求和。

166 个已有 parent 首次服务记录的 RETURN 观察→首个完成 GPU batch 的 P50/
P90 为 268.79/7157.13 ms，请求可见→服务为 153.62/7098.16 ms。它们是已经
发生的关联，不是 GC、恢复或优先级各自的因果收益。结合亚毫秒的回收，现有
证据不支持继续把“更快执行终态释放”当成主要剩余瓶颈；较长的续接尾部仍需
按实际容量、依赖、排队和执行拆开。诊断也有成本，但 V1163 的 300 秒窗口里，
已计时 `terminal_sampling` self 仅累计 0.586 秒；初始观察包含于事件路径，
不能把所有控制成本归给这个采样器。

原生客户端本身也通过 executor 关闭 session；关闭引用与实际丢弃私有缓存是
两件事，不能声称 native 从不关闭 session。下一步先使用 V75 同 batch native
和 V76 隔离额外控制/状态策略的净效果，再判断这些累计释放实际改变了哪些
备份、重算和服务等待。容量释放的存在本身不证明有论文级收益。

### 4.7 追加已有工作约束：生命周期与 Host 写入准入

本轮核对落盘的版本化 arXiv PDF/摘要页，不把搜索结果的题名或相似编号直接
当作已读论文。原始来源与摘要、正文抽取的 SHA256 保存在
`experiments/reports/v1173_related_work_primary_20261016/`；绑定见 V1174。

- Lachesis [R9] 已利用 harness 的 segment 身份、spawn 记录和关闭边界，按
  生命周期选择 HBM/HBF，并在边界到达后释放对应 block；worker 的生命周期
  排名也是其已有机制。故“识别已结束 child、即时 GC、按生命周期选 tier”
  不能独立主张新颖性。其评估是 HBF 耐久性的 trace-driven 模拟，不能将该
  结果当作当前 HBM/DRAM 机器的 JCT 或传输收益。
- EfficientAgent [R10] 已建立 agent 的 Host reuse working set/stack-distance
  估计，并在容量不足时拒绝大块 refill、保留已缓存前缀的增量写入，容量充足
  时恢复完整准入。“少做无消费备份”或“一律过滤 Host 写入”不能再单独作为
  新机制。若研究 PREPARE/写回，必须与这种容量感知准入合理适配后比较。

本轮检索中，候选题名对应的 `2607.11686` 和 `2607.14785` 实际分别是机器人/
宇宙网论文，已排除，不作为 EfficientAgent 或 CacheScout 的证据。后者尚未
匹配到可核对的原始来源，不据此断言它具有或缺少某种能力。正式论证以本节
已经核对的版本为准。

当前更强的对照约束是：保留共用修复的 native、使用同一生命周期信息的简单
回收、合理适配的 Host 容量感知写入准入，以及将来选出的系统机制。前两项
工程修复和后两项研究差异分别归因。若简单方法已覆盖全部剩余收益，应撤销
该候选，而不是追加控制概念；核心系统机制与质量匹配的端到端收益仍未成立。

### 4.8 最新恢复依赖审计与质量评测前置检查

V1175/V1176 复用既有只读分析器，先分析 V75 predictive 的封存前缀，再分析
完整 V73 native。分别读取并哈希 3,074,590,066/3,564,209,575 字节的服务审计
前缀，另绑定首次服务事件和传输记录；第二步重新核对已封存事件 SHA256。
V75 实际引擎源码一并绑定。V1178 保存执行参数、原工具副本和来源哈希。
复用旧工具时，V1175 的前两条 limitations 残留了旧 V73 运行状态；这两条文字
由 V1178 的 corrected_scope 更正，量测值和原文件保留。当前 live 是 V75，
V73 native 已完整结束；本次跨版本诊断不能作为批量写回或新策略的因果消融。

| 已观测项目 | V75 predictive 前缀 | 完整 V73 native |
| --- | ---: | ---: |
| 完成的 prefill batch | 36,628 | 38,206 |
| 配对的恢复依赖样本 | 27 | 89 |
| 第一次等待出现在最后 Mamba 层的样本 | 10 | 8 |
| 上述前置等待 P50 | 3.513 ms | 0.018 ms |
| 上述样本全部层等待累计 | 37.172 ms | 15.429 ms |
| 单请求且实际 extend<512 的短样本 | 7 | 42 |
| 短样本全部层等待 P50/最大值 | 0.093/3.420 ms | 0.075/0.616 ms |

探针只采每 16 个 load-consuming prefill 中的一个，表中样本数不能除以所有
prefill 当作真实发生率，也不能把等待累计外推为整轮可移除时间。Mamba 整份
COW 确实会在有本批 H2D consumer 时等待最后 Mamba 层；当前模型对应 layer38，
其前面经过该传输批次 9/10 的 FULL 层和全部 Mamba 层。它约束的是本批缺失
数据，不是整个 agent 的 KV。

V75 10 个屏障样本中有 6 个混合 Host-hit 与无 Host-hit 请求；native 的 8 个
全部混合。此关联仍没有给出每个 COW 源物理槽与全部在途 H2D 目标的排除证明，
不能直接移除等待。当前 native 的短样本等待很小，未证明重做恢复流水线有足够
额外收益；保留此依赖作为正确性条件，暂不为它增加新控制或专门 GPU 实验。
继续从 V75 同 batch native 和 V76 的真实服务/控制成本选择主要剩余对象。

质量方面，V1177 用固定官方 harness `f7bbbb2`、固定 dataset Arrow 和全部
156 项任务，核对两侧导出、原始 summary/patch 哈希以及官方 test spec 的缓存
镜像身份，156 个镜像均可用。两侧仍各 154 completed/2 incomplete、155 个非空
补丁；非空补丁不等于正确。仅执行 `prepare`，没有调用 worker/supervisor，
没有启动评测容器，当前正确率仍未评分。该前置检查去掉旧一次性预约截止的
内存配置，不登记新的运行任务；正式评分仍需独立记录资源重叠与原始失败。
不得把过去因为未尝试而计入的 evaluator error 当作模型失败。

### 4.9 V75 缺失前缀复核与质量自动接续

V1179 复用主线只读混合前缀分析器，读取 V75 最新前缀。39,216 个首次服务请求
中 2 个尚不能连接到较早封存的客户端身份；40,093 个 prefill batch 中另有
1 个样本缺原生身份，均保持 unknown。实际 extend 总量 44,036,091 token，
其中 FULL 匹配超出有效复用边界的位置共 3,183,465 token。这不是可消除计算量。
768 个至少 512 token 的大差额中，746 个是首次请求或 compaction 后请求；
普通续接仅 22 个、24,741 token。相同 session 的旧输入缺失代理合计
1,201,683 token，P50 为 31 token，不等于逐层重算或全部由驱逐造成。
该前缀继续不支持“普通续接大规模丢失可复用状态”作为主要收益来源。

为补齐质量验收，新增 `scripts/run_official_quality_after_series.py`，在独立的
ops 副本冻结并于 10:17 PDT 登记为 V1185。V1180 的 29 项 CPU 检查包括
6 项新队列检查和 23 项既有导出/评分检查；官方评分 venv 的 CLI 接线也已执行。
真实等待进程为 `3806721/start_ticks634129312`，10:20 已核验存活，
`grading_started=false`，尚无评分容器或正确率结果。

它等待 V75 supervisor/driver 与 V76 supervisor 的已登记实际进程身份退出，
保留 PID 启动时间校验；随后验证三侧完整 156 项 summary，导出全部补丁，
再次核对官方 harness、dataset、镜像与源码。评分固定按最新数据优先：
V76 reactive、V75 native、V75 predictive，单 worker、1 核、8 GiB、无 GPU。
原始 error/incomplete/空补丁不丢弃；evaluator error 与模型 unresolved 分列，
worker 退出 0 不自动表示 156 项报告完备。旧一次性预约截止不启用，也不恢复
每日停实验规则。之后若与新的 GPU 开发重叠，须另记录，不能称匹配资源正式实验。

- 实际状态：`ops/quality_eval/quality_v75_v76_cpu_20261016.state.json`。
- 登记/绑定：V1185/V1186；ops helper 与登记的 core/exporter/scorer 冻结。
- 未来导出：V1181/V1182/V1183，未来环境核对 V1184；当前尚未生成。

这补齐的是质量执行链。已有 V75/V76 的模型服务、策略、引擎和启动 helper
保持冻结；系统论文机制、任务正确率和相对 native 的端到端收益仍待证明。

### 4.10 V75 predictive 完整结果与真实成本量级

predictive 已结束，156 项全部有终态：155 completed、1 incomplete。全程
7919.168 秒，全部 156 项原始 workflow duration 均值 1806.897 秒；只取
completed 的均值为 1783.130 秒，两个分母不可混用。154 项补丁非空，尚未
官方评分。11 个 completed workflow 有既有 guard 介入记录，原生 agent JCT
eligible 为 144；保留全部任务与这些标签，不能把它们全称为未干预自然完成。
唯一 incomplete 是 pytest-7490，最后 root 响应为 `finish_reason=length`，
context epoch120，未触发 workflow deadline；不增加输出上限或重标成功。
完整结果、逐项来源 SHA256 和终态依据见 V1187。

V75 native 于 10:37:24 PDT 核验真实 GPU 服务，scheduler3831770/
start_ticks634234241，使用同一应用2ad32d0、引擎53020ce3和 batch32；
`enable_beliefkv_admission=false`，到达表与物理容量已核对。V1143 为实际启动
凭据，不是仅有计划。V76 和官方质量队列仍等待既定进程顺序，不改冻结源码。

| 完整 predictive 观察 | 数量/字节 | transfer-stream 区间累计 | submit→ACK 累计 |
| --- | ---: | ---: | ---: |
| 原生需求 H2D | 352 批 / 115.203 GB | 3.899s | 20.663s |
| JOIN/tool 标记 H2D | 204 批 / 12.156 GB，492 个节点命令 | 0.726s | 8.431s |
| D2H | 35,082 批 / 1.749 TB | 213.984s | 431.229s |

这些均不是可直接从 JCT 扣除的时间或 oracle 上界；区间可能与计算、其他
请求和控制路径重叠。FULL 分配写回合并成功 8863 批/50662 节点、19731247
token，入队失败及部分批次均为 0。该工程机制真实执行，但不能与旧 V73
流量不同的总量相除宣称因果提速。

按独立等待事件，JOIN 完成前启动且下一请求确认复用的 FULL 为93次/2.443GB，
tool 为13次/0.599GB。节点命令口径下 JOIN FULL 总复用2.663GB，包含更晚
启动的命令，不能全算提前预取。JOIN 末 ACK→首次服务 P50/P90 为2.190/
11.416秒（115个有关联 ACK 的事件），tool 为0.680/84.954秒（15个）。
不能与按357/135个节点命令统计的长尾混用；一次大 burst 会重复贡献多个
节点命令。工具正 FULL 计划只有47个独立事件，不能将全部22,588个工具等待
都称为可立即预取机会，也不能因没有预取计划就推断后来没有需求恢复。

V1193 进一步以原生 batch 核对完整计算阶段，所有39,919个客户端请求身份均
进入角色分类。共40,932个 prefill 和461,524个 decode batch，记录的 worker
区间累计分别为1226.440/6411.942秒；没有缺失采时，二者全部记录 graph replay。
decode 约占两类累计 worker 区间的84%，这不是 CUDA kernel 独占时间比例。
仅含摘要的3752个 prefill batch累计395.493秒；含摘要的混合批次累计26.014秒，
混合时间保持整批，不按 token 或角色分摊。摘要输入15,276,527 token，
压缩后首个正式请求另有6,503,522 token，合计占45,174,567个 extend token约
48.21%，不能称为48.21%的时间或可消除工作。

由此调整筛选顺序：继续完整 V75/V76 消融，首先量化高频运行时控制和真实
生成/上下文计算；预取保持消融对象，不再仅以提高 RETURN 精度为主要收益假设。
任何新执行复用方案须指出少做了哪些实际模型计算，并验证混合状态正确性和
已有方法差异。D2H 仍有可测成本，但应先与已有批处理、生命周期回收和
EfficientAgent 类写入准入比较，不能把修复共用路径直接当作 workflow 创新。

- V1187：完整 predictive、终态和传输/预取口径绑定。
- V1190：首轮依赖图审计；V1193在同一完整输入上保留拓扑并补齐阶段成本。
- V1192：19 项 CPU 检查，覆盖身份、嵌套/内部调用、缺失证据、跨 workflow
  同名、重复等待、循环、混合批次不分摊、非法采时及跨阶段重复服务身份。
- 运行源码、引擎、GPU helper 和官方质量 helper/依赖均未修改。

### 4.11 跨 agent 共享的已有工作与当前负载必要条件

V1188 封存 TokenDance [R11] 和 SAGA [R12] 的原始摘要页、PDF、正文及 SHA256。
TokenDance 第4.1–4.4节已包括共享块接口、兼容请求的 collective PIC、
Master-Mirror 差异存储及融合恢复。其当前实现基于 CacheBlend，第6.6节的
等价对象是同一 PIC 路径；不能解释为与原始模型逐 token 精确等价，也不能
直接外推为 Qwen3.5 recurrent 状态可安全跨前缀复用。通用跨 agent 共享、
差异存储或再加一套逐层恢复不自动具有新颖性。

V1193 的实际依赖图有367个 dynamic child，全部为深度1；708个内部调用
单列，不当作嵌套 subagent。200个 JOIN 的声明等待者均为1个；没有观察到
同一非空生产者集合对应多个声明消费者。156个 workflow中127个一轮JOIN，
20个两轮、7个三轮、1个五轮、1个七轮；多轮共29个（18.59%）。
因此不能把当前结构直接视作 TokenDance 的 All-Gather 场景，也不支持以
“新增多个 child”本身推导群体共享收益。

该审计只核对调用依赖，不记录等价 token/KV，不排除多个 agent 重读相同
文件、工具文本或公共前缀。后续若选择计算/状态共享方向，必须先补齐实际
渲染 token、可见源数据、因果/位置依赖及已有 native 命中证据；没有这些条件
就不实现近似 recurrent 状态复用，也不人为制造相同输出来提高共享率。
SAGA 的 AEG、缓存保留和公平调度仍是对照边界，不能靠换名称绕开。

### 4.12 V75 同配置 native 的两个完整窗口

V1195/V1198 复用已验证的 worker/控制分析器，改用 V75 当前 native；
应用2ad32d0、引擎53020ce3、batch32相同，不再用旧 V73 解释共用修复收益。
两个窗口均已完整观察，时钟按 America/Los_Angeles 校验；native 全程仍未结束。
V1200 汇总相同窗口的实际 decode token delta、监控和传输。

| 指标 | 600–900s predictive/native | 900–1200s predictive/native |
| --- | ---: | ---: |
| 实际 decode token/s | 1175.06 / 1237.65 | 1314.06 / 1188.10 |
| GPU 利用率 | 91.38% / 94.05% | 93.78% / 94.66% |
| prefill worker 区间累计 | 75.961 / 71.606s | 56.640 / 76.413s |
| 提交时 uncached 输入 token | 2,141,241 / 2,052,828 | 2,221,941 / 2,624,996 |
| predictive 已埋点 Python self 墙钟累计 | 17.024s | 22.191s |

满48、相近上下文且 graph replay 的 decode 均值比例为1.003–1.005和
0.999–1.007。短单请求 prefill 的均值比例随窗口、工作量和邻接执行变化，
中位数仍有差距；不能把均值下降当作隔离提速，也不能将某一窗口倍数推广为
固定内核差距。worker 区间包含排队、依赖和结果处理；self 墙钟也不是可直接
扣除的 GPU stall。

当前 native 在这两个窗口未记录已完成 H2D payload，而 predictive 有恢复和
PREPARE。后者的 H2D 不能直接称为替代 native 的恢复等待，仍需结合容量、
有用重算和完整 JCT/质量判断。第二窗口多出的 decode 服务同时对应约20秒
更少的 prefill worker 区间与更少 uncached 输入；不是已证明的预取收益。
保留 V76 无预取消融以核对净控制/状态效果，不据此新增高频控制或改动冻结实验。

### 4.13 生成加速的必要证据与原生能力边界

通用 agent 草稿不能直接作为新主机制：AgSpec [R4] 已有检索共享语料；
AgentSpec [R13] 已有共享 n-gram、批次预算和工具/提示词边界复用。
ReplaySSM [R14] 已有 Mamba-2/GDN 的局部状态回放与推测回滚。
V1199 封存本次核对的后两篇原始 HTML 和 SHA256，不搬用其 kernel/模型
加速比解释当前 workflow。

本地原生引擎已有 `NGRAMWorker`、共享 corpus/外部语料 API、
`commit_mamba_states_after_verify` 和 ReplaySSM 入口。BeliefKV admission
初始化在 `scheduler.py` 明确拒绝 non-NONE speculation；它们的组合尚未验证。
不能删掉这一正确性约束就宣称支持，也不能把简单开启原生选项当作论文贡献。

现有 trace 的计数、正文和解析后的工具参数不足以还原实际生成 token。
补充默认关闭的 `BELIEFKV_RECORD_OUTPUT_TOKEN_IDS=1`，仅在原生请求完成时，
把不可变 `req.output_ids` 快照附在既有 `llm_result.attributes.output_token_evidence`
中；ready/status 明确记录开关。没有逐 decode 上送或额外 worker，没有记录
prompt token。native/admission 两条观测路径使用同一实现，不改变模型/工具/
终态行为。V1197 的151项CPU检查通过，包括11项新增检查：默认关闭不复制、
两模式、0/3/8192 token、后续列表修改及重复完成、非法配置。

这些是引擎保留的生成 IDs，可包含 reasoning、工具序列化和特殊 token；
一次 LLM 请求完成不等于 child RETURN。后续因果重放必须绑定模型/tokenizer、
同一时钟及请求身份，草稿源只能使用当时已可见的数据。仅有完成序列时，应先
保守地限制为目标请求提交前已完成的源，不利用未来 response 或本轮完整输出
构造草稿。离线 token 一致仍不证明真实接受率、Mamba 回滚正确性或净提速。

V75/V76 的已冻结候选未加入此采集，当前旧 trace 的缺口仍存在；没有补造
历史 token、登记新 GPU 任务或改变已排队实验。它是下一独立候选的数据能力，
尚无实机开销/推测接受率结果。下一机制仍须以实际减少的模型工作、控制/恢复
成本、资源预算和完整任务质量证明价值。

### 4.14 原生 session 关闭回收的简单对照与主线保存

为检验终态生命周期收益是否需要完整工作流控制器，独立引擎候选
`f1dc97d` 在显式 session close 后复用现有安全回收检查，仅丢弃已经没有
活跃引用、共享依赖、锁和在途传输的私有节点。受阻节点保留身份与generation，
在原生HiCache完成/锁释放后的安全点有界重试；锁释放不直接丢页，晚结束
请求也在cache清理后排队。此项不依赖RCCG、预测、PREPARE或parent优先级。

这是简单工程对照，用于证伪复杂控制器的必要性，不主张新的生命周期贡献。
`SGLANG_NATIVE_SESSION_CLOSE_RECLAIM` 默认关闭。独立增量补丁以完整V75
tree53020ce3为底，得到tree76cf97d2；V1208绑定提交、patch和验证哈希。
规范staging patch及冻结V75/V76未修改，独立论文素材未纳入此次系统提交。

V1205有103项真实CPU radix/allocator及生命周期检查通过；V1206有151项应用
检查通过。V1204的首次14项检查中1项因夹具缺少swa_evicted_seqlen失败，补齐
夹具后通过，原XML保留。V1207按随机顺序测每模式200次关闭，构建与插入不计时：
native/回收的线程CPU P50为0.011349/0.068110ms。回收每次释放32个私有FULL
token，并验证活跃parent前缀仍可用；原生保留私有缓存。两者工作量不同，
这只能量化该小夹具的额外关闭成本，不能推出GPU吞吐/JCT或全程控制成本。

当前CPU资格限单rank HiCache的FULL或FULL+Mamba；没有多GPU、非HiCache及
其他缓存模式的重试证明。候选HEAD与现有launcher固定的94602c9不一致，
后续需要独立组合补丁和启动资格，当前未登记/启动新GPU实验。是否保留此
简单基线及复杂控制器，仍由同任务质量、完整JCT/吞吐和真实状态成本决定。

### 4.15 最新 V75 窗口与简单对照的启动资格

V1209继续读取同一V75两臂的3000–4200s，GPU监控、服务和客户端时钟已核对，
窗口两侧均完整观测。其动态来源尚未逐文件封存前缀，属于开发观察，不作
正式性能验收。完整native仍在运行，不能用已结束子集计算最终平均JCT。

| 指标 | predictive | native |
| --- | ---: | ---: |
| decode token/s | 1293.30 | 1273.97 |
| GPU利用率 | 96.25% | 96.87% |
| 提交的uncached输入token | 9,084,046 | 8,953,101 |
| prefill worker累计区间 | 253.865s | 252.253s |
| D2H实际字节 | 306.329GB | 421.349GB |
| D2H transfer-stream累计 | 38.957s | 62.568s |
| 全部H2D transfer-stream累计 | 0.222s | 0.234s |

这个窗口显示需要分开测写回、生命周期和控制成本；它并不隔离任何策略。
两侧running、waiting和真实生成工作不同，不能把约1.5%的decode差距当作
预取、回收或批量写回的因果收益；流时间也不是可直接扣除的JCT。

V1211把上一节的简单关闭回收候选准备为显式launcher选择：
`native_session_close`使用完整组合补丁，保留94602c9的HEAD约束和反向patch
检查。新独立检出的实际tree精确等于76cf97d2，未改V75/V76共用引擎。
58项启动、NUMA和来源CPU检查通过；直接导入新检出的禁用/启用两模式也
验证了0/2个私有FULL token释放与活跃parent保留。既有103项引擎检查仍
绑定相同源码树；不把源码相同误写成新GPU验证。
V1210的原56项检查保留；补充缺少源码/开关与补丁不匹配的静默未启用风险
后，V1212的最终58项全部通过，来源摘要以V1211为准。

尚未登记新GPU单臂实验。现有pair driver不接受单独native，后续需要独立
应用检出、同表任务/到达/预算计划和启动登记。这个对照用于判断完整工作流
控制器是否必要，不新增已成立研究贡献；最终仍以全部任务质量、平均JCT、
有效吞吐和系统成本选择下一机制。

### 4.16 V77 独立原生关闭回收对照及可证伪条件

V77用于检验一个具体问题：终态生命周期管理的效果，有多少可以由显式session
关闭触发的原生私有页回收取得，是否需要完整工作流控制器。不是把通用关闭
回收重新命名为论文贡献；已有方法边界仍按4.7和4.14执行。

独立候选应用52a2b8e、引擎tree76cf97d2已由V1214/V1215冻结登记，任务、prompt、
模型调用及工具执行代码相对V75保持一致。native入口无admission控制器，
仅启用关闭回收，保留batch32、Host200GB/80:20、HBM ratio0.9、原生逐层恢复、
通知与终态规则；关闭PREPARE、预取、语义模型、额外优先级和生成IDs采集。
实际V75计划的38项任务/到达/资源字段相同，156项Poisson表摘要不变。

新单臂驱动的34项CPU检查覆盖同表/预算、来源冻结、真实进程等待、实际开关
缺失/失配，以及后处理保留所有任务和失败标签。原103项引擎、151项应用观测、
58项启动检查与源码也随资格绑定，不把跨检出的同树验证称为GPU结果。
监督1076769/start_ticks634866646在12:20实测存活，等待完整V76成功结束和
资源释放。V75 native继续原运行，V76和质量队列顺序未改；当前V77尚未启动GPU。

验收先确认真实关闭/释放发生、私有FULL/Mamba容量和pending重试，再比较D2H、
Host回收、原生恢复、有用重算及请求服务。独立单臂会生成完整HTML与历史native
开发对照，保留全156项和终态标签；不能仅用释放字节或完成子集JCT称收益。

可证伪判断：

1. 若native-close在质量匹配后取得控制器的大部分效果，生命周期释放用这个
   简单基线解释，不宣称复杂控制器或动态DAG机制是必要条件。
2. 若V76与native-close仍有差距，需沿实际服务、模型工作、驻留/写回及公平性
   分解，不把差距自动归给工作流感知。V75与V76同时改变预取、语义采集与扫描，
   也不能把二者差值当作单一H2D算子的隔离收益。
3. 若三者均接近native，缩减相应工程范围，继续寻找有规模的系统成本与已验证
   现有方法差异；不扩大控制器或制造无用分支、异常循环以维持研究假设。

原V75/V76官方CPU评测可能在V77运行时占用单核/8GiB，计划明确记录这一限制；
本轮不作为正式匹配资源的重复实验。V77完整结果后单独补充全任务官方质量
评测，当前未登记该新质量队列。V1216/V1217/V1219是未来启动、完成和NUMA
证据，尚未生成。最终研究与质量匹配的JCT/有效吞吐目标仍保持未完成。

### 4.17 V75 完整配对结果与下一步归因

V1226绑定完整两臂摘要、原定比较、物理传输和native失败轨迹。V75在12:34:06
以AB driver退出0结束，observer_errors为空；全部156项都保留，native客户端
exit1并不代表缺少任务收集。相同应用2ad32d0、引擎53020ce3、batch32、任务/
Poisson表和容量，仍不能约束实际模型轨迹相同。

| 指标 | predictive | native |
| --- | ---: | ---: |
| completed / incomplete | 155 / 1 | 153 / 3 |
| 收集时长 | 7919.168s | 6841.743s |
| 全156项原始duration均值 | 1806.897s | 1665.374s |
| completed-only原始duration均值 | 1783.130s | 1608.445s |
| completed workflow/h | 70.462 | 80.506 |
| 显式native JCT可用workflow | 144 | 141 |
| 有既有guard介入标签的workflow | 11 | 13 |
| 客户端LLM请求 | 39,211 | 36,166 |
| 客户端工具调用 | 38,927 | 35,896 |
| 需求原生H2D | 115.203GB | 13.610GB |
| 预测H2D | 12.156GB | 0 |
| D2H | 1.749TB | 1.935TB |

predictive原始耗时均值高8.50%，完成吞吐低12.48%。152个共同completed任务
中，predictive较快78个、native较快74个，但前者原始均值仍高169.377s；
其中仅132个两侧都显式JCT可用。官方质量未评分，不能用完成子集或guard
终态宣称平均JCT收益，也不能把完整结果的劣势改写成先前局部窗口的优势。

native的pytest-6197、django-13089、django-11179末两次root响应均为length，
deadline均未过期，错误字段为空；11179还记录一次既有guard介入。输出8192
配置和原终态保持。质量评分后仍按全部任务及失败/guard标签分别报告。

需求H2D差额需要拆分策略引发的状态变动和实际轨迹，不能把predictive的
115.203GB当作可替代native的13.610GB。全部H2D流累计约4.624/0.661s，
D2H为213.984/276.723s，均非可直接扣除的JCT。native和predictive的同session
旧前缀丢失代理为1.148M/1.223M token；有限历史代理不是精确有用重算证明。

继续原定V76消融和V77简单对照，先量化控制成本、实际工作、恢复/写回与服务
延迟。V76于12:34:09接续；V1166在12:37:09确认真实首服、同到达/同池及无预取，
仍保留PREPARE、工具时长、生命周期/驻留/收尾调度。当前没有V76净收益结论。
不以本轮完整负结果扩展高频控制，也不据局部相近worker区间忽略真实额外工作。

### 4.18 V77 全任务官方质量队列

V1223/V1225登记独立V77队列，复用APP52a2b8e中的冻结series及原CORE、exporter、
scorer，不改原V75/V76队列。新薄封装仅选择V77、独立run ID和输出路径，等待
准确监督身份并追踪后来启动的driver/worker，避免先前监督退出但worker仍活跃
时并发第二个grader。17项CPU检查与实际quality venv中的组合检查通过；
V1227绑定源码/检查/登记摘要。监督1121673/start_ticks634983330已实际存活。

等待完整V77和旧评分队列退出后，导出并评分全156项，单worker/1核/8GiB，
官方测试超时仍1800s、无GPU，保持已有scorer的measurement_complete条件。
完整官方正确率评分和原生JCT测量有效是两个条件；有incomplete或guard标签时
不能为了产生有效吞吐数字而放宽后者。V1222输入、V1224环境和正式评分仍为
未来输出，当前grading_started=false。新系统贡献及质量匹配性能目标仍未完成。

### 4.19 V76 首个完整 600 秒窗口

V1228复用既有monitor_window/control_profile，按真实reactive臂读取，无新运行时
采集。报告绑定分析模块摘要、方法、时区及控制文件前缀/边界行摘要。V76与
V75 predictive的已埋点Python self墙钟累计分别15.043/25.377s，实际counter
快照跨度600.617/600.036s。剩余V76最大几项为control_drain3.343s、
scheduler_maintenance3.059s、event_apply2.671s、join_prepare1.693s。

关闭预取/语义/机会扫描的配置已由V1166确认；较少的控制支出与这一变化一致。
但事件数、forward数和实际工作不同，上述值不是线程CPU、GPU stall，也不是
隔离的省时比例。不能把self墙钟差直接从完整JCT中扣除。

V1229使用既有支持任意臂名的service分析器读取同样0–600s：

| 指标 | V76 reactive | V75 native |
| --- | ---: | ---: |
| decode token/s | 1001.40 | 932.72 |
| decode时间加权batch | 25.68 | 23.66 |
| 提交uncached输入token | 3,745,230 | 3,650,788 |
| prefill worker累计区间 | 119.633s | 123.824s |
| 需求H2D | 5批 / 0.758GB | 无 |
| D2H | 120.021GB | 139.944GB |
| 其中PREPARE | 1.781GB FULL | 无 |

较大的batch和不同生成需求也能改变速率；早期段还不能反映完整压力、长尾和
质量。V76仍有需求恢复而native此段没有，后续要关联PREPARE、回收、真实缺失
前缀和实际请求，不能单凭字节差就归咎策略或宣称有用迁移。
本轮证据足以继续当前消融，尚不足以扩展高频控制或形成新的论文主张。后续
优先V76有压力窗口及完整结果，再与V77简单对照和全任务官方质量合并判断。

### 4.20 V76 等待状态回收、恢复关联与独立分池候选

V1233封存截至13:23 PDT的逐文件完整行前缀，按workflow/invocation/context/epoch
关联定向回收、此前PREPARE ACK、下一较高epoch原生请求和同节点H2D。新增
`audit_wait_state_recovery.py`及既有生命周期审计的94项CPU检查通过（V1232）。
V76没有旧审计器要求的safe_point_census；首次调用旧工具时长审计因此拒绝。
新审计只用服务端wall时钟，不伪造配对时钟，不报告工具预测准确率。

| 观察 | JOIN等待 | 工具等待 |
| --- | ---: | ---: |
| 含定向回收的context/epoch | 129 | 13 |
| 原生节点回收记录 | 489 | 27 |
| 后继首次服务已观察 | 100 | 13 |
| 同节点H2D与后继匹配路径可关联 | 100 | 12 |
| 首次回收到服务观测间隔P50 | 366.300s | 2.773s |
| FULL与Mamba一起释放的记录 | 238 | 26 |
| 只有FULL释放的记录 | 251 | 1 |

这142个context/epoch不是独立工具或JOIN事件的完整机会分母。112个已关联轮次中，
未观察到首次服务前的“定向回收→同节点恢复ACK→再次定向回收”序列；未服务的
29个轮次，以及已服务但缺时间证据的1个轮次保持未知。demotion和原生H2D没有
节点creation identity；即便同node ID出现在后继匹配路径，也不是组件forward
复用证明。原生未标记驱逐、节点拆分及其他epoch的活动不能据此排除。
长JOIN间隔支持这些parent确实长期等待，不能仅凭新增H2D断言卸载是错误的。

PREPARE为510个JOIN节点命令/4.431GB FULL、578个工具节点命令/7.106GB FULL，
均有ACK，Mamba字节为0。整个封存transfer前缀有613批H2D/254.923GB，
流时间累计7.939s；与回收可关联的112整批共17.946GB/1.067s。整批包含其他
状态与页，不能计作回收agent的独占字节、避免的传输或可扣除JCT。V1234以相同
前缀摘要复核数量、运行开关和引擎源码；逐文件封存不是跨文件原子快照。

V1235/V1238核对另一个完整1200–1800s窗口：

| 指标 | V76无预取 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| decode token/s | 1339.85 | 1333.46 | 1329.42 |
| decode时间加权batch | 47.501 | 47.494 | 47.491 |
| decode worker平均区间 | 28.314ms | 28.300ms | 29.130ms |
| prefill worker累计区间 | 120.821s | 123.336s | 111.090s |
| GPU利用率采样均值 | 96.58% | 97.21% | 93.96% |
| 已埋点Python self墙钟累计 | 32.778s | 无工作流控制器 | 49.376s |

两控制counter实际跨度599.609/599.759s；V76剩余最大项为JOIN PREPARE5.903s、
residency_hint4.147s、event_apply3.669s、control_drain3.284s。工作量、排队和
请求组成仍不同，短prefill也未在这里逐请求匹配；这些数值不证明隔离提速、
线程CPU节省或完整质量匹配JCT收益。它们支持先完成当前消融，不恢复高频预取。

具体工程问题有源码依据：V76为static_separate_full_mamba，516个定向回收均释放
正FULL；预取与handoff关闭，原生FULL等待候选路径的全节点demote会级联释放Mamba。
仅为FULL容量触发时，丢弃独立池中的仍有效状态不是释放FULL的必要条件。
新独立候选de4118f/tree35cf6411基于V75完整树53020ce3，在没有剩余Mamba缺额时
使用已有FULL-only原语，保留原Mamba对象/引用/LRU；同时缺Mamba则保持原生级联。
之后真正出现Mamba压力时，仍由原生备份、ACK和状态回收处理。

V1237的236项回归及27个subtest、V1239的13项最终候选检查通过；后者含新增
真实CPU池压力下保留状态的原生备份回收，两个集合去重有237个用例身份。
CPU检查验证FULL恢复后hybrid checkpoint再次匹配、联合/状态缺额、活跃/共享/
在途/过期身份保护，不是GPU DMA、模型数值或性能验证。增量补丁为
`patches/sglang-v0.5.20-waiting-full-only.patch`，只应用于完整53020ce3树。
它是减少自引状态搬运的工程候选，不是新论文贡献；尚未选择进launcher或登记GPU。

V1240绑定上述来源与CPU证据，重新核验V75/V76/V77应用干净、两引擎树仍为
53020ce3/76cf97d2，29份已登记文件指纹匹配。V76继续原版本，V77及质量接续
顺序不变。最终仍需完整质量/JCT/有效吞吐及相关工作差异，研究目标未完成。

### 4.21 V76 后续窗口与主线审计整合

本次将已完成的两个只读审计器、测试及V1241–V1246报告纳入主线；V1247对
两个新测试文件及既有比较器共46项CPU回归全部通过。没有将未实现的原生
prefill顺序消融混入本次提交，也没有登记新GPU实验或改动冻结运行版本。

`audit_online_completion_windows.py`直接读取收集器记录的相对到达/完成时间，
保留时点前全部应到任务，包括未提交、未完成、失败和结果未知者。终点为
run_one返回后的workflow_finished，包含setup和产物收集，不等于result.duration_seconds。
失败/未完成任务在完成口径下持续累计到观察时点；各均值是有限观察期的逗留面积，
不是最终JCT、Kaplan-Meier RMST或质量匹配吞吐。V1242的同表队列结果为：

| 观察时点 | 应到任务 | V76 completed | V75 native | V75 predictive |
| --- | ---: | ---: | ---: | ---: |
| 600s | 42 | 5 | 7 | 7 |
| 1800s | 111 | 40 | 49 | 46 |
| 3600s | 156 | 80 | 105 | 101 |

3600s的completed口径全队列平均截断逗留为1476.103/1267.947/1273.804s，
顺序同上。已记录completed均显式标为JCT可用，但官方任务质量仍未评分。
这些是单轮开发观察，不能由不同实现轨迹直接分摊策略因果收益。

V1243封存完整2400–3000s服务窗口：

| 指标 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| decode token/s | 1009.645 | 1304.050 | 1245.198 |
| decode时间加权batch | 47.188 | 47.490 | 47.438 |
| H2D总量 | 637.786GB | 7.190GB | 102.896GB |
| 其中FULL | 448.096GB | 2.811GB | 66.838GB |
| D2H总量 | 725.583GB | 224.260GB | 263.761GB |
| D2H传输流累计 | 132.184s | 34.727s | 38.457s |

V76该段没有PREPARE，迁移属于原生恢复/写回。传输流累计不能直接当作可减JCT。
V1244按真实上下文分桶后，满48请求、20480上下文桶的decode worker均值为
21.244/21.557/21.889ms；47请求紧邻单请求prefill的同桶区间为
89.469/51.555/56.953ms。worker区间包含周转/依赖和重叠，不是纯kernel；
这将差距定位到请求周转附近，但尚不能归因到某个排序或回收函数。

`audit_native_host_demand.py`按首次服务匹配路径的observed_ts_ms归窗，并以准确
workflow/invocation创建记录区分root和child。V1246的V76正Host FULL命中为
1473请求、145个context、94个workflow；child贡献约366.834GB，占报告需求约82%。
队列到服务观测最短10.075s、P50为15.574s。cached_tokens_host合计是重复需求，
不是独占页数、实际组件传输或forward消费证明；队列间隔包含准入/恢复/控制。
大量需求分布于多个agent，不能仅按单个工具长尾解释，也不能断言10s老化是原因。

下一步需独立隔离排序、生命周期与原生周转因素。原生prefill顺序消融仍是待实现
方案；已有FULL-only CPU候选只覆盖特定等待回收路径，尚未证明能解决后续大量
原生迁移。native在同工作负载没有同规模新增迁移，因此消除V76自身成本属于
工程修复，不能作为论文创新。完整V76/V77及质量证据继续按既定顺序收集。

## 5. 既有候选一：降低上下文摘要和重建的计算量

这是目前最有规模证据的性能对象。当前 `_summary_input` 把历史消息转成 XML，
放进一个独立摘要 prompt；摘要请求登记的 prefix hit P50 只有 192 token，
uncached P50 为 21,451 token。摘要后普通请求的 uncached P50 为 8,556 token。
原始历史已经在 agent 请求中计算过，换成另一种输入后需要重新处理。

具体探索：

1. 验证摘要请求能否保留原对话的真实 token 前缀，在末尾附加总结任务。必须核对
   system/tool schema、chat template、thinking 和 tool_choice 的实际渲染，不能
   仅因消息内容相似就宣称 KV 可复用。输入改变后的摘要保真度必须独立检查。
2. 将已确定的工具事实和原始日志作为可恢复记录，只对新增决策和未决事项生成
   增量总结；衡量是否真正减少摘要生成和再次输入的 token，而不是把日志隐藏后
   迫使模型多次回读。摘要阈值不能只调大来推迟成本。
3. 分开测摘要本身、压缩后首次请求、后续 decode 与重复调用。上下文缩短也可能
   带来信息损失或更多后续生成，必须核对完整任务和失败。

已有工作边界：SmoothAgent [R1] 已覆盖上下文变换的提前计算，且明确包括
summarization 与 subagent isolation/流式指令预构建；不能声称它只研究单 agent。
TokenPilot [R2] 已覆盖稳定 prefix、工具输出精简及可恢复原始内容。因此
“异步摘要”“缓存友好的 prompt”或“给工具输出一个引用”本身不能算新贡献。
这些应先作为强工程对照。若它们已拿到全部收益，就不围绕它们包装新论文。

## 6. 既有候选二：让父子 Agent 复用有版本依据的调查与验证结果

直观问题是：child 已经读取某段代码或完成一项验证，parent 或 sibling 又为同一
问题产生新调用；另一方面 parent 编辑后，旧结论可能已经过时。需要区分“已经
知道且仍成立”“已经知道但版本过期”和“从未确认”，不能只记一段自由文本报告。

例如 child 在版本 v0 完成 `pytest tests/test_a.py`。parent 修改了其他文件时，
是否仍可用这条证据取决于测试实际依赖及环境；若修改了被测实现，旧通过结果
只能作为 v0 的历史事实，不能被总结成当前版本通过。相同命令不是充分条件。

候选实现是记录 `(工具参数、实际依赖版本、结果、原始日志引用)`，让 sibling/
parent 能取得同一份记录，必要依赖改变时使结论失效。对确定的文件读取可先用
文件内容版本；任意 shell、网络、时间、随机性和未知读写集仍执行原工具。
不能通过拒绝重复调用、强制 RETURN 或把历史结果冒充本次执行来获得收益。

研究问题是这些记录能否同时减少有效工具执行、重复模型生成和摘要输入，并在
动态编辑、JOIN 与 compaction 后仍保持正确。必须实际追踪结论使用和失效，
不能把当前 2,432 次 root 重复直接解释为摘要丢失所致：目前没有因果证据。

已有工作边界：TVCache [R3] 已有状态感知工具结果缓存、工具调用历史的最长前缀
匹配和并发共享。候选需要与它的合理适配以及简单版本缓存比较。不同调用历史、
共享且持续修改的 workspace、依赖级失效以及模型上下文中的事实复用，是需要
验证的差别，不是“此前无人解决”的结论。数据库增量维护/MVCC 等也是必要对照。

这条路线更贴近动态多 agent，但当前跨 agent 同参数工具合计只有约 307.60 秒
墙钟且不能全部复用。单做工具缓存不优先；只有同时减少实测重复推理或上下文
计算，才值得升为主线。

## 7. 既有候选三：利用当前代码与已产生的结果加速模型生成

本轮共输出 8,156,973 token，其中 child 4,469,761、root 3,046,689、摘要 640,523。
可以测试从当前 repository、已可见工具结果及已完成 child 的文本中取草稿，
交给目标模型验证；不依赖预测 RETURN。草稿只能使用当时已存在的信息。

优先复用成熟推测解码实现，核对当前混合模型的验证、拒绝回滚和 Mamba 状态成本。
离线字符串重复只能说明有候选，不能代替目标模型接受率和实际服务成本。还需
与普通 n-gram/后缀草稿及 engine 可用的推测方式比较，不能手写简化内核后比较。
batch=48 的当前并发、长上下文、验证额外计算和状态占用均可能使收益消失。

已有工作边界：AgSpec [R4] 已针对 coding-agent pipeline 做检索草稿、共享语料与
agent 相关草稿长度控制；其生成吞吐结果不能直接当本项目 JCT 收益。SpecReason
[R5] 已研究小模型草稿/大模型检查的推理加速。普通检索草稿、按 agent 区分接受率
或换小模型本身都不能算新意。本方向目前是独立性能候选，非已成立论文贡献。

## 8. 当前系统研究的执行顺序

研究范围是系统，具体主机制仍待证据选择。优先完成以下系统侧判断：

1. V1135 已完成大差距的客户端阶段与 native batch 位置归因，普通续接的规模
   不支持主瓶颈假设；首次/压缩后公共前缀问题保留为与 Marconi 等方法的对照，
   配置失配直接修正，不包装新协议。继续分解真实计算和关键路径暴露，worker
   墙钟不是纯 CUDA kernel 时间，已有统计相加不能充当 oracle 加速。
2. 当前具体工程候选是 V1136 的原生顺序 write-back 合并。先验证真实 GPU 下
   合并比例、控制成本、DMA 正确性/时延、Host 回收及重算，再测同应用 JCT。
   该项涉及 native 共用路径，有效后应匹配进入新 native 对照；V73 只能作历史
   诊断，不能用旧 native 的缺少共用优化制造新策略优势。V75已按此开始同配置对照。
3. 核对动态分叉与续接时实际共享的物理数据及重复执行，用“少计算多少、少读写
   多少、少占多少”定义候选机制。共享 KV 不自动等于共享了 decode 计算，已有
   prefix caching 不自动覆盖所有执行复用；具体收益和已有方法差异均须实测。
4. 前端路径先与原生 session、TokTier/Delta Tokenizer 等简单强方法比较；若可
   消除成本只占很小端到端份额，按工程优化收尾，不据此扩展论文主线。
5. 只有当一个系统机制具有可测成本对象、明确实现位置、可信的已有方法差异及
   同任务质量的收益预期时，再做独立默认关闭的候选和必要 GPU 验证。最终比较
   平均 workflow JCT、有效完成吞吐、质量、全部终态、长尾和资源成本。

这不是恢复旧的“可消费状态”论证，也不要求先预设三项创新。若当前负载只提供
很小增量，先判断真实应用是否存在有规模的系统需求，再调整 workload；双方
使用相同应用和自然需求，不能强制无用 spawn、重复输出或异常循环创造收益。

### 暂不优先与历史探索

工具执行环境值得保留诊断，但当前 sandbox execute 的锁等待 P50 约 0.00124ms，
不是主要锁竞争瓶颈。execute 墙钟 P50 约 402ms，包含真实子进程工作，尚未分出
可移除启动开销；不能把这些耗时都称为 Docker 或 Python 初始化成本。
先不建设新的工具调度器、常驻解释器池或零拷贝框架。

此前候选的筛选记录，受本节系统优先约束：

1. 已完成：取消旧候选自动启动，审计最新完整 trace，并用真实 batch 核对输入计算。
2. 摘要格式原型的 CPU token 条件验证已完成，但暂不继续其 GPU 实验。其工程
   改进须以 SmoothAgent/TokenPilot 所涵盖的强对照约束，不自动计为论文贡献。
3. 对既有候选二验证有效依赖、重复结论和跨上下文使用的规模；如只省短工具而不省
   模型工作，降低优先级。先澄清因果，再决定是否增加观测和实现。
4. 对既有候选三先测可验证草稿与混合模型的真实成本，不能照搬其他模型/较小 batch
   的论文加速比。使用正确任务，避免由异常重复循环制造接受率。
5. 选定后再做一个独立候选。native 加相同 harness 工程修复、强简单方法与新机制
   必须分列；不拿自身早期低效实现或未评分任务数量证明创新。

新 workload 可以增加真实协作编辑/独立验证和自然层级，但双方必须使用同一应用，
不能强制无用重复、重复事实或无效 spawn 来创造收益。最终以官方任务正确率、
全部到达任务的 JCT/完成曲线、长尾和有效吞吐共同判断。

## 参考资料

以下均按实际阅读的预印本版本比较，不视为其已获特定会议录用。

- [R1] SmoothAgent，arXiv:2607.00151v1，第 3.4/3.5 节。
  `https://arxiv.org/html/2607.00151v1`
- [R2] TokenPilot，arXiv:2606.17016v1，第 3 节。
  `https://arxiv.org/html/2606.17016v1`
- [R3] TVCache，arXiv:2602.10986v1，第 2.3/3 节。
  `https://arxiv.org/html/2602.10986v1`
- [R4] AgSpec，arXiv:2610.01108v1，第 3/4 节。
  `https://arxiv.org/html/2610.01108v1`
- [R5] SpecReason，arXiv:2504.07891。
  `https://arxiv.org/abs/2504.07891`
- [R6] Marconi: Prefix Caching for the Era of Hybrid LLMs，MLSys 2025；
  核对作者论文与 artifact，不能把混合状态 checkpoint admission/eviction 本身
  当作未解决问题。`https://arxiv.org/abs/2411.19379`
  `https://github.com/ruipeterpan/marconi`
- [R7] TokTier: Exact Stateful Tokenization for Agentic LLM Serving，核对当前
  arXiv 摘要与作者实现，二者版本/实验口径不混用。
  `https://arxiv.org/abs/2607.29678`
  `https://github.com/asu-idi/toktier`
- [R8] Delta Tokenizer: Reference-Identical Tokenization of Session Deltas and
  Segment-Parallelism for Long Prompts on Multicore CPUs，2026-10-14 提交；
  本次核对摘要，尚未复现实验或通读其全部实现。
  `https://arxiv.org/abs/2610.19801`
- [R9] Lachesis: Lifetime-Aware KV Cache Placement for Agent Serving across
  HBM and High-Bandwidth Flash，arXiv:2610.08378v1，2026-10-06；
  本次核对原始摘要和 PDF 第 4/5 节，区分 HBF 模拟与实机性能。
  `https://arxiv.org/abs/2610.08378v1`
- [R10] EfficientAgent: What Makes KV Cache Offloading Work for Concurrent
  Agents?，arXiv:2609.33762v1，2026-09-27；本次核对原始摘要和 PDF
  第 3/4 节的 working-set 估计、容量条件写入规则与去重边界。
  `https://arxiv.org/abs/2609.33762v1`
- [R11] TokenDance: Scaling Multi-Agent LLM Serving via Collective KV Cache
  Sharing，arXiv:2604.03143v1；本次核对第2.2、4.1–4.4、6.6节，保留其
  PIC 等价对象和模型适用性限制。`https://arxiv.org/abs/2604.03143`
- [R12] SAGA: Workflow-Atomic Scheduling for AI Agent Inference on GPU
  Clusters，arXiv:2605.00528v2；本次重新核对第3–6节的 AEG、概率保留、
  session-affinity batching 和 agent 级公平。`https://arxiv.org/abs/2605.00528v2`
- [R13] AgentSpec: Speculative Decoding for Batch Inference of LLM Agents，
  arXiv:2608.24004v1；本次核对第3.2–3.4节。
  `https://arxiv.org/html/2608.24004v1`
- [R14] ReplaySSM: Fast Memory-Efficient State Replay for State Space Model
  Inference，arXiv:2608.20961v1；本次核对摘要和第3–4节。
  `https://arxiv.org/html/2608.20961v1`
