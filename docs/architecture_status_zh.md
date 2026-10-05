# BeliefKV 当前架构与实现状态

更新日期：2026-10-05。

本文是当前实现事实的权威入口，不是逐日开发日志。当前主线已是
Qwen3.5-35B-A3B BF16 / SGLang 0.5.20，不再以旧 Qwen3/SGLang
0.5.2rc1 的 P6 能力描述代替新版事实。迁移前关键节点为
`checkpoint/pre-sglang-model-upgrade-2026-09-22`；本页重整前的
详细开发记录保存在 Git 的 `c219604:docs/architecture_status_zh.md`，
更早记录保存在 `docs/archive/snapshots/architecture_status_zh.md`。
历史实验目录、trace、模型和失败证据没有因本页重整而删除。

## 1. 当前结论

1. 新架构的动态 root/child、工具、JOIN、上下文和原生 session
   身份链已接入。FULL/Mamba 必须共同管理，物理容量由 native
   allocator 和 UnifiedRadixCache 决定。
2. 有界 PREPARE_HOST 与 JOIN H2D 原生事务已实际运行，不再是
   默认关闭且无 GPU 证据的阶段。v3 有10个H2D ACK、v4有6个；
   v4六个全部确认FULL首次复用，五个确认Mamba forward复用。
3. 工具时间模型已独立上线到开发配置，不更改旧模型产物
   `online_eligible` / `predictive_action_eligible` 标志。
   v4发生工具等待KV回收，但工具predictive H2D仍为零。
4. 尚未证明端到端吞吐净收益或普遍亚秒级RETURN预测。
   v4 predictive比reactive的整轮完成吞吐低28.14%；具体退化
   不能仅归于路径差异，也不能仅归于预取。
5. 当前运行v5的84-root单波开发对照，先reactive、后predictive。
   不排额外重复、不要求固定需求GPU回放；多轮取平均留到
   正式实验。运行中的代码、prompt、权重和参数冻结。

## 2. 当前配置

| 项目 | 当前主线 |
| --- | --- |
| Conda | `beliefkv-next`，agent/serving/实验共用 |
| 模型 | Qwen3.5-35B-A3B，权重与FULL KV均BF16 |
| Serving | SGLang 0.5.20，单GPU/TP=1，pinned upstream `94602c9` + staging patch |
| Device | FULL 1,798,995 token，约36.843 GB；Mamba 513单位，约33.096 GB |
| Host | 200.010 GB，NUMA node 1；FULL约105.358 GB、Mamba约94.652 GB |
| 池比例 | Device `mamba-full-memory-ratio=0.9`；Host匹配实际Device字节比例 |
| GPU执行 | running=48，chunked prefill=4096，CUDA graph覆盖decode batch 48 |
| 当前负载 | manifest前84个root，单波，不是64+20延后到达 |
| 生成 | context=131072，completion=8192，temperature=0，seed=21 |
| 预算 | workflow=14400秒，graph=2048，允许提前32步FINALIZE |
| Harness | native in-graph 1-4 child，鼓励多轮，自然语言RETURN有效 |
| 当前预测窗口 | 实际动作目标为RETURN/TOOL_END前0-1000 ms，仍独立审计真实提前量 |
| 运行代码 | v5启动commit `c219604`，包含runtime修复 `1aba1be` |

以上GB为十进制；Mamba单位是完整状态/检查点，不是FULL的一个token。
池usage、物理occupancy、可驱逐容量和free-list不得混用。
旧70:30、30:70、180 GB、36-root和64+64配置属于历史实验，不是
当前默认值。默认未启用SSD KV层、KV FP8或新的SGLang版本迁移。

## 3. 控制与预测

### Runtime 与因果图

Deep Agents允许root在自己的持续对话中派发并等待child。
`native_dynamic_1to4` 的外部bootstrap planner不保证JOIN前后
prefix延续，不用于当前主实验。RCCG根据真实事件更新，不要求
应用预先提供完整DAG，不根据自然语言猜测身份。

请求绑定workflow/invocation/context/epoch/request/attempt与
native session/generation。普通LLM轮次推进epoch但不必关闭
session；成功压缩、终态和显式失效释放旧引用。
summary保留调用child的callback祖先链，不再回落到root。
foreground CALL和JOIN的依赖必须都满足后才能唤醒parent。
并行工具按稳定tool_run_id跟踪，首个TOOL_END不提前唤醒agent。

没有重复工具或无进展语义强制终止、completion格式门禁或
固定child数guard。graph安全收尾、真正空响应的一次恢复和
实际执行故障独立记录，不能都声称为自然RETURN。

### JOIN 阶段与剩余工作

线上冻结MiniLM encoder及阶段头，使用已送达的有界正文、
通知、历史工具/轮次和因果有效decode进度。预测当前生成轮
是否为最终报告，以及条件剩余token工作；不是parent首次GPU
服务时间，不学习离线不可识别的预取净收益。

语义推理在独立CPU process；每个正文frame只提交一次，只为
真实Host-only恢复目标的关键child推理。100 ms正文快照与
Linux monotonic clock domain证据已接入；仅同域才取消100 ms
回溯，旧trace不回填新时钟证明。

native `<tool_call>` token提前使该轮终态信号失效，announce
调用轮与其后的最终报告轮分开。前EOS时机采用已有工作上界；
无工具EOS保留50 ms以内的协议窗口。模型的阶段和长度预测
仍不够准确，不能将协议窗口预取都称为准确的语义时间预测。
MLP和短token CDF候选没有稳定改善，不作为当前默认模型。

### 工具等待

独立加载 `qwen35_native_event_horizons_20260928_calibrated.json`，
验证权重SHA、训练源manifest、当前模型revision及0.5.20身份。
模型给出残余时间和事件CDF，runtime决定备份、回收、加载。

v4的CDF>=0.8二次否决导致目标候选全部未准入；`1aba1be`改为
配置的剩余P50窗口，CDF保留诊断。短工具和同一等待有界降频，
时间hint接受不立即扫描物理ancestry，动作选择时才做有界
inspection/cache，enqueue前仍重新验证。
工具/语义两个worker FD都进入idle poller，有等待时有界唤醒。

## 4. 物理数据面

| 能力 | 当前事实 |
| --- | --- |
| Action-local FULL/Mamba闭包与free-list | 已接入；只读机会不是容量预留 |
| PREPARE_HOST | 原生D2H shadow，ACK前不宣称Host副本有效 |
| 等待态KV回收 | 真实allocator短缺时，dead/cold优先，独占/未锁定/备份已settle才回收 |
| JOIN H2D | WAIT_JOIN且关键child有效，真实Host-only输入、容量与服务证据下发 |
| 工具H2D | 代码已接入；v4零动作，修复后的v5待验证 |
| 提交队列 | 纯预测load_queue安全点立即启动；不抢混合原生队列producer |
| 同步 | 保留native stream fence、layer event、producer/consumer及同步ACK |
| 首次消费 | FULL前缀/node/value证明；Mamba单请求COW forward证明或明确未验证 |
| 传输模型 | pool shape/相近size的服务估计，enqueue-to-submit与submit-to-ACK分账 |
| Host eviction归因 | FULL精确prefix/区间reaccess；Mamba hit-location仍不完整 |
| 完整COMMIT/JointPlan/handoff | 旧算法不能直接外推，新架构完整联合执行闭环仍未验收 |
| Running retraction | 无完整新版selective release适配，不开放旧全套物理开关 |

H2D源可以来自native D2H或PREPARE，不要求PREPARE先被消费。
恢复必须对应下一输入可复用的安全checkpoint，不使用生成输出
末端。身份、epoch、session或工具/RETURN事件变化撤销旧intent。
partial backup/多node预算不等于完整context已恢复。

旧 `--enable-beliefkv` 全套物理路径仍fail closed；现在可用的是
独立native admission和有界动作路径。这个区别不能简写成
“新版完全没有H2D”，也不能反向宣称完整旧checkpoint算法已迁移。

## 5. 已验证证据

| 开发实验 | 已观察的主要事实 |
| --- | --- |
| v2，64-root | 两側完成64/63；预测H2D为0；summary错归parent阻断WAIT_JOIN |
| v3，64-root | 10个ACK/814.94 MB，8个RETURN前启动，2个迟发 |
| v4，64-root pair | 两側64/64；6个ACK/491.09 MB，全部FULL复用，工具H2D为0 |
| v5，84-root pair | 运行中；包含`1aba1be`，不写入尚未完成的收益 |

v4六次动作中，五个是在native EOS后协议窗口触发；唯一前EOS
动作提前约6.59秒，是剩余工作低估。全部早于RETURN不等于全部
满足100-1000 ms或已隐藏同步等待。没有独立patch grading时
只能报告completed-workflow吞吐，不称正确任务吞吐。

v4退化的详细复核写入原有
`docs/experiments/joint_tool_join_h2d_v4_zh.md`，数据在
`experiments/analysis/v4_gpu_root_cause_20261005.json`。
GPU service记录是scheduler/worker墙钟区间，不是kernel时间。
NVML近似积分显示额外约1117秒空闲；主要长尾来自django-16938
两次600秒全量测试。有服务需求时仍存在差距，控制面扫描、
工作量/batch变化和迁移干扰需要分别分析，不能用路径差异一句
带过。现有日志不提供每个CPU函数的独占时间。

## 6. 当前阻塞项

1. 验证84-root是否增加可消费PREPARE/恢复机会，而非只增加
   Host churn、有用KV丢失或预取驻留。
2. 修复后的工具P50策略是否能及时触发实际H2D；v4机会139个
   采样中大部分时间头本身未进入窗口，改规则不保证有动作。
3. 量化scheduler主线程物理inspection/CDF/事件处理开销。
   没有profile的v4不能把差值精确分配到单个函数。
4. harness对SIGKILL/timeout反馈和管道上游失败存在歧义，
   需在后续同配置两侧修复；不在运行中改shell/prompt/timeout。
5. root多轮spawn仍少，v4每轮均一个child；不能靠强制取消/
   返回门禁制造JOIN或“提高自然完成率”。
6. Host块归因长prefix probe有溢出；不能以观测子集证明全量
   重算率很低。Mamba逐节点命中位置仍有缺口。
7. 稳定端到端收益尚未证明。开发单pair用于机制验证，正式
   实验再多轮取平均/报告方差，保留失败/截断，不筛分歧轨迹。

## 7. 权威入口

- 当前设计：`docs/beliefkv_design.md`。
- 当前状态：本文件。
- 执行顺序：`docs/implementation_plan.md`。
- 不可违反的实验约定：`docs/experiment_operating_notes_zh.md`。
- 最近完成的pair：`docs/experiments/joint_tool_join_h2d_v4_zh.md`。
- 当前单pair验收：v5 raw目录的 `development_validation_plan.json`。

修改主线时同步维护上述文件，不以新增实验报告代替更新状态页。
历史报告保留原配置和原始结论，新诊断明确标注为后续复核。
本次仅修改文档；启动后的443个Python/shell运行文件指纹仍为
`4907f0437b65489812eca98b07035952cadf87cab6a00c6a1241cb63a72c4a55`，
指纹算法见 `beliefkv/experiments/decision_characterization.py`。
Git文档提交的变化不应被误记为v5中途更换运行代码。
