# V127 质量排除与消融接续

日期：2026-10-22，北京时间。

V127首项no_join_prefetch在22:25完成：36/36 workflow完成且系统测量
有效，35/36具有无guard的自然agent完成资格，driver原始退出码1。
完整平均JCT为2073.53s，外部LLM请求6601次，原生请求6604次。
V2847逐请求核查确认额外3次均为内部上下文压缩，所有请求双端对齐。
这些数字不构成与历史native的合格加速比。

## 停止原因

最后一个任务为
locobench__python_mobile_game_hard_060_multi_session_development_expert_01。
它执行454次外部模型请求和499次工具调用，JCT3933.20s，触发图预算
收尾：graph_step为2017，硬限制2048、保留32步，收尾前连续64次错误、
无进展及重复失败，最近工具均为edit_file。结果同时记录missing_semantic_completion及
guard_intervened_completion。没有HTTP或workflow错误，系统测量有效；
不能将其改成自然完成，也不能以其余35项的较小均值冒充完整JCT。

原监督器只允许退出码1与单一guard排除原因共存，因此把上述双重质量
排除归为进程故障，在收集其余组之前停止。V127原登记、状态、
退出码和trace全部保留，仍为stopped_for_investigation。

## 收集逻辑修复

run_fresh_internal_comparison.completed_arm允许在所有系统测量有效、
无workflow错误、原始退出码1且有guard证据时收集结果；每个排除项必须
包含guard_intervened_completion，可同时含missing_semantic_completion。
其余错误、超时、缺项、未知退出码、无guard的语义失败仍拒绝接续。
质量排除原因、退出码及performance_accepted=false全部保留。

run_eos_pair_after_modules增加可选resume-registration，仅复用这个已终止
且完整收集的首项。接续前检查原进程身份已退出、来源和summary哈希、
全部在线代码/配置不变、任务manifest一致、实际策略/种子与遥测有效。
允许变化的源码仅为列明的离线分析、监督器及其测试；不允许在线运行时、
harness、模型配置或引擎改动混入同一组比较。

新run目录用链接引用首项raw数据并完成后处理；后续从
no_background_prepare开始，再运行no_tool_duration_model、no_child_reclaim、
Full、native。原被取消的第3、8、9项不恢复，不重跑首项GPU工作。
最终比较保留全部普通长尾、每组质量排除和逐任务工作量。

V2845的141项聚焦检查通过。V2850保留V2828全部4221项并新增52项，
4273项及2个subtest通过，无失败、无跳过；V2851绑定应用源码。
此前扩展文件范围自动发现的可选Docker测试不在原资格中，继续使用原
CPU资格范围。V2846含该额外跳过的记录、V2848缺少两个旧参数化ID的
记录均保留且不作为启动依据；最终V2850恢复全部旧ID。

V128接续登记为../ops/v128_eos_seeded_modules_resume_20261022.registration.json，
进度以同名registration.state.json为准。CPU资格不代表GPU性能已达标。

## 最新运行前缀证据

V2842/V2843/V2844封存本组结束前独立读取的JSONL完整前缀及哈希，
不是原子快照，也不是最终结果的替代。当前组主动关闭JOIN预取，
因此其JOIN零传输是预期消融行为。

- 工具FULL传输138条、1.978GB，30条确认物理首次复用、0.327GB。
  108个独立工具等待在完成前出现正FULL计划，56个启动传输，
  19个确认早启且由下一请求复用。命令数、等待数与字节口径不同。
- 80个工具等待出现FULL预算不足；96条拒绝记录的预算在输入/decode
  预留后均为零，不是首个节点过大却仍有正预算的拒绝。
- 提前传输事件的ACK到首服P50约12.15s，全部命令级P50约13.95s。
  保护到期不等于实际丢失，但当前复用不足仍需与排队/容量一同核查。
- 后台idle FULL备份5649批、168.06GB，每批平均29.75MB，
  中位14.42MB；没有触及512MiB或32节点上限。
- 72次带诊断的回收中14次FULL目标未满足，3次观察到未备份拒绝，
  session_referenced拒绝观察507次。观察可重复，不代表独立页/字节；
  没有证据支持绕过活跃/共享引用，或继续扩大后台备份上限。

应使用后续关闭后台prepare的消融及Full结果判断净收益。上述工具覆盖
和备份量不能替代同工作量、同完成质量的JCT与吞吐比较，也不能将
native Host hit字节当作物理H2D或可提前恢复的oracle需求。
