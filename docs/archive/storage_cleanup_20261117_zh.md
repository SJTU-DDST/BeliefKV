# 2026-11-17 实验文件清理与磁盘占用

本轮净回收约 253.45 GB。清理完成后的磁盘可用空间为 360.51 GB
（约 336 GiB），使用率从 97% 降至 90%。统计时间为北京时间
2026-11-17 15:31–15:33；并发写入会使目录差值与独立回收量略有不同。

## 清理结果

| 对象 | 数量 | 原始占用 | 净回收 |
| --- | ---: | ---: | ---: |
| 旧 detached worktree | 201 个 | 228.47 GB | 227.97 GB |
| 10 月旧实验服务器和调度日志 | 8 轮、26 个文件 | 26.14 GB | 25.48 GB |
| 合计 | | 254.61 GB | 253.45 GB |

worktree 仅选择 11 月 10 日之前创建、无本地改动且未被当前任务引用的
副本。近期副本、有分支或改动的目录、正在使用的目录及实验输入保留。
原提交和 HEAD reflog 提交固定在 `refs/archive/20261117-worktrees/`，
额外文件和原 Git 管理信息单独压缩保存；未使用强制删除。

独立核验已确认原 Git tree、归档 SHA256、成员列表和恢复入口。
另外实际重建了一个完整 checkout，还原并比较了其中 407 个额外文件，
包括一个符号链接，再正常移除临时恢复目录。

26 个旧日志都在完整 tar 内容比较通过后才移除原副本。请求 token、
workflow 事件、结果、配置和当前源轨迹保留。另有一轮旧实验因文件类型
检查未通过而保留。本轮没有启动 GPU 实验，也没有清理其他用户的数据。

## 各用户主目录

以下按 `/home/<用户名>` 的实际分配块统计，不跨文件系统、不跟随符号链接。
这不是按 UID 扫描全盘的配额，公用容器和模型目录不计入个人主目录。

| 用户 | 主目录占用 |
| --- | ---: |
| zhuoer | 1,322.33 GB |
| longhao | 706.22 GB |
| yunyi | 272.69 GB |
| guifeng | 200.04 GB |
| shushi | 37.88 GB |
| xfusion | 440.60 MB |
| yitian | 13.08 MB |
| ddst | 434.18 KB |
| xingda | 266.24 KB |
| dashboard | 53.25 KB |
| cong | 45.06 KB |

`/home` 合计约 2,539.61 GB。另有共享目录 `/var/lib` 约 345.21 GB、
`/opt/downloaded_models` 约 187.16 GB、`/srv/ai` 约 133.01 GB，
没有把这些空间归到某个个人用户。

## 剩余主要占用

| 目录 | 清理后占用 |
| --- | ---: |
| `experiment/.worktrees` | 203.12 GB |
| `experiment/BeliefKV` | 419.61 GB |
| 其中 `experiments/raw` | 349.46 GB |
| 其中 `experiments/reports` | 28.75 GB |
| 其中 `experiments/archive` | 10.39 GB |

后三项包含在 BeliefKV 总量内，不应重复相加。

## 恢复与证据

- [worktree 恢复索引](../../experiments/archive/20261117_retired_worktrees/README.md)
- [旧日志恢复索引](../../experiments/archive/20261117_historical_logs/README.md)
- [维护记录](../../experiments/reports/storage_cleanup_20261117/README.md)
- [清理后磁盘统计](../../experiments/reports/storage_cleanup_20261117/disk_usage_after.json)

详细选择清单、执行回执和逐批进度保存在同一维护目录。恢复命令会重建
原 worktree 路径并还原额外文件，或将对应日志解压回原实验目录。
