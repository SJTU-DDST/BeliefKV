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
