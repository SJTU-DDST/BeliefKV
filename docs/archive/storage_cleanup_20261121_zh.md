# 2026-11-21 旧实验文件与 checkout 清理

本轮净回收约 **211.66 GB**，所有批次已通过独立核验。回收量按原始分配
空间减去保留的压缩归档计算；磁盘和目录的前后差值还包含并行实验及其他
用户的写入。

北京时间 2026-11-21 00:30 的最终观测为：磁盘可用空间从
**111.45 GB 增至 322.27 GB（约 301 GiB）**，使用率从 **97% 降至 92%**。

| 目录 | 清理前 | 清理后 |
| --- | ---: | ---: |
| `/home/longhao` | 952.02 GB | 741.16 GB |
| `experiment` | 912.47 GB | 701.61 GB |
| `experiment/.worktrees` | 319.73 GB | 271.05 GB |
| `experiment/BeliefKV` | 547.97 GB | 425.48 GB |
| 其中 `experiments/raw` | 477.15 GB | 349.07 GB |

这些目录存在包含关系，不能相加。`experiments/archive` 清理后约
16.05 GB，`experiments/reports` 约 29.24 GB。

## 清理对象

| 对象 | 数量 | 净回收 |
| --- | ---: | ---: |
| `.worktrees` 中的旧 detached checkout | 28 个 | 33.92 GB |
| 闲置的旧分支 checkout | 34 个 | 14.57 GB |
| `experiment` 根目录下的旧 detached checkout | 36 个 | 39.60 GB |
| 使用根目录元数据的旧实验日志 | 51 轮、101 个文件 | 54.59 GB |
| 使用 stage 元数据的旧实验日志 | 55 轮、120 个文件 | 68.97 GB |
| 合计 | 98 个 checkout；106 轮、221 个日志 | 211.66 GB |

checkout 选择早于 11 月 18 日创建的副本，排除当前引用、活动进程、
容器挂载、锁和本地修改。34 个原分支在移除 checkout 后仍指向原提交，
没有删除分支。原 HEAD 和 reflog 提交另外固定在归档引用下，避免被 Git
垃圾回收；额外文件、符号链接和原 Git 管理信息分别压缩保存。

日志选择早于 11 月 14 日的实验。第二批补充支持原有 stage 目录结构：
要求既有 `server_info.json` 和 `launch_configuration.json`，并保存
stage 中小型 JSON 元数据的 SHA256。没有因缺少根目录结果文件而丢弃
已有的有效元数据，也没有生成或修复实验结果。

每个日志都通过完整 tar 内容比较后才移除原副本。独立核验进一步检查
归档 SHA256、成员列表、原始元数据和恢复标记。请求 token、workflow
事件、源轨迹、结果和配置保留；缺少原始 server/launch 身份的旧运行
没有纳入归档。

## 恢复验证

实际重建了一个完整旧 checkout，还原并比较其中 **867 个额外文件和
4 个符号链接**。恢复后 Git 工作区干净，临时恢复目录通过普通
`git worktree remove` 移除，没有使用强制删除。

所有 checkout 和日志都带有逐项恢复命令：

- [旧 detached checkout](../../experiments/archive/20261121_retired_worktrees/README.md)
- [旧分支 checkout](../../experiments/archive/20261121_retired_branch_checkouts/README.md)
- [根目录旧 checkout](../../experiments/archive/20261121_retired_direct_checkouts/README.md)
- [第一批旧日志](../../experiments/archive/20261121_historical_logs/README.md)
- [stage 结构旧日志](../../experiments/archive/20261121_historical_stage_logs/README.md)

## 执行边界

清理期间有 Oracle 实验正在运行，其工作树、输入和输出目录均保留。
维护使用 nice19、idle I/O 和每个归档一个 zstd 压缩线程；没有启动 GPU
实验、停止任务、修改服务策略或清理其他用户的数据。

较大的选择清单和执行回执保存在本地维护目录，Git 只跟踪维护脚本、
紧凑的独立核验和最终磁盘观测，避免再把大清单复制进后续 checkout。

- [维护索引](../../experiments/reports/storage_cleanup_20261121/README.md)
- [最终磁盘统计与证据哈希](../../experiments/reports/storage_cleanup_20261121/final_storage_review.json)
