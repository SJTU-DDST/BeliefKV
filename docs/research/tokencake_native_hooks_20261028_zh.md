# TokenCake 原生调度接线修复

2026-10-28。保留首轮正式启动的失败及零完成回执，不计为性能对照。
原因、进程退出和cleanup证据位于原native-session目录的
formal_r1_failure.json。此前直接adapter/PrefillAdder组件不覆盖实际
scheduler循环，不能继续将其描述为完整原生接线资格。

## 修改范围

单独原生分支`beliefkv-tokencake-scheduler-hooks-20261028`基于
common11b84，commit7424373，tree131d175450fe2b74d31a0508c2e13c55a3750a56。
只有scheduler.py和schedule_batch.py改变：

- 每个候选在原生输入初始化前经过已有的容量guard；拒绝时不准入。
- 原生decode回收使用已有的TokenCake victim选择回调。
- 在真实释放请求KV前结清TokenCake容量，OOM终止路径也保留该边界。

这些是原独立TokenCake引擎已经具有的接线，不是新的调度算法。
未修改compute、KV复制、allocator或Full策略。新增
tokencake_engine.py按精确AST撤去这些可选hook后，要求全部源码等于
common版本；原始队列、native victim回退、victim合法性检查保留。
规范patch从fa5cb52生成，SHA256为
56d7fdefec0beb0fb5277aaa1704c1bcc0e380d49ee2240ee96905c2977557a6。

## 原生循环证据

三个旧引擎CPU用例全部复现“跳过容量guard”：正常准入、容量拒绝、
连续成员容量结清。首次夹具误用了浮点waiting score，已单独保留；
第二轮三项均复现真实异常。修复后测试实际执行原生scheduler方法，
调用真实Req、PrefillAdder、cache与allocator，拒绝请求不会初始化
prefix或分配KV。后续原生decode测试核对victim边界与先记账后释放。
保留最初空Tensor布尔判断的夹具失败，没有放宽生产检查。

新GPU资格入口已改为在全部十二场景中执行原生scheduler候选循环，
包括H2D未完成时的实际拒绝。校验器同时要求准确调用次数、原生hook
源码、原来的所有字节/ACK/consumer/容量/session证据。旧报告不能
满足这个新门槛，但仍由各自冻结的旧校验器保留原始有效范围。

当前bound_cpu一次执行812项全通过，无跳过，保留原759项，1884份
来源在执行前后相同。原生为干净committed131d树，copy/event仍是
stand-in。CPU入口首次把累计patch错误应用到已包含该patch的HEAD，
在执行pytest前被拒绝；已明确改为检查干净目标tree，将规范patch
应用到独立fa5cb52准备checkout后再做实际注册绑定。

新声明`configs/baselines/tokencake_locobench_native_hooks_20261028.json`
相对首轮只更换原生package/flavor；七个TokenCake参数、完整V230/V311、
18项目、running/decode24、805198 Device tokens、200GB Host和seed21
不变。Full工具预取保持关闭；paper自己的temporal策略保持独立。

后续先冻结run_components.py并验证当前GPU/hold、失败轮实际退出及
两次新鲜资源状态。新树还需实际GPU与model/position检查，再单独
登记完整重试。尚无该修复的GPU或性能结论，整体目标保持active。

## 实际GPU组件完成

冻结8808c1ac随后通过新的十二项真实GPU场景，退出码0并完成owned清理。
full-upload/early-D2H各执行3次原生候选循环，early-H2D执行4次，
expiry/retirement/pressure各执行2次；plain与native-session两组分别
完整。原先各层K/V字节、D2H/H2D ACK、早返回、容量和Host pin门槛
均保留。实际GPU报告PID1118109与Popen子进程一致且已退出；初始
`/proc`回执的argv为空，不能将其当作独立argv观测证明。

gpu_components_r1.entry.json绑定原始报告及完整输入，静态native
compatibility也通过精确投影。准备checkout从fa5cb52应用规范patch，
实际tree仍为131d；canonical path绑定不改变代码。

start_position.py为独立后续入口：核验812项CPU、实际组件及失败轮
清理后，冻结新应用并启动24请求token/session/native-stop检查与
running1/8/24 sampler校准。其后只登记TokenCake完整重试，不采native、
不重跑ThunderAgent。实际模型/position结果和完整性能仍待产生。

## 模型检查与完整重试已启动

24请求token/session/native-stop检查已实际通过，24项均原生停止；
running1/8/24 sampler校准完成。position supervisor1119760及所属
server退出并清理后，完整TokenCake重试从冻结22caa011/native131d
在18705启动，登记为
`ops/tokencake_locobench_native_hooks_20261028_r1.registration.json`。

独立observe_live.py/live_start.json核验实际nice0 watcher1122651、
server1123520、driver1124738和唯一GPU scheduler1123824，以及原生hook
投影、七个实际策略参数和启动前新鲜prefix/十二场景资格。封存318个
完全一致的完成回执，未知per-agent预算保持null。该前缀不代表完整
3674请求、正常尾部或最终JCT/吞吐。

所有完整工作量、V311工具时间、容量、seed和普通数值设置保持原合同。
原生tree不同于V345/KVFlow，比较时明确保留这个必要hook差异。
main之后的Agentix CPU session适配没有进入此冻结TokenCake运行。

## 等待期间的完整结果准备

后续独立封存的collecting_during_agentix_consumers.json包含2089/3674
精确完成回执，预算未知仍保留null；这是未匹配的运行前缀，不与完整
Full/KVFlow/V345作JCT或吞吐优劣结论。运行及原始失败均保留。

新增complete_review.py先核对完整源、工具时间、容量、seed和三种
已审查引擎，再调用各实验自己的冻结configuration observer。实际
`--precheck`接受三个完整control并拒绝仍在运行的TokenCake。
完成后要求3674请求/精确token/原生停止、所有18个正常尾部、两次
新鲜idle快照及实际进程退出，再生成Full/TokenCake/V345曲线；
KVFlow的既有完整指标与独立图保留。尚未生成当前完整比较或性能结论。

## 完整重试失败与控制路径修复

该运行随后在2922/3674个精确完成回执后失败，不生成完整JCT/吞吐
结果。formal_native_hooks_failure.json核验冻结来源、原生异常、
精确回执、实际进程退出及owned cleanup。原生异常先发生在
TokenCakeNativeLifecycle.waiting_pressure -> capacity.probe ->
_path_extents；回放事件socket的ConnectionError是其后的断连。
最后实际快照有197次path_depth_declined、1082122次容量probe，
preservation/plan/guard累计wall为1613.533/509.408/526.401秒。
这些区间可能嵌套，不能相加成暴露GPU等待或可消除JCT。
失败时的具体节点没有记录，不能伪造其身份或精确深度。

CPU真实原生树复现一个独立的实现缺口：合法前缀分裂得超过可选
NativePrefixTransfers的扫描上限后，空间记账也调用同一capture，
因而抛出不可恢复ValueError。修复只将空间记账改为一次原生祖先
遍历，以完整上下文token上限约束正长度extent总和，仍检查当前节点
对象、命名空间、循环、缺页与加载依赖。可选传输的扫描边界不变，
不通过忽略异常、漏记共享页或授权多占Device容量来绕开问题。

另一个明确冗余是没有任何ready upload时仍计算critical waiting
demand，触发无用途的长前缀probe。四个旧代码用例分别在无checkpoint、
尚未到期、传输在途和已驻留时复现。现保持原有capacity refresh及
deadline检查，仅在ready为空后返回；存在上传目标时保留原公式。

no_ready_upload_r2_cpu先通过816项；加入原生碎片路径及错误路径检查后，
fragmented_capacity_cpu一次通过821项，无失败/跳过，保留原812个
字面ID，1879份来源前后一致；93项定向检查单独保存，不与821相加。
此前测试插入位置造成的fixture失败和原红测试均保留。原生仍为
7424373/131d，未改变KV kernel、allocator或论文调度参数。这是CPU
资格，不证明新GPU安全性、整模型或完整性能。新完整TokenCake必须
重新执行当前来源的组件及模型/position检查，不能续跑失败的2922前缀。

依赖TokenCake完整成功的Agentix CPU waiter按原合同停止且无GPU child。
可在核验失败运行实际清理、当前hold与空闲资源后单独执行其已经冻结
的四组小池字节检查；这是独立资格，不将失败的TokenCake当作成功前序。

## 修复后的独立完整重试入口

retry_fragmented_capacity.py先绑定当前821项CPU/1879份来源、独立Agentix
四组GPU检查实际退出及失败TokenCake清理，重新运行当前来源的十二个
原生scheduler/生命周期GPU场景。通过后再登记新的24请求model/position
与running1/8/24采样校准，最后仅运行新的完整TokenCake。
入口使用18707和20261029独立路径；保留旧失败registration和socket。
仍使用原七项参数、V230全部3674请求、V311工具时间、18项目、
Device805198/Host200GB、running/decode24及seed21；不重采native、
不重跑V345或KVFlow，不做消融。实际重试结果尚待执行。
