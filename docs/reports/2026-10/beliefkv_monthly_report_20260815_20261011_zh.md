# BeliefKV 阶段进展汇报

**汇报周期：2026 年 8 月 15 日至 10 月 11 日。**

## 项目介绍

BeliefKV 研究单 GPU、HBM 容量受限环境中的动态多 Agent 调度与分层 KV 缓存管理。
Agent 会在运行时创建 subagent、执行外部工具并等待 JOIN，执行依赖和
下一次服务需求并非预先固定。系统从 SPAWN、TOOL、RETURN、JOIN
事件构建因果图，结合收尾通知、输出正文及已观测服务进度，估计
child 和工具的完成过程。

预测模型提供完成进度和剩余工作量提示；runtime 根据有效 checkpoint、
Host 副本、HBM 容量、传输成本和准入机会，决定增量备份、压力回收、
提前 H2D 和恢复后的优先执行。SGLang 继续负责物理页分配、缓存有效性
和实际数据依赖。项目目标是在有真实迁移机会的负载下，相对原生
SGLang 提高成功 workflow 吞吐并降低 JCT，同时控制重算和对其他
workflow 的干扰。

可用于口头汇报的一句话：

> 我们利用动态 Agent 工作流的因果关系和完成信号，提前准备下一次执行
> 所需的 KV，并让缓存恢复与 GPU 准入协同，减少恢复等待和无效迁移。

## 本阶段工作

| 时间 | 主要工作 | 得到的认识 |
| --- | --- | --- |
| 8 月 15–31 日 | 建立因果前沿、联合调度与 KV 替换；优化事件驱动 JointPlan；实现原生 subagent harness、parent 对话连续性与 JOIN 后前缀复用；探索 oracle 与需求回放。 | 动态工作流可以映射到物理缓存与请求身份；8 月 21 日三个完整 workflow 的 parent 前缀保留率为 100%。该轮没有 D2H/H2D，不构成迁移或吞吐证明。 |
| 9 月 1–21 日 | 接通预测性 D2H/H2D、工具等待备份、滚动恢复、传输 ACK、驻留租约和生命周期管理；修复物理恢复与原生驱逐的竞态。 | 9 月 20 日旧架构实验中 25 次预测 H2D 有 24 次物理完成、23 次被首次服务归因为有用；这是机制验证，没有配对吞吐结论。 |
| 9 月 22–27 日 | 迁移到 Qwen3.5 与 SGLang 混合 FULL/Mamba 缓存；完善 context/epoch 连续性和传输埋点；采集 reactive 训练 trace，拟合预测头并进行项目隔离校准。 | 校准只能检验预测误差与区间覆盖，不能证明调度收益。早期 RETURN/JOIN 墙钟回归混入排队和服务间隙，误差不足以支持精确预取。 |
| 9 月 28 日–10 月 4 日 | 转向通知与正文语义驱动：比较 token 内容、长度和 EOS；实现阶段分类、条件剩余生成工作量；分离服务进度与等待；探索轻量 CNN 和预训练文本表示。 | 最终报告阶段与下一轮工具调用必须分开判断；生成剩余工作比混合墙钟标签更明确，但未来无服务间隙仍需 runtime 处理。尚未证明一般性的亚秒级 RETURN 精度。 |
| 10 月 5–11 日 | 基于较早的 36-root 结果，比较 64/84/108-root 压力；增加 2–4 child 和多轮 SPAWN；引入原生 SGLang 对照与补充到达批次；实现 FULL 增量 PREPARE、必要 Mamba 恢复、缺失页批量与部分恢复、驻留保护、恢复感知准入、终态 child 容量交接；持续降低客户端和调度控制开销。 | 有效预取取决于有效 Host-only 前缀、可回收空间、及时提交和后续执行消费。ACK 数或传输字节不能代替收益；过多控制、重复恢复和恢复后排队会抵消传输重叠。 |

## 当前结果与判断

- **架构与数据链路已经贯通。**目前可以区分增量备份、实际 HBM 回收、
  完成边界前 H2D、需求 handoff、原生恢复，以及下一请求对 FULL/Mamba
  的实际复用。PREPARE 不等于卸载，H2D ACK 不等于有效消费。
- **预测与调度的职责已经明确。**模型预测阶段和完成进度；runtime
  判断物理可行性及执行机会。JOIN 按完整 child 集合处理，不把 parent
  首次 GPU 服务时刻当作 child RETURN。不能把未来等待直接放进输入。
- **端到端性能目标仍未达成。**最新已完成的 V22 同版本对照中，两侧
  均为 155 completed、1 incomplete；predictive 成功 workflow 吞吐
  比 native 低 17.56%，配对平均 JCT 高 17.82%。动态执行轨迹不同，
  不能把全部差距归因于单一策略，也不能宣称当前已有稳定性能优势。
- **较新的运行观测显示消费等待缩短，但不是最终结论。**截至
  10 月 11 日 14:41:13 CST 的 V23 逐文件审计，40 个 JOIN 和
  380 个工具等待在完成边界前启动 H2D 并由下一请求复用 FULL，
  合计约 0.515 GB 和 6.132 GB；另有 93.742 GB 的需求 handoff
  单列。最后 ACK 到首次服务的 P90 分别约 1.772 秒和 0.919 秒。
  这是运行中观测，尚无该轮完整同版本 native 对照。

当前实验使用 156 个任务、108+48 两波到达，第二波在 3600 秒加入，
服务端 running=48；Host 200 GB、FULL:Mamba=80:20；HBM 使用
Mamba/full 容量参数 0.9；工具上限 180 秒、workflow 上限 14400 秒。
这些配置用于制造持续迁移机会，不代表已找到最优 workload。

## 下一步重点

1. 提高完成边界前、下一请求确实复用的 FULL 覆盖，继续优化 child
   最终生成与工具残余时间预测，不用需求恢复冒充预测预取。
2. 让恢复计划、容量回收、驻留保护和下一批准入协调，减少 ACK 后
   等待、首次服务前再次加载，以及缺乏消费的备份。
3. 持续削减重复候选检查、前缀扫描、控制交付与诊断序列化成本，
   用同版本 native 的繁忙窗口吞吐、成功 workflow 吞吐和配对 JCT
   判断净收益。正式结果采用多轮平均，改进阶段先做快速验证。

## 三张配套图

PowerPoint 各含三页，文字、模块、页块、箭头均为原生可编辑对象。
所有时序均为机制示意，不表示实测加速；英文版本可用于论文配图修改。

- [中文可编辑 PowerPoint](assets/beliefkv_architecture_editable_zh.pptx)
- [英文可编辑 PowerPoint](assets/beliefkv_architecture_editable_en.pptx)
- [六张中英文矢量 PDF](assets/beliefkv_figures_bilingual.pdf)
- [图源、600 dpi PNG 和生成信息](assets/README.md)

| 图 | 表达内容 |
| --- | --- |
| 系统架构 | 动态 workflow、因果状态、预测、runtime 决策与原生执行的边界及反馈。 |
| 预测与调度协同 | Child 阶段/工作量预测、工具残余时间分布、物理条件决策与部分 H2D 重叠。 |
| 增量迁移与容量交接 | FULL 缺失段备份、必要 Mamba checkpoint，以及较早 child 释放容量支持 parent 预取与准入。 |

## 主要依据

- [当前系统设计](../../beliefkv_design.md)、
  [架构与实现状态](../../architecture_status_zh.md)、
  [实验运行注意事项](../../experiment_operating_notes_zh.md)。
- [8 月 21 日 parent 连续性验证](../../experiments/beliefkv_h200_native_subagent_semantic_gate_2026-08-21_zh.md)。
- [9 月 20 日旧架构预测 H2D 验证](../../experiments/beliefkv_p6_predictive_h2d_v58_2026-09-20_zh.md)。
- [9 月 24 日训练采集](../../experiments/qwen35_native_training_reset_2026-09-24_zh.md)。
- [9 月 30 日阶段与工作量原型](../../experiments/child_report_phase_work_2026-09-30_zh.md)、
  [预训练语义表示实验](../../experiments/child_semantic_work_stage2_2026-09-30_zh.md)。

历史机制验证使用不同模型、缓存实现和负载，其传输与性能数字不能与
当前 FULL/Mamba 实验直接混算。本文仅汇总截至报告期已有代码与记录，
不将 CPU 夹具收益、运行中统计或离线误差改善当作 GPU 端到端收益。
