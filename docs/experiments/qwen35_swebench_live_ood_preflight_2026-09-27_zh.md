# Qwen3.5 新项目 OOD 验证来源冻结与 CPU 预检（2026-09-27）

## 数据来源与隔离

旧 SWE-bench Verified 冻结 split 的 12 个项目已用于训练、
开发、校准或密封测试，不能重新作为完全未见项目。
新来源选择公开的 SWE-bench-Live Python `lite` split，
固定 dataset revision
`b51a86422e10cfd403beb4773e5a2947953e36ec`。
下载的 `lite-00000-of-00001.parquet` SHA-256 为
`7ee0a75c41bfc954fd441b67ce738fc5c1cbae00721c4e30e7db4d893057c9ab`。

`scripts/freeze_swebench_live_holdout.py` 严格只投影
`repo`、`instance_id`、`base_commit`、`problem_statement`
四列；不加载解答 patch、测试 patch、hint 或以解答统计
出来的难度标签。按预先指定的仓库和实例 ID 字典序，
从 cfn-lint、Haystack、Reflex 各取 8 个任务，冻结
24-root 目标集合。pvlib 的首个任务只用于环境 pilot，
不用于目标精度分母。四个项目均未出现在旧 Verified split；
目标 manifest 与 pilot manifest 相互隔离。

冻结产物位于
`configs/migration/qwen35_swebench_live_lite_ood_2026-09-27/`：

| 产物 | SHA-256 |
| --- | --- |
| `holdout.json` | `75cf3add7f4b02626587662ad108841bfee7e219d87ed26fa14bbc4c3474d6dd` |
| `pilot.json` | `6423c5584d578d1d3ba36b2a20849ca10088ce6ce2a55e772fcbade2428b9ce4` |
| `provenance.json` | `5383cff43958a575f401a235c75daea66d327713f378cda7c40980df5dcc78aa` |

## 已验证与未验证

- 24/24 个目标镜像的 registry manifest 可查询；**没有**
  拉取目标镜像、检验各 repo base commit 的文件对象或
  执行其中任何目标任务。
- pvlib pilot 的 source repo 已在本地完整取得目标 commit
  对象，`verify_workload_source_objects` 通过；其镜像已
  拉取，实际 `DockerWorkspaceBackend` 在独立 checkout
  上通过启动、`/workspace`、Python 和关闭预检，
  `pvlib` 与 `pytest` 可导入。这不等于完整 agent
  workflow、动态 JOIN 或任务正确性通过。
- Live pilot 镜像的 Python 为 `/usr/local/bin/python`，
  与原 Verified 镜像的 `/opt/miniconda3/envs/testbed/bin/python`
  不同；runner 已支持 `--sandbox-test-env /usr/local`，
  不必更改旧工作负载的默认值。真正启动前还须按仓库检查
  其它目标镜像的 Python/依赖合同。
- 本机空余磁盘约 91 GB，已结束密封运行中的 218 个
  workspace 有 100 个含修改或未跟踪内容，不能全量清理。
  对其余 118 个干净 checkout 的删除命令被执行环境拒绝；
  **目录均未删除**。在校验真实空间预算及保存诊断产物
  之前，不拉取全部镜像或启动新目标批次。

当前状态为**冻结验证来源，非可运行批次，更非模型胜利**。
先在训练项目上冻结新时间预测方法和全分母评分，
再以 pvlib 做完整 runtime/沙箱 pilot；之后才允许
针对这 24 个未见目标项目采集同配置压力批次。
正式报告须同时给出工具时间的全体/长窗口覆盖与
逐项目误差、完整 JOIN 集合时钟与动作效用，按任务
聚类做配对检验；如发现目标环境不可用，应记录
失败并预先冻结新的来源，而非查看目标标签后换题。
