# KVFlow 完整输入原生 session 适配

2026-10-28。本轮继续完成同一 V230/V311 负载上的剩余 baseline 适配。
保留已完成的 FULL R1/R2/ACK 修复结果和 V345 工具衰减 ThunderAgent；
不重复运行 native 或 V345，也不把接口修复当作 predictive 性能收益。

## 确认的问题与修复边界

`KVFlowObservedAdapter._input_key()` 原来拒绝所有非空 `session_id`。
当前 LoCoBench 重放实际携带原生 radix session，故直接使用旧 AgentX
入口会失败。原生顶层 `session_id` 只标识缓存，不重建输入；它和
`session_params`/`req.session` 的会话历史重建语义不同。实际 scheduler
在调用 `register_visible_request()` 前已经分配 `session_generation`。

新入口只接受明确声明 `full_prompt_replay_guaranteed=true`、缓存启用
radix session、当前 generation 存在且未关闭的请求。仍然拒绝嵌入输入、
位置覆盖及会话历史重建。检查在 context 更新之前完成，非法新 epoch
不能清除旧 context 的前缀证据。sessionless 入口保持原语义。

KVFlow 仍以请求实际提供的 token、extra_key、cache_salt 匹配普通原生
radix 树。固定前缀只从同 context 的已完成输入学习；不把 session ID
加入 token 命名空间，不读后续请求或未来 DAG。所有权仍为
workflow/context/epoch，原生 session 引用和 generation 继续由缓存维护。
复用原有优先级、ACK 对账、原生叶释放和完整输入增长预留，不移植 FULL
的 JOIN 优先、agent 资格、prepare_host 或工具预测卸载策略。

## CPU 证据

`experiments/reports/kvflow_radix_session_20261028/` 保存：

- 新引擎上的聚焦回归 145 项通过。
- V345 实际部署的 prepared 引擎上的 369 项 paper-policy 回归通过。
- 同一 `11b84f7c3198` 内容的 clean 原生 checkout 上，最终 379 项单次
  回归全部通过，无失败或跳过；运行前后核对 1868 个源码文件和原生状态。

最终 `bound_paper_cpu.json/.xml/.sources.json` 与 `run_bound_cpu.py`
绑定同一次执行。22 个新 session 用例覆盖真实原生 Req 注册、已完成
同 context 输入学习、session 标签下 D2H/ACK/释放/H2D/ACK/后续准入、
共享前缀分裂与独占页记账，以及缺失保证、旧 generation、未知/关闭
session、重建会话和非法 epoch 的拒绝。复制和事件仍为 CPU stand-in。

第一份 red XML 是夹具导入遗漏；补齐后第二份 red XML 的两个真实
原生用例均在旧 session 拒绝条件处失败。它们与初次不存在的测试模块
路径错误分别保留，不当作通过证据。源码绑定脚本第一次正确拒绝
prepared 引擎的旧 HEAD tree；最终使用相同内容的 clean checkout，
没有修改原生实现或放宽来源检查。

## GPU 与正式比较

现有 `qualify_paper_prefix_transfers.py` 新增独立 session 场景：真实
完整输入和 session 注册、前缀学习、ACK 前释放拒绝、覆盖全部释放页、
各层 K/V 精确恢复、后续准入、共享 peer 及最终 128-slot 容量归还。
新增三个必需证明字段，并校验 session 场景自己的 generation、ACK
数量、共享长度、字节及容量结果，旧的 sessionless 证明不能直接替代。

冻结应用 `f9f105bd` 已在原生 `11b84f7c3198` 上完成真实 GPU 组件：
原有各项物理证明和新 session 场景均通过。完整组件 ACK 为 D2H78/
H2D54 token，其中独立 session 场景分别为12/12；25份 paper 原生
回执由真实 runtime 对账。新场景在覆盖全部释放页后验证各层 K/V，
并保持原生 session 引用、共享 peer 和最终128-slot容量。它是两层小池
组件，不是 LLM 执行、整轮 baseline 或性能证据。

`run_bound_gpu.py` 校验当前预约、上一轮实际 owned 退出、完整 CPU
清单和精确 native patch 后执行。组件耗时约7.25秒，退出码0；
调用方确认实际进程结束、其 GPU context 消失，并再次检查来源未变。
`gpu_components_r1.entry.json`、原始 log 和 `gpu_components_r1/summary.json`
分别保存入口检查与原始组件结果；冻结工作区没有修改。

正式模型/重放入口和整轮 KVFlow 仍未执行，因此没有新的 JCT/吞吐结论。
现有 LoCoBench causal supervisor 还没有像 AgentX supervisor 那样绑定
paper prefix 资格，下一步补齐这一启动衔接，并验证正常停止及完整对照。
新实验继续保持同一完整 V230/V311、18 个不同项目、3674 请求、
145565966 输入与 549671 输出 token、原始停止和全部正常长尾。
其他 baseline 及全体优越性目标仍未完成。
