# 动态多 Agent 的系统层研究筛选与最新 Trace 证据

更新：2026-10-16，已撤下混合状态Host准入候选、取消V80自动启动，
补齐V79b至3600s的迁移阻塞证据与模型通用方向筛选，并准备10秒Poisson
到达的独立负载V81已通过真实首服核验，尚未选定新论文贡献。

## 1. 本次调整

最新决策覆盖下文历史候选：用户明确否定“根据真实后继token前缀决定混合
状态Host写入/保留”作为主线。撤下该候选，不继续为Mamba checkpoint设计
专用控制器。新的研究筛选转向模型通用的迁移阻塞及跨请求干扰，详见4.61；
不预设一次异步工程修复就有足够新颖性，也不恢复更早撤下的三个机制。
V80未启动的自动接续已取消，V1442保留证据；V79b已完成，V81继续负载验证。

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
完成吞吐。V75/V76已完整收集，当前V77用于检验简单原生关闭回收对照，
核心论文机制尚未选定；原生顺序候选仅CPU资格，不把工程减负视为新贡献。

研究范围进一步明确：harness 提供已有的 SPAWN/JOIN、工具和会话身份事件，
主实现及待验证机制放在 serving/runtime 的执行、数据布局、缓存和传输路径。
新设计必须指出实际少做了哪些计算、搬运或同步，以及多 agent 工作流信息
为何比普通请求信息更有用；普通工程修复与 workflow 信息的额外收益分别验收。

## 2. 数据与口径

最新证据优先级是V78b的完整已观测窗口、完整V77/V76和同配置V75 native，
见4.45–4.52；早期“当前运行”段落只保留其当时观察，不替代最新状态。
V76全156项原始duration均值1974.421s，native为1665.374s；前者156 completed，
后者153 completed/3 incomplete，guard和质量差异保留，不能改称质量匹配JCT。
下列V73数据继续作为已完成成本分析的历史依据，不代替最新运行的性能结论。

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
ReplaySSM [R14] 的状态回放与 TreeWY [R15] 的 GDN 推测验证须分开比较。
旧 V1199 中标为 ReplaySSM 的 HTML 实际属于 TreeWY；V1260 补存正确来源，
保留原归档及摘要。其 kernel/模型加速比均不能解释当前 workflow 收益。

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

普通 ReplaySSM decode 与 speculative verification 是两个入口；admission
拒绝 non-NONE speculation 本身不等于拒绝普通 buffered decode。当前
extra_buffer 策略与普通 decode 入口仍有独立兼容性约束，见4.27。

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

### 4.22 原生顺序独立消融与 V76 6000 秒完成窗口

V1251复用上一节审计器，封存完整3600/5400/6000s全到达队列。到6000s均已到达
156项；结果如下，不能删除未完成、失败或guard标签再比较：

| 6000s观察 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| runner finished | 149 | 151 | 144 |
| recorded completed | 149 | 150 | 144 |
| 显式JCT可用completed | 143 | 141 | 141 |
| completed口径全队列截断平均逗留 | 1943.412s | 1654.351s | 1748.971s |

native已观测1项incomplete，另5项未结束；V76有7项未结束，predictive为12项。
上表终点和逗留语义沿用4.21，显式JCT标签不是任务正确率。5400s时V76/native
完成143/140项，而截断逗留仍为1907.026/1613.809s，说明单一时点完成数会改变
相对顺序，不能代替整个到达队列的时延/完成曲线。官方质量和最终结果仍待收集。

为隔离排序因素，在独立应用检出实现b698416并快进合入main，基线为eed5945；
不改引擎、agent输入、工具执行或终态。新开关BELIEFKV_NATIVE_PREFILL_ORDER
默认0，启动时设1后保留调度器传入的原生顺序，跳过因果/长度、驻留、已完成工作、
老化、已恢复及收尾优先级，并关闭该候选遍历中的自定义容量绕行。
标记请求仍需实时身份/会话/attempt、可见性及终态检查，未标记请求位置保持；
512个标记候选上限、semantic revision以及原生PrefillAdder准入和逐层恢复仍在。

该开关不关闭PREPARE、工具时长预测、事件、生命周期和完成回调，也不改变原生
物理分配器。每次规划清除旧promotion标记，原生模式不记录错误的排序hint消费；
低频状态新增prefill_ordering_mode，其他优先级布尔量继续表示原配置。
未来针对V76的无预取排序对照须保持handoff/预测H2D关闭，并继续使用53020ce3
引擎；若同时加入FULL-only候选或native-close，会混合排序与回收因素。
这里隔离的是整套候选排序/绕行边界，不是每个启发式的独立因果贡献。

新增20项用例覆盖默认路径、原生/未标记位置、启动配置、跳过排序工作、容量观测、
过期身份/终态/候选上限、准入计数、PREPARE/完成回调和实际模式记录。
V1248首次回归有4项旧用例失败；V1249在未修改main eed5945复现同样4项，原因
是测试假定关闭预取仍调用预取处理器。仅在两个测试夹具中显式开启被测回调，
未扩大生产派发。V1250对6模块共599项CPU回归全部通过，不覆盖GPU DMA或性能。
首次失败和原main复现回执均保留，不用最终通过覆盖原始证据。

V1252绑定干净候选、源码及三份CPU回执，核对V75/V76/V77应用与两份引擎
来源未变、29份已登记文件SHA256匹配；14:22 PDT的真实V76调度/驱动、V77及
两个质量等待进程仍在。没有登记新GPU实验或更改接续顺序。
先完整收集V76/V77与任务质量，再决定下一候选。排序消融用于排查自引成本，
尚未证明它能消除后续迁移，更不是新的论文贡献；原系统研究目标保持未验收。

### 4.23 重复 context 的 Host 需求集中度

V1253直接重分析V1246绑定的同一2400–3000s窗口行，不重读动态raw。
新增repeated_context_demand按workflow/context隔离并按服务观测排序，
将窗口内首个正Host观测与后续正观测分开。V1254对Host、完成窗口和比较器的
50项离线回归通过，其中4项新增检查覆盖排序、命名空间、缺失身份和空分母。

| 正Host需求观察 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| 正命中请求 / context | 1473 / 145 | 34 / 32 | 273 / 111 |
| 窗口内多次正命中的context | 137 | 2 | 75 |
| 同context后续正命中次数 | 1328 | 2 | 162 |
| 后续报告FULL需求 | 417.048GB | 0.262GB | 40.988GB |
| 占该段报告FULL需求 | 93.07% | 9.32% | 62.63% |

V76相邻正观测间隔P50为21.645s，1328次中1074次属于child、254次属于root。
这说明新增需求的主要规模落在同context后续访问，而不只是首次遇到冷context，
支持下一步优先隔离排序、缓存生命周期及请求周转。它不能证明排序已经是原因；
native只有2个重复观测，不能从其间隔分位数推导普遍行为。

窗口边界左删失，“首个”不等于context生命周期首次；两个正观测间可能有HBM
命中请求，不能称连续模型请求都恢复。context epoch及token前缀可变化，计量
没有物理page/generation证明，不把417.048GB全部算作重复搬运、可避免流量或
可减JCT。间隔包含生成、工具、准入和恢复，不是工具时长预测标签。
当前仍先完成V76/V77和质量收集，原生顺序仅CPU资格，不恢复高频预取、不把
修复自身缓存周转作为论文贡献。生产运行源码和已登记helper均保持冻结。

### 4.24 安全输入 checkpoint 与快速续接的 Host 需求

V1256重新封存原生事件前缀，在同一2400–3000s完整窗口，对每个正Host请求
关联立即前一同workflow/context请求及其完成事件。要求request ID和invocation
匹配、上一完成不晚于当前入队；前一安全checkpoint必须在旧输入内，并出现在
当前有效匹配前缀的节点路径上。中间存在未完成请求时不跳过它寻找更早“命中”。
V1257核验新旧窗口每个请求的既有V1246字段完全一致，并绑定来源与CPU回执。

| 正Host样本指标 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| 关联上一安全checkpoint / 正Host请求 | 1473 / 1473 | 34 / 34 | 273 / 273 |
| 上一完成→当前入队P50 | 0.400s | 547.129s | 0.505s |
| 当前入队→首服观测P50 | 15.574s | 6.387s | 10.898s |
| 1s内续接的正Host请求 | 1273 | 7 | 199 |
| 这些请求的报告FULL需求 | 389.613GB | 0.935GB | 47.737GB |

V76大部分正Host需求发生在上一轮结束后很快入队、且旧安全checkpoint仍在当前
匹配路径上的续接中。输入格式改变本身不足以解释这个队列；排序、等待期间驻留
变化和原生请求周转应优先隔离。这个结论针对Host命中队列，不能外推全请求
快速续接的miss率，native正Host样本也不能代表其全部请求的闲置分布。

上一完成到当前入队包含客户端交付、真实工具/JOIN和应用工作；入队到服务观测
包含准入、恢复和控制。不能相减两个分布中位数分摊时间，也不把15.574s当H2D。
checkpoint完成记录只含node ID及prefix长度，缺creation identity；当前路径观测
不能证明同一物理page、组件forward复用或全部Host需求都可避免。关联也不排除
checkpoint之后的输入变化，更不能据此量化重算。

V1255对3个离线模块共63项回归通过，新增13项检查覆盖epoch推进、安全前缀、
缺失完成、错ID/不同invocation、时间重叠、缺路径、越界及中间未完成请求。
本轮没有改变运行中应用、引擎、工具或GPU登记。V76已观测155个runner结束，
最后一个仍有真实模型服务；V77和官方质量接续继续等待。原生顺序开关保持
独立CPU资格，完整质量/JCT和原生相对收益仍待验收。该项是工程归因证据，
不是新选定的论文机制；既有相关工作约束不变。

### 4.25 已结束队列的更晚时点覆盖与 V76 原始均值下界

固定观察窗口审计此前要求最后一条到达/结束事件时间不早于比较时点，导致native
全部计划任务已结束后，仍拒绝与V76更晚时点比较。现仅在全部计划任务的真实
runner结束都已落在观测前缀内时允许后续时点，保留实际日志时间及完整队列标记；
不补造心跳或无限时间。未完成计划任务和超出观测的结束仍不能充当完整性证明。
失败/未知结果在completed口径中继续累计到观察时点，未从分母删除。

V1258的66项离线回归通过，含3项新增覆盖/失败标签检查；与V1255的63项重叠。
V1259重新封存同表6000/7000s完成窗口，6000s数据与V1251一致。7000s观察为：

| 全156项队列指标 | V76 | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| runner finished | 154 | 156 | 153 |
| recorded completed | 154 | 153 | 152 |
| runner口径截断平均逗留 | 1963.595s | 1666.324s | 1798.399s |
| completed口径截断平均逗留 | 1963.595s | 1679.550s | 1799.569s |

native在7000s前全部已结束，原始runner均值此后不变。V76仍有2项未结束，
其runner截断平均逗留为最终同口径均值的下界，已经比native高297.271s。
因此最后任务结束不能让本轮原始全队列runner均值反超native；不能只等待长尾
结束后期待这个原始比较逆转。当前实时已观察155项结束，与7000s的154项不同。

这个下界严格限于含setup/收集和失败终点的runner口径；native3项incomplete、
guard标签和任务质量差异均保留，不能把结论改称质量匹配JCT或策略因果差距。
completed口径对失败继续计龄，也不能套用runner均值在结束后不变的性质。
V76/V77与质量接续继续冻结；原生顺序消融及已有回收对照仍用于后续隔离原因，
本轮没有新GPU登记或已成立的论文贡献。

### 4.26 原始来源身份更正

V1260保存原始HTML、实际标题和SHA256：ReplaySSM是2610.13778v1，
标题为“ReplaySSM: Cache SSM Inputs, Not State”；2608.20961v1实际是
“TreeWY: Speculative Verification for Gated DeltaNet Hybrids”。
旧V1199错误的文件名、README和指纹原样保留，以本节及R14/R15更正当前引用，
不再把TreeWY的内容称为已核对ReplaySSM。

同批来源包括Hydragen [R16]和Semantic Parallelism [R17]；Sem-MoE是后者
的框架名。共享前缀attention分解已有强方法，不能把一般共享计算作为新意。
专家并行通信方法还需区分当前TP1/EP1、无all-to-all的配置。此次只完成来源
身份和方法边界核对，没有复现论文性能，也没有确立新的workflow系统贡献。

### 4.27 普通 ReplaySSM decode 的预算修复候选

当前引擎普通decode的d/k/g ring按持久Mamba槽分配，原预算路径仅在
spec-verify启用时计入ring。独立候选91a9359/treef231ed24补齐普通decode
每槽ring、padding和剩余内存核算；联合legacy verify scratch仅在主状态侧
计入ring，保持spec专用scratch独立。关闭该功能时保留原有容量推导。

V1261和V1265保留原始复现失败；V1263的39项CPU检查及8个subtest通过，
包含真实CPU MambaPool的GDN/KDA、FP32/BF16、不同ring长度、自动/显式/
radix关闭容量和legacy verify scratch。V1264绑定提交、代码、测试和增量patch，
在临时Git index中从完整53020ce3精确重建候选树，反向应用检查也通过。

该patch已纳入主线保存，未被launcher选用，也未启用新的GPU候选。当前实验
enable_linear_replayssm=false，因此该预算遗漏不是V76退化的原因；当前
extra_buffer与普通ReplaySSM还不兼容，保留原生拒绝检查。CPU预算资格不覆盖
GPU峰值、数值正确性或性能。这是工程修复，不是新论文机制。

### 4.28 V76 后处理恢复与 V77 真实接续

V76全部156个workflow已完成收集，recorded completed为156，保留13个既有
guard介入标签，官方质量仍未完成。原driver因最终历史比较器硬编码当前臂名
predictive_h2d而退出2；65503eb改为接受真实当前臂，同时要求正确native引用
和单一当前运行目录。恢复后补齐原缺失的native_reference_comparison.json，
没有把reactive改名，也没有改写原driver_failed/exit2状态。

V1267–V1269记录完整任务/终态、同表/容量、遥测、旧进程退出和冻结来源核验，
新wrapper仅向原V77 helper提供带失败来源的已验证收集/报告完成凭据。
V1270的117项CPU检查通过；与较早72项有重叠，不相加。V77应用52a2b8e、
引擎76cf97d2及原启动检查不变，没有加入本轮ReplaySSM或FULL-only候选。

V77于2026-10-16 15:20:49 PDT接续，V1216在15:23:51核验真实首服，
scheduler2646481/start_ticks635952682，native close GC启用、原生准入、
同156项到达表与物理池，V1219记录NUMA放置。此前“V77等待”是历史状态。
官方质量队列已开始评分，尚无完整质量结果；完整性能和系统贡献继续待验。

### 4.29 V77 首个完整 600 秒窗口

V1271沿用现有service-window分析器，V1272核对正Host需求，V1273按全部应到任务
统计完成窗口。V77与V75 native的物理池/应用/到达配置匹配，但实际模型工作不同，
且V77与单核官方质量评分重叠；不是正式重复对照或GC因果验收。

| 0–600s观察 | V77 native-close | V75 native |
| --- | ---: | ---: |
| decode token/s | 946.62 | 932.72 |
| 时间加权decode batch | 23.08 | 23.66 |
| prefill worker累计 | 117.517s | 123.824s |
| 入队uncached输入token | 3,642,214 | 3,650,788 |
| D2H | 102.134GB | 139.944GB |
| H2D | 0 | 0 |
| 600s全部应到任务 | 42 | 42 |
| recorded completed | 9 | 7 |
| runner截断平均逗留 | 271.664s | 272.993s |

V77实际关闭回收已生效，但释放量和本窗口D2H差额不能直接充当省下的关键路径
等待。没有H2D只描述本早期窗口，不能用来否定后续压力，也不能说已经超过native。
服务窗口沿各臂首GPU采样计时，任务完成窗口沿runner到达时钟计时，各保留原始
起点，不把两个600s窗口拼成相同的逐请求关键路径区间。
旧native窗口复用V1229，避免重新分析同一已封存报告；V1276绑定文件。
V1271现有CLI没有逐文件前缀摘要，保留为开发观察，不能称正式来源完整性验收；
V1272的Host和V1273的到达/结果核验各有独立封存凭据。继续冻结V77与质量队列。

随后V1277补齐最新600–1200s窗口，三侧均完整覆盖：

| 600–1200s观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1211.45 | 1212.88 | 1323.36 |
| 时间加权decode batch | 42.75 | 44.27 | 46.51 |
| prefill worker累计 | 144.692s | 148.019s | 123.035s |
| D2H | 168.700GB | 226.189GB | 182.522GB |
| H2D | 0 | 0 | 4.171GB |

V77/native的D2H流时间累计20.904/32.441s，decode几乎持平，实际输入/生成/
batch不同。这一结果不支持仅凭D2H差额宣称吞吐收益，也不能把11.537s直接
扣成JCT。V76该局部窗口输出较多并未避免后来整轮原始耗时落后，进一步说明
局部吞吐不能替代全队列完成、质量和最终性能。V1277同样保留现有CLI的来源
前缀限制，未修改任何运行中代码或登记新的GPU任务。

### 4.30 快速续接的完整已服务请求分母

此前V1256只包含正FULL Host样本，不能判断全部快速续接中有多大比例需要Host。
现补充served_request_queue_cohort：native的llm_submit在第一次launch才发出，
ts_ms仍为早先入队时刻。本新队列按入队窗口计入所有已观测服务的请求，包括零/
未知FULL Host计数；未服务请求不在其中，首次服务可在入队窗口以后。
它与原按first-service观测时刻选择的正Host窗口是不同队列，不能共用分母。

V1275重分析同一2400–3000s入队窗口：

| 已服务请求队列 | V76 reactive | V75 native | V75 predictive |
| --- | ---: | ---: | ---: |
| 全部请求 | 3,902 | 4,373 | 4,427 |
| 正FULL Host计数请求 | 1,536 | 34 | 274 |
| 正计数比例 | 39.36% | 0.78% | 6.19% |
| 前次完成后1s内入队请求 | 3,243 | 3,475 | 3,249 |
| 其中正FULL Host计数 | 1,328 | 7 | 199 |
| 快速续接中的正计数比例 | 40.95% | 0.20% | 6.12% |
| 快速续接报告FULL需求 | 408.623GB | 0.935GB | 47.737GB |

三侧本队列的Host计数均有记录；未知仍在代码中独立保留。有效续接要求立即前一
同workflow/context/invocation请求已完成，中间有未完成请求不可跳过。V1274对
Host/完成窗口/比较器85项CPU检查通过，其中13项新增；V1275旧服务窗口的
summary/rows/角色/工作流/context/重复需求/checkpoint字段与V1256逐项一致。
原1473项服务窗口与新1536项入队队列的区别正是窗口口径，不是篡改旧结果。

这补强了“快速续接仍大量落到Host”的规模证据，不能单靠客户端长等待解释，
但仍未证明相同物理页、每字节可避免或额外排序的因果贡献。V76全156项原始
duration均值1974.421s，native1665.374s，质量仍在评分，不能只用预取关闭后的
CPU减负宣布目标完成。下一独立GPU判断优先使用已有native-order候选隔离排序/
自定义容量绕行，其余PREPARE/生命周期、53020ce3引擎与工作负载保持。
不叠加FULL-only、关闭回收或ReplaySSM，避免无法归因；当前没有登记新GPU。
如果原生顺序下该比例仍高，继续核对实际准入和状态回收，不能先把排序写成根因。

### 4.31 V78 原生顺序独立候选已登记等待

已有默认关闭的原生顺序候选现补齐真实启动入口。应用56f2c5e冻结在独立
beliefkv-v1278-native-order-gpu-20261016，复用精确53020ce3引擎和batch32。
V78计划相对V76只改变有效排序/自定义容量绕行及其版本/来源元数据，保留
156项任务、Poisson15s/root、seed21、模型/工具/prompt、PREPARE、工具时长、
终态回收、全部资源和原生需求恢复，预取/handoff仍关闭。
源码相对V76有两份生产文件差异：既有原生顺序分支，以及默认关闭输出IDs/
可选关闭回收计数的遥测变化；不声称逐字节单因素或严格因果对照。

V1280对7模块324项CPU检查通过，含40项新驱动、20项原生顺序、5项无预取、
151项遥测、31项比较器、26项接续环境及51项后处理检查。既有599项顺序资格
通过runtime源码指纹复用，不能与重叠套件相加。V1281绑定应用/引擎/CPU来源，
V1282冻结计划及环境，V1285记录真实接续监督3412628/start_ticks636206265，
2026-10-16 16:03 PDT启动后实测存活。当前状态waiting_for_complete_v77，
尚无V78 GPU服务。原V77及官方质量队列继续，不修改已登记helper或运行代码。
完整计划另存V1287，字节及摘要与V1282登记的raw/ab_plan.json完全一致。

新入口只在完整V77成功收尾、原监督/driver真实退出、GPU/资源与端口释放后
冷启动reactive单臂，用同配置V75 native作历史开发参考。启动核验实际首服、
prefill_ordering_mode=native、正执行计数、同表/容量/NUMA和无预测H2D，
旧resident_first等字段保留为配置。结束后保留全156项、失败/guard标签、HTML、
传输与历史比较；质量仍需官方评分。V1283/V1284/V1286是未来凭据，未生成。
不叠加FULL-only、native-close或ReplaySSM。该项用于排除自身工程损失，
不是选定论文贡献，也不以CPU通过或接续进程存活代替端到端性能。

### 4.32 V77 至 2400 秒及 V76 恢复突增边界

V1288新读V77的1200–1800/1800–2400s完整服务窗口，V1289复用V1243的
历史封存结果作比较基础，V1291另封存1800–2400s的Host与续接队列。
V1292使用全应到任务的到达/终态凭据，不把未完成任务从分母删除。

| 1800–2400s服务观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1300.05 | 1296.69 | 1317.23 |
| 时间加权decode batch | 47.46 | 47.43 | 47.47 |
| prefill worker累计 | 118.737s | 108.095s | 121.316s |
| D2H | 169.214GB | 206.565GB | 174.662GB |
| H2D | 0 | 0 | 6.089GB |

1800s的111项应到任务，V77/native/V76完成49/49/40项；2400s的149项完成
64/64/54项。2400s的runner截断平均逗留为732.122/744.667/828.759s，
不是最终JCT或质量匹配收益，且不能与GPU服务窗口拼成相同关键路径。
V77与native本窗口均无正FULL Host服务样本；V76有33项，其中入队队列的
1s快速续接仅1/3756项为正计数。下一2400–3000s旧V76窗口却有1473个正
Host服务样本，说明该现象具有明显阶段性，不能用全程平均或早期吞吐解释。

进一步复核V1275的正Host服务样本：V76的1473/1473项入队→首服观测均不少于
10s，native为0/34项；V76中1s内客户端续接的1273项仍全部排队超过10s，
P50为15.601s。冻结V76 runtime的waiting_rank确有10s老化分支，当前原生
顺序候选会跳过这组额外排名。这是需要GPU消融验证的关联，不能因时间接近
便宣称该阈值已被证明造成缓存失效，亦不能将所有报告Host字节认作浪费。
若V78的额外恢复和排队不下降，应否定这一解释并继续查准入/实际工作集。

随后V1293/V1294/V1295补齐V77的2400–3000s完整窗口，出现重要反证：

| 2400–3000s观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1093.70 | 1304.05 | 1009.65 |
| H2D | 174.643GB | 7.190GB | 637.786GB |
| 正FULL Host服务样本 | 416 | 34 | 1473 |
| 1s续接队列中正FULL Host请求 | 332/3367 | 7/3475 | 1328/3243 |
| 该队列正计数比例 | 9.86% | 0.20% | 40.95% |

V77明确没有额外工作流排序，却也出现显著恢复与吞吐下降，因此额外排序不是
该现象的必要条件，不能把10s关联升级成唯一根因。V78仍用于测量排序/容量
绕行的贡献，同时需要沿终态回收、有效FULL/Mamba前缀、实际准入和工作集核对。
单轮轨迹/资源不同，当前也不能直接宣称GC导致全部退化。V77的416个正Host
样本均可关联立即前一完成请求的安全输入checkpoint到当前匹配路径；这不是
逐页/代际复用或“节点被删除”的证明，不能先按某种缓存故障命名。

V1297在runner日志真实覆盖3000s后保留全部156项，V77/native/V76完成
74/83/66项，runner截断平均逗留1032.496/1027.185/1148.225s。V77先前
2400s的小幅均值优势未延续，不等待长尾后再以早期窗口宣称成功。此前同数值
时点的首次任务审计被覆盖检查拒绝，V1296没有生成；没有放宽规则或改造日志。
质量仍在评分，完整JCT/有效吞吐与新的系统贡献继续未验收。

### 4.33 恢复压力、实际上下文与准入阶段成本

V1301新增只读审计，复用已有geometry读取器，按经过验证的native/client时钟
连接2400–3000s完整服务窗口、既有客户端压力和Host采样。V1300的30项检查
覆盖新审计器及已有geometry分析；V1298的初始7项与之重叠，不能再相加。
首次V1299把同流eviction事件误计为未知occupancy样本，原件保留，由V1301
严格筛host_pool_usage更正；geometry及已知占用值没有变化。

| 2400–3000s观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| 观察到的decode平均上下文token | 22105 | 22545 | 21356 |
| 48请求、22528-token桶worker均值 | 21.678ms | 22.268ms | 21.877ms |
| 47请求、22528-token桶worker均值 | 103.411ms | 50.630ms | 120.778ms |
| 客户端排队计数采样P50 | 59 | 51 | 76 |
| 活跃FULL占用比采样P50 | 0.458 | 0.464 | 0.436 |
| Host FULL占用比采样P50 | 0.999900 | 0.999894 | 0.999936 |
| Host Mamba占用比采样P50 | 1.0 | 1.0 | 1.0 |

表中decode分桶均保留实际graph=true。满48区间接近，47等换批区间明显不同；
worker区间含调度/结果处理，不能认作47请求kernel本身慢一倍。上下文是可见
batch/request统计，不是去重后的物理工作集；分桶也不是相同请求或相同计算。
原生metrics明确used=total-available-evictable，当前客户端resident_pressure
由num_used_tokens/FULL容量计算，排除可驱逐缓存。表中约46%不代表HBM有54%
空闲，亦不提供Mamba总驻留。不能据此将恢复增加归为“容量明明足够”的策略错误。
V77边界采样间实际关闭175个session、释放1589469个设备FULL token及359个
设备Mamba槽，但这些累计值不证明它们与后续恢复为同一物理页，也不能证明GC
是恢复突增原因。

V1303先复用既有prefill阶段分析，V1306进一步逐请求连接原生llm_submit
Host计数与同clock domain/forward/request的短prefill采样。短请求定义为
单请求、实际native extend<512 token；不是按旧缺省字段猜测。V1305的23项
检查覆盖7项精确关联用例及16项已有phase分析，与V1300合计53个不同CPU用例。

| 短prefill采样及选批线程CPU | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| 所有短prefill样本 | 98 | 124 | 112 |
| 正FULL Host组样本 | 11 | 0 | 37 |
| 正FULL Host组选批CPU均值 | 158.750ms | 未采到 | 89.180ms |
| 已知FULL/Mamba均零组样本 | 87 | 124 | 75 |
| 已知零组的选批CPU均值 | 5.314ms | 3.694ms | 6.919ms |
| 正FULL Host组占采样选批CPU | 79.07% | 未采到 | 86.41% |

关联按完整request ID，不用时间最近样本替代；零FULL但缺Mamba观测的情况单列。
正Host少量样本集中了大部分采样选批CPU，因此下一步优先分解原生需求恢复、
为其分配容量时的驱逐/写回与同步路径，而非仅增加预取扫描或继续查controller。
这仍只是关联：采样选批包括全部native batch准备，不提供其中各函数独占时间；
线程CPU可含底层驱动工作，不能直接称全为Python算法或拷贝等待。
一轮16步采样不外推为全程成本，亦不能把158ms当每次H2D可节省的JCT。
V77零Host组的run_batch线程CPU仍为54.398ms，native为39.538ms，说明恢复
关联尚未解释全部forward差距。须继续核对实际prefill geometry和执行路径，
不能将这两组不同请求的均值差直接归因关闭回收。

### 4.34 V77后续窗口与首份官方质量结果

V1302新读V77的3000–3600/3600–4200s完整服务窗口；V1304另读同配置历史
V75 native/V76的3600–4200s，未启动新的native GPU。V77 H2D从前一窗口
174.643GB降为3000–3600s的4.267GB及3600–4200s的2.763GB。突增是阶段性
现象，不能将其外推到整段稳定高压。

| 3600–4200s服务观察 | V77 native-close | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| decode token/s | 1307.888 | 1212.677 | 1268.590 |
| decode加权batch | 46.765 | 38.553 | 46.293 |
| 排队计数均值 | 9.382 | 0.674 | 15.531 |
| H2D | 2.763GB | 1.108GB | 5.197GB |
| D2H | 154.155GB | 207.155GB | 166.705GB |

该窗口V77原始decode较高，但native已接近无排队，剩余工作和有效并发不同。
不能因此宣称GC加速或掩盖前段落后。V1307使用runner到达/完成时钟保留全部
156项：3600s三臂完成89/105/80项，runner截断逗留1313.327/1267.947/
1476.103s；4200s完成116/124/115项，截断逗留1522.430/1426.191/
1704.592s。完成样本、资格和未知项单列，均非最终或质量匹配JCT。

原单worker官方评分队列已完成V76 reactive，156项严格分成101 resolved、
54 unresolved、1 empty patch，无evaluator error/incomplete。空patch为
django-12858，不能遗漏后以155作分母。101/156约64.74%是官方正确率，
native和predictive评分尚未齐，不能宣称质量匹配。
所有156项原始outcome为completed，其中11项原生JCT资格为false，保留既有
guard/语义终态原因；97项同时正确且原生JCT合格。冻结scorer要求完整原生JCT
资格，故维持measurement_complete=false及有效完成吞吐null，不更改标签或
剔除这些任务。评分完成与性能测量完整是两个条件。

V1308绑定V1298–V1307输入及官方摘要/原始summary摘要，并于16:48 PDT核对
真实进程incarnation：V77监督/driver仍活跃，V78仍等待完整V77，官方worker
正在评分V75 native，V77质量等待器继续。V77/V78及质量helper保持冻结。
本次变更只含离线审计、测试和文档；研究目标未完成，具体论文机制仍未选定。
继续独立V78消融，并依据上述关联分解原生恢复的实际CPU/同步成本；不得把
工程修复、池配置或一轮局部速率当作新的系统贡献。

### 4.35 选批中的同步写回与独立跨分支合并候选

V1310新增只读物理receipt对齐。来源为V1306已精确配对的短prefill样本、
原压力/时钟来源及stat封存的transfer_telemetry前缀。按direction/command ID
去重检查，不用相近时间的另一次传输替代目标；只报告选批内提交及是否在其
结束前ACK，未知/未完成receipt不充当成功。V1309的5项CPU检查通过。

| 正FULL Host短prefill样本的选批区间 | V77 native-close | V76 reactive |
| --- | ---: | ---: |
| 样本数 | 11 | 37 |
| 有D2H提交的样本 | 11 | 37 |
| D2H批次，均在选批结束前ACK | 43 | 108 |
| 记录的D2H节点数 | 590 | 1375 |
| D2H字节 | 6.372GB | 15.792GB |
| D2H transfer-stream累计 | 1.440s | 2.842s |
| H2D批次 | 11 | 37 |
| H2D字节 | 5.614GB | 15.809GB |
| 选批结束前完整H2D ACK | 0 | 0 |

原生`UnifiedRadixCache._evict_components`在需要write-back的设备驱逐上调用
`writing_check(write_back=True)`，后者对finish_event执行synchronize，再释放
容量；恢复自身则通过controller.load排队。表中支持进一步检查“为恢复先腾
空间”的同步链，而非把整个长尾都称H2D传输慢。H2D已提交而ACK尚未返回，
不能改称尚未传输。时间对齐没有函数栈，仍不证明每批由哪次分配调用触发；
stream含依赖和dispatch间隙，三种时间不能相加、相减或外推成可减JCT。

现有V75/V76批处理只把满足原生优先次序的一条祖先链合并；遇到另一分支更早
的victim就停止。为减少多分支树上的重复提交/阻塞，建立独立CPU工程候选：

- 应用主目录仅保存增量patch和证据；引擎独立检出为
  `sglang-v1311-frontier-writeback-cpu-20261016`。
- 基线snapshot0fa4118的完整tree为已使用的53020ce3，不叠加native-close、
  waiting-FULL-only或ReplaySSM。候选commit98b2670、treec8424eb3。
- `SGLANG_HICACHE_WRITE_BACK_BATCH_FRONTIER`默认关闭，节点预算仍由原参数控制。
  开启时，规划器按原堆优先级合并不同分支及虚拟暴露的父节点。只查看二叉堆
  需要的头部，不额外复制整个原生堆；不在规划阶段改变实际节点、锁或堆。
- 仍使用原生backup、CacheOperation合并与ACK。成功入队的前缀统一ACK后才
  demote；无Host容量、部分成功、异常及ACK失败均保留原保护。修复批量释放后
  每个分支父节点的堆入口，避免只保留最后一个分支。

V1314最终196项引擎CPU检查通过，其中34项新用例。真实CPU cache/allocator
按2种缺额、8种优先级seed与single/chain/frontier三模式比较，实际设备victim
前缀顺序、释放容量和Host页映射一致；真实FULL/Mamba夹具另检验两池映射。
补充部分Host容量、enqueue异常、ACK失败及4096候选中只读所需堆头的检查。
初始V1311的19项失败来自未初始化的假游标及重复复用模拟controller；
V1312剩余两项是测试在后续victim未backup/ACK前直接demote。修复夹具后，
未放宽生产保护或改判失败；原记录保留，V1313/V1314不按重叠用例累加。

V1315另运行32组真实CPU FULL树夹具，三模式都释放192节点/384token、顺序
相同，模拟物理提交single/chain/frontier为192/135/32。这不是实际trace中的
可合并次数，更不是吞吐改善比例；小CPU池和模拟DMA不提供GPU/PCIe/JCT证明。
并批时同时保护多个Host目标，会改变暂存/Host回收机会；multi-rank和实机
影响未验收。V1316绑定源码/CPU/patch，并用临时index从53020ce3精确重建
c8424eb3；增量patch未替换规范staging patch，也没有注册新GPU。

该候选属于共用路径工程优化。若在独立V78后选择实机验证，native和新策略
必须采用同一共用引擎；不能把修复基线或减少同步本身冒充workflow独有创新。
现阶段既没有选定新论文机制，也没有恢复旧三机制为既定贡献。

### 4.36 最新V77任务窗口及运行边界

V1317新读runner实际日志覆盖的6000s窗口，保留全部156项。V77/native/V76
记录completed为149/150/149，runner已结束149/151/149项，native另1项
incomplete；原生JCT合格completed分别147/141/143。runner截断逗留均值
1769.327/1654.199/1943.412s，资格与失败分开，不是最终或质量匹配JCT。
V77在更晚任务窗口仍落后于native，不能由恢复后段变少或局部decode较高宣布
关闭回收取得净收益，亦不能仅因负结果停止完整收集。

V1318绑定本轮所有新输入及限制，17:17 PDT逐一核验真实process incarnation：
V77 scheduler2646481、监督2645733和driver2646017活跃；V78监督3412628
仍等待完整V77；原官方评分监督3806721/worker3802150正在评分V75 native，
V77质量等待器1121673继续。分别从两份冻结检出执行verify_source成功，
原app、engine、plan、helper和CPU凭据均保持。下一GPU仍按原V78登记，
不得在等待期间偷偷替换为跨分支候选。研究及完整性能/质量目标继续未验收。

### 4.37 写回与完整恢复读取之间的数量边界

V1319/V1320新增独立只读审计，分别选取前3000/6000s提交、已完成的D2H，
再把同一臂封存前缀内的全部已完成H2D都算作可能读取，即使读取早于写入、
位于cohort之外，或是同一Host源向cache和request CoW的重复目标复制。
按direction/command ID拒绝重复receipt，验证时间、pool单位数与实际字节
完全一致。8项专用检查及7项既有依赖检查通过；来源、时钟和完整行前缀摘要
保存在报告中。表中GB均为十进制，读取范围是各臂完整观测前缀，并非6000s窗。

| 臂 | 前6000s FULL写回GB | 前6000s Mamba写回GB | 全部FULL读取GB | 全部Mamba读取GB | FULL来源份数上界 | Mamba来源份数上界 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V75 native | 806.130 | 1071.564 | 3.952 | 9.658 | 0.490% | 0.901% |
| V77 native-close | 599.404 | 956.694 | 123.417 | 61.170 | 20.590% | 6.394% |
| V76 reactive | 1258.966 | 1185.919 | 775.320 | 334.952 | 61.584% | 28.244% |
| V75 predictive | 581.635 | 1017.284 | 73.851 | 53.507 | 12.697% | 5.260% |

边界的对象是已完成物理写副本：设某池cohort写入W份、全部读取目标为R份，
最多min(W,R)份不同写副本能作为这些读取的来源。每次Host读只使用一个确定
版本，重复目标只能放宽这一上界。规范Mamba源码BACKUP_HOST复制完整状态，
LOAD_BACK可从同一Host源复制到cache和request两个目标，因此按目标计数尤其
保守。它不需要假定node ID等于物理generation，也没有给具体checkpoint贴上
“未复用”标签。FULL单位20480字节，Mamba单位64389120字节。

这些比例不是cache hit rate、逐页复用率或在线可安全丢弃集合。正在传输的
部分层、未完成读取与封存之后的未来读取不计；低比例也不能否定释放HBM、
保护未来恢复的价值。流时间含依赖/dispatch，写入GB不能直接换算可减JCT。
V75/V76/V77的实际模型路径与工作集不同，不能由表中差额宣称策略因果收益。

文献重新核对收紧了新颖性边界：Marconi [R6]已明确报告其测量中仅0.4%的
SSM缓存状态被复用，并按输入共享/最终输出前缀做checkpoint准入；EfficientAgent
[R10]已有Host reuse-working-set估计与容量条件写入准入。因此“大量Mamba写后
未读”“少备份”“只留有用checkpoint”均不足以成为新贡献。当前源码也已经保留
安全输入session leaf，不能把这一既有功能再当新机制实现。

后续先回答两个可证伪问题：哪些写入在当时已有可识别的失效/续接依据，以及
Marconi式checkpoint准入与EfficientAgent式Host准入的合理组合能否拿到全部收益。
若简单组合已足够，作为共用工程对照收尾；若仍有可观的动态fork/join独有成本，
再据精确身份、消费和关键路径证据选择机制。暂不添加新的GPU候选，不把减少
传输量本身当论文验收，也不恢复已撤回的旧三机制为既定方向。

### 4.38 V77完整终态、native门槛与V78接续恢复

2026-10-16 17:40 PDT附近，V77最后一个django-16938结束，runner的submitted/
started/finished均为156。最终summary为155 completed、1 incomplete，无workflow
异常；原始均值1814.634s，对照native1665.374s，采集时长8276.154/6841.743s。
这仍是包含全部终态的原始耗时，不是官方质量匹配JCT。两侧实际多轮workflow为
31/30个。完整V77的D2H/H2D为1672.269/184.587GB，native为1935.037/13.610GB；
少写约262.768GB没有转成该口径性能优势，不能证明关闭回收已成功。

唯一incomplete为django-12708：children和JOIN均完整，root仍在解释修复与测试
矛盾，既有graph-budget finalization/guard介入；最后正文未形成语义完成。
deadline未到期，无取消，不将该任务改记自然RETURN或从分母移除。V77中native
JCT合格共148项，另7个completed同样保留既有guard/语义终态不合格标签。

原`run_deepagents_swebench.py --gate native`在合格数不等于156时返回exit1。
冻结V77监督将此当作采集失败，在执行后处理前停止；原V78随后按原规则停止。
这是“完整采集”和“native验收通过”的接线混淆，不能靠放宽论文/质量门槛恢复。
V1324的33项CPU检查含18项恢复用例、8项读写审计及7项依赖检查，保留不同
终态/资格，拒绝缺任务、重复finish、异常、缺startup、未知退出和未排空遥测。

独立`resume_v78_after_complete_v77_20261016.py`逐项核对156份result与summary、
全部runner结束、真实进程退出、已冻结源码和startup、遥测零drop/failed及队列
排空，再调用原V77 postprocess。V1321封存原始失败state、exit1、全部标签及
独立恢复来源；原失败state不覆盖。完整HTML和同任务/同物理容量比较均已生成，
原V1217完成凭据只表示完整开发采集和质量未评分，native_gate_passed仍为false。

V1322/V1323登记独立接续，17:49 PDT启动监督230068/start_ticks636843838，
driver230265/start_ticks636843929真实存活。新动态state为
`ops/v78_native_order_recovered_20261016.state.json`；旧停止state原样保留。
仍由原冻结V78 helper验证原56f2c5e应用、53020ce3引擎、参数、来源与资源，
只替换等待的已验证collection状态和监督输出路径。没有修改运行源码、策略、
工具、到达表、质量门槛或V77/V78冻结文件。该次尝试随后因独立观测器参数错误
被原监督终止，未生成V1283；下节记录新目录重试，不能将旧尝试视为完整实验。

### 4.39 NUMA观测器的reactive兼容与V78b新目录重试

V78实际原生排序已出现，但首服验证调用旧`observe_v55_numa_placement.py`时，
argparse的choices只含native/predictive_h2d，拒绝reactive并返回2。冻结监督
按原错误分支停止其driver。17:52后的旧监督/driver均已退出，V1325封存失败
state/log及关联V1322来源；原V78部分raw不删除、不覆盖、不冒充全量完成。
这属于观测/启动接线缺陷，不是GPU策略错误或新颖性证明。

独立`observe_owned_scheduler_numa.py`要求显式run-root，支持三种实际臂名，
继续验证同用户scheduler PID/start_ticks、容量census与读取前后进程存活，
记录原始/proc NUMA文本摘要和实际驻留字节。它不迁移内存，也不把large map
当作准确pool身份或带宽证明。旧已登记observer文件保持不变。

V1332最终39项CPU检查通过，含6项新observer/重试检查及前述33项。先前V1331
的同39项通过记录保留；首次prepare的Path类型接线在注册前修正，未启动GPU，
最终凭据只绑定修正后的源码，不把两个重叠suite相加。

V1326登记新run目录`qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order`。
原应用56f2c5e、引擎53020ce3、原V78计划的全部字段和环境逐项一致，唯一允许的
环境差异为RUN_ROOT。独立入口继续调用原冻结helper的资源、启动、监控和后处理；
只把其只读NUMA观测命令定向到新增observer，注册并校验两层helper及新observer
的来源摘要。它没有跳过startup、弱化容量/实际排序条件或修改runtime/harness。

V1327于17:57 PDT启动监督243510/start_ticks636892226，新state是
`ops/v78b_native_order_20261016.state.json`。V1328于18:00:31 PDT核验真实首服、
正native排序计数9、全部到达表/物理容量和PREPARE/工具时长保留、预取关闭；
V1330完成实际NUMA观察。18:01再次验证冻结来源与环境，native排序累计370，
额外排序/预取计数均0，physical_disabled=false。scheduler244015/
start_ticks636893629及driver243802/start_ticks636892336真实存活。该证据
证明启动配置，不能外推完整性能或因果收益；V1329仍留给完整结束。原官方
质量队列继续，原始负结果与研究目标均未改判。

原质量队列于17:50 PDT完成V75 native官方评分，原摘要
`v1182_v75_native_quality_inputs_20261016/official_quality_summary.json`保留
全156项：95 resolved、59 unresolved、2空patch，无evaluator error/incomplete。
空patch为django-11490/13028。冻结评分器的correctly_completed_ids为90项，
measurement_complete=false、有效完成吞吐为null；原3个native incomplete及
resolved但未正常完成任务均保留。V76为101 resolved、54 unresolved、1空patch，
不能仅比较resolved个数宣称性能或质量匹配JCT优势。真实worker231698/
start_ticks636850048正在评分V75 predictive，V77质量等待器1121673继续。

### 4.40 写回角色的可识别范围与默认关闭的路径观测

V1334在V1320同一封存transfer前缀与0–6000s写入cohort上，关联原生请求事件。
只使用D2H submit之前严格更早的事件；新请求开始、空/缺失snapshot会把先前
锚点的当前状态改为unknown。只有单节点且Mamba单位为1的receipt才赋角色，
多节点/分池身份不能确定的部分单列。V1333的26项CPU检查含11项新用例，
覆盖未来信息、同刻事件、共享锚点、新请求、未知snapshot、冲突及重复证据。

| 0–6000s Mamba写副本 | V75 native | V77 native-close | V76 reactive | V75 predictive |
| --- | ---: | ---: | ---: | ---: |
| 总份数 | 16642 | 14858 | 18418 | 15799 |
| 可关联单节点份数 | 16298 | 13842 | 14202 | 15158 |
| 最后观测仍为当前安全锚点 | 1014 | 37 | 173 | 174 |
| 先前安全锚点、当前状态未知 | 27 | 38 | 164 | 43 |
| 先前安全锚点已被观测到的新锚点替代 | 369 | 24 | 345 | 419 |
| 此前未被观测为安全锚点 | 14888 | 13743 | 13520 | 14522 |
| 无法分配单节点角色的份数 | 344 | 1016 | 4216 | 641 |

即使允许使用未来snapshot，native/V77的可关联写副本中，也只有1410/99份曾被
观测为安全锚点。该额外覆盖数明确不作为在线输入或动作标签。native已知被替代
的369份只占总Mamba写入约2.22%，约23.760GB；不能靠这一小类解释1071.564GB
写回。剩余大类是“当前证据不知道”，不是“无用输出状态”或“可以安全丢弃”。
逻辑node/prefix关联不补造物理generation、真实session_ref或未来消费证明。

为辨明未知写回，新增独立诊断模块`checkpoint_path_probe.py`，由
`BELIEFKV_CHECKPOINT_PATH_SHADOW=1`显式开启，默认0。仅在模型请求结束、
cache_finished_req已处理后，从req.last_node沿实际缓存路径读取至多256个节点；
记录Mamba状态的node/creation_time、相对本次输入的prefix位置、引用计数、
树分叉数量及设备/Host存在性。输入前缀、恰好输入边界和生成输出尾部分开，
不把边界状态直接当可复用。它不读取tensor内容、不分配或迁移KV、不改引用。
这是scheduler线程的同步完成时采样，序列化继续由原后台writer处理；每次
另记wall/thread CPU，实机开销仍须计入，不能由CPU资格宣称无额外成本。

V1338最终187项CPU检查通过，包括原151项遥测、新10项路径观测及前述26项，
用例重叠不相加。V1337初始两项失败是新夹具遗漏原生launch descriptor的
mamba_forward_candidates；只修夹具，未放宽生产路径。V1339绑定源码、初始/
最终检查及18:19 PDT实际进程验证。新观测未部署V78b、未登记新GPU实验。
它只弥补归因缺口，不是新缓存策略或已选定的论文机制。下一步须连接实际写前
状态、精确prefix兼容与后续消费，再对比Marconi/EfficientAgent的合理组合。

### 4.41 V78b首1200s的完整窗口

V1335新封存0–600s实际服务窗口，与V76/native按各自已验证GPU时钟比较：

| 首600s | V78b native-order | V76 reactive | V75 native |
| --- | ---: | ---: | ---: |
| Decode token/s | 950.070 | 1001.397 | 932.722 |
| 时间加权decode batch | 24.185 | 25.683 | 23.655 |
| Prefill worker累计s | 122.009 | 119.633 | 123.824 |
| D2H GB | 116.879 | 120.021 | 139.944 |
| H2D GB | 0.566 | 0.758 | 0 |
| PREPARE_HOST GB | 1.155 | 1.781 | 0 |

这时实际running仅约24，恢复只有4/5/0批，不能回答后续高压段的恢复与排序
根因，也不能由decode略高于native宣布加速。V1336保留全部42个应到任务，
分别记录完成7/5/7项，runner截断逗留282.809/287.924/272.993s；尚未完成
任务都在分母内，非最终或质量匹配JCT。V78b相对V76完成更多，但未优于native。

18:19 PDT验证V78b监督243510、driver243802及scheduler244015真实存活；
冻结source/env检查通过，native排序计数9035，额外排序/预取计数0，
physical_disabled=false。原官方worker231698仍评分V75 predictive，
V77等待器1121673继续。当前不热改实验，也不据早期结果注册替代GPU候选。

随后V1340补齐600–1200s窗口，V78b/V76/native的decode为1286.643/1323.358/
1212.875token/s，时间加权batch为46.575/46.514/44.270；prefill worker累计
134.826/123.035/148.019s。H2D为4.744/4.171/0GB，D2H为187.955/182.522/
226.189GB；V78b含3.738GB PREPARE。排序关闭仍出现恢复，说明额外排序不是
这一阶段恢复存在的必要条件，不能由此推断后续大量恢复的全部原因。

V1341保留1200s前全部75个应到任务，完成22/22/28项，runner截断逗留均值
514.824/528.719/484.254s。V78b与V76相比该原始口径有所改善，仍落后native。
V1342核对实际平均decode context为21577/22014/22068 token；running中位数
47/47/45，queue中位数5/5/0，三侧Host Mamba占用样本均达满池，Host FULL
中位占用约30.56%/27.30%/42.59%。resident_pressure仍排除可驱逐缓存，不能
把约0.43的活跃占用当作HBM实际空闲比例。

源码核查也排除了一个过于简单的解释：`_evict_backed_join_parent`在最老无锁
候选session_ref为0时已直接返回，不能未经实际victim证据就宣称它无视所有
无引用缓存而优先牺牲parent。后续继续核对真实驱逐对象、prefix兼容与消费，
同时观察后续压力窗口；这些数据还未建立最终JCT、质量或新机制收益。

### 4.42 生成前缀精确兼容性诊断的主线整合

在4.40默认关闭的路径观测上补齐下一请求的实际token比较。请求完成时只在开关
开启后快照其生成输出，复用同一次checkpoint路径采样；既有后台writer在最多
1024个context历史中保存实际输入、生成输出及路径证据。下一同作用域请求到来
后，先比较旧输入，再比较旧生成输出的连续前缀；对实际观测checkpoint分别记录
exact_prefix_compatible、token_prefix_diverges、new_input_ends_before_checkpoint
或outside_observed_transcript。历史容量淘汰、会话/命名空间变化和epoch倒退
不补造兼容性结论。新事件不写入原始token ID，默认关闭时不快照生成输出。

V1345的203项CPU用例通过，包含16项新增兼容性、边界、作用域及容量用例；
主线整合时同套203项再次通过，两次不相加。V1352绑定当前四份源码/测试与原始
XML和独立原生资格，未覆盖V1339的旧源码哈希。同步完成时采样/输出快照与
后台比较的成本仍需实机计量；当前冻结V78b未部署该诊断。

精确前缀不符不能独立识别序列化、模板或工具轮次的原因；前缀兼容不证明状态
仍驻留、被forward消费或曾作为物理传输来源，也不授予全局安全丢弃权限。
生成末token可能尚无缓存，结论只涉及观测到的checkpoint位置。当前完成的是
系统归因能力，不是新缓存机制或性能验收。

### 4.43 原生Mamba路径上限是必须保留的简单对照

当前引擎已有mamba_max_states_per_path，默认-1。其驱逐路径优先移除浅层、
符合条件的设备Mamba状态，保留FULL及已有Host副本；尾节点、树分叉、锁定
状态和设备叶节点受到保护，故它是软上限。源码没有单独保护session_ref，
小上限可能移除下一请求需要的输入checkpoint，不能假设cap=2天然合适。
当前实验配置不变，也不把这一原生参数或少存SSM状态本身当作新贡献。

V1347首次直接运行既有测试模块为3失败、4通过、5跳过，失败来自fake tree
未初始化已存在的host eviction observer；CUDA夹具在CPU环境跳过。独立脚本
qualify_native_mamba_path_cap_cpu.py仅把该fake构造字段补为生产默认None，
选择原TestMambaPathStateCap，保留全部原断言与驱逐条件，7项CPU用例及2项
子测试通过。V1348/V1349保留首次资格，V1350/V1351补齐准确来源标识：
引擎HEAD树0fa78f29657df0caa5520eca15bde377d19b9411，实际规范patch树
53020ce3efef3ed362e9af0db77147b39070f064。两者不能混为同一部署源码。
这只验证既有CPU策略/CLI，不证明GPU开销、读写减少、重算或JCT收益。

### 4.44 完整native机会规模与本次封存窗口

V1346审计完整V75 native请求事件文件61695327字节，SHA256为
a2cc76a6d3130d20325ed204068129118d3da7da8fcb6ac9f4abaac7868445cd。
36786个请求共有775765383输入token，734072267有效缓存命中，41693116未命中。
FULL匹配超出有效混合前缀3086927 token，占未命中的7.404%；正差距17278项的
P50仅36 token，差距至少512 token的774项共2505259 token。因此不能由巨大
写回字节推导出同等规模的可恢复计算或JCT空间。

首次观测context请求1140项占14984073未命中token，普通续接35646项占
26709043；“首次观测”包含root、child、内部摘要等，不能全部称摘要。前端
chat转换含模板tokenization的P50为51.815ms，累计/并集1918.133s，是墙钟
跨度，非独占CPU、暴露GPU空闲或可减JCT。既有旧输入缺失代理约0.157%，
未覆盖旧生成输出，不能据此声称全量无重算；新诊断用于补齐这一证据缺口。

本次代码整合一并封存V1343的1200–1800s窗口，V78b/V76/native的decode为
1317.33/1339.85/1333.46 token/s，加权batch为47.519/47.501/47.494，
decode worker均值28.330/28.314/28.300ms；H2D为4.128/7.093/0GB。
V1344保留1800s时全部111个应到任务，完成46/40/49项，runner截断逗留均值
664.747/684.125/613.734s。该历史截面不是当前运行终点、完整JCT或质量匹配
收益；它没有建立优于native的结论。继续以serving/runtime系统证据筛选机制，
整体研究目标保持未验收。

### 4.45 V78b后续压力窗口排除了哪些简单解释

V1353补齐1800–3600s服务窗口。2400–3000s有：

| 600s完整窗口 | V78b原生排序 | V76 reactive | V75 native |
| --- | ---: | ---: | ---: |
| Decode token/s | 1201.168 | 1009.645 | 1304.050 |
| H2D GB | 275.811 | 637.786 | 7.190 |
| FULL H2D GB | 190.044 | 448.096 | 2.811 |
| D2H GB | 437.555 | 725.583 | 224.260 |
| PREPARE GB | 0.171 | 0 | 0 |
| 平均decode context token | 22239 | 21356 | 22545 |
| 采样queue P50 | 63 | 76 | 51 |

V1355保持真实graph标记、batch及2048-token上下文桶。V78b满48请求的四个
匹配graph桶与native的worker均值比为1.001以内至1.009；47请求的22528-token
桶则约1.508，不能解释为同一个纯CUDA kernel慢了50.8%。worker区间含重叠/
调度周转。三侧Host FULL与Mamba均接近满池，reported resident_pressure仍
排除可驱逐缓存，不证明HBM还有约一半可随意使用。

V1356将真实请求ID、前后完成和匹配路径连接：V78b正FULL Host首次服务请求
666项，504 child/162 root，合计190.044GB报告需求；均关联上一安全输入
checkpoint节点。该报告没有creation identity，不升级为物理页或forward复用。
同一context窗口内后续正Host观测548次，占报告字节84.78%，不等于重复搬运
同一批页。按入队窗口、完成后1s内续接的已服务请求，V78b/V76/native正Host
为569/3229、1328/3243、7/3475。两种队列/服务分母仍严格分开。

V1357的一比十六采样短单请求prefill中，V78b正FULL Host15项选批线程CPU
均值94.150ms、累计1412.245ms；零Host77项为4.551ms、累计350.412ms。
正组占该采样选批CPU80.12%。V76正组37项为89.180ms、占86.41%；native
124项零Host为3.694ms。恢复与慢选批的关联继续存在，额外排序并非必要条件。
不能由不同请求组差值分摊函数、外推全部请求或宣布可减JCT。与4.35同步写回
证据一起看，下一步必须同时核对恢复为何增加及原生容量回收路径，不能只扩预取。

V1360补齐随后3600–4200s的完整窗口，V78b/V76/native H2D降至
2.246/5.197/1.108GB，D2H141.925/166.705/207.155GB；decode为1311.798/
1268.590/1212.677token/s，但加权batch为43.413/46.293/38.553。高恢复不是
整段运行的恒定现象，后段原始速率也受剩余工作/排队和batch影响。应核对真实
活跃工作集及其反馈，而不能只挑恢复多的窗口或以之后速率较高宣称最终加速。

### 4.46 等待卸载的窗口反证与不可提前选定的机制

新增只读audit_runtime_lifecycle_window.py使用已验证服务时钟、实际机会事件
和前后runtime状态。V1358的15项CPU用例通过，覆盖精确半开事件窗、不同快照
边界、缺失覆盖、计数回退、非法单位和异步落盘乱序。V1359在2400–3000s记录
V78b/V76的parent_pressure_demoted事件均0，累计计数也都无增量；但窗口前已
累计457/516次，不能排除那些动作后来造成影响。两侧普通原生驱逐和终态回收
仍发生，“该窗口0次”不能写成“系统没有迁移或回收”。

因此目前的判断是：

1. “额外排序或预测H2D是恢复突增的唯一原因”不成立，V78b两者关闭仍有大量
   恢复；这不证明原生顺序完全无益，V78b比V76的多个原始窗口确有改善。
2. “压力窗口中大量新PREPARE/定向卸载直接导致恢复”缺乏支持，V78b本窗口仅
   0.171GB PREPARE且无新增定向卸载。此前动作、引用保留/释放、回收和实际
   工作集仍待隔离，不直接按0计数给旧动作免责，也不把单轮相关性写成因果。
3. “用更大的预取覆盖就能解决差距”仍无依据。须先解释普通快速续接为何失去
   驻留及恢复的控制/写回成本，同时证明native自身存在可减少的有效工作。

文献约束继续收紧：Marconi[R6]已覆盖混合模型checkpoint准入/驱逐；
EfficientAgent[R10]已有Host工作集与写入决策；原生路径软上限也是简单对照。
本轮补核SuffixReplay[R18]原始摘要，其少量SSM锚点与有界后缀重放直接约束
“少存状态再重算”这一方向。未复现其实现、质量或当前负载收益，不能照搬论文
TTFT提升，也不能宣称生成前缀兼容性诊断已经构成新贡献。

下一独立诊断应连接真实生成前缀、下一实际输入、必要checkpoint及其后续消费。
先完成这条必要条件，再在相同引擎/有效任务下比较已有准入和保留机制。当前
没有据此登记小cap策略或替换冻结V78b，也不恢复已撤下三机制为选定研究路线。

### 4.47 V78b最新任务截面与incomplete归因

V1354保留3000/3600s全部156个应到任务。3600s的V78b/V76/native recorded
completed为93/80/105，runner截断平均逗留1354.919/1476.103/1267.947s。
V1361进一步覆盖4200s，三侧recorded completed为120/115/124，另有V78b的
1项incomplete；全156项runner截断逗留1542.452/1704.592/1426.191s。
V78b若仅按completed终点累计，其截断逗留为1557.706s；失败任务继续累计到
窗口终点。原生JCT合格完成数分别120/114/123，guard/语义标签保留。以上均非
最终或质量匹配JCT，不能因完成数接近就称性能达标。

该incomplete为pylint-dev__pylint-7080，runner原始duration1722.240s。
2个child均已返回、JOIN满足；root最后请求
beliefkv:01a14776-f396-7551-a473-0bc85e345a03的llm_result.finish_reason为length，
输出正文32216字符、无工具调用，最终无语义完成且patch为空。原始文本反复讨论
同一ignore-paths判断，不能按自然任务完成计；workflow_deadline.expired=false，
无截止时间强停。保留8192输出上限和原始标签，不新增guard或增大上限。

19:13 PDT再次核对V78b监督243510、driver243802、scheduler244015真实存活；
冻结source/env验证通过。原质量worker231698仍评分V75 predictive，V77等待器
1121673和原监督3806721继续，未放宽质量门槛。新审计只推进归因与研究筛选，
完整性能、质量和已有方法差异仍不足，目标继续保持未验收。

### 4.48 V75/V76三侧官方评分已完整收尾

19:14:33 PDT原队列完成最后一臂V75 predictive评分，监督和worker正常退出。
V1363绑定三份原始official_quality_summary，检查结果类别互斥且其并集精确
覆盖原156项instance_ids；没有修改冻结评分器、任务结果、输出限制或资格标签。

| 官方评分 | V75 predictive | V75 native | V76 reactive |
| --- | ---: | ---: | ---: |
| Resolved | 98 | 95 | 101 |
| Unresolved | 56 | 59 | 54 |
| Empty patch | 2 | 2 | 1 |
| Evaluator incomplete/error | 0/0 | 0/0 | 0/0 |
| 冻结correctly_completed_ids | 95 | 90 | 97 |
| Measurement complete | false | false | false |
| 有效完成吞吐 | null | null | null |

predictive的空patch为django-11087/12304。resolved与correctly_completed
不相等，原predictive的1项incomplete、native的3项及各臂guard/语义终态资格
继续保留，不能以官方评分完成为由重标或从分母删除。评分器给出的正确完成
子集JCT分位数也不能当全156任务的质量匹配JCT。当前单轮resolved多3项不证明
质量等价，更不能覆盖V75 predictive原始全任务均值1806.897s落后native
1665.374s这一负结果；正式收益仍须按原完整口径验收。

V77原等待器1121673已自动接续其官方CPU评分，worker1604042/
start_ticks637354434于19:14:40启动，后续ps确认真实存活。原GPU V78b仍冻结运行，
不因评分队列切换热改资源参数或注册新候选。本次是补齐质量证据与状态更新，
不宣布研究机制已成立或目标完成。

### 4.49 V78b的4800s截面

V1364补齐4200–4800s的实际服务窗口。V78b/V76/native H2D分别为
1.000/1.143/1.286GB，D2H123.031/146.531/180.360GB，decode为1082.763/
1132.570/1056.228token/s，加权batch为24.513/30.259/25.881，queue均值
均低于0.1。这是有需求阶段之后的低排队窗口，不能拿其速率宣称高压段胜出。

V1365保留4800s前全156项：V78b/V76/native recorded completed为132/132/131，
V78b另有1项原incomplete。runner截断逗留均值1643.987/1832.021/1533.602s；
只有completed才使用结束端点的口径为1663.087/1832.021/1533.602s。
原生JCT合格完成数132/129/130，guard/语义标签照旧。V78b完成数略多并未
推翻其原始逗留仍落后native，完整性能与质量匹配证据继续待验收。

### 4.50 下一独立全量native前缀诊断的必要性与资格

当前证据缺口是：一次生成后实际留下哪些checkpoint，旧生成输出在下一实际
输入中保留多少，以及对应状态是否仍在缓存路径。已有旧输入探针排除了新增
输入，却不覆盖旧生成输出；正Host请求的匹配路径只有当前创建身份，上一安全
checkpoint事件也不证明逐页/物理副本世代。不能直接将数TB写回标成可丢弃。

V79因此准备为完整156任务的native诊断，不做新的策略A/B。沿用V75的完整
53020ce3引擎、同任务和Poisson到达、running48、Host200GB80:20、HBM ratio0.9、
write-back32、工具180s、root14400s及输出8192；模型、prompt、harness与工具
代码不改。仅设置BELIEFKV_CHECKPOINT_PATH_SHADOW=1，原生策略保持，
prefetch/PREPARE、额外排序、native-close GC、路径cap和frontier合并均不启用。
新观测的同步路径/快照和后台比较开销仍须计量，不能称无成本。

研究判断按实际数据推进：统计输入前缀/边界/生成尾部checkpoint的精确兼容性；
将重复观测的逻辑checkpoint与不同物理写副本分开；区分普通续接与首次fork、
换会话及compaction。后两类没有相同会话历史时保持未知，不能由普通续接结果
推导全部大缺口。兼容性不是驻留、forward消费或安全丢弃证明；即使存在可过滤
状态，也须与原生路径软上限、Marconi/EfficientAgent及后缀重放的合理方法比较，
并证明native本身可减少的成本，才选择新机制。当前仍未选定论文贡献。

新启动器start_v79_checkpoint_diagnostic_20261016.py只允许独立冻结候选，
核对前轮真实进程退出、完整156项及干净遥测，再等待GPU/资源释放。原生门槛
失败不重标为成功；完整采集的exit1保留在独立证据中。启动后还必须看到实际
generation_continuation_prefix_probe、原生服务参数、同到达/容量与真实NUMA，
不能仅凭环境变量认为观测已运行。最终V1374的257项CPU检查通过，包含203项
既有观测/审计、39项新驱动和15项生命周期用例；先前253/256项重叠不相加。
加载worktree脚本的用例在登记前覆盖了根目录解析问题。当前只是CPU准备完成，
独立检出、登记和实际GPU启动仍要分别封存，原V78b及质量队列不改。

### 4.51 V79独立诊断已冻结登记并真实等待

候选c40d2f6在独立beliefkv-v1366-checkpoint-diagnostic-gpu-20261016检出，
只通过ignored symlink复用原完整引擎53020ce3，未修改运行引擎。V1367/V1368
绑定clean应用、规范patch、257项最终CPU凭据、前轮进程/登记、原生任务到达、
容量和helper。V75实际完整采集也通过新完整性判定的CPU只读核对：153 completed、
3 incomplete、141项原生JCT合格、client exit1原样保留，159项输入绑定，不将
这一核对当作新GPU或质量/性能验收。

V1369于19:42 PDT启动监督1906740/start_ticks637518375；随后ps确认nice0、
真实argv及waiting_for_complete_v78b。未启动V79 driver，也未生成实际服务/
placement/结束凭据V1370–V1372。19:45再次核对V79来源与环境通过，V78b监督
243510、driver243802、scheduler244015仍真实存活，V77评分worker1604042继续。
这构成已执行的登记与可验证等待，不是新观测已采到数据、性能提高或论文机制
成立的证明。后续只有前轮完整收集、实际退出和资源释放后才能冷启动。

### 4.52 V78b原始runner均值的单调负结果

V1377在已覆盖的5400s任务时点保留全部156项，V78b/V76/native recorded
completed为143/143/140，V78b另有1项原incomplete。runner截断逗留均值为
1708.632/1907.026/1613.809s；只有completed使用终点的口径为
1731.578/1907.026/1613.809s，原生JCT合格完成为143/139/135。完成数略多
不能代替总体逗留，失败和未完成任务仍在分母中。

以同一runner终点减计划到达为口径，V1259已封存native全156个runner实际结束，
7000s观察的1666.324260906s即其最终原始均值，本轮只读复核一致。V78b在5400s
的1708.632188036s已是最终原始runner均值的下界，所以该指标后续无法反超。
这不是和result.duration均值1665.374s相减，也不是质量匹配JCT或推断全部新
机制无效；原V78b继续完整运行，不因原始负结果提前停止或改变任务。

V1375服务窗口5400–6000s的V78b/V76/native H2D为0.148/0/0GB，D2H为
59.298/79.175/98.018GB；decode687.300/777.105/778.060token/s，加权batch
6.687/8.402/9.209、queue均值均低于0.03，属于不同剩余工作的小batch阶段。
不能由GPU利用率接近98%推断整体吞吐或公平性改善。

同数值6000s的任务审计因到达/finish日志尚未覆盖而拒绝，V1376没有写出。
后续检查该日志最新finish约5921.662s，GPU服务覆盖不能替代任务时钟证据；
采用V1377的5400s可验证截面，不补心跳或放宽审计。V1378封存当前登记/真实
等待、这一原始指标负结果和限制。总目标继续未验收。

### 4.53 快速续接之间的可观测上下文竞争

新增只读审计器按native事件写出顺序统计：同一逻辑context上一请求完成后，
到下一请求首次服务登记前，有多少不同的其他逻辑context开始服务。不能按
llm_submit的ts_ms排序，因为该事件到首次服务才写出，时间却回填为较早入队时刻。
窗口成员仍按入队时间定义，与V1356的已服务请求cohort一致；尚未服务的请求
不在此cohort，服务本身可能发生在窗口结束后。

首版V1381将快速cohort限定为同epoch，三侧均为空集。真实窗口内可关联前驱的
3814/3812/4264次续接均推进epoch，不能将其等同compaction、失效或新会话。
修正schema2保留全部同逻辑context续接，再单列同epoch和变化；V1381原始结果
及V1380的18项CPU凭据保留，V1382最终20项通过，重叠检查不相加。
V1383保留准确读取前缀及源码hash；V1384绑定修正原因、CPU和独立分母复核。

2400–3000s中，上一完成到下一入队不超过1s的cohort如下：

| 观测 | V78b | V76 | Native |
| --- | ---: | ---: | ---: |
| 快速续接请求 | 3229 | 3243 | 3475 |
| 报告正FULL Host请求 | 569 | 1328 | 7 |
| 正FULL Host占比 | 17.62% | 40.95% | 0.20% |
| 其他逻辑context首服数P50 | 66 | 82 | 56 |
| 其他逻辑context首服数P90 | 75 | 92 | 65 |

三侧快速cohort的分母、正Host数和报告FULL字节与独立V1356逐项一致，
竞争身份均已知。原生排序减少了观测到的竞争数量，但仍不能将V78b剩余恢复
直接归因于这一数量：例如提示16384–20479 token、竞争48–63个context时，
V78b为62/270正Host，native为2/495；提示20480–24575、同竞争桶则为66/325
和1/440。两侧相同粗桶仍存在差异，需要进一步连接实际驻留/驱逐和前缀变化。

这些不是物理页LRU距离、去重工作集、逐层访问或暴露恢复时间。decode访问、
共享前缀、实际驱逐、namespace/session和物理副本世代未由此计数证明；同一
逻辑context和epoch也不证明相同物理副本。历史单轮、workflow内相关请求及
粗分桶仍有混杂，不能把64等桶边界直接设成新准入阈值或宣布策略因果。
下一步使用V79的实际输入/生成前缀和checkpoint观测补齐兼容性，之后再判断
需要何种物理来源证据；目前没有从该关联直接选定新的缓存策略或论文机制。

### 4.54 V78b完整结果及V79自动接续

V1329确认V78b于20:05 PDT完整收集，HTML和同容量native比较已生成。
V1385利用三个cohort所有已结束runner，在9000s时点核对全156项：

| 最终观测 | V78b | V76 | Native |
| --- | ---: | ---: | ---: |
| Runner结束 | 156 | 156 | 156 |
| Recorded completed | 155 | 156 | 153 |
| Incomplete | 1 | 0 | 3 |
| 明确原生JCT合格完成 | 146 | 145 | 141 |
| 最终runner平均逗留/s | 1764.123 | 1975.416 | 1666.324 |

V78b比native的原始runner均值慢5.869%，确认4.52的下界负结果。其
result.duration全终态均值为1763.135s，native1665.374s，两种时钟端点不混用。
只有completed/合格completed才停止累计的9000s截断指标另列于V1385，不能当作
最终runner均值；失败及不合格任务继续留在分母。V1379后来已验证6000s真实任务
覆盖，原V1376的拒绝和未写出状态保持，不补造早期覆盖。

完整同池比较中，V78b/native原生H2D为339.650/13.610GB，其中FULL
223.492/3.952GB、Mamba116.158/9.658GB；两侧预测和handoff传输均为0。
V78b多轮spawn为28个workflow，native为30个，实际模型/工具工作仍不同。
原始完成速率75.589/80.506个每小时也不是质量匹配有效吞吐；V78b官方质量
未评分，原incomplete和全部JCT/guard资格保留，不以多完成2项宣称性能胜出。

原监督、driver及scheduler实际退出后，V79原登记监督1906740于20:06:01 PDT
自动启动独立driver2032542/start_ticks637662487。只启用已登记诊断，应用
c40d2f6和引擎53020ce3保持冻结；驱动存活不替代实际首服/前缀探针及NUMA
凭据V1370/V1372。V77官方CPU评分继续原队列，未因本轮结束重启或修改评分器。
当前完整性能、质量匹配和已有方法差异证据仍不足，研究目标继续未验收。

### 4.55 V79观测器导入故障及同配置V79b重试

原V79已产生实际请求和generation_continuation_prefix_probe，但20:09 PDT
NUMA观测子进程发生ModuleNotFoundError: server_reservation。观测脚本按自身
路径parents[2]/ops导入，在冻结.worktrees目录中得到不存在的.worktrees/ops，
而真实操作目录为experiment/ops。原监督按失败路径停止driver与GPU服务，
状态stopped_for_diagnosis；不是模型完成、NUMA容量不足或cache策略失败。
V1370/V1372未形成资格，原部分数据及失败state/log原样保留，不能充当完整实验。

主线观测器改为从显式run-root的祖先定位真实ops。新的独立重试入口加载原
冻结V79 helper，只重定向外部NUMA observer和新输出/凭据位置；原应用c40d2f6、
完整引擎53020ce3、所有任务/资源/模型/prompt/工具及诊断开关保持。原始注册输入
继续逐项验hash，重试额外绑定observer、入口、CPU、失败和新plan，不热改冻结源。
首次prepare还发现按新RUN派生的arrival文件路径发生变化，严格拒绝且未写登记；
现保留原冻结arrival文件引用，仅RUN_ROOT变化，新plan内容必须与原plan完全一致。

V1393最终56项CPU检查通过，含worktree外部helper定位、原失败分类、只替换
observer命令、配置生成保留原arrival路径、完整前轮及原启动条件；V1392初始
55项重叠凭据仍保留，不累计。V1386/V1387/V1388绑定故障、原注册来源及实际
监督，V1394记录修复资格。原进程确认退出后，V79b监督2048213/start_ticks
637731046以nice0在20:17:27 PDT启动，driver2048545/start_ticks637731177运行。
V1389/V1391于20:20:30确认真实首服、新probe、NUMA和与原native完全相同的
FULL/Mamba容量，scheduler2048787/start_ticks637732486实际运行，冻结来源
再次核验通过。Host大映射驻留约159.574GB在N1、42.386GB在N0，声明策略为
preferred；这些映射不能直接逐池归因或当带宽证据。原V77评分继续，未重新启动。

这次修复只恢复已登记的证据采集，不产生性能/质量收益，也不是新的缓存机制。
V1395压缩保存V78b完整native比较，与V1385全队列审计一起维护负结果；下一步
依据正常独立诊断的真实兼容性和物理来源选择研究机制，完整总目标仍未验收。

### 4.56 实际后继前缀与生成尾部写回

V1396/V1402/V1408给出V79b与原native的三个完整服务窗口，时钟、池单位及
实际诊断启动均已验证。V1405/V1403/V1409由最终审计源码独立封存读取前缀，
V1407的57项CPU检查覆盖未知路径、token计数、身份冲突、聚合写入、同刻/未来
信息及跨context兼容反例；先前48/51/56项重叠凭据保留、不累加。

| 观测 | 0–600s | 600–1200s | 1200–1800s |
| --- | ---: | ---: | ---: |
| 已观测同会话续接 | 3993 | 4833 | 4736 |
| 上一输入最后1个token处分歧 | 3968 | 4796 | 4713 |
| 保留正生成前缀的续接 | 25 | 37 | 23 |
| 全部Mamba D2H/GB | 106.950 | 133.672 | 129.680 |
| 关联上次观测生成尾部的D2H/GB | 100.511 | 122.726 | 117.124 |
| 写前已出现后继输入且已观测比较仅不兼容的副本/GB | 95.296 | 110.170 | 104.825 |
| 上述后继输入出现到写提交P50/s | 33.036 | 17.297 | 12.742 |

第一项只包含已有历史并出现实际后继的请求，不是所有完成请求。前两个窗口
的写角色均可关联单节点；最新窗口另有7份/0.451GB聚合Mamba副本不能逐节点
归因。角色来自严格早于D2H submit的完成快照，receipt没有creation/物理副本
generation，所以这是历史关联。输入出现到写提交的提前量也不是后台比较完成
或动作可用时间：探针ts_ms在输入入队时记录，实际比较随后由writer执行。
先前窗口的比较不计入本窗口时序分区，未知仍保留；不能由此宣布在线安全丢弃。

共有4/11/3个生成尾部checkpoint观测仍精确兼容，不能统一删除所有decode末态。
三个窗口中严格输入前缀状态的兼容观测为50989/30167/22918，均保持精确兼容；
输入边界本身仍有3/8/15次不兼容。其原因尚未从chat template、thinking、
工具消息重编码和session行为逐项分出，不将最后1个token分歧直接归因某一个。

离线路径选择给出必须保留的简单对照：

| 保留集合中仍有兼容checkpoint的路径 | 0–600s | 600–1200s | 1200–1800s |
| --- | ---: | ---: | ---: |
| 最远一个checkpoint | 2426/3993 | 2888/4833 | 2848/4736 |
| 最远两个checkpoint | 3992/3993 | 4829/4833 | 4729/4736 |
| 最近的严格输入前缀checkpoint | 3993/3993 | 4833/4833 | 4736/4736 |

严格输入方案保留最远兼容点的数量为3988/3993、4822/4833、4733/4736；
少数更远的输出或边界状态可兼容，不能把表中“能恢复某个前缀”当零重算。
选择仅使用上一请求长度和已观测位置，后继兼容性只作离线标签；没有模拟全局
缓存、真实容量、共享引用、Host准入或执行变化。原生path cap保护tail/fork/
locked/device leaf，保留已有Host副本；它沿当前tail的祖先链执行，不是上述
两点选择，也不证明会清掉别的旧分支。该来源已在当前冻结引擎mamba.py核对。

三个窗口两侧原生H2D均0。全D2H stream累计为13.832/34.233/34.938s，
不是可减JCT；原native相应为13.466/32.441/34.777s。最新窗口满48附近的
加权batch为47.463/47.494、decode worker均值28.464/28.300ms，不能据此
断言总体性能等价或诊断没有开销。首窗口同步路径探针累计374.587ms，仅覆盖
path walk，不含输出快照、队列、后台前缀比较和序列化。

### 4.57 候选研究问题与已有方法边界

当前有证据支持的问题是：在后继实际token前缀已不再延续某个生成分支时，
为何还要自动将其大份混合状态写入Host，怎样保住仍有价值的输入checkpoint，
又不增加已解决的高频控制工作？候选依赖实际请求转移，不依赖RETURN报秒数，
不改变模型prompt、工具工作或任务完成标准。它仍只是待验证的系统候选。

Marconi [R6]第4.1节已覆盖基于历史分叉的prefill准入，并将末次decode状态作为
input-and-output续接缓存点；其文字明确以对话通常追加而非从中间分叉为依据。
当前前缀分歧提示这个适用条件需要检查，但不证明Marconi的合理适配一定失败。
少存checkpoint、保留fork或只缓存两份本身不能成为新颖性。
EfficientAgent [R10]第4节/附录D已有工作集估计与容量条件写入，且警告在容量
足够时固定拒写会损害恢复。必须按实际FULL/Mamba大小合理适配后比较，不得
只对照原生全写或把其方法误称为一律关闭迁移。
SuffixReplay [R18]用近似后缀重放替代状态checkpoint，属于另一条强相关路线；
其质量和本任务成本不能直接照搬。本文同时将R18的正式标题校正到原始摘要。

下一步先完成强工程对照：原生不启用Host迁移、已有path cap和容量条件准入。
若关闭迁移或简单保留已拿到全部收益，就按工程配置收尾，不能把更复杂的策略
包装成论文机制。继续原V79b观察后段压力，不更换运行源或因局部负/正结果截断。

只有简单对照仍留下明确空间时，才在独立候选中实现按实际后继反馈的分量级
Host准入/回收：在请求转移时更新受影响分支，保留可用输入锚点；在原生回收点
核对活跃/共享引用及在途传输，再决定是否备份低复用Mamba状态。未知仍走原生，
FULL和必要状态分别核算；回收后缺缓存须由正常重算恢复。CPU夹具先证明共享、
引用、异常和恢复语义，再检验少写是否减少真实阻塞、是否增加有用重算。

当前现象也可能发生在单agent，尚非动态fork/join独有。需要用自然协作的共享、
分叉和终态语义证明增量，不能强制无用spawn或改变任务工作来创造优势。最终
必须超过正确配置native及合理已有方法，交付完整质量、平均JCT、有效吞吐、
长尾、公平性和重算证据。目前只是规模与时序必要条件成立，净收益和新颖性未成立。

### 4.58 V77官方评分结束

原CPU监督于20:38 PDT正常完成，worker_returncode=0，监督和worker随后实际
退出，无需重启。V1406封存原state、评分和156项分母验证；V77为90 resolved、
65 unresolved、1空patch（django-11138），evaluator incomplete/error均0。
原native非完成django-12708保留，correctly_completed_ids为90，measurement_complete
仍false、有效吞吐仍null。与同156项V75 native的95 resolved比较，80项双方
resolved、10项仅V77、15项仅native；单轮差异不作显著性或质量等价声明。
不能因两侧correctly_completed数量均90就假定是同一个质量匹配子集。
原V77平均duration1814.634s仍落后native1665.374s；终态回收未取得已验证收益。
原V79b仍继续独立诊断，未改任务标签或评分器，总目标继续未验收。

### 4.59 后段恢复反例与无Host原生强对照

V1410/V1411和V1420/V1421分别封存V79b的1800–2400s、2400–3000s，
沿用已核验时钟、启动及原生池单位，没有热改原诊断的应用或引擎。

| 窗口 | V79b H2D/GB | 历史native H2D/GB | V79b D2H/GB | decode token/s，V79b/native |
| --- | ---: | ---: | ---: | ---: |
| 1800–2400s | 0.421 | 0 | 220.165 | 1312.552 / 1296.692 |
| 2400–3000s | 821.767 | 7.190 | 923.520 | 938.623 / 1304.050 |

后段两侧平均running仍约46.9，decode时间加权batch47.182/47.490；
worker均值43.676/28.993ms，不是相同kernel耗时或纯计算退化。
此前1800s两侧H2D为0的结论仍适用于原窗口，但不能外推全程。相同任务和
到达不保证相同模型路径、物理工作集或观测成本，当前没有把差额分配给具体原因。
原诊断继续收集，不因负性能中止；它本来就不是新缓存策略性能实验。

1800–2400s的4930次续接有4897次在末输入token分歧，生成尾部关联写回
106.049GB；写前已观测后继且仅不兼容为97.485GB。2400–3000s相应为
3386/3365次、55.375GB和52.477GB，但该段另有3282份/211.325GB聚合
Mamba副本无法逐节点归因，不能沿用前段比例推断它们也是输出尾部。原生
Mamba H2D在该段245.580GB，也不能继续把所有先前写回当无恢复需求。
后段最远两点离线选择保住最远兼容checkpoint3378/3386，严格输入点能保住
某个兼容点3386/3386，却只保住最远兼容点3377/3386。反例和额外重算风险保留。

先实现能证伪复杂方案必要性的强对照。V80只取消原生Host缓存分配和迁移，
不改任务、prompt、调用规则、输出8192、工具180s、workflow14400s、156项
Poisson到达、原生排序及session引用语义。Host实际分配0、可用预算仍200GB；
显式固定FULL1798992 token及Mamba513槽，启动时核对与原生相同的物理
HBM69,939,384,320字节。保留CPU node1及内存preferred1，禁用额外诊断、
PREPARE、handoff、预取、关闭回收和path cap；原生未命中走正常重算。
现有引擎registry原本就支持UnifiedRadixCache不挂HiCache，新增实现只拆开
启动器和观测对Host的绑定，未修改原生引擎或发明另一套驱逐策略。

最终V1423的301项CPU检查通过，涵盖Host缺失/部分挂接拒绝、同HBM字节、
实际shell固定容量参数、NUMA、遥测关闭和不匹配启动拒绝。V1412最初因测试
断言读取不存在的顶层字段失败，生产路径无该错误；已改读实际嵌套字段，
V1413/V1422中间301项通过保留，最终保留已有无Host admission-only启动；
新实验入口才限定native。四套凭据重叠，不相加，不覆盖失败记录。
V80在21:25登记前仅完成CPU准备；独立冻结、绑定来源后等待V79b完整终态、
原进程及GPU实际释放。只有实际服务、零Host分配、同HBM和NUMA核验才算启动。
最终保留全部任务、质量/JCT资格和有用重算。若简单关闭Host就获得全部收益，
按工程对照解释；否则再检验原生path cap、容量条件写入及后继反馈的增量。

### 4.60 V80无Host对照登记与实际等待

21:25 PDT，V1414/V1415绑定独立应用3700fa465641531e2defd0c366f61fc6ce64e525、
完整原引擎53020ce3efef3ed362e9af0db77147b39070f064、V1423及同一156项任务/
Poisson表。新引擎检出基于原94602c9并应用完全相同canonical patch，没有
修改V79b的应用、引擎或已登记helper。来源、环境和输入绑定已再次验证。

V1416记录真实监督3176720/start_ticks638138015；ps确认nice0且state为
waiting_for_complete_v79b。原V79b的2048213/2048545/2048787仍真实存活，
并非仅由state推断运行。V80尚无driver、GPU服务、性能或质量结果。
自动接续要求前轮全156项summary/result/finish、有效生命周期及排空遥测，
保留原始exit1/incomplete/guard；随后核对实际GPU/进程释放，才启动新独立输出。
V1417/V1418是未来真实启动/NUMA凭据，不提前生成或把等待写成实验已开始。

真实聚合回执可见node_commits/tagged_child_commits为空，node_ids也不等于
Mamba状态清单，不能按节点数量均摊上述211.325GB。暂保留未知，不为提高机会
统计而修改归因契约；额外细粒度观测是否值得加入，需先看无Host强对照的规模。

### 4.61 撤下混合状态候选；改查模型通用的迁移阻塞与干扰

用户认为后继前缀驱动的混合状态Host准入不可靠、新意不足且依赖Mamba。
本轮撤下它的研究主线地位，保留此前诊断、CPU实现和反例，不继续堆叠控制器。
V80仍是有价值的工程控制实现，但此次自动接续是为旧候选准备；已在
21:53:57 PDT核验精确进程身份、仍在等待且无driver/子进程后取消。
V1442保存完整原state、登记和冻结launcher哈希；driver/GPU均未启动。
ops状态改为cancelled_before_gpu，原V79b继续冻结，不设置全局停实验。
未来只有新的具体问题需要该对照时再独立登记，不自动恢复旧队列。

**新筛选问题。** 一次agent续接在分配恢复空间时，是否因冷KV写回而拖住
其他已经可执行请求？如果是，能否在相同HBM/Host容量与缓存复用条件下，
让这部分迁移在不阻塞无关请求的情况下完成？这里研究的是物理页回收、
数据依赖及共享传输路径，不修改agent决策，不要求准确预测RETURN。
它应在纯Attention模型中也成立；混合模型运行一条FULL路径不足以证明泛化。

V1431/V1432用已验证时钟比较真实worker几何，不要求Host占用流，支持未来
无Host对照。脚本audit_native_window_geometry.py及既有依赖的最终34项CPU
检查见V1430，不与先前重叠凭据累加。新脚本只读已存在日志，不增加运行探针。
V1434–V1441沿用既有prefill/恢复/传输审计器，全部报告保留原始读取范围和hash。

| 观察 | V79b 2400–3000s | 同段历史native |
| --- | ---: | ---: |
| 原生H2D | 821.767GB | 7.190GB |
| 相近上下文、满48且graph=true的worker比值 | 约0.981–1.007 | 参考 |
| 正FULL Host短prefill采样/选批线程CPU均值 | 57 / 87.956ms | 本采样无正组 |
| 零Host短prefill采样/选批线程CPU均值 | 36 / 3.867ms | 124 / 3.694ms |
| 正Host样本选批内D2H | 174批 / 25.394GB | 不可匹配正组 |
| 正Host样本选批内H2D | 57批 / 24.323GB | 不可匹配正组 |

57个正组样本全部有D2H提交，174批D2H均在选批结束前ACK；57批H2D的ACK
全部晚于选批结束。D2H记录stream累计4.441s，选批墙钟累计5.438s，二者
不能相除宣称因果比例或相加作为成本。3000–3600s正组仅1个样本：
选批线程CPU53.267ms、3批D2H/347MB在选批结束前ACK。恢复高峰有阶段性；
不能把57个采样按采样率外推，也不能由诊断臂反推native必有巨大瓶颈。

V1443/V1444继续按同一clock-domain、精确forward_iter前后邻居核对，
V1445核算已有CPU阶段的区间并集。2400–3000s的
decode47→prefill1→decode48有55/75个样本，prefill run_batch返回至下一
run_batch入口均值为2.831/2.852ms；该边界不是CUDA kernel结束或GPU空闲。
它也发生在当前选批之后，不能用来解释或扣除前述87.956ms选批线程CPU。
目前没有证据把全部选批等待称为GPU暴露停顿。这是新候选的必要反例，
不是异步方案收益的证明。两个最初的直接脚本入口因scripts模块路径失败，
没有产生报告；改用python -m后生成独立V1443/V1444，未改源码或服务环境。

**函数口径更正。** V79b冻结应用的third_party symlink实际指向
sglang-v1138-batched-writeback-gpu-20261016，完整树53020ce3。
主目录旧脏third_party不是运行来源。冻结引擎_try_write_back_chain已经
合并入队再start_writing；之后writing_check(write_back=True)仍等待
finish_event.synchronize，ACK后才demote。批处理不是缺失能力，
同步存在也不证明所有174批都由同一个函数或目标请求触发。

**与已有方法的边界。** 本轮重新核对下列原始论文/官方文档：

| 已有能力 | 对新筛选的约束 |
| --- | --- |
| CachedAttention最新版摘要及AttentionStore v1 §3.2.2 [R19]：异步保存、写缓冲及逐层加载 | 换成异步DMA、增加buffer或减少一次等待本身不是新意 |
| LMCache官方async loading文档 [R20]：异步取回、事件同步与分配预算反压 | 非阻塞API或页生命周期不是独立贡献；该页标明旧进程内模式已deprecated |
| KVBM官方组件文档 [R21]：跨层块管理与事件生命周期 | 不能把统一页状态机或接入NIXL称为新机制 |
| TokenCake原始摘要/作者实现 [R22]：agent感知空间管理、工具期卸载及预测上传 | 不能把工具/SPAWN事件加异步卸载简单组合后宣称首次提出 |
| CONCUR原始摘要及v1 [R23]：反馈调节agent准入、缓解中段抖动 | 单纯降低并发或限制工作集应作为对照，不是本项目新机制 |

尚未证明这些方法在目标条件下留下同一缺口。候选只有在“已有异步机制、
合理并发与固定内存下仍有显著跨请求阻塞”成立时才值得实现，不能把文献未逐项
报告当前指标当作未解决的证明。SAGA/R12已有workflow执行图、保留和公平机制，
也必须保留为相关对照，不把优先parent改称新研究问题。

**接下来的具体工作与停止条件。**

1. 先在正确native中核算选批等待是否暴露到GPU供给、影响哪些其他请求，
   区分调用线程等待、已有GPU计算覆盖、copy带宽干扰及必要恢复依赖。
   现有日志缺少直接等待时保持未知，不把stream秒数当可去除停顿。
2. 用相同页身份/版本连接写回、回收、后续恢复和重算，判断是不可避免的真实
   工作集，还是可以改变时机而避免同步的写回。诊断额外成本和轨迹变化需单列；
   只在诊断臂出现的退化不能作为优于native的论文收益。
3. 若规模足够，独立验证事件驱动的回收完成处理，使仍可执行的batch继续前进；
   源页必须保护到DMA完成，目的容量不可提前虚报，不能直接删synchronize。
   这一步先归为共用工程对照；workflow阶段信息能否再减少暴露写回及抖动，
   必须独立消融，不能混在基础异步实现中。
4. 若简单强对照已覆盖全部收益，或正确native只剩很小空间，停止把本候选升为
   论文主线。只有相同任务/质量/资源下的JCT、有效吞吐和重算都支持，且证明
   纯Attention适用性及已有方法差异，才进入新机制的实机开发。

当前状态是完成候选撤回、队列取消、最新证据整理和文献边界筛选，
不是新机制实现、证明存在新颖性或已优于native。性能总目标继续未验收。

### 4.62 提高在线到达率，验证有效双向FULL迁移机会

用户指出当前负载未提供足够迁移机会，建议缩短任务到达间隔。采用一个开发
候选：Poisson均值从15秒/root改为10秒/root，到达率从1/15增至0.1，即提高
50%。保留156个不同任务、原始任务顺序、seed21及相同指数随机数，实现原
到达时间轴的2/3缩放。不改变running48、Host200GB80:20、实际HBM容量、
工具180s、workflow14400s、输出8192及agent行为，不混入新预测/回收策略。
10秒是初次验证值，尚未证明最优，不进行多率扫描或重复实机开发。

V1446绑定原plan/profile/manifest、原始native比较与到达日志的实际sha256，
生成experiments/plans/qwen35_native_poisson_156root_mean10s_20261016/workflow_arrivals.json。
原生load_workflow_arrivals已验证全部156项、顺序及digest；最大缩放浮点差
约1.36e-12秒。状态prepared_not_launched，尚无GPU监督或新结果。
V79b原15秒输入、应用、引擎及helper继续冻结。

| 到达项 | 原15秒 | 新10秒候选 |
| --- | ---: | ---: |
| 平均到达率，root/s | 0.066667 | 0.1 |
| seed21实际平均间隔，秒 | 15.883 | 10.589 |
| 600s前已到达 | 42 | 58 |
| 1200s前已到达 | 75 | 111 |
| 1800s前已到达 | 111 | 156 |
| 最后计划到达，秒 | 2477.769 | 1651.846 |

标称Poisson均值与这156次抽样的实测均值分开。原native日志中156次started/
finished完整，峰值活跃workflow为91；该历史值不外推新轮并发，更不当作物理
工作集。更快到达可能增加同时存活的agent及上下文，也可能只增加队列等待，
迁移量不能按1.5倍推算。

需要纠正“两个方向都很少”的笼统表述：V75 native完整结果D2H为
1935.037GB，其中FULL832.695GB；H2D仅13.610GB，其中FULL3.952GB。
写回多、后续读回少，不是可利用双向迁移充足的证据。原native归因FULL
eviction重算10148 token是已观测范围，不是全部重算；缺失/未知保持原口径。
V79b的2400–3000s恢复爆发也不能代表无额外诊断native的持续工作集。

下一次GPU负载资格先使用native原生策略，不为增加机会而强制驱逐热KV、
重复搬迁终态数据或提前开启新控制器。检查：

1. 多个连续时间窗是否存在真实FULL写回及后续需求恢复，恢复是否被下一请求
   实际使用；按agent工具/JOIN阶段分列，不把Mamba备份GB或已结束child垃圾
   回收充当有用FULL迁移。
2. 实际HBM/Host缓存压力、可回收量、Host驱逐、有用数据重算及首次服务等待；
   活跃token gauge不代表全部物理驻留，排队更多也不自动等于工作集更多。
3. GPU服务与完整任务质量、全部终态、JCT、长尾；纯计算饱和或频繁有用KV
   重算不能仅因H2D增加便宣布理想负载。旧15秒native只作上下文参考，新设计
   的相对性能必须匹配10秒到达表。

有限156项更快到达会把供给时段由约41.3分钟缩至27.5分钟，可能增加收尾
阶段占比。它用于提高瞬时重叠，不保证延长高压时间。若仍缺持续迁移，
优先增加来自同类真实任务的不同root、延长随机供给时段，再考虑降低间隔；
不靠继续压到极高到达率制造短暂抖动，也不声称当前只需调率就一定有效。

### 4.63 V81登记接续；后续窗口仍是写回多、有效恢复少

V81独立入口start_v81_native_arrival_pressure_20261016.py复用原生实验、
完整收尾和实际NUMA检查；只把已有10秒到达表接入原策略。应用87ce506、
完整引擎树53020ce3在独立worktree冻结，不改V79b原源码、引擎或helper。
新引擎仍基于94602c9加完全相同canonical patch，不混入未验证跨分支合并。
V1447/V1448绑定来源、配置、实际156项到达表加载、旧manifest和物理池目标。
这是参数及loader验证，没有为单一到达率改动新增策略测试或GPU证明。

22:18 PDT V1449登记监督3592377/start_ticks638458271，真实ps为nice0；
当时waiting_for_complete_v79b，未提前占用GPU。V79b于22:21:43完整收尾，
原监督/driver实际退出后，V81于22:21:51自动启动driver3595930。
22:24:43 V1450/V1451核验真实首服、实际156项到达表、完整同池及NUMA，
scheduler3596344/start_ticks638478865真实运行。三进程均nice0，
V1455再次核验冻结来源、登记/启动/NUMA凭据和实际进程身份。
V1452仍预留完整收尾，不能把首服当负载合适、质量或性能验收。
准备时最后的pytest-6202已自然完成，不因收尾较慢提前终止。

V81保持Host200GB80:20、HBM FULL1798992 token/Mamba513槽，完整池字节与
原native一致；保留session、NUMA preferred1、running48及所有任务行为。
禁用额外前缀诊断、预测头、PREPARE、预取、额外准入和终态回收。
原native server_info的max_total_tokens/max_mamba_cache_size为null，表示原
启动未显式传参数，不表示实际池无界；原census才是物理容量参考。
新轮显式传同容量参数，首服时同时核对参数及完整census，不能仅凭env验收。
新到达率的性能对照不能直接使用原15秒native作为匹配控制。

等待期间V1453使用既有比较器补齐两个完整时间窗，继续优先最新V79b轨迹：

| 项目 | V79b 3600–4200s | native同窗 | V79b 4200–4800s | native同窗 |
| --- | ---: | ---: | ---: | ---: |
| D2H/GB | 221.375 | 207.155 | 182.711 | 180.360 |
| H2D/GB | 3.048 | 1.108 | 1.581 | 1.286 |
| 其中FULL H2D/GB | 0.344 | 0.078 | 0.551 | 0.127 |
| 平均running请求 | 44.89 | 38.37 | 28.07 | 25.73 |
| 平均waiting请求 | 8.21 | 0.67 | 0.066 | 0.058 |
| decode token/s | 1278.67 | 1212.68 | 1089.84 | 1056.23 |

后20分钟V79b仍写回约404.086GB，FULL恢复却仅0.894GB，说明早期恢复峰值
不能代表全程持续双向机会。后段较高原始token/s伴随较大batch和不同剩余工作，
不称因果加速或JCT收益。GPU采样利用率、传输stream秒数及worker区间均保持
各自语义，不据此推算oracle或可节省停顿。

V1454补齐完整V79b与预先选定的历史native比较，保留全部156项：

| 完整采集项 | V79b诊断 | 历史native |
| --- | ---: | ---: |
| completed / incomplete | 156 / 0 | 153 / 3 |
| 全终态原始duration均值/秒 | 1910.675 | 1665.374 |
| 客户端采集时长/秒 | 7219.416 | 6841.743 |
| 原生D2H/GB | 2882.386 | 1935.037 |
| 原生H2D/GB | 879.088 | 13.610 |
| 其中FULL H2D/GB | 611.358 | 3.952 |
| 已归因FULL eviction重算/token | 10644 | 10148 |

V79b原生JCT资格仅145项，11项guard介入完成，其中3项另缺semantic completion；
原始exit1、分母156和所有标签保留。遥测writer_error/dropped/failed均无错误，
队列和pending均排空，官方任务正确率尚未评分。表中均值不是质量匹配JCT，
不同轨迹、诊断和已实现工作量仍未隔离；迁移大幅增多不能直接归因于某项策略
或宣称出现了论文收益。V1454第一次调用缺少明确timeline参数而拒绝、未写出；
补齐两侧已有密封timeline后成功，原native数据及缓存未覆盖。

接下来查看V81连续窗口的有用FULL迁移、物理工作集、
重算和native暴露等待。若更快到达只提高排队/无用备份，不判作理想workload；
若已有异步强对照覆盖收益，不把共用工程修复升级成论文新贡献。
研究问题、新颖性、纯Attention泛化和同质量性能目标继续未验收。

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
2. V75已完成两侧同配置批量write-back比较，原始均值与完成吞吐没有取得优势。
   先完成V77简单关闭回收对照与全部任务质量，再用独立原生顺序候选拆分V76
   快速续接恢复增加的原因。共用路径工程修复继续匹配放进native；不拿缺少
   修复的旧对照制造优势，也不因局部吞吐相近就恢复复杂控制机制为论文贡献。
   V1306已确认采样选批CPU集中于正FULL Host请求，下一步沿需求恢复、容量
   回收/写回和同步分解，不把相关性直接当作某函数根因。V77的后续恢复下降
   说明要保留阶段性；V76首轮质量结果也不能代替其余各臂评分。
   V1310已进一步连接同步写回receipt，跨分支合并只完成独立CPU资格；继续
   原V78消融，再判断共用候选的实机价值。若采用则匹配更新native，工程
   资格、批次数下降和workflow独有论文贡献分别验收。
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
- [R14] ReplaySSM: Cache SSM Inputs, Not State，arXiv:2610.13778v1；
  正确来源保存于V1260，替代此前错误编号。
  `https://arxiv.org/html/2610.13778v1`
- [R15] TreeWY: Speculative Verification for Gated DeltaNet Hybrids，
  arXiv:2608.20961v1；旧V1199误标为ReplaySSM的内容实际来自本文。
  `https://arxiv.org/html/2608.20961v1`
- [R16] Hydragen: High-Throughput LLM Inference with Shared Prefixes，
  arXiv:2402.05099v1，原始HTML与标题保存于V1260。
  `https://arxiv.org/html/2402.05099v1`
- [R17] Semantic Parallelism: Redefining Efficient MoE Inference via
  Model-Data Co-Scheduling，arXiv:2503.04398v5；框架名Sem-MoE。
  `https://arxiv.org/html/2503.04398v5`
- [R18] Just Let Linear States Forget the Distant Past: Prefix Caching via
  Suffix Replay for Hybrid LLMs，系统名SuffixReplay，arXiv:2609.33477v1；
  本轮再次核对原始摘要及正式标题，未复现完整实现、质量或当前系统收益。
  `https://arxiv.org/abs/2609.33477`
- [R19] CachedAttention: Cost-Efficient Large Language Model Serving for
  Multi-turn Conversations。arXiv当前摘要与v1名称AttentionStore分别核对，
  本次机制核对v1 §3.2.2，不混用不同版本实验数字。
  `https://arxiv.org/abs/2403.19708`
  `https://arxiv.org/html/2403.19708v1`
- [R20] LMCache官方Async Loading文档。本页明确指旧进程内模式，
  已deprecated；不能当作最新MP实现的完整能力清单。
  `https://docs.lmcache.ai/kv_cache/async_loading.html`
- [R21] NVIDIA Dynamo KVBM官方overview与组件文档；按各自标明版本阅读，
  不由接口描述推断目标负载的性能。
  `https://docs.nvidia.com/dynamo/v1.4.0/knowledge-base/modular-components/kvbm/overview`
  `https://docs.nvidia.com/dynamo/v-0-8-1/components/kvbm/design-deep-dive`
- [R22] TokenCake原始摘要与作者仓库；本次不据仓库标题认定会议录用或复现结果。
  `https://arxiv.org/abs/2510.18586`
  `https://github.com/pku-lemonade/TokenCake`
- [R23] CONCUR原始摘要与v1正文，二者标题存在版本差异；
  本次仅比较反馈准入机制，不沿用跨论文加速比。
  `https://arxiv.org/abs/2601.22705`
  `https://arxiv.org/html/2601.22705v1`
