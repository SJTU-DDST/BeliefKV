# 跨轮 subagent 会话与选择性 KV 保留

日期：2026-10-22，北京时间。实现位于 main；独立新 workload 的 GPU 验证
尚未进行，不能把 CPU 正确性或历史比较写成平均 JCT 收益。

## 连续排队的当前判断

短工具返回后继承受限排队连续性仍是待验证的优化，尚未证明净正收益。
V124b Full 完整平均 JCT 2262.19s，历史同任务 native 2223.38s；繁忙窗口
吞吐改善不能抵消平均完成时间的负结果，也不能消除轨迹差异的影响。

V125 的 schedule_only 已完成，完整平均 JCT 1875.68s、输出 1,560,515 token；
V124b Full 输出 1,820,060 token。两组都启用连续性，不能据此推断该模块的
单独收益。两组均为 36 个系统测量有效、35 个无 guard 合格，任务正确性
仍未独立评分。V125 正继续 kv_only，之后的 no_tool_continuity 才移除
连续性。V125 使用冻结工作区，本次代码与新 profile 不进入矩阵。

结果来源：

- `experiments/reports/v2801_v124b_completed_findings_20261022.json`
- `experiments/raw/qwen35_locobench_long_36root_running8_modules_20261022_v125/module_comparison.json`
- `../ops/v125_module_ablations_20261022.registration.state.json`

## 会话语义

`beliefkv/runtime/reusable_subagents.py` 和
`beliefkv/runtime/reusable_subagent_middleware.py` 实现：

- 新建：`task(description, subagent_type)`，结果返回稳定 `agent_id`。
- 再次调用：`task(description, subagent_type, agent_id=...)`，在自己的完整
  私有历史之后追加新任务；私有 todos、文件状态和压缩 cursor 保留，
  每轮 guard/protocol 执行状态重新开始。
- 每轮创建新的 invocation 与 JOIN；原 DONE 保持终态。handle 由 root
  命名空间及创建序号产生，不依赖随机 tool call ID。
- 同 handle 单 writer；跨 root、跨 parent、错角色、未知或忙碌 handle
  被拒绝。拒绝不会创建无人完成的 JOIN；允许 resumed child 再次调用它
  之前的 grandchild。
- `close_agent(agent_id)` 关闭空闲会话；取消与 workflow 结束撤销 handle
  并清理私有状态。物理 KV 被驱逐不会关闭 handle。
- 普通 resume 保留 native session；物理释放后允许原生重新建立 generation，
  仍重放相同逻辑输入。压缩单独关闭旧 session，旧 generation 不再授权回收。

root 接收完整项目材料，child 只接收职责相关的委派并自行检索源码。
新 prompt 鼓励基于检查结果、测试和新证据继续多轮/嵌套委派；相关追问
复用原 specialist，独立判断创建新 specialist，不规定固定轮次或 DAG。

## 物理状态与保留模型

`beliefkv/runtime/subagent_retention.py` 只在 native post-forward 安全点
处理已返回、没有活跃 invocation 的休眠 context。原生引擎 checkout
无需修改；复用其已验证的增量备份、锁、ACK、后缀丢弃和 allocator API。

每轮最多检查四个待处理 context、每个最多 256 个最新 path/component
节点，最多发一批 256MiB FULL 后台备份。没有待处理状态时立即返回；
不逐 decode token 重算特征或扫描图，限频维护复用 post-forward 检查。
容量分配计划仅在边界/实际 extent
变化后更新，同一轮最多重排一次。必要 Mamba state 独立异步备份。

HBM 宽限可被真实 parent 需求立即撤销；读锁、共享引用和在途 D2H/H2D
不会被策略绕过。Host 尚未发布或 ACK 未完成时，不按“已备份”释放源
页。共享前缀仅计一次，物理丢弃只释放原生证明可回收的私有后缀。

选择分数使用已知职责、未解决项、实际复用历史、真实后续委派的相似性，
估计复用概率、避免重算、缺失备份和恢复成本，并按容量预算选择。
冷启动回退为预算 MRU；当前成本为明确记录的先验，尚非 GPU 校准模型。
正标签来自同 handle 的新 invocation 真正提交模型；负标签只来自
自然完整 workflow 的未复用 episode。guard、未完成和执行前取消删失。

恢复前的 HBM/Host snapshot 是潜在可复用状态，不能当作随后 native
确实消费的字节；收益还需关联首次服务与 cache hit/重算证据。遥测已
分离 declared/started resume、物理 drop 后再次调用、extent 去重容量、
备份及回收动作，避免把 task 数量当成真实复用。

## 对照与初始容量

独立配置：`configs/migration/reusable_subagent_retention_20261022.json`。
四组共享同一 API、prompt、工作集、36 roots、running/decode8、seed21、
256K 和 95% 压缩：

| 策略 | 行为 |
| --- | --- |
| all | 保留为原生可撤销软缓存，不强制主动备份每个返回状态 |
| drop | 释放物理 KV，保留相同私有历史 |
| lru | 同 dormant Host 预算的近期优先保留 |
| selective | 有界 HBM/Host/重算三层选择，加边界概率模型 |

32GiB dormant Host、2GiB HBM 及最多 1s 宽限是初始参数。Host 预算使用
去重最新 checkpoint 的保守容量，尚未完全扣除与活跃 context 共用的
边际占用。先测真实新增独占字节和有用前缀重算，不能仅以逻辑全历史
上界将 roots 减至 16；如需改为 20，所有策略同步修改。

## CPU 证据

43 项新增聚焦用例通过，覆盖真实同步/异步 LangGraph 调用、嵌套跨轮
grandchild 复用、并行同描述隔离、无效 handle、取消、压缩/generation、
真实 CPU radix/allocator、共享页、在途传输和 ACK 前后释放。
`scripts/run_reusable_subagent_cpu_checks.py` 从 V2800 XML 保留原 4064 项
资格测试，并加入这些用例；V2806 最终 4107 项全部通过、另有 2 个
subtest，通过集合完整包含 V2800 的保留集合，无 skip。沿用 V2800 对
既有 Docker 沙箱测试的排除，不将其写成已验证。

V2805，101 个样本、正常 GC 的 metadata policy benchmark：

| dormant 数/每个 extent 数 | 冷启动规划 P50 | 已有标签规划 P50 |
| --- | --- | --- |
| 36/9 | 0.219ms | 0.371ms |
| 128/17 | 0.727ms | 1.410ms |
| 512/33 | 4.106ms | 5.998ms |

空待处理 dispatch P50 约 0.081us，有界语义特征提取约 154us。它们不包含
native 路径观察、控制事件传输、DMA 或 GPU 等待，不能据此外推 JCT。
512 个状态的规划仍可能达到毫秒级，应在真实 profile 中检查边界频率。

## 工作量不同时的评价口径

主指标保持同等任务质量/完成要求下的配对 workflow JCT，保留普通长尾；
极端工具异常单列并提供完整结果。并列报告输入/输出 token、请求、
工具、spawn/resume、上下文、吞吐和完成资格。

`JCT / 本轮实际输出 token` 仅为辅助诊断，会奖励更多输出：
100s/1000=0.1s/token，120s/2000=0.06s/token，但后者完成慢 20%。
它也忽略输入 prefill、工具等待和并行依赖。比值平均与总量相除的
权重还不相同。模块因果收益应补相同请求/依赖/生成需求的受控 replay，
不能靠输出 token 归一化替代工作量控制。
