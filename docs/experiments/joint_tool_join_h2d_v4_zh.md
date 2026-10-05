# v4 工具等待与 JOIN 联合恢复

## v3 的真实传输规模

v3 的 87 个原生控制器 H2D batch，共传输 10.8041216 GB。
其中 10 个带 BeliefKV PREFETCH_GPU 来源标记的 batch，共
814.940160 MB；其余 77 个 batch 为原生恢复，共 9.989181440 GB，
没有原生/预测混批或未知字节记录。预测标记只是来源分类：
v3 中两次实际 submit 晚于 child RETURN，不能算提前动作。

原生部分 FULL 为 3577 token，即 73.256960 MB；Mamba 为
154 个物理状态单位，即 9.915924480 GB。次数按控制器 batch
统计，不冒充 agent 数或请求数。不能直接把全部原生字节当作
可提前恢复的空间，还需区分工具等待、JOIN、压缩和首次请求。
可复核脚本为 `scripts/audit_native_h2d_sources.py`。

v3 没有加载工具等待 worker，12050 次
`tool_wait_predictor_unavailable` 是未启用而不是工具执行报错。
当时只有 JOIN parent 准备和 JOIN 前 H2D。

## 修复

1. 启动窗口放宽为 1000 ms。它是 runtime 的估计窗口，真实
   submit 到 child RETURN/TOOL_END 的提前量独立审计，不宣称
   修改上限就达到了真实 1 秒精度。
2. 纯预测 load_queue 在安全点立即启动。保留已有 load fence、
   producer/layer-done event，后续命中待完成页的 prefill 仍等待
   同一 producer；队列含原生加载时不抢走其 consumer index。
   容量和生命周期在 enqueue 前再次检查。记录 issued、enqueue、
   controller submit 与 ACK，区分排队和实际服务。
3. ACK 固定/轮询开销不按字节比例放大。按 FULL/Mamba/hybrid
   形态和相近大小选取样本，报告服务 P50/P90；缺相近证据时
   回退，不将几 KB 的延迟线性推成几十 MB 的数十秒。
   v3 回放的 80 MB hybrid H2D 为约 50/81 ms；不是新 GPU 验收。
4. 原生已生成 `<tool_call>` token 时立即撤销该轮的终态预取
   信号，不等客户端完成解析；announce 调用轮与其后报告轮
   分开。未通知的自然回复仍可 RETURN，保留无工具原生 EOS
   窗口，不添加格式修复、返回门禁或 agent 取消。
5. P50 剩余工作在 EOS 前塌缩到零时使用已有上界，不将数值
   clipping 当作生成结束证明。阶段/encoder 权重不变。

## 工具路径

独立加载已校准的工具事件时间模型。验证权重 SHA、训练源
manifest、当前模型 revision 与 SGLang 0.5.20。使用已标注的
“所有活跃工具返回”时间/CDF，不拟合离线不可识别的预取净收益。
原有 `online_eligible=false`、`predictive_action_eligible=false`
不被改写；旧 admission 模型的授权条件也不被放宽。

长等待依据剩余 P10 或短时返回概率选择；PREPARE_HOST 在
有分配压力、Host 空闲可容纳时备份安全输入，native allocator
只在真实短缺时回收独占、未锁定、备份 ACK 已完成的 FULL/Mamba。
优先回收不再使用的缓存规则保留，不人为清池制造机会。

临近工具完成时，runtime 根据滚动 CDF/残余时长、有效 Host-only
副本、当前物理 free-list 和服务证据发起 H2D；原生 D2H 副本
同样可用。TOOL_END、新 epoch、失效 session 或预测变化撤销旧
intent。单等待上下文最多两个加载节点，ACK 仍由物理账本确认。
预算、状态、收益判断均不是模型动作收益输出。

并行工具使用稳定 tool_run_id 跟踪，短工具先结束时保持
WAIT_TOOL。未解析为单活跃工具的并行组暂不进行时间驱动的
投机动作，不改变 agent 的工具并发或强制任何工具返回。
工具/语义两个 worker 的结果 FD 都注册，idle 时保留有界刷新，
避免 GPU 空闲时睡眠一秒错过返回窗口。

## 实验配置与口径

64-root 单波、running=48、BF16、HBM static=0.94，
Mamba/FULL=0.9、Host 200 GB 与 Device 比例匹配、NUMA node 1。
context=131072、completion=8192、seed=21、workflow=14400 秒、
graph=2048/预留32、native-reactive guard profile、自然语言终态。

两侧均启用完成通知/收尾控制、JOIN 与长工具等待的准备及真实
压力回收；只有 predictive 侧启用提前 H2D。因此比较隔离提前
恢复，不是未经修改的 native baseline 或完整算法消融。
启动命令固定加载 tool timing、service seed 和 1000 ms 窗口，
记录所有 SHA。无 canary、无扩大并发、无人工驱逐。

分别报告 JOIN/工具动作、真实提前量、ACK/首次 FULL/Mamba
复用、提前驻留、Host churn、可归因重算与完成吞吐/JCT。
`audit_tool_transfer_windows.py` 对齐实际 controller submit 和
工具完成，不使用 agent 后续首次 GPU 服务代替 TOOL_END。
未经独立 grading 不称正确任务吞吐；配置修复不代表精度或收益
已经达标。

## 完成结果与后续修复

两侧均 64/64 completed，无终态 error、遥测写入/丢包异常，
GPU 已释放。predictive / reactive 整轮分别 4219.81 / 3032.21 秒，
完成吞吐 54.60 / 75.98 workflow/小时，均值 JCT 为
1614.74 / 1341.92 秒。predictive 完成吞吐低 28.14%，但两侧
LLM 调用 12200 / 10242、工具调用 11993 / 9989、输出 token
2219857 / 2023931，实际工作轨迹不同，不把差值归因于 H2D 本身，
也不隐去负结果。未经独立 grading，不称正确任务吞吐。

JOIN 有六个 H2D ACK、491.09 MB，全部 FULL 首次服务复用，
五个有 Mamba forward 复用证明、一个未验证。实际启动均在
RETURN 前，中位提前 269.85 ms；两个落在 100–500 ms，三个
落在 100–1000 ms。零迟发，但一个提前 6594.69 ms，且一个
仅提前 0.38 ms，后者不能证明时间精度达到了亚毫秒。

工具模型已加载，接受 83864 次预测，工具 PREPARE 1460 次，
真实工具等待压力回收 12 次，却没有工具 predictive H2D。
`audit_tool_prefetch_candidates.py` 回放 6871 个工具机会采样：
139 个有可恢复目标、61 个当时容量足够、涉及六个 context。
139 个的独立 CDF 概率均未达到 0.8；P50 在一秒内的一个样本
也被该阈值否决，而且其采样时容量不足。采样不是完整调度
轨迹，不能断言修改准入规则就一定会得到有效传输。大部分样本
的时间预测本身仍未进入窗口。

本轮后的代码修复：

- 工具准入采用配置的剩余 P50 时间窗口，不再由独立 horizon
  classifier 的 0.8 阈值覆盖；CDF 保留为诊断，不改变 agent。
- timing-only 预测接受不再立即遍历完整物理 ancestry；需要
  备份或加载时才检查，动作前的原生身份/容量证明保留。
- 快工具不反复预测，长等待按剩余量有界降频，临近窗口刷新
  更频繁；相同等待的重复提交有计数。
- 工具恢复机会使用短期缓存，epoch/session 和动作前重新
  验证；不把旧 free-list 观测当容量预留。
- H2D ACK 后重新确认还有缺失节点，避免只为已经恢复的节点
  再次 drain GPU overlap。
- 6594.69 ms 的 JOIN 早发来自前 EOS 剩余工作低估：当时
  generated=516、实际最终=746，预测 936 ms、实际到 EOS
  约6046 ms。前 EOS 决策改用已有工作上界；已知无工具 EOS
  的确定性窗口保留。这是保守 runtime 调整，不声称模型已准。

修复尚需新的 GPU 验证。本次检查不自动重跑，先完成系统修复、
CPU 回归和提交。已有完整 trace、模型、patch 和失败诊断保留；
两侧旧完成 workspace 已由实验脚本清理。

## GPU 利用率与吞吐退化的根因复核

2026-10-05后续复核，仅读取v4原始trace，不修改当时配置或
回填新埋点。分析脚本及完整数值在
`experiments/analysis/v4_gpu_root_cause_20261005.py` /
`experiments/analysis/v4_gpu_root_cause_20261005.json`。
本节取代“差距主要只是模型路径不同”的简略解释。

### 1. 口径

NVML的gpu_utilization是采样期间kernel-active时间比例，不是
SM occupancy。以下“busy/idle equivalent seconds”是GPU采样
的时间加权积分，剪到workflow活跃时段；它是近似量，不是
CUPTI kernel计时。raw summary均值包含略有不同的前后采样边界，
所以60.67/82.21与下表60.77/82.40不矛盾。
分析只累计有效采样覆盖，相邻采样权重上限为一秒，不把采样
缺口当作已观测时间。官方口径见NVIDIA System Management
Interface的Utilization说明：
`https://docs.nvidia.com/deploy/nvidia-smi/index.html`。

`gpu_service_sample` 是scheduler/worker interval，其start为
max(batch launch,前一batch完成)，包括CPU调度、同步和结果
处理；不能累计后称为真实GPU计算时间。工具duration同样可能
包含host排队/回调，不将多个并行工具时长相加成critical path。
running/queue来自约一秒采样，与NVML采样有滞后；分组是关联
诊断，不是每毫秒的精确线程状态。

| 时间加权近似量 | Predictive | Reactive | 差值 |
| --- | ---: | ---: | ---: |
| 有效GPU采样时段，秒 | 4199.99 | 3012.93 | +1187.06 |
| Busy equivalent，秒 | 2552.28 | 2482.69 | +69.59 |
| Idle equivalent，秒 | 1647.71 | 530.24 | +1117.47 |
| 全程GPU利用率，% | 60.77 | 82.40 | -21.63个百分点 |
| 有running/queue时GPU利用率，% | 77.34 | 83.16 | -5.82个百分点 |
| 无running/queue时段，秒 | 946.36 | 99.28 | +847.08 |
| GPU利用率<=10%的时段，秒 | 934.37 | 58.65 | +875.73 |

整轮多出来的约1187秒中，近似忙碌时间只多70秒，额外约1117秒
为空闲当量。问题不是GPU多算了20分钟，而是尾部没有GPU请求
和非空服务阶段发射/服务间隙两部分。额外idle中，约871秒落在
采样显示无server demand的时段，约247秒落在仍有需求的时段；
后者不能再用“都在等待工具”解释，但也不能仅据此归因某个CPU函数。

### 2. 主因：最后一个workflow的工具长尾

Predictive第63个workflow完成后，只剩django-16938，持续约
896.10秒；GPU利用率约1.03%，busy equivalent约9.20秒，
idle equivalent约886.89秒。Reactive的单workflow尾段约117.36秒，
idle equivalent约31.77秒。仅这部分额外idle为约855.12秒，
占全部额外idle约76.5%。这是实测长尾归因，不是泛指路径差异。

django-16938的原问题是自定义manager/select_related下的m2m
序列化，root在尾部却执行了不带测试标签的两个Django全量测试：

```text
cd /workspace && python tests/runtests.py --parallel 2>&1 | tail -50
cd /workspace && python tests/runtests.py --parallel=1 2>&1 | tail -50
```

两次TOOL_START到TOOL_END分别约600.078/600.072秒；
sandbox内部执行约600.077/600.072秒，lock_wait均约0.001 ms，
退出码137，正文仅为Killed。可定位到sandbox audit的sequence
170/171，trajectory中的对应AI/tool消息ordinal 317-320附近。
因此不是等待execute锁，也不是GPU队列/H2D耗时。

执行包装器使用 `timeout --signal=KILL 600s /bin/sh -c ...`，
与两次时长及退出码一致。没有进程栈/逐测试进度，不能进一步
断言是哪个测试死锁、CPU很慢还是全suite超出预算；不能把
“触达执行上限”自动解释为模型循环或OOM。

第一个测试开始时其他workflow尚未全部结束，其后半段和第二个
600秒测试占据了孤立尾部。此时running/queue基本为0，GPU没有
可选agent；KV恢复得再快也不能缩短CPU测试本身。
固定数量的一批workflow、没有后续到达，最后一个同步工具等待
会放大makespan吞吐退化。不能把这段896秒全部当算法GPU效率。

取predictive从首个workflow开始到第63个完成的分段，3303.75秒
内完成2,217,828个output token，即约671.31 token/s；其后
孤立尾部只产生2029 token。reactive完整workflow活跃区间
3012.93秒完成2,023,931 token，即约671.75 token/s。
二者近似相当，predictive在bulk阶段也确实有更多需求。
这是阶段诊断，不是删除慢任务后重新报告“公平吞吐”。
两个分段并非完全配对，不能据此认定控制面无开销或算法性能
相同；它说明全程平均GPU利用率不能单独证明持续的服务速率退化。

平均JCT还需要另外解释：django-16938自身JCT为4205.71/
1622.32秒，其差对64-task平均增量贡献约40.37秒，仅占总体
平均增量272.82秒约14.8%。工具孤立尾部主要解释整轮makespan
和全程GPU平均值，不能拿它解释全部mean JCT退化。

这不意味着只要删除该workflow结果就合理：必须保留原始
54.60/75.98吞吐和全量JCT。completion curve显示第63个完成时
predictive也已比reactive晚约407秒，说明主尾部之外仍有退化，
尾部诊断不是洗掉整轮负结果。

### 3. Harness反馈存在两个具体问题

- timeout后只返回Killed/137，工具分类为command_failed，没有
  明确告诉agent是配置的600秒预算到期；未知的进程被杀与timeout
  信息混在一起，不能假设agent知道应改测试范围。
- 后续命令 `python tests/runtests.py tests 2>&1 | tail -50`
  输出显示找到6837个测试、大量error及multiprocessing
  MaybeEncodingError/PicklingError，但ToolMessage为status=success，
  尾部写Command succeeded with exit code 0。

后一个不是抽象模型瑕疵：backend用/bin/sh执行管道，不传播
上游Python失败，tail的0掩盖了真实错误。模型收到“异常+成功”
相互矛盾的反馈，测试质量和下一轮行为都会受影响。当前prompt
本已有focused-test指示，不能再断言“prompt直接要求跑全量测试”。
这些问题需后续在两侧同时修复，不能运行中修改shell或统一
缩短正常长工具上限，也不通过guard强行终止workflow。

### 4. 非空需求阶段：控制开销真实存在，精确占比尚不可识别

只取采样显示有running或queue的区间，predictive GPU利用率
仍为77.34%，reactive为83.16%。即使去掉长工具无请求时段，
仍存在约5.82个百分点差距。17-32 running分组为69.46/78.37%；
对应17-32 decode的scheduler/worker interval均值为23.875/
21.995 ms、P95为70.526/63.724 ms。该分组没有同时固定context
长度和每轮需求，不把差值全称为Python调度耗时。

可从源码与计数证明的额外工作：

| 控制工作 | Predictive | Reactive |
| --- | ---: | ---: |
| 接受工具预测 | 83864 | 59279 |
| 立即读取工具物理闭包：available+unavailable | 83864 | 59279 |
| JOIN/工具PREPARE issue | 2588 | 2324 |
| native shadow decline | 53316 | 61808 |
| 语义模型接受结果 | 1278 | 0 |
| 缺目标跳过的语义扫描计数 | 68442 | 0 |
| 请求一次overlap drain的JOIN动作 | 6 | 0 |

v4 `_accept_tool_wait` 每次refresh都在scheduler主线程调用
`capture_shadow_candidate`，即使没有要下发的动作；还会在
等待refresh构造features时扫描invocations。predictive额外执行
语义frame检查、phase/work、关键parent目标查询及roll_final_stage。
旧工具H2D的CDF>=0.8额外否决与这些无动作扫描共同存在：CPU做了
大量工作，但工具H2D为0，不能把模型运行次数作为收益。

已实际记录的opportunity sampler累计成本为80.24/59.72秒，
差20.52秒；每次均值19.28/19.99 ms，没有“predictive单次
采样特别慢”的证据。累积差异部分来自整轮更长。
已接受语义结果记录的推理成本合计约39.43秒，发生在独立CPU
process，不能把这39秒直接加为scheduler阻塞或GPU stall。
83k物理闭包读取没有逐函数duration，无法诚实算出它独占多少秒。

`1aba1be` 的推迟闭包检查、重复查询降频、短期机会缓存和ACK后
避免二次drain就是针对这一实现浪费，已进入v5。但它的实际
吞吐改善仍待GPU结果；后续应做低开销scheduler阶段计时或CPU/
CUDA联合profile，避免编造“扫描贡献了某个精确百分比”。

### 5. 工作量、batch结构与迁移的证据

两侧64个初始prompt fingerprint全部相同，但49个root的第一个
AI输出（正文/工具名参数，忽略随机tool-call ID）已不同。
这发生于轨迹开端，不可能都由后面的六次H2D造成。需要同时
区分serving数值/批次非确定性和后续工具反馈，不能把所有
输出变化说成预取策略改变KV或算法本身出错。
这也不排除预测控制面的CPU工作通过batch时序影响输出；
初始输出差异不能当作算法之外的纯随机扰动。

Predictive LLM/工具调用多19.12/20.06%，prompt token多22.21%，
output token多9.68%。Native uncached input为11.049/9.760M，
但FULL输入hit约95.98/95.66%，没有明显命中率崩溃证据。
更多uncached token也可能来自新增输入，不能全部称为重算；
block probe溢出仍使全量重算归因不完整。

工具与batch结构也不同：predictive的grep为2504次，reactive
1409次；predictive 17-32 decode为43817个batch，reactive33737，
而2-8 decode分别69471/133384。这改变CPU/GPU重叠和按时间
加权的平均利用率，不能只比较batch数或只比较总输出token。

Native enqueue到first scheduler service的P50约68.37/63.60 ms，
P95约167.99/327.98 ms，均值97.67/102.01 ms；没有predictive
整体GPU准入队列被严重堵死的证据。GPU结果到client结果均值
620.39/613.92 ms也相近，不能把全部尾部归为OpenAI客户端。

| 实际传输 | Predictive | Reactive |
| --- | ---: | ---: |
| 全部H2D batch/GB | 127 / 16.324 | 94 / 12.288 |
| H2D stream event累计秒 | 0.714 | 0.544 |
| H2D submit-to-ACK累计秒 | 10.452 | 7.624 |
| 全部D2H batch/GB | 23872 / 356.863 | 19571 / 308.471 |
| D2H stream event累计秒 | 67.823 | 56.186 |

这些累计值可重叠，不等于暴露stall，但额外H2D stream时间
只有约0.17秒、D2H约11.64秒，不能直接解释额外1187秒。
六个预测H2D的enqueue-to-submit为约1.40-2.29 ms，
submit-to-ACK约4.61-9.48 ms，v3的几百毫秒排队问题已改善。
不能把“有更多IO”与“DMA本身拖慢20分钟”混为一谈。

### 6. 可以确定与仍不能确定的结论

**已确定：**最大的平均GPU利用率/整轮吞吐退化来自具体的
工具超时长尾；工具反馈及pipeline状态存在系统问题；
额外控制工作存在且工具H2D未转化为动作；即时H2D提交已修复；
非空请求阶段仍有次级差距，不能只归于孤立尾部。

**仍未识别：**每个CPU函数的独占时间、发生超时的Django
测试内部堆栈、非空阶段中控制开销与context/batch变化各自的
因果占比。v4没有采集这些profile，不能事后以模型路径差异或
一个总开销数字补造根因。本轮84-root仍冻结；结束后优先修复
真实反馈并补阶段计时，不引入agent guard，也不以删长尾任务
美化正式吞吐结果。
