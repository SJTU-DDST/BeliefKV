# V108 策略加 EOS 与最近实验比较

日期：2026-10-22，北京时间。用户最新要求以 V108 策略为主，开启工具期
预取、resident-first 和阶段优先，JOIN 只由 EOS 触发。此前的 V123 默认
配置改为历史参考，不再是下一轮选择。新配置为
[`v108_eos_reference_policy_20261022.json`](../../configs/migration/v108_eos_reference_policy_20261022.json)。

## 策略

保留 36 roots 同时到达、running/decode8、256K/95% 历史压缩、16384
单请求输出上限、200GB Host FULL/Mamba 80:20、write_back、NUMA preferred。
排序使用 workflow，开启 resident-first、JOIN parent 与 final-stage
priority；工具使用 speculative 恢复。后台按安全缺失 FULL 增量备份，
恢复 complete，保留 V108 的额外驻留保护及当前压力撤销正确性实现。
普通 frontier、handoff、短工具排队连续性保持关闭。

JOIN 与工具是两个触发来源：JOIN 只接受实际 EOS；工具期预取仍使用
工具等待信息。不开启 child 剩余工作量或剩余 report token 的提前预测。
保留固定服务端/客户端 seed21、两侧一致的 token-block cache 和后续
CPU/正确性修复，不复现 V108 的随机服务端 seed 和不对称前端设置。
这是一份策略配置，并不宣称与 V108 的旧二进制/轨迹相同。

V108 实际服务端 seed 为 488387661，历史 V103 native 为 1029041560。
predictive/native 的 token-block cache 实际分别为 true/false。
V108 的 selective_report_or_eos 实际启用：至少512MiB恢复需求、200ms
间隔、最多4个候选、有界最多1秒提前量。新配置取消这条 child 预测路径。

## 完整结果

全部为36项原始完整平均 JCT，保留普通工作长尾。输入/输出单位为百万
token，吞吐是全程输出 token/s，不是仅 decode 的忙时速率。

| 实验 | 平均 JCT / 秒 | 全程输出 token/s | 输入 / 百万 | 输出 / 百万 | LLM 请求 |
| --- | ---: | ---: | ---: | ---: | ---: |
| V103 native | 2370.07 | 486.12 | 667.059 | 2.024 | 8195 |
| V108 Full | 2105.47 | 551.77 | 529.954 | 2.128 | 8459 |
| V122 native | 2223.38 | 512.29 | 567.608 | 1.836 | 8005 |
| V122 JOIN-only | 1903.38 | 517.61 | 477.735 | 1.761 | 6768 |
| V122 Full | 1899.64 | 447.97 | 429.467 | 1.528 | 6119 |
| V123 Full | 1788.69 | 524.18 | 429.255 | 1.566 | 6353 |
| V124b Full | 2262.19 | 521.63 | 566.752 | 1.820 | 8163 |
| V125 schedule-only | 1875.68 | 513.68 | 465.967 | 1.561 | 7099 |
| V125 KV-only | 2202.19 | 530.62 | 485.372 | 1.704 | 7263 |

V123、V124b 和 V125 schedule-only 均为35/36无guard合格；其他所列组为
36/36。任务正确性没有独立评分。V125 schedule-only 是JOIN排序加短工具
连续性，不等同于 V122 的简单 JOIN-only。V125 KV-only 按原生请求顺序，
保留该轮自定义 KV 管理；它没有本次新启用的工具期预取和 workflow 排序。

V124b 与 V122 native 的输入/输出/请求差约 -0.15%/-0.89%/+1.97%，是
当前工作量最接近的一组；完整平均JCT仍高1.75%，不能用其他少执行工作的
较好JCT掩盖这项负结果。V123 与 V122 Full 的工作量更接近，但相对 native
少14.7%输出、20.6%请求、24.4%输入，因此不作为合格直接加速比。
V125 KV-only 相对 native 少7.2%输出、9.3%请求、14.5%输入，也尚未合格。

## 后续评价

新的比较器为每一对 JCT 附加工作量检查。输出 token、LLM 请求数和输入
token 三项都需接近；默认以5%对称相对差作为预先声明的工程筛选线，
并保留每一项原始数与相对差。工具次数另列，不用 JCT/token 替代 JCT。
这只是必要条件，仍需相同任务、上下文/容量/seed/前端及可比的完成资格，
并检查逐 workflow 工作量，防止总量相近只是相互抵消。

V125 原冻结矩阵按用户要求在第3项中止；第8、9项取消。剩余四项与
Full/native接续使用补齐种子后的最新V108/EOS配置，详见
[种子修复与接续计划](seeded_eos_modules_v126_20261022_zh.md)。原CPU审计程序
`audit_ablation_workload_after_completion.py` 逐 arm 比较 V123/native，
使用三项工作量的 log-ratio 距离描述更接近哪侧，同时单列是否通过5%
检查；更接近不代表足够接近。旧九项矩阵不再要求全部收齐，保留已完成
两项的部分审计。新结果同时进行逐workflow工作量检查，不以总量相近或
事后选择的有利子集宣称系统加速，也不自动重跑直到碰到有利结果。

数据来源：
`v108/native_reference_comparison.json`、
`experiments/reports/v2767_v123_complete_workflow_comparison_20261022.json`、
`v125/module_comparison.json`。详细机器可读审计输出为
`experiments/reports/v2815_v125_realized_work_compatibility_20261022.json`。
