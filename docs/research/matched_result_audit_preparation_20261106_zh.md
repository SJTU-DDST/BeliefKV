# 持续到达对照的结果审计准备

2026-11-06，America/Los_Angeles。GPU 暂停继续有效。本轮使用 CPU 检查已有
73 项目配对和已准备的 83 项目配置，补齐结果读器的比较范围；没有启动或
注册 GPU 任务，也没有修改运行时、原生引擎或实验输入。

## 查出的缺口

`review_matched_work_ranking.py` 原先比较两份配置的 workload、工具、到达、
排序 artifact 和资源设置，并逐执行验证完整测量工作。它没有像
ThunderAgent 结果读器一样，直接交叉比较两份执行 registration 中记录的
流量和服务资源。

配置比较还漏掉了 `private_analysis_cohort`。实际 cohort 报告仍有独立成员
核对，因此这一遗漏本身不能证明已有 JCT 使用了不同成员；但准备声明中的
私有 cohort 路径、哈希或投递标志不同，原配置比较器仍会接受。
新增三项回归先复现了这一行为，原失败 XML 保留。

## 修改

从已有 ThunderAgent 对照读器提取 `matching_serving_traffic`，两类对照共用：

- 原始 token 来源、workflow 人口、模型请求计划、工具 overlay 和到达表一致。
- 采样、running 上限、超时、响应测量和原生执行配置一致。
- source capacity、预期 replay capacity 和显式 Host 容量实验一致。
- Host NUMA 分配策略一致；不比较观察到的页面数量或分布比例。

排序结果入口在计算收益前执行这项比较。原来的完整回执、fresh drain、
实际进程退出和原生执行验证仍按各自职责执行。配置层的纯排序与组合对照
均增加私有 cohort 声明比较。

结果增加 `matched_actual_traffic_and_serving_conditions_verified`，并绑定共享
比较器所在文件的哈希。该字段表示两次执行记录匹配，实际资源仍由已有的
完成执行验证器核对；它不表示本轮新分配或重新测量了 Host/GPU。

## CPU 证据

相关 116 项测试与 11 个子测试通过，无失败、错误或跳过。覆盖范围包括：

- 三种私有测量声明差异被拒绝。
- 排序结果入口拒绝 17 种实际流量、执行或容量差异，且不发布收益结果。
- 原 ThunderAgent 配对规则保持有效。
- 相同 NUMA 策略下，不同实测页面分布可以通过；不同池容量被拒绝。
- 原固定测量组、背景右删失、完整 JCT 和共同吞吐窗口检查继续通过。

CPU 使用显式隐藏 CUDA、CPU0、nice19 和单线程计算库。服务冻结快照仍为
`631911fb`，保持干净；其此前 8188 项与 23 个子测试的完整证据原样保留。
本次没有为分析读器改动重跑整套服务回归或 GPU 资格测试。

## 已有配对与新配置

对原 73 项目完整配对，新增交叉比较通过；原配置继续使用历史读器已经允许的
显式 legacy 声明兼容，不改写旧配置。随后从原 cohort 报告重新计算：

- 完整平均 JCT 下降仍为 **1.895652494685529%**。
- 共同窗口输出吞吐变化仍为 **-0.554585%**。
- 所有分组、原阈值和检查结果与 `comparison_retry2.json` 完全一致。

这里重算的是已有封闭报告和 registration 元数据；没有重新执行原始轨迹的
完整资格审计，也没有产生新的性能样本或重复性证明。此次修复不改变旧结论。

新 83 项目的 Full/Oracle 配置也通过了补充私有测量声明后的比较。
20 初始、平均 60 秒到达、400 GB Host 配置和固定 18 成员保持原样；
实际竞争、迁移、JCT 与吞吐仍需 GPU 恢复后验证。

## 产物

证据继续放在原候选的具名子目录，不增加主实验版本：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  host400_registered_copy_pressure_r1/capacity_review_correction_r1/
    arrival_pressure_followup_r1/background83_mean60/
      result_audit_preparation_r1/
        inspect_inputs.py
        cohort_gap_before_fix.xml
        related_cpu.xml
        input_and_metric_review.json
```

`input_and_metric_review.json` 绑定分析源码、CPU 报告、原配对 registration、
配置、cohort、旧结果和新候选配置的 SHA256。原始失败 XML 保留；没有覆写
旧性能报告或 GPU 暂停状态。当前 goal 仍未完成。
