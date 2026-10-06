# BeliefKV 当前架构与实现状态

更新日期：2026-10-06。

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
   默认关闭且无 GPU 证据的阶段。v5有17个H2D ACK/1.298 GB：
   JOIN 11个FULL首次复用，工具6个未复用；10个Mamba forward
   复用已确认。ACK本身不是收益。
3. 工具时间模型已独立上线到开发配置，不更改旧模型产物
   `online_eligible` / `predictive_action_eligible` 标志。
   v5工具H2D已下发6次，但都在ACK后被自身pressure parking
   再回收，后续仍需native H2D。代码已统一条件时间分布并加入
   ACK后的有界策略租约，CPU回归通过；新GPU验证尚未完成。
4. 尚未证明端到端吞吐净收益或普遍亚秒级RETURN预测。
   v5 predictive完成吞吐低8.95%，共同完成83项的均值JCT
   低8.84%，但需求/路径不同，不能直接认定预取净收益。
5. v5的84-root单波对照已结束，reactive 84 completed，
   predictive 83 completed/1 length-truncated incomplete，
   无child取消/serving writer故障，GPU已释放。没有新GPU实验
   或重复队列；修复后的下一对为同配置v6，不排正式多轮。
   正式阶段再多轮平均，不要求固定需求回放。

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
| 最近负载 | 已完成v5：manifest前84个root，单波，不是64+20延后到达 |
| 生成 | context=131072，completion=8192，temperature=0，seed=21 |
| 预算 | workflow=14400秒，graph=2048，允许提前32步FINALIZE |
| Harness | native in-graph 1-4 child，鼓励多轮，自然语言RETURN有效 |
| 当前预测窗口 | 实际动作目标为RETURN/TOOL_END前0-1000 ms，仍独立审计真实提前量 |
| 最近运行代码 | v5启动commit `c219604`，包含runtime修复 `1aba1be` |

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
配置的剩余P50窗口，H2D准入中CDF保留诊断。但长等待/pressure
parking仍使用条件CDF<=0.1；v5证实其与P50倒计时同时支持相反
驻留决策。新代码从同一CDF按仍未结束的条件逆算P50；
不再把过期点预测clipping到0当作完成信号，不增加CDF>=0.8否决。
经验证H2D ACK建立最多lead+1000 ms的策略租约（当前2秒），
从自身冷回收候选中排除该node，覆盖下一epoch及native session
接力；首次GPU服务、预测变化、终态、过期或原生失驻留显式释放。
它不是全局pin、容量预留或复用证明，native最终回收权保留。
短工具和同一等待有界降频，
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
| 工具H2D | v5六个ACK/0.444 GB，均ACK后再回收，FULL未复用 |
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
| v5，84-root pair | 84/83 completed；17个ACK，JOIN11个FULL复用，工具6个自我回收；吞吐-8.95% |

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

v5详细结果见
`docs/experiments/joint_tool_join_h2d_v5_84root_zh.md`。
两侧均有113个child/完整JOIN、29次JOIN后再派发，但每轮child
仍为1。v5十一个JOIN H2D全部在native EOS后启动，RETURN前
提前中位863 ms，6个在1秒内；不能据此声称语义时间预测已准。
工具六次早发3.70-7.11秒，全部ACK后94-1652 ms被回收并再次
native H2D。38次pressure释放均有先前PREPARE ACK，涉及32节点，
包括重复循环，不全部计为收益。
Host两池都达满池，FULL hit约95.5%，块归因大量overflow；
84-root有机会，但不能据此证明有用重算很低或冻结正式负载。
GPU利用率76.50/81.66%，v4式大空转未重现；predictive仍有
约899秒仅两workflow的小batch忙碌尾段，不能以利用率高认定吞吐好。

## 6. 当前阻塞项

1. 新代码的条件时间与策略租约已有CPU验证，需v6检查实际
   H2D是否保留到服务、自身重复回收是否消失、原生失驻留及
   明确撤销是否可归因，不能只看ACK增加或减少。
2. v5只读回放525个采样恢复目标中，新口径有1个near且容量fit，
   旧6次错误早发均不再触发；这不是充分的工具时间精度验证。
   剩余工作头仍未产生pre-EOS动作，阶段/encoder权重未改变。
3. 量化exposed restore stall和控制处理开销。
   没有profile的v4不能把差值精确分配到单个函数。
4. harness对SIGKILL/timeout反馈和管道上游失败存在歧义，
   需在后续同配置两侧修复；不在运行中改shell/prompt/timeout。
5. v5多轮workflow为19/15个，但并行fanout仍只有1；不能靠
   强制取消/返回门禁制造JOIN或“提高自然完成率”。
6. Host块归因长prefix probe有溢出；不能以观测子集证明全量
   重算率很低。Mamba逐节点命中位置仍有缺口。
7. 稳定端到端收益尚未证明。开发单pair用于机制验证，正式
   实验再多轮取平均/报告方差，保留失败/截断，不筛分歧轨迹。

## 7. 权威入口

- 当前设计：`docs/beliefkv_design.md`。
- 当前状态：本文件。
- 执行顺序：`docs/implementation_plan.md`。
- 不可违反的实验约定：`docs/experiment_operating_notes_zh.md`。
- 最近完成的pair：`docs/experiments/joint_tool_join_h2d_v5_84root_zh.md`。
- 最近单pair验收：v5 raw目录的 `development_validation_plan.json`。

修改主线时同步维护上述文件，不以新增实验报告代替更新状态页。
历史报告保留原配置和原始结论，新诊断明确标注为后续复核。
v5期间仅修改文档；当时443个Python/shell运行文件指纹为
`4907f0437b65489812eca98b07035952cadf87cab6a00c6a1241cb63a72c4a55`，
指纹算法见 `beliefkv/experiments/decision_characterization.py`。
Git文档提交的变化不应被误记为v5中途更换运行代码。
v6使用新的运行源码指纹，由新launch记录冻结，不回填v5。
