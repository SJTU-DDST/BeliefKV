# 论文策略复现与联合方案实施顺序

日期：2026-10-24，America/Los_Angeles。

用户要求先在当前框架复现同类论文，再实施计算、恢复、容量和准入的联合方案。
两段联合设计合并为一个近期执行计划：逐层截止时间决定提前量，容量预算约束
实际可恢复对象，准入机会与同一对象绑定，底层只等待该批真正依赖的事件。
本轮没有开始实现该新调度算法。此前已提交的无关预取等待修复继续独立验收。

## 共同实验条件

采用当前 Qwen3-Coder-30B-A3B-Instruct、18 个不同项目、running/decode
各 24、256K 上下文、Host 200GB、seed 21，复用 v195 自然采集的固定
请求、token、工具和依赖轨迹。继续使用 pretokenized `/generate`，不增加
完整 native 重放，所有正常长尾进入 JCT。改变内部活跃 agent 数属于相应
baseline 的策略，其入口等待仍计入 workflow JCT。

统一底座结果命名为 `method-adapted-sglang`。作者代码、补全论文策略和
额外动态图扩展分别登记。共享原生有效性、容量和 DMA 处理，不共享本系统
的 JOIN 优先、resident-first、短工具续接、PREPARE 或预测器。

机器可读清单位于
`configs/baselines/paper_reproductions_20261024.json`。清单中的 pending
项目仍需完成，不能把已有 CPU 决策函数计为完整系统复现。

## 首批已实施内容

| 方法 | 已实施 | 尚未验证或补齐 |
| --- | --- | --- |
| ThunderAgent | 单后端 reasoning/acting 工作集、工具边界暂停、恢复分组与 BFD 投影，已接入当前原生请求选择和完成回调 | 完整 CPU 资格、真实 GPU 数据面检查、固定轨迹 JCT/吞吐 |
| Continuum public | 公开版均值与 2 秒 TTL 决策，已与作者代码核对 | 多会话 native pin、工具解析与准入接线 |
| Continuum paper | §4 经验 CDF、全局回退、指数冷启动及 TTL 目标与排序核 | 模型恢复代价、在线队列/记忆性统计、TTL 强制与死锁解除 |
| CONCUR | 论文 AIMD 公式与阈值，重复样本不重复收缩窗口 | GPU 命中口径、agent 级暂停/恢复、动态 JOIN 依赖映射 |
| InferCept | §4 的 chunk-discard 代价、全体中断请求按最小浪费排序、换出预算和剩余 preserve/discard 决策 | 实测 profile、每轮预算求解、原生部分换出和 discard、恢复队列 |

随后继续 TokenCake、Agentix/ATLAS、KVFlow，以及清单中与论文主张相关的
CacheWise、CacheScout、PBKV、Pythia。需要预声明 Step Graph 的方法使用
单独输入组；当前动态轨迹的未来图不能作为在线 metadata。

## 源码与一致性证据

作者源码下载在实验 ops 目录，未安装作者 serving 引擎或改动当前环境：

| 作者仓库 | 固定提交 |
| --- | --- |
| ThunderAgent-org/ThunderAgent | `7ddc8610270e56d3b109eed8796b3a4360fc67c9` |
| Hanchenli/vllm-continuum | `316a58794a6ff86b216e579b74fd56ed0c5a911f` |
| WukLab/InferCept | `3a1a4dac37cb569e767bc4a95ef8ed0995a13b99` |
| pku-lemonade/TokenCake | `1901ebf31fdf63590148e61d5c2f65e9fb7ec010`，保留之前冻结的源码与 PDF |

`scripts/verify_paper_baseline_components.py` 从固定源码提取实际策略类，
在 CPU 上执行作者方法。只去除方法内部的模块导入，保留同一常量和调度表达式，
不导入或启动作者 serving 引擎。首轮验证：

- ThunderAgent：1000 个工作集、暂停及恢复状态组合，决策和容量计算一致。
- Continuum public：12 个已识别/未知工具、均值阈值边界样例一致。
- 这不是端到端论文性能复现，也没有验证所有运行时状态转换。

证据：`/home/longhao/experiment/ops/v3758_paper_author_component_fidelity_20261024.json`。
本轮 26 项决策组件测试另覆盖 TTL 目标、queue 成本、AIMD 双条件、Host 预算、
残余 discard 和请求边界暂停；组件测试通过不替代运行时或 GPU 资格。

## ThunderAgent 适配边界

当前每个顺序 agent invocation 对应一个 program。不同并发 child 不合并到
一个只有单一状态的作者 program 对象，workflow JCT 仍按完整 root 统计。
这一映射需要在结果中明确，不能把单会话路由器说成原生动态多线程调度器。

沿用公开代码默认 5 秒控制周期、acting 权重 1、每 program 100 token 预留。
公开代码标记运行中 program 时不会立即释放其容量，当前实现同样只在请求
完成边界暂停。1800 秒请求等待保险规则单独计数。

输入使用当前框架已有的精确 token 数，省去公开 chat proxy 的字符/token
估算及 HTTP 转发；这是明确的统一底座适配。单后端恢复保持原优先分组，
然后采用 BFD 放置顺序。native allocator、HiCache、Host 回收和需求恢复
继续拥有物理控制权，不增加本系统的生命周期感知回收。

未接入的论文名称在预检中直接拒绝。运行日志必须出现真实 paper policy
身份和全套关闭的本系统策略；不能只凭 arm 标签认定 baseline 已启用。

## 最新内部对照

V205 scheduler-only 已完整完成，2054 请求、116355888 输入 token、
387242 输出 token，与 v204 和 v195 工作量完全一致，原生停止条件保持。
两次新鲜 idle telemetry 观察后清理了所有自有进程。

| 条件 | 平均 workflow JCT（秒） | 全程输出 token/s |
| --- | ---: | ---: |
| v195 自然 native | 1509.740834 | 201.434306 |
| v204 Full | 1415.542276 | 214.032350 |
| v205 scheduler-only | 1458.076082 | 208.423131 |

这是同一开发轨迹的单轮结果。Full 相对 scheduler-only 的增量仍较小，
尚未经过配对重复或独立任务正确性评分。旧输入重算、需求 H2D 和额外
控制成本需要分别审计，不把这些数字当作已经达到论文验收目标。

## 原始材料

- ThunderAgent：`https://github.com/ThunderAgent-org/ThunderAgent`；
  `https://arxiv.org/abs/2602.13692`。
- Continuum：`https://github.com/Hanchenli/vllm-continuum`；
  `https://arxiv.org/abs/2511.02230v7`，本次核对 §4–5。
- InferCept：`https://github.com/WukLab/InferCept`；
  `https://proceedings.mlr.press/v235/abhyankar24a.html`，本次核对 §4。
- CONCUR：`https://arxiv.org/abs/2601.22705`，本次核对 §4.3 的公式和 §5 的参数。
- TokenCake：保留 ops 下 `tokencake_prefetch_20261020` 原始源码、PDF 和审计。
