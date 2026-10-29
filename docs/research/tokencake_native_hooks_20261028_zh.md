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
