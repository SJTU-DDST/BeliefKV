# 2026-11-22 旧实验数据清理

本轮将 **30 轮旧实验的 119 个大型日志文件**无损归档。
原文件占用 **95.79 GB**，保留归档及恢复索引约 **4.93 GB**，
净回收 **90.86 GB（约 84.62 GiB）**。

2026-11-22 07:44（服务器本地时间，UTC-08:00）的最终观测：
磁盘可用空间约 **260.40 GB（243 GiB）**，使用率 **93%**。
归档清单冻结时可用空间为 169.64 GB、使用率 96%。

| 目录 | 清理前 | 清理后 |
| --- | ---: | ---: |
| `experiments` | 520.68 GB | 430.03 GB |
| `experiments/raw` | 436.03 GB | 340.44 GB |
| `experiments/archive` | 32.62 GB | 37.55 GB |

目录有包含关系，不能相加。净回收量按选中文件原有分配空间减去所有
新增归档文件和目录的分配空间计算；目录及文件系统的前后差值还包含
并行实验及其他用户的写入。

## 清理范围

选择目录日期早于 11 月 20 日、且文件最后修改时间早于
`2026-11-20T00:00:00-08:00` 的闲置实验，只处理直接位于 `server`
和 `opportunities` 下的大日志。实际归档来自 11 月 13 日至 19 日的运行。

| 日志类型 | 文件数 |
| --- | ---: |
| `runtime_audit.jsonl` | 30 |
| `admission_opportunities.jsonl` | 29 |
| `eviction_attribution.jsonl` | 29 |
| `transfer_telemetry.jsonl` | 28 |
| `host_pool_telemetry.jsonl` | 2 |
| `runtime_events.sglang.jsonl` | 1 |

检查活动进程、打开文件、运行中容器挂载、已登记输入以及代码和文档的
明确文件引用后才归档。当前 ThunderAgent 运行、待执行任务及其输入输出
保留；源轨迹、请求 token 包、workflow 事件、实验结果、配置、项目工作区、
模型、环境和先前归档均保留。

一轮旧 Qwen3.5 运行缺少完整的原始 server/launch 身份元数据，因此
没有纳入本轮清理。未通过筛选的文件没有被删除。

## 校验与恢复

两个互不重叠的批次使用 nice19、idle I/O，每个归档单线程 zstd。
每轮压缩后先将全部归档内容与原文件逐字节比较，再次确认文件和
保护条件未变化，之后才移除原始日志副本。

独立核验检查全部 30 个归档的 SHA256、成员列表、原始元数据哈希、
原副本移除状态和恢复标记。两个批次各选择一轮实际解压，恢复的
**6 个日志文件**均再次通过完整 tar 比较，并记录恢复文件的 SHA256；
临时恢复目录已清除。

每个受影响运行目录下都有 `OLD_LOGS_ARCHIVED_20261122.md`，
包含对应归档哈希及恢复到原路径的完整命令：

- [第一批恢复索引](../../experiments/archive/20261122_historical_logs/batch_0/README.md)
- [第二批恢复索引](../../experiments/archive/20261122_historical_logs/batch_1/README.md)
- [维护清单与操作入口](../../experiments/reports/storage_cleanup_20261122/README.md)
- [最终空间统计和恢复证据](../../experiments/reports/storage_cleanup_20261122/final_storage_review.json)

大清单和执行回执留在本地维护目录；Git 只提交维护工具、紧凑核验及本说明。
本轮没有启动 GPU 实验、停止现有任务、改动实验策略或处理其他用户数据。
