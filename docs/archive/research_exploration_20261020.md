# 方向探索暂停与源码清理

整理日期：2026-10-20（北京时间）。

按用户要求暂停研究方向探索，将未提交的有效修正和研究结论整理到 main，
移除 scripts/tests 中没有生产或启动入口引用的一次性研究文件。
整理开始时已在 main，HEAD 为 `4ef9e2e`；拉取 origin 后两侧一致，
没有需要合并的当前分支，也没有合并冲突。

## 保留内容

- [研究记录](../research/non_scheduling_directions_20261016_zh.md)保留候选筛选、
  否定证据、文献与观测边界，作为历史记录，不自动恢复探索或实验。
- `audit_agent_work_redundancy.py` 保留输入指纹重复审计及压缩阶段分账；
  `audit_native_hybrid_reuse.py` 保留通过 JOIN waiter 补齐完成事件归属的修正。
  两者的对应测试一并保留。
- 四份核心文档移除重复的研究摘要和已过时的执行指令，合计 2,470 行；
  详细结论集中在研究记录，当前运行合同以 main 的最新状态为准。
- 原始实验数据、冻结检出、运行时代码、已有分支及 LoCoBench workload
  文件保留。生成的 LoCoBench root-scoped 目录加入 `.gitignore`，
  当前 45 份 reference/provenance JSON 和 9 份源码压缩包留在本地，
  不再作为待提交文件出现。本次不启动实验、不删除 worktree、不推送远端。

## 已移除文件

删除 61 个未提交的一次性审计/候选筛选脚本和 50 个配套测试，共 111 个
文件、975,469 字节。删除前检查了 Python 导入与生产/启动入口的路径引用，
没有外部引用；这些文件之间的研究依赖不构成继续留在工作目录的理由。

删除前已对全部 111 个文件、两个保留审计器及其测试、五份文档做源码快照，
共 120 个成员。压缩包逐成员核对大小及 SHA256 后才清理工作目录。

- [源码压缩包](../../experiments/archive/research_exploration_20261020/source_snapshot.tar.gz)
- [文件清单与逐文件 SHA256](../../experiments/archive/research_exploration_20261020/manifest.json)
- [文档收拢记录](../../experiments/archive/research_exploration_20261020/consolidation.json)

压缩包大小：1,408,777 字节。

SHA256：

```text
b7f6a57e04c998517207aaa02c631aa11c62c21c44f7ed535bc62f366576180a
```

压缩包沿用 `experiments/archive/` 的本地数据归档规则，不纳入 Git。
研究记录中提及的已删除脚本名称是当时的来源身份，对应源码可从清单查找；
这些历史名称不是当前可运行的命令。

复核历史结果时，可只取所需成员，在独立目录中恢复其依赖；不要将整份
快照覆盖回 main，否则会重新引入本次删除的脚本及旧版文档。已有冻结源码、
报告内的审计器快照和原始输入仍分别保留，不将它们当作当前默认实现。

另清理上述文件遗留的 91 个字节码缓存。被中断的 V1646 资料筛查尚未形成
研究结果，其 15 份下载资料单独压缩并校验后移除原目录：
[资料压缩包](../../experiments/archive/research_exploration_20261020/unfinished_source_downloads.tar.gz)、
[清理清单](../../experiments/archive/research_exploration_20261020/temporary_cleanup.json)。
这部分是下载缓存，不涉及实验 trace 或已完成报告的输入。

## 核查结果

保留的两个审计器及对应测试共 32 项通过。974 个现有跟踪 Python 文件
完成语法和静态导入核对，没有指向本次删除模块的导入。压缩包 120 个成员
逐项校验通过，归档链接可访问；本次只在 CPU 执行核查。
