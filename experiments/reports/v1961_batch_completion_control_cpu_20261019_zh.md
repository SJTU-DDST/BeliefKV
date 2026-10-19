# 复用完成 ID，减少 runtime 与 native 的重复扫描

日期：2026-10-19。BeliefKV 基线为 `392d256`，原引擎为 V86，
候选为独立重建的 V87。GPU 实验仍按用户要求暂停。

## 定位及频率边界

核对最新完整 V85d 的 V1923 报告及原始 opportunities 最后一条记录：
`physical_planning_after_forward=1811270`，
`native_physical_completed=558`，工具计时模式开启，详细共享计时关闭。
因此不能一直只依据 64/96-node 低频迁移微基准选择优化。

同时，post-forward 可在没有新模型 batch 的调度循环中执行，不能将
181 万次当成 batch completion 次数。该 trace 记录 prefill worker 区间
2944 次、decode 138546 次，合计 141490；这也不是额外模式或未记录区间
均为零的证明。本轮不使用这些数字推算累计 CPU、GPU 或 JCT 节省。

已有来源：
[V1923](v1923_v85d_complete_predictive_analysis_20261019.json)，报告 SHA256
`d0b219ecef2edb697d31c938a3b0c5bf97554711d3728dfb91ab3f1a4ac90272`。
原始 admission_opportunities.jsonl 的 SHA256
`05d80418cf724afb0a3327f39d85d00fcfdace47072c1103c14d7ed60009f1ab`
与其绑定一致。没有启动新的采集。

## 修改

原 runtime 在 `on_batch_completed` 中逐请求调用 `finished()`；
native 适配层收到返回后，再遍历相同 batch，查询注册集合并调用
`finished()` 清理已结束 ID。原生 predicate 只是读取 finished_reason，
但整个第二次 Python 遍历和生成器在普通 decode 中也会执行。

现在 runtime 在原扫描中收集完成 ID 并返回 tuple，适配层直接将这些
ID 从注册集合中移除。没有完成请求时返回空 tuple，不创建完成列表。
未登记/untagged 请求的已完成 ID 也可返回，集合差分自然忽略它们。
每一批重新检查，不跨 batch 复用完成状态。

适配层始终先把同一个原始 batch 交给 runtime，保留服务观察、ACK 前
首次服务记录、context/token 更新、EOS 和 visible 清理。返回空 tuple
不代表可以跳过这些工作，也不是模型终态门禁。

四种组合均保留：

- 旧 runtime + 旧适配层：原路径。
- 新 runtime + 新适配层：复用完成 ID。
- 新 runtime + 旧适配层：返回值被忽略，原第二次扫描仍执行。
- 旧 runtime + 新适配层：None 返回值使用兼容扫描。

因此只有更新 runtime 和引擎适配层后才获得该路径的降本。

## CPU 对照

V1960 使用完整冻结的旧 event_channel、observer、physical 和 runtime。
从 V86/V87 的实际 scheduler.py 提取适配层 AST，从候选
schedule_batch.py 提取原生 `Req.finished()`，不导入 GPU scheduler。
所有源码 SHA256 保留。计时内 predicate 没有计数包装。

24 组覆盖 1/8/48 请求、0/1 个完成请求、工具计时模式开关、纯 tagged/
混合 untagged，每组四种组合，151 样本、20 个预热样本，测量顺序交替。
无请求完成时每样本 100 次回调；有完成时每样本一次，逐次在计时外重置
visible、context、注册和计数，避免只测已经被清理的请求。
构造、重置、状态比较、计数 predicate 均在计时外。

这里测量 runtime 加 native 完成适配层，包含原 runtime diagnostics
入口的无 writer 返回，不包含整个 native result processor、模型计算、
活跃最终报告阶段或后台观测写入。不能称为完整调度器速度。

纯 tagged、工具计时模式开启，线程 CPU P50：

| 请求数 | 完成数 | 基线 us | 新组合 us | 降幅 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 0 | 1.905 | 1.487 | 22.0% |
| 1 | 1 | 6.582 | 6.043 | 8.2% |
| 8 | 0 | 3.230 | 1.901 | 41.1% |
| 8 | 1 | 7.801 | 6.346 | 18.7% |
| 48 | 0 | 11.384 | 5.161 | 54.7% |
| 48 | 1 | 17.073 | 10.653 | 37.6% |

24 组新组合 P50 均更低，范围约 0.96%–54.66%。较大的百分比来自
没有完成请求的短回调，不可用于宣称整个 decode 或端到端加速。

8 请求、无完成的兼容组合为：新 runtime/旧适配层 3.187us，
旧 runtime/新适配层 3.201us，均未得到新组合的 1.901us。
有完成时，新 runtime/旧适配层为 7.896us，略高于基线 7.801us；
返回完成列表本身有成本，兼容正确不等于兼容组合也加速。
所有组合的壁钟、线程 CPU、均值和 P50 原样保留。

四种组合的注册集合、visible、context session、token 状态、semantic
revision、计数和 pending 状态均一致。计时结束后另行包装 predicate，
8 请求纯 tagged 场景的检查次数为旧组合 16、新组合 8、两个兼容组合
各 16，确认减少实际扫描而非跳过服务回调。

## 引擎与待恢复配置

两个引擎都基于上游
`94602c9c2b7cbdb8efd5c52802dac6a1c180089e`：

| 来源 | 引擎树 |
| --- | --- |
| V86，保留 | `fb4dfe6bbb4f194b3b930d13abd4cb5943bd4bf1` |
| V87，新 CPU 候选 | `db73b95512687e956e94d08389829ad20098295e` |

两树只相差 scheduler.py 完成回调和 test_beliefkv_lifecycle.py。
完整 staging patch SHA256 更新为
`130d376d7e50a7072f6f9bb63319438f87fa94eb1e3e29189c21fac10754668e`，
从干净上游 index 可应用，在 V87 工作树可反向核对。
V86 的旧 scheduler SHA256 仍与基准绑定一致。

`run_qwen35_long_repository_ab.sh` 默认源码改为
`sglang-v87-completion-cpu-20261019`，输出候选目录改为 v87；
24-root 配置绑定相同源码与补丁。显式 SGLANG_SOURCE_CHECKOUT 仍受尊重。
24 distinct root、16+8/900s、running8、256K、95% 压缩、Host80:20、
动态委派与前端对照开关保持原值。GPU 暂停及自动恢复禁用均保持，
没有启动 V87，也没有修改 V85d/V86 冻结来源。

## 检查与未完成目标

主仓 2045 项 CPU 回归通过，无 skip，包含新增 6 项完成 ID、跨批更新、
context 清理、profiling 和 ACK 前首次服务检查。引擎 lifecycle、
scheduler hook、shadow dispatch 共 86 项通过，另有 41 个 subtest。
首批 6/38 项与以上集合重叠，不重复计数。

扩展回归还覆盖工具/JOIN、handoff、部分恢复、驻留、物理 ACK、
长任务配置、patch 重建和真实 CPU radix/allocator/reclaim。
全部显式禁用可见 GPU。启动脚本通过 bash 语法检查。

本轮删除的是完成回调中的重复扫描。post-forward 的到期检查、真实
尾部规划、逐命令来源验证和其他控制仍有成本，未证明都已优化完成。
当前没有新的在线预取复用、GPU 利用率、吞吐或 JCT 结果；最终相对
native 的性能目标仍需在用户恢复 GPU 实验后验收。

原始对照：
[V1960](v1960_batch_completion_cpu_20261019.json)。
