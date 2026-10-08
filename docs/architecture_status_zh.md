# BeliefKV 当前架构与实现状态

更新日期：2026-10-08。

## v8 Predictive 最终结论

v8d于2026-10-07 23:47结束，107 completed/1 incomplete，耗时
9394.63秒，完成吞吐41.00 workflow/小时；JCT P50/mean
3727.62/4125.44秒，GPU平均66.07%。物理通道全程未禁用，
receipt failure/遥测丢失/写入错误为0，旧D2H split问题未复现。
GPU已释放，不自动追加实验。下面“最新诊断与启动”是历史记录。

真正预测H2D为1094次/65.623 GB：JOIN 46次/3.306 GB，工具1048次/
62.317 GB；原生H2D仍8596批/2.649 TB。所有预测ACK均有首次服务
记录，但FULL目标复用只有71次（JOIN22、tool49），确认复用
0.668 GB/所传FULL 7.544 GB。Mamba只确认14次/0.901 GB；
当前证明要求单请求prefill batch，其余57.178 GB是未验证，
不能全算浪费。预取占总H2D约2.42%，不能用ACK数量称成功。

核心时机问题：19次estimated_work/EOS前JOIN预取，实际提前量
P50 12.968秒，0次落在RETURN前1秒。27次observed_no_tool_eos
P50 0.899秒，9次落在前500ms、12次在前1秒；这不是模型预测成功。
例如django-11400预测剩24.28token/322.94ms，后续输出计数从570
增至793，约15.56秒才到EOS；剩余工作和实际服务节奏均需核查。
Tool有685/1048次在TOOL_END前1秒，但FULL复用仅49/1048。

核心联合管理问题：ACK到首次GPU服务P50 8.167秒（JOIN10.759秒、
tool8.002秒），而策略租约只有1500ms且不是allocator pin。
1060个租约中724次native_residency_lost、244次prediction_window_left、
80次到期、12次在首次服务释放；168个目标又被native H2D恢复。
issue到submit P50 2.33ms，旧发射延迟已不再是主要问题。
不能只延长全体租约：须联合恢复预算、准入及有界驻留，并区分
需求已就绪、预测变化与native LRU。源码检查点见runtime的
`_prefetch_lease_invalid_reason` / `_register_prefetch_service_lease`。

PREPARE ACK11885次/40.047 GB，全部FULL；自定义parent pressure
demotion为0，现有prepare_consumption为空。不能据此说全部备份
无用（native eviction也可消费），但尚无净收益/原生消费归因。
Host两池均满，FULL/Mamba累计驱逐1.084/1.151 TB；输入token
命中95.28%，旧输入缺失代理0.152%。FULL确认驱逐后重算35882
token，但28103条索引到期、Mamba位置未知，不能当完整重算上限。

Workload仍91个仅一轮、16个两轮、1个四轮；后续11个单child组。
incomplete是pytest-6197 root反复输出同段分析后finish_reason=length，
不是结构化终态门禁、deadline或child取消；它的JOIN已满足。
v8c物理通道失效且v8d改变pipefail等反馈，不能将表面吞吐+2.03%
作为公平加速。相对旧R平均JCT反而+11.57%，输出token+10.08%。
下一步优先修正JOIN工作/时间投影及恢复后的准入/驻留协同，
补batched Mamba和native PREPARE消费证据，再补同版本reactive。

## 最新诊断与启动

v8c reactive 已于2026-10-07 19:45结束，108/108 completed，
原生遥测无丢失/写入错误，所有119个JOIN满足、232个child返回。
但它不是完整工作的PREPARE baseline：17:05:16第18次D2H的
Radix节点37在途拆为535/37，账本只允许原发布集合37，
错误触发 `child publication does not match native ACK`。
随后物理动作全程禁用，仅17个PREPARE获得ACK，原生HiCache仍运行。
原生迁移/缓存和agent轨迹可作诊断，不可据此声称完整策略对照有效。

总耗时9675.25秒，吞吐40.19 completed workflow/小时，平均JCT
3697.63秒、P50 3628.62秒，GPU平均利用率55.33%。最后一个
django-11734耗时9642.11秒，反复全量测试构成长CPU长尾；workspace
初始及最终HEAD均为要求的base commit，不支持模型“HEAD更新”的说法。
H2D 8382批/2.473 TB，CUDA-event累计73.14秒、submit→ACK累计
825.29秒；D2H 2.234 TB。ACK等待不是可直接减去的oracle JCT。
11/108多轮且仅两轮，后续6个单child组；不补造或取消这些模型输出。

修复保留失败关闭机制，仅用native树祖先关系、原anchor代次及
原始Host目标索引确认D2H拆分后的发布集合，并仍校验字节、
pool、session/epoch和重放。忙碌writer改为定时发布状态；
测试形态不再把管道过滤器/选项参数当作测试标签，shell采用
bash pipefail反馈上游失败。不新增agent guard或短预算。

已结束的运行是全新目录
`experiments/raw/qwen35_joint_wait_h2d_predictive_108root_2to4_v8d`，
修复后的108-root单波predictive机制诊断，保留v7头、500 ms/
250 ms窗口、running48、Host200 GB/NUMA1和0.9池比例。
不自动重跑reactive，不将v8c和v8d解释为公平配对或预测独立加速。
严格比较须后续补同修复版本reactive。以下v8c启动记录作为历史证据保留。

v8d启动commit `0f04f70`；首轮108条实际全部双task，检查时
340次PREPARE issued/ACK均已完成、physical_disabled=false、
receipt failure为0。语义worker ready，JOIN/tool prefetch均启用，
模型SHA与v7冻结产物一致。相关回归395 passed/1 skipped。
旧reactive的108个已归档workspace已清理，trace/patch保留。
v8d期间再次冻结代码、prompt、模型及参数，不把启动健康当收益证明。

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
   默认关闭且无 GPU 证据的阶段。v6有7个JOIN H2D ACK/
   0.533 GB，全部FULL首次复用，6个Mamba forward复用确认；
   工具H2D为0。ACK本身不是收益。
3. 工具时间模型已独立上线到开发配置，不更改旧模型产物
   `online_eligible` / `predictive_action_eligible` 标志。
   v5工具H2D已下发6次，但都在ACK后被自身pressure parking
   再回收，后续仍需native H2D。代码已统一条件时间分布并加入
   ACK后的有界策略租约。v6的7个JOIN目标没有自身再次回收/
   原生重载；工具没有新动作，不能声称该链路已获GPU验证。
4. 尚未证明端到端吞吐净收益或普遍亚秒级RETURN预测。
   v6 predictive完成吞吐高4.62%、平均JCT低8.01%，但LLM/
   工具/输入/输出量都更少，84条请求序列均不同。所有动作仍在
   native EOS后启动，不能认定语义预测或预取的独立净收益。
5. v6的84-root单波对照已结束，两侧84 completed，measurement
   valid均84、guard干预计数为0、serving writer无故障。
   每侧另有8个workflow触发允许的2048步提前32步FINALIZE；
   guard计数不包括它，不能声称全部无干预。
   工作头CPU拟合/回放及独立观测EOS路径修复已完成；
   v7已结束，reactive83 completed/1 incomplete，predictive84 completed；
   12个JOIN H2D全部FULL复用，其中3个在EOS前启动，工具H2D仍0。
   当前v8c改为用户授权的108-root单波/每轮2–4 child，
   保留500 ms center/250 ms协议窗口，不排额外重复。
   正式阶段再多轮平均，不要求固定需求回放。
   首次v8尝试因首轮auto绕过委派已停止，不用于2–4对照。
   本机命名task约束只允许一次调用；可重复语法修复后的v8b
   仍108条全单child，也已停止。v8c首轮原生生成范围2–4，
   不拒绝回复/补造child；后续轮次保持prompt驱动并审计实测。
   v8c在 `c744461` 启动；108个root首轮已全部实际派发双child，
   不是只修改配置。reactive运行中，结束后按冻结配置启动predictive。

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
| 当前负载 | v8c：manifest前108个root单波，与每轮2–4 child联合压力诊断 |
| 生成 | context=131072，completion=8192，temperature=0，seed=21 |
| 预算 | workflow=14400秒，graph=2048，允许提前32步FINALIZE |
| Harness | native_in_graph_2to4，同轮2–4调用、鼓励多轮，自然语言RETURN有效 |
| 当前预测窗口 | 实际动作目标为RETURN/TOOL_END前0-1000 ms，仍独立审计真实提前量 |
| 最近运行代码 | v8c `c744461`；v8 `debe99d` 和v8b `026f650` 已停止 |
| 当前v8c | 沿用v7 log-work头/工具CDF；新并发与fanout下仅诊断，不假定校准有效 |

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
旧MLP和短token CDF候选没有稳定改善，不作为默认模型。
v6暴露106.25 token的固定上界投影下限；新产物先扩张signed
residual再截断，旧产物保留原语义。当前只改善条件工作头，
用v6真实100 ms观测补训练，并比较log-work分位数校准。
Runtime增加显式center/upper时机选择，默认upper保持旧行为；
候选不等于已部署，零剩余估计不等于native EOS。
正常stop且有非空正文、无工具标记的native观测另建立仅H2D阶段，
短报告不再因NN forecast缺失或TPS不足而完全漏掉协议窗口。
空白/reasoning-only、length、abort和internal不形成此证据，
不改变agent返回行为。日志独立标记observed_no_tool_eos，
不计作模型预测成功。v7工作开发结果见
`docs/experiments/conditional_work_v7_development_zh.md`。

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
| 工具H2D | v5六个ACK均ACK后再回收；v6为0，不能证明修复后实际收益 |
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
| v6，84-root pair | 84/84 completed；7个JOIN ACK，全部FULL复用，无再次回收；单轮吞吐+4.62%，需求混杂 |

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

1. v6已闭合7个JOIN的ACK到首次服务链，无再次回收/原生重载。
   两个租约过期后仍复用；不把过期视为miss。工具H2D为0，
   仍缺该链路修复后的GPU行为证据。
2. v5只读回放525个采样恢复目标中，新口径有1个near且容量fit，
   旧6次错误早发均不再触发；这不是充分的工具时间精度验证。
   v6的5154次前EOS上界检查全部被过早判断挡住，仍无pre-EOS
   动作。阶段/encoder权重冻结，改进工作头而非改eligibility。
3. 量化exposed restore stall和控制处理开销。
   没有profile的v4不能把差值精确分配到单个函数。
4. harness对SIGKILL/timeout反馈和管道上游失败存在歧义，
   需在后续同配置两侧修复；不在运行中改shell/prompt/timeout。
5. v6多轮workflow为18/13个，但单轮JOIN仍只有1 child；不能靠
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
- 最近完成的pair：`docs/experiments/joint_tool_join_h2d_v6_84root_zh.md`。
- 最近单pair验收：v6 raw目录的 `development_validation_plan.json`。

修改主线时同步维护上述文件，不以新增实验报告代替更新状态页。
历史报告保留原配置和原始结论，新诊断明确标注为后续复核。
v5期间仅修改文档；当时443个Python/shell运行文件指纹为
`4907f0437b65489812eca98b07035952cadf87cab6a00c6a1241cb63a72c4a55`，
指纹算法见 `beliefkv/experiments/decision_characterization.py`。
Git文档提交的变化不应被误记为v5中途更换运行代码。
v6使用新的运行源码指纹，由新launch记录冻结，不回填v5。
v6当前指纹：
`c65caec44ecc934cd5cff9d740ec96f19459f48527505c85927bd4ae969fd6b9`。
目录为 `experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261006_v6`；
225项相关CPU回归为v6启动时证据。v6终态与生命周期已核对，
正向吞吐观测仍有需求混杂。新候选的拟合/回放不回填v6。

v7目录：`experiments/raw/qwen35_joint_wait_h2d_ab_84root_v7`。
启动commit为 `5dfdd30`，runtime源码445文件指纹：
`4257db63689e9a92786f339e706180e56c5370efd421c7bb9d96336be2c466b9`。
两侧已结束：reactive83 completed/1 incomplete，predictive84 completed。
分别耗时5389.94/5209.46秒；12个预测JOIN H2D，3个EOS前启动，
全部FULL首次复用。旧fanout全为单child，不能视为2–4负载证据。

v8b（已停止）目录：
`experiments/raw/qwen35_joint_wait_h2d_ab_108root_2to4_v8b`。
启动commit `026f650`，staging patch SHA：
`4cf11ec7e8041fe262312d02a9fe47d0777860e768311164e9e592196e0e03ab`。
模型和运行参数见该目录 `ab_plan.json`。虽然CUDA graph捕获成功，
108个首轮全部单task，不是合格2–4数据。当前v8c使用新目录
`experiments/raw/qwen35_joint_wait_h2d_ab_108root_2to4_v8c`，
首轮以原生语法范围2–4生成，不用启动配置代替实际fanout。
v8c启动commit为 `c744461`，staging patch SHA为
`53109f07cce1662afeaefa369ab913f94c0dd6e249e0a8b00f327c56838f7087`。
2026-10-07启动核对：108条首轮全部2个task、108个双成员JOIN；
服务端记录108次2–4生成约束，decode CUDA graph覆盖48。
native遥测dropped/failed/writer error均0，旧前缀探针已实际写入；
终态样本位于 `opportunities/admission_opportunities.jsonl`，不是
`server/runtime_audit.jsonl`。很小的旧前缀缺失不能自动当作驱逐
重算；需要与对齐尾部和块级归因分开，Mamba逐层重算仍未精确量化。
本轮尚未结束，不报告完整H2D预算或吞吐收益。后续只改文档，
保持运行源码、权重、prompt和参数冻结。

启动后的持续忙碌阶段发现监控口径限制：`native_telemetry_status.json`
只在writer队列空闲0.5秒或关闭时更新，因此其17:18:56快照不能
代表后续实时计数；JSONL仍持续写入。直播分析须核对原始记录时间、
队列溢出/处理错误和载荷单位一致性，不能沿用旧快照的零错误/命中数。
原始记录一次后续截面已有8,083个native H2D、约2.419 TB，
CUDA-event区间累计71.22秒、submit→ACK区间累计790.49秒；
提交时间/完整载荷没有重复，字节与FULL/Mamba单位数一致。
这些是进行中的累积量，不是完整makespan或可直接减去的oracle JCT。
首轮全双child后，已出现少量后续单child组；记录实际成员，
不补造child或取消正常workflow，也不声称全程已保证2–4。
忙碌时定时发布状态的修复留到冻结pair结束后，两侧运行代码不混用。
