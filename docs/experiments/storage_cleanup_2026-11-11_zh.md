# 2026-11-11 旧实验存储清理

按用户要求清理已不用的旧实验文件。采用已有的归档和工作树回收流程，
不删除唯一的实验结论、任务轨迹或用户修改。

## 已完成

| 项目 | 范围 | 净释放 |
| --- | --- | ---: |
| 旧服务日志 | 12 组早期 Qwen3.5 实验，166 份服务、传输、机会及镜像日志 | 83.66 GB |
| 过期工作树 | 17 个无当前引用、无修改、无运行进程的 detached checkout | 17.26 GB |
| 合计 | 保留可恢复归档及原始 Git 提交 | 100.92 GB |

精确核验值为 100,923,748,352 字节，约 94.0 GiB。磁盘可用空间从约
114 GiB 增至 208 GiB，使用率从 97% 降至 94%。这是全盘观察值；其他
进程仍可写入，因此不把 `df` 的差值当作本次操作的独占回收量。

日志先压缩，经过完整 GNU tar 逐字节比对后才移除原件；随后重新核对
全部压缩包 SHA256、成员清单、原实验计划及恢复指针。包含 V29/V58 的
历史 native 和 predictive 服务日志；需要重跑这些旧日志分析时先恢复。
任务事件、请求 token、结果、失败记录、工作目录及报告继续直接保留。

工作树只选择 2026-11-08 前创建的旧副本，继续保留原有的更早引用扫描
范围，以保护祖先 source/input。检查覆盖 4,953 个引用文件；跳过
275 个有引用副本、3 个有修改或用户文件的副本，以及 2 个包含非私有或
非普通 ignored 数据的副本。原 HEAD 与 reflog 提交固定在
`refs/archive/20261111-worktrees/`，ignored 文件和 Git 管理资料经过
归档及完整比对。实际重建代表 checkout 通过后，使用不带 force 的
`git worktree remove` 回收；事后再次核对提交、Git tree、归档和注册表。

## 保留与复核

当前 83 项目、400 GB Host、平均 60 秒到达的待跑实验保持完整。
清理结束后，执行计划绑定的 23 个输入文件，以及冻结执行源码清单中的
2,673 个文件，均重新通过 SHA256 检查。模型、当前 trace、预测器、原生
引擎、当前及待跑实验的冻结源码保持原状。现有协调器继续存活，本次
维护没有启动 GPU 实验或停止其他用户的进程。

维护使用 nice19、idle I/O 和单线程压缩。未改动现有未提交开发文件。

## 恢复入口

- [旧日志恢复索引](../../experiments/archive/20261111_unused_logs/README.md)
- [工作树恢复索引](../../experiments/archive/20261111_retired_worktrees/README.md)
- [日志校验结果](../../experiments/reports/storage_cleanup_20261111/verification.json)
- [工作树校验结果](../../experiments/reports/storage_cleanup_20261111/worktrees/verification.json)

完整清单和执行过程保存在
`experiments/reports/storage_cleanup_20261111/`，日志归档及工作树管理资料
分别保存在对应的 `experiments/archive/20261111_*` 目录。
