# 公开工具时长驱动的 CPU 回放准备

## 当前结论与边界

用户允许在自然轨迹难以体现收益时，从公开记录获得工具执行时间分布，
合成工具计时。用户随后要求 V260 结束后暂停全部 GPU 实验，直至明确
通知恢复。本次只下载公开数据、准备计时表、执行 CPU 和 mock HTTP 检查；
没有注册 GPU 实验、启动等待器或撤销暂停。

V260 的计费修补不足以满足整体验收。其平均 JCT 为 1395.545895 秒，
全程吞吐 284.644416 token/s；相对同工作量 V247 ThunderAgent，JCT
慢 5.7066%，吞吐高 9.5662%。保留完整负结果与正常尾部。

完整迁移 CPU 审计
`experiments/reports/v263_v260_restore_churn_complete_20261027.json`
核对了实际 ACK 与请求 Host hit：

| 十进制 GB | V247 Full | V260 Full | V247 ThunderAgent |
| --- | ---: | ---: | ---: |
| 全部 H2D | 733.582 | 677.398 | 242.572 |
| native demand H2D | 606.647 | 549.812 | 242.572 |
| tagged 恢复 H2D | 126.935 | 127.586 | 0 |

V260 的 16 个不超过 200ms 的工具后继关联 152.164 GB 需求 Host hit，
而全部工具后继为 153.373 GB。关联不是单个 DMA 的归因，也不能单凭
这些字节判断某次逻辑归还造成驱逐。现有数据仍支持检查资格、连续性及
恢复代价，不能认为剩余问题已经全部变成带宽瓶颈。

## 公开来源与计时语义

数据为 University of Washington SyFI Lab 的 TraceLab，发布
`v0.0.1`，发布时间 2026-06-22。下载资产是
`syfi_coding_trace.jsonl.gz`，SHA-256：

```text
9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b
```

官方发布的 digest 与本机文件一致，原始压缩包保存在
`/home/longhao/experiment/ops/public_tool_traces/tracelab_v0_0_1/`。
数据许可证为 CC BY 4.0，配置内保留作者归属、网站、发布 URL 与
许可证。没有复制原始 prompt、工具输出或尝试恢复贡献者身份。

主要依据：

- 项目：`https://github.com/uw-syfi/TraceLab`
- 发布：`https://github.com/uw-syfi/TraceLab/releases/tag/v0.0.1`
- 格式核对固定于 commit
  `5c6550b0d08beff983e9159fe2575e7a2334c8c1` 的 README 和 extractor。
- 许可证：同 commit 的 `LICENSE-DATASET.md`。

`tool_wall_latency_ms` 是发出工具到记录结果的间隔，可能包含审批、
调度或外层等待。此次不使用它补齐样本。只采用工具/runner 报告的
`tool_internal_latency_ms`，不把任意相邻 trace 时间差当作工具执行。
`exec_command` 与 `write_stdin` 的单次响应时间也不能直接等同于整条
异步命令的生命周期，因此 shell 档位使用同步的 `shell_command`。

按 provider、session、tool_call_id 去重；重复身份如果计时冲突就拒绝。
保留零值和错误调用，报告内部计时缺失数量，不按时长选择“长样本”，
不乘放大系数、不裁剪尾部。

## 两个预先声明的档位

| 项目 | Shell 档位 | 网络工具档位 |
| --- | ---: | ---: |
| 公开工具类型 | Codex `shell_command` | Claude `WebFetch`、`WebSearch` |
| 符合类型的原始记录 | 7624 | 3260 |
| 缺少内部计时 | 21 | 2944 |
| 去重后计时池 | 7603 | 313 |
| 计时池均值 | 2.243s | 10.114s |
| 计时池 P50 | 报告为 0s | 6.167s |
| 计时池 P99 | 54.300s | 51.345s |
| 计时池最大值 | 2438.600s | 61.752s |

Shell 零值很多，不能据此认定真实工具完全没有执行成本，也不应私自
为零值补一个固定延迟。网络档位只有约 9.6% 的原始记录具有去重后的
内部计时，不能宣称它代表全部公开网络调用。它是有明确来源和覆盖限制
的敏感性实验。

两个档位在进行任何新 GPU 比较前声明并保留。选择网络工具档位研究
工具阻塞较多的条件，不依据 Full 是否获胜筛选公开样本或 workflow。
把该分布应用到 LoCoBench `execute` 明确属于网络工具时长合成，
不声称这些 shell 命令在真实项目中执行了相应时间。

本机 V230 中共有 3474 次工具调用；只有 931 次 `execute` 被替换。
文件读写、目录查询、todos 和完成意图通知均保留原计时：

| 931 次 execute 的计时 | 原自然轨迹 | Shell 合成 | 网络工具合成 |
| --- | ---: | ---: | ---: |
| 均值 | 0.636s | 2.202s | 9.776s |
| P50 | 0.129s | 报告为 0s | 6.167s |
| P90 | 2.064s | 0.800s | 25.901s |
| P99 | 2.620s | 69.100s | 49.336s |
| 超过 1s | 133 | 54 | 867 |
| 超过 10s | 1 | 19 | 245 |
| 最大值 | 180.081s | 300.000s | 61.752s |

原先那次 180 秒 execute 也按相同身份映射规则替换；这是整个目标工具
集合的计时干预，不是单独删除一个不利尾部。原自然源与全部 root、
请求、token 和工作流尾部仍保留。

## 回放接入与公平性

`scripts/prepare_empirical_tool_timing.py` 校验原始数据 digest，离线生成
私有表。取样以 seed21、workflow、invocation、tool_run_id 的 SHA-256
确定，与候选方法的完成顺序、并发顺序及随机数消费顺序无关。
公开池排序固定，实际比较固定同一张表的文件 digest。

`beliefkv/experiments/tool_timing_overlay.py` 只修改每个目标 TOOL_END
节点相对其实际 TOOL_START 的延迟。原始事件、请求、生成/停止参数、
上下文生命周期、所有 JOIN 和依赖边均不变。并行工具分别从自己的
开始事件计时；后继保留原来的所有依赖，不累加本应并行的工具等待。
没有新增逐 token 逻辑、控制器扫描或 GPU 数据面权限。

驱动的 `--tool-duration-overlay` 与比较注册器使用同一接口。注册器把
计时文件纳入不可变输入；结果必须携带完全一致的计时证据。旧自然结果、
另一张取样表或漏报 synthetic 标签都无法通过新结果核对。相同 token
集合并不足以证明相同计时实验。跨 registration 复用也比较该证据。

采样后何时结束只保存在私有 DAG。TOOL_START 不新增 duration、sample_id
或结束时刻；TOOL_END 仍报告已经实际经过的时间。现有在线模型只能使用
已经发生的开始、返回与历史观察，不能从公开池中读取当前调用的取样值。

自然 native 的时间不是合成档位的性能基线。注册器移除该自然 baseline，
拒绝用自然源速度门槛校准合成回放，不生成虚假的 native 加速百分比。
原请求停止条件和完整内容仍然验证；`benchmark_content_synthetic`
为 false，单独标记 `benchmark_timing_synthetic`。

## CPU 证据与尚未验证项

最终准备文件：

- `experiments/reports/v268_v230_empirical_shell_timing_cpu_20261027.json`
- `experiments/reports/v269_v230_empirical_network_timing_cpu_20261027.json`

两张表都绑定完整 V230 的 18640 个节点、25635 条依赖边、18 个不同
项目的 workflow、72 次 JOIN、3674 个请求、145565966 输入 token 与
549671 输出 token。完整 CPU DAG 以零模型服务时间作一致性检查；
原图和变体均完成全部节点。这个夹具没有 GPU、HBM 或真实服务竞争，
其时间不用于预测性能或迁移机会。

`v264`、`v266` 是保留的早期准备快照，`v265`、`v267`、`v270` 是保留的
阶段 CPU 报告；不改写旧文件。最终
`v271_empirical_tool_timing_final_cpu_20261027.xml` 通过 433 项，
没有失败、错误或跳过；`v272_empirical_tool_timing_cpu_evidence_20261027.json`
绑定代码、测试、配置和两张表的实际 digest。相关测试覆盖公共样本缺失/冲突、
稳定取样、完整目标覆盖、私有未来信息、并行工具、mock HTTP 的准确
token/停止/会话收尾、自然基线隔离和现有因果回放。它不是此前 2249 项
共享回归的新替代，也不自动赋予新 launcher GPU 资格。

下一次 GPU 比较仍需用户明确通知服务器空闲。恢复后保持 Full 先运行、
审计正常后再运行 ThunderAgent 的顺序，使用共同计时与容量。公开
ThunderAgent 还有 `--use-acting-token-decay`：resume 用 acting token
的 `2^-t` 容量，暂停检查保留原容量；目前论文投影不含该可选模式。
必须另行补齐、标注并验证这条长工具策略，不能通过忽略它来声称优于
ThunderAgent 的长工具设计。

实际运行还要检查真实冷页、逻辑资格和物理迁移是否一起发生，以及是否
只增加了 GPU 空闲或工具长尾。只有完整 JCT、全程吞吐、恢复等待、重复
迁移和控制成本证据共同支持，才能接受收益；目前没有新 GPU 性能结论。
