# Host 丢失前缀的实际 prefill 工作

本记录补充当前 Oracle 和 D3 的 CPU 只读检查。不改变运行或待启动的
GPU 配置，不将重算计数、worker 区间或局部选择直接转换成 JCT 收益。

## 检查方法

`scripts/audit_host_block_prefill.py` 使用原生
`host_block_reaccess_attributed` 的精确 namespace、前缀指纹、请求归属、
FULL 区间和命中量，重建真正缺失的前缀区间，再与同一请求的
`prefill_extend_input_len` 实际输入区间求交。

只有归属一致、单一原生 attempt/session/generation、单一归因 probe
时钟且输入几何有效时，才计入支持的实际 prefill token。重叠丢失区间
和重复执行区间分别求并集；缺失观测或多身份保持未知。完成版先验证
fixed cohort 的完整测量工作、fresh drain 和实际 owner 退出，再将原始
归因总量与最终原生 Host-block counter 核对。运行中前缀只验证实际活体
owner，分别封存 JSONL 的完整行前缀，不将可增长 counter 作为其分母。

218 项相关 CPU 用例实际通过，无失败、错误或跳过。报告为
`experiments/reports/terminal_service_integration_20261117/host_block_prefill_cpu_r3.xml`，
包含真实 `NativeReactiveTelemetry` 产生的部分命中记录与实际 prefill
几何检查；此前缺少原生 handle attempt 身份的失败 fixture 继续保留。
这里只验证观测与分析，不代表新的 GPU 调度配置已通过资格检查。

## 已完成的 Host400 Full 与 Oracle

复用原 fixed18 cohort 和原注册，完成模式均通过。两组测量工作仍为
2991 个请求、362157 个输出 token，没有重新筛选完成样本。

| 实际 Host 丢失前缀 prefill | Full | Oracle |
| --- | ---: | ---: |
| 全运行正丢失请求数 | 31 | 35 |
| 全运行支持的实际 prefill token | 51364 | 70746 |
| 测量组正丢失请求数 | 0 | 1 |
| 测量组支持的实际 prefill token | 0 | 6052 |
| warmup 支持的实际 prefill token | 11063 | 15881 |
| background 支持的实际 prefill token | 40301 | 48813 |

所有正丢失区间均观察到实际输入执行，没有未知身份或重复区间。
Oracle 的测量组记录属于 productivity workflow 的一个 first-call
请求；这条 workflow 的完整 JCT 仍改善约 41.05 秒。
因此，目前没有证据将 Oracle 的平均 JCT 回退主要归因于测量组重算。
全运行背景工作有不同的右删失边界，不能以累计差值替代匹配测量结果。

完成版结果位于同一 CPU 证据目录：

- `full_host_recompute_complete_r1.json`；
- `oracle_host_recompute_complete_r1.json`。

每份结果保留原注册、cohort、实际测量执行证明、分析源码摘要、
原始 JSONL 的完整前缀摘要和逐请求结果。它们也补充说明：
此前 same-session previous-input-prefix proxy 为零，不等于全部
Host-block 重算为零。

## 运行中的 D3 对照前缀

原计划为 133 个原项目、Host200、initial20/mean55、fixed18，
与上节 Host400/mean60 Oracle 不是匹配对照。
2026-11-17 10:41:37 UTC 的独立前缀得到：

| 归属 | 正丢失请求数 | 支持的实际 prefill token |
| --- | ---: | ---: |
| measured | 25 | 1272664 |
| warmup | 37 | 2199020 |
| background | 44 | 1116803 |
| 合计 | 106 | 4588487 |

测量组中，14 个 continuation 请求、9 条 workflow 重算 1213109 token；
11 个 first-call 请求、6 条 workflow 重算 59555 token。两类 workflow
人口可能重叠，不能相加。first-call 只表示原 source 的首 context epoch，
不能自动等同于 D3 限制的真实 parented child 首次物化。

106 个请求的全部丢失区间都进入实际 prefill 输入，没有不支持身份、
重叠丢失或重复输入范围。涉及 2310 个 prefill batch；这些 batch 的
完整 worker 区间之和约 559.87 秒，包含其他输入和批次执行，不能称为
可消除 GPU 时间、重算独占时间或 JCT 上界。

原始结果为：
`/home/longhao/experiment/.worktrees/beliefkv-subagent-materialization-pressure-20261116/experiments/reports/subagent_materialization_20261116/gpu_campaign_guarded_3600/control_host_recompute_live_r2.json`。
该前缀的 attribution/runtime 完整行字节数分别为 112278286/930037732；
SHA-256 和活体 owner 身份保留在结果中。之前误将 campaign plan
传为 cohort 的 CLI 失败也保留，未产生性能结论。

这证明当前 D3 对照存在实际有用前缀丢失和额外输入执行，尚不证明
KV 背压可以改善它。待 control、pressure 各自真实退出后，按同一 fixed18
重跑完成版审计，同时比较完整 JCT、共同窗口 decode 吞吐、ready 供给
和控制开销，不能只比较 Host 逐出量。

## Oracle 后续修改边界

当前 Oracle 平均 JCT 1276.770573 秒，对照 1274.833468 秒；
共同窗口输出吞吐下降约 2.66%，平均 decode batch 从 15.514 降到
14.996。共同窗口工具替换检查线程 CPU 从约 24.833 秒增加到
43.345 秒，其中额外工具边界 plan 约 5.565 秒，属于嵌套计时，
不得与总检查成本重复相加或直接折算 GPU 等待。

因此沿用 `ff7eae70`：后续默认关闭额外工具边界抢占，普通预测式
工具卸载保留；在原普通路径中，先过滤不可能的逻辑配对和不合格
donor，再支付工作量排序成本，动作前仍重新验证原生权限。
`20e6332d` 仅修正 JOIN choice 记录实际 grant key 的观测身份。
不增加连续老化、集合效用、覆盖评分、容量积累或新的迁移阈值。

历史 83 项目与当前 133 项目仍有负载和必要修复差异，本轮 Full 对照也
同时缺少 Oracle 排序与额外抢占；当前结果支持停用额外抢占，
并不是一个严格隔离该机制的因果消融。先完成既有 D3 四组，
再按统一分支与匹配配置验证后续候选。
