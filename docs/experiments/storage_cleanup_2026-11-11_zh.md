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

## 追加：worktree 与项目目录清理

用户随后明确要求继续清理 worktree 和项目文件夹。本批额外回收
39 个旧 checkout，其中 3 个位于 `experiment/` 同级目录，另外 36 个
位于 `.worktrees/`。范围仍限 2026-11-08 前创建、干净、detached、
未被进程或容器使用的副本。

本批区分历史文档提及和实际程序输入：历史文档中出现过的旧副本允许
保留恢复资料后回收；源码、测试、配置、近期操作元数据及输入的引用
继续保留原有保护范围。分支附着、有修改、近期创建、正在使用，以及
包含特殊或非私有 ignored 文件的 checkout 继续保留。一个旧 v132
副本因非私有或特殊 ignored 数据未通过检查，未清理。

| 项目 | 数量 | 本批净释放 |
| --- | ---: | ---: |
| 普通旧 checkout，含 3 个项目同级目录 | 29 | 9.363 GB |
| 带符号链接的旧 checkout | 10 | 7.195 GB |
| 主项目 Python 字节码缓存 | 2,482 份 | 77,938,688 字节 |
| 主项目 pytest 缓存文件 | 5 份 | 2,117,632 字节 |
| 本批合计 | 39 个 checkout 及可再生缓存 | 16.638 GB |

本批归档、提交引用、Git tree 和工作树注册表的复核均通过。
带链接副本额外实际恢复了一个完整 checkout、225 个 ignored 文件及
1 个符号链接；解压后完整 tar 比对通过，临时 checkout 已无 force 地
移除。外部引擎及数据链接目标保持原状。

清理后，待跑 mean60 实验的 23 个输入及 2,673 个冻结绑定文件再次通过
SHA256 检查。主项目已有的未提交源码、设计文档和实验实现继续保留。
观察到磁盘可用约 224 GiB；两批共净释放约 117.56 GB。

恢复与核验：

- [本批普通 checkout 恢复索引](../../experiments/archive/20261111_project_worktrees/README.md)
- [本批带链接 checkout 恢复索引](../../experiments/archive/20261111_project_linked_worktrees/README.md)
- [普通副本校验](../../experiments/reports/storage_cleanup_20261111/project_worktrees/verification.json)
- [带链接副本校验](../../experiments/reports/storage_cleanup_20261111/project_linked_worktrees/verification.json)
- [实际恢复检查](../../experiments/reports/storage_cleanup_20261111/project_linked_worktrees/ignored_restore_probe.json)
