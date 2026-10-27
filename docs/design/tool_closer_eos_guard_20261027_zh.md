# 工具闭合标记的 EOS 负证据

在完整 sibling FULL 的实际数据中，dashboard 的一个单 child JOIN
预取发生在真实 JOIN 完成前约 124799.506 ms。该次 JOIN 完成后原生
排队约 3022.645 ms；约 127.6 秒 ACK 到服务间隔不能归为已就绪请求
的纯排队、恢复等待或公平性损失。

真实触发请求为 `beliefkv:01a177a9-1efb-7f53-9ae7-178ef8416a6a`。
其完整 58 个输出 token 用实际本地 tokenizer 解码后包含：

```text
<function=ls>
<parameter=path>
/workspace
</parameter>
</function>
</tool_call><|im_end|>
```

它没有专用起始 token 151657 (`<tool_call>`)，但有闭合 token 151658
(`</tool_call>`)。原生正常 EOS 结束的是这次工具响应，child 后来仍有
很多模型请求，尚未 RETURN。原策略只检查起始 token，因而将它当作
无工具的最终回答，错误地恢复了 parent。

## 修复

`NativeAdmissionRuntime` 从实际 `added_tokens_decoder` 加载工具标记时，
将 `</tool_call>` 也纳入已有的工具证据集合。现有 completion 和可选
report-progress 路径继续使用同一集合；完整响应中任一标记都足以否决
“无工具 EOS”证据。内部历史字段名保留，避免无关接口改名。

没有新增 tokenizer decode、语义模型、每 token 全队列扫描或工具时长
阈值。在线只读取已经生成的 token，既不查看未来 RETURN，也不使用
重放最终步数或后续请求来决定预取。普通最终报告的 EOS 路径保留。

这是一项错误投机触发的实现修复，不是新的调度算法。它不能保证所有
无专用标记的工具格式均被识别，也不证明修复后 JCT/吞吐必然提高。

## 证据与验证

`experiments/reports/reference_join_siblings_20261027/tool_closer_trigger_audit.py`
独立封存实际触发、完成 token 和 replay 生命周期。60 个观察到的
`observed_no_tool_eos` 触发中，56 个有实际最终请求证据，4 个只有闭合
工具标记且后面仍有请求。后续请求只用于离线标签，没有注入在线策略。

新增 CPU 回归使用上述真实 58-token 输出，复现旧代码错误产生
`_semantic_eos_proofs=True`，同时保留带 opener 工具和普通报告的对照。
原失败为 `tool_closer_eos_before_fix.xml`。最初定向报告还有 4 个原生
fixture 因未显式指定 SGLang 目录而跳过，不能作为完整资格验证。

最终 source-bound 共享执行通过 3247 项及 4 个子测试，无失败/跳过，
保留原 3174 项身份并增加 73 项。资格脚本现在明确加入 EOS 和新授权
审计测试，设置并记录本地 `BELIEFKV_CONTEXT_TOKENIZER_TEST`，封存
实际 tokenizer_config/tokenizer JSON 摘要；原生 fixture 使用已声明的
SGLang 源及 copy/event CPU 替身。三种真实构造器隔离仍通过。

报告为该目录下 `tool_closer_shared_cpu.xml`、
`tool_closer_shared_cpu.sources.json` 和
`tool_closer_shared_cpu.evidence.json`。CPU 验证不等于 GPU 性能证明。
冻结 525c7cc6 的已完成 FULL 未包含本修复；后续只能另行冻结完整 FULL，
保持相同工作量/工具时间/容量/种子并继续复用完整 V345。

## 实际运行

完整 sibling R1 的 owned 退出、最新预约、空闲 GPU、端口和 socket 检查
通过后，登记 `ops/reference_tool_eos_guard_20261027_r1.registration.json`。
只执行修复后的 FULL，配置仍为相同 sibling 配置，没有重跑 V345。
冻结应用为 `ac94b2bcd1187906c2439c4a69705cbc74264cef`，native 树仍为
11b84f7c3198，端口 18663，socket 为 `/tmp/bkv-causal-18663.sock`。

`experiments/reports/reference_tool_eos_guard_20261027/live_start.py/.json`
确认实际 watcher280028/start730778349、server280459/start730781053、
GPU scheduler280797/start730782464、driver281924/start730796166 均为
live/nice0，GPU PID 与 NVML 一致。13 项工作量、工具时间、容量、引擎、
流式和种子字段与完整 V345 相同；实际 serving tokenizer 摘要与 CPU
真实 tokenizer 测试一致，冻结 runtime 源摘要与 3247 项资格证据一致。

这只是启动及源码身份验证，尚未到达并验证四个旧误触发案例，不作为
完整请求正确性、传输行为、JCT 或吞吐证明。源、配置和外部输入冻结。

CPU 绘图等待器281239/start730784077为 nice19，隐藏 CUDA，显式使用
`ops/cpu_plotting_20261027/bin/python` 生成最终图表，仅在完整成功且
实际 owned 退出后执行，失败则停止，不启动 GPU。状态为
`ops/reference_tool_eos_guard_20261027_r1.plots.state.json`。没有后续
GPU 实验排队。

## 旧误触发案例的真实重放核验

`scripts/audit_tool_eos_guard_cases.py` 只在离线审计中使用四个历史反例。
它先封存当前已完成的 native receipt 前缀，将每个请求的输入、输出、
哈希与原生停止条件逐项对照上一轮完整工作量。随后检查四个原请求
仍为相同 context/epoch、相同输出且只有工具闭合标记，并要求同 context
更高 epoch 的已知后继也真实完成。最后要求同一 monotonic clock domain
下的 runtime 快照晚于后继完成及 EOS 观察窗口，才允许将“没有误触发”
判为通过。缺失请求、后继、时钟证明或足够晚的快照均保留为未验证；
出现旧触发则明确记录失败。

`guard_case_audit_cpu.xml` 通过 68 项定向测试，无失败或跳过，覆盖新审计、
已有 JOIN 授权服务审计与资格观察审计。报告位于
`experiments/reports/reference_tool_eos_guard_20261027/`，与同目录
`collecting_guard_cases_after_resume.json` 一并保留。

该真实前缀封存 2013 个精确完成请求，四个历史反例全部为
`completed_tool_response_without_final_eos_trigger`，均观察到后继完成及
超过 1000ms 证据窗口的 runtime 快照。它验证了已知误触发的修复；
未验证所有工具格式，也不是完整工作量、JCT 或吞吐结果。运行中的
冻结应用和 engine 未因此改动，没有登记新的 GPU 实验。
