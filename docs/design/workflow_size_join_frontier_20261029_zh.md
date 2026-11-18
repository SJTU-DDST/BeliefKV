# 面向 workflow 平均 JCT 的容量感知准入

2026-11-17 当前开发候选补充：严格短优先改用可选的
`remaining_model_tool_critical_path_seconds` 点值；并行工具和 child 在
离线标签中取依赖路径的 max，扣除已发生的工具等待。真实 ready sibling
跨恢复类别整体检查容量，不能整体容纳时保留部分进展；默认六名、至多
八名候选及原生最终权限不变。不启用路径头时保留原排序，新增头部尚未
获得 GPU JCT 收益证据。CPU 实现、全部负结果及复现见
[2026-11-17 关键路径审计](../research/r5_completion_path_audit_20261117_zh.md)。
下面的概率、成本组合和分档方案保留原日期语义。

2026-10-30（北京时间），R5 推荐设计。当前建议见
[第 14 节：条件剩余工作分布驱动的 JOIN 组合准入](#r5-conditional-join-admission)：
保留 workflow 剩余模型工作量的短/中/长概率，以有界组合选择统一周期恢复、
空余额度补入和 sibling 补入。默认六名候选，必要时扩展到八名；原生数据面
决定最终恢复和服务。不在线预测每个候选安排的完整 JCT，不要求完整未来树、
Monte Carlo rollout 或 CP-SAT。

按用户最新要求，原 Full 开启的 ACTING decay、恢复后的压力处理和
1800 秒兜底原样沿用。新策略不直接选择 active 受害者；撤回上一版
“按全额 active 预算额外限制恢复、保证整轮旧 active 不减少”的要求。
`active_after_resume=A_before union S` 只描述恢复子步骤，不约束后续
原有压力处理后的 active 集合。

第 2--13 节保留 R2--R4 模型、实现假设、CPU 算例及局限，冲突之处以
第 14 节为准。R4 图示及组内相邻排序不代表 R5 已实现组合选择。
三种容量的 oracle 反例仍用于说明新启发式的边界，不作为它的性能承诺。
R4 分档/排序已有实现、训练及完整开发实验，尚未确立可推广的性能收益；
R5 条件分布训练、统一组合选择和对应成本核算仍待实现与验证。本文更新
只固化方案，不修改运行时、模型、配置、冻结实验或 GPU 实验登记。
实际实现与历史结果见
[恢复实现状态](workflow_size_resume_implementation_20261029_zh.md)。

## 1. 决策范围和目标

**workflow 是 JCT 目标和剩余工作分布的预测单位；agent 是恢复选择单位。
新策略只优化合法 ready-paused 候选的选择，沿用原容量与压力处理。**

当前范围是恢复选择阶段，不改变 active 的原有续调、decay 和压力策略，
也不新增 decode 抢占、强制时间片或原生物理权限。R5 拟统一多个恢复入口
的候选选择逻辑，各入口的预算与生命周期语义继续保留；原生首次到达资格和
native batch 不被这个选择器整体替代。

目标为完整 workflow 的平均 JCT，同时公开 P95 JCT、长任务退化和全程
吞吐。平均 JCT 下降不等于总完成时间缩短，后面的时间线展示了这个代价。

![历史 R4 设计：workflow 依赖、恢复顺序与容量分配对照](../../experiments/figures/workflow_size_join_frontier_20261029/design_selection_r4.png)

本图保留 R4 的同一候选快照：小上下文不等于少量剩余工作；按 workflow
档位选择可使同一 ALL-JOIN 的 ready sibling 共同推进。配套的时间与容量
设计图、计算过程和局限见第 13.6 节；模块架构图保留于第 13.5 节。
这些均为 R4 设计说明，不是 R5 已经上线或已取得性能收益的证据。
下面 R2--R4 的准入顺序与时间推演保留为历史对照。

## 2. 调度器维护哪些状态

| 范围 | 状态 | 明确定义 |
| --- | --- | --- |
| workflow | `p=(p_low,p_mid,p_high)` | 当前预测的剩余固有模型工作量三档概率 |
| workflow | `a_w` | 根及所有 descendant 已实际获得的归一化模型服务工作量 |
| workflow | `h_w` | 有待服务 ready 请求、却没有任何分支实际获得模型服务的连续时长 |
| workflow | `arrival,id` | 到达次序与稳定身份，用于确定性打破平局 |
| JOIN 分支 | `r_i` | 本 JOIN 内该分支已经实际启动的模型调用数 |
| 请求 | `q_i` | 当前调用连续 ready 但尚未首次获得模型服务的时长 |
| 请求 | `g_i,b_i` | 最近一层真实未满足 ALL-JOIN 身份、它所依赖的分支身份 |
| 资源快照 | `B0,F,slots,input` | 已运行批次、实际剩余容量、剩余请求槽位、native 输入预算 |

这里的 ready 指已经提交且应用依赖满足的请求。等待恢复、逻辑资格或
native 准入仍属于 ready 等待，不能通过撤销资格把它从饥饿计时中排除。
所有分支都在工具或真实外部依赖中等待时，停止并清零 `h_w`；下一次出现
ready 请求再开始计时。任一分支实际获得服务也会清零 `h_w`。因此这个
老化只保护 workflow 的服务机会，不能保证其每个 sibling 都不饥饿。

`a_w` 是服务账本，**不是硬执行预算，也不是多个请求服务墙钟时间之和**：

```text
a_w += calibrated_prefill_work(actual_new_input, history_length)
a_w += calibrated_decode_work(actual_generated_tokens, history_length)
```

成本表用固定模型/硬件校准，统一成参考 decode 工作单位 U；并行分支的
工作都计入根账本。spawn 不创建新的公平身份，也不重置已有服务量。
实际发生的重算仍消耗服务并记入账本，但预测标签中的固有工作不包含这些
缓存政策制造的重复计算。授权、入队、H2D ACK 都不增加 `a_w`。

`r_i` 在一次调用的首次实际 native forward 后加一，后续 decode 步不再加；
同一逻辑调用的重排队重试不重复增加。它衡量调用级服务机会，不衡量 token
数，更不能被解释为 child 的真实剩余工作量。请求 attempt 及 native
服务证据单独关联，避免将逻辑 grant 当成 GPU 服务。

## 3. 小模型的输入、输出和更新

### 3.1 输入输出固定下来

建议首个可训练版本为 CPU 上的小 MLP，所有数值都是待验证的初始配置：

```text
任务指示 prompt
    -> 仅在训练集拟合的字符 2--4 gram TF-IDF，最多 4096 维
    -> 静态线性投影 64 维，任务到达时缓存

运行时已观察特征
    -> log/标准化 -> 线性投影到同一 64 维

两项相加 -> ReLU -> 32 维隐藏层 -> 3 维 softmax
                                           -> p_low,p_mid,p_high
```

输入不含项目代码。任务文本不随每次请求重复编码；动态输入包含已获服务
`a_w`、已生成 token、已完成模型调用、spawn 轮次/总 child 数/已观察最大
fanout、未完成/已返回 child 数、ready/running/tool 中分支数、已完成 JOIN
轮次、最大已观察嵌套深度、root 是否处于真实 WAIT_JOIN，以及已观察的
completion intent。尚未出现的分支、下一轮 spawn 和真实未来工具耗时
一律不能输入。

这个结构约有数十万参数，参数量由词表和动态特征维度确定；尚未测量推理
耗时或证明精度。初始预测使用同一个模型，未发生的计数为零。

### 3.2 标签是剩余工作，不是排队后的 JCT

离线对每个时刻 `t` 标注此 workflow 未来尚需的固有 prefill/decode 工作：

\[
R_w^*(t)=\sum_{\text{未来逻辑模型工作}}
         c_{\rm ref}(\text{必要输入增量、decode token、上下文长度}).
\]

首次请求的必要输入与后续逻辑新增输入分开核算，不累计每轮完整 prompt
作为新 prefill。剔除排队、外部工具等待、DMA 以及缓存丢失/重试造成的
重复模型计算。跨策略可复用的标签必须绑定同一条固定逻辑轨迹；自然生成
轨迹发生变化后，不能继续声称标签工作量相同。

用训练 workflow 初始总固有工作量的 1/3、2/3 分位数作为候选固定阈值
`tau1,tau2`，将各时刻的 `R*` 分为 low/mid/high。运行时不修改这两个阈值，
因此长任务临近结束时也能降到 low。每个训练 workflow 等权采样，避免
长轨迹拥有更多快照而主导拟合。按项目及任务模板留出测试，不将同源子
请求随机切到训练和测试两侧。

当前随机合成工具时间与任务语义没有逐次对应关系。该模型预测模型服务
工作；它不能从原始命令中恢复随机采样的未来工具时长，更不能承诺准确
预测完整 workflow 的墙钟剩余时间。

### 3.3 更新频率和不确定性

在 spawn、真实 JOIN 状态变化、工具开始/结束、模型调用完成，以及新增
128 个 workflow 输出 token 后将该 workflow 标为 dirty。CPU 预测最多
每 200ms 更新一次；合并同时到达的事件，不逐 token 扫描全部 workflow。
当前快照采用最近完成的预测，不为等待预测阻塞 native 选批。

候选平滑规则为 `p <- 0.5*p_old + 0.5*p_new`，初始直接使用 `p_new`。
若 `max(p)>=0.6`，采用最大概率档位，平局取较低索引；否则退回已获服务
分级：

```text
level = count(a_w > sigma for sigma in [sigma1, sigma2])
```

`sigma1,sigma2` 使用冻结的训练服务工作量分位点。回退的 level 是优先级
代理，不是剩余工作量预测。算例中的 `5U/9U` 只是人工构造值。
这条回退不能纠正所有高置信错判；仍需单独测量“长任务误判为 low”的代价。

## 4. R2 对照：一次准入的确切规则

下面的层级规则已有可运行 CPU 参考，但第 10 节给出其严格次优的反例。

![R2 对照的具体决策](../../experiments/figures/workflow_size_join_frontier_20261029/admission_decision_r2.png)

### 4.1 先排列 workflow

只排列至少存在一个 pending ready 请求的 workflow。按下列元组升序：

```python
if h_w >= 1800_seconds:
    key_w = (0, -h_w, a_w, arrival, workflow_id)
else:
    key_w = (1, level_w, a_w, arrival, workflow_id)
```

因此普通情况下先 low、再 mid、再 high；同档先服务较少者。
连续 ready 且没有服务达到 1800 秒的 workflow 进入老化通道，优先于普通
队列，老化通道内部先等待更久者。

按用户要求撤回旧的 10 秒配置，先采用 ThunderAgent 的 1800 秒尺度。
这不是服务时延承诺：正在 decode 的请求不被
抢占，容量不足或排在前面的老化任务仍可能继续延长等待。`a_w` 在不同档
之间也不会覆盖长短优先级，不能称为严格公平或保证最大等待时间。
公开 ThunderAgent 的 1800 秒针对 paused program 的恢复等待超时；
本 CPU 对照的 `h_w` 是 workflow ready 等待，并非完全相同的计时口径。
实际接入时沿用当前运行时的暂停/恢复兜底，不增加 10 秒工作集切换，
也不绕过原生物理容量、锁或 DMA 依赖。

### 4.2 在选中 workflow 内确定组和 anchor

先保留当前单独可容纳的 pending ready 请求；资源暂时不足时允许绕过，
不阻塞其他 workflow。每个请求只归入最近一层**真实观察到的阻塞
ALL-JOIN**；没有这种 JOIN 的请求各成单成员组。ANY、后台 spawn、
基于完整离线 trace 推断的未来/隐含 JOIN 都不适用此规则。

嵌套时只使用最近一层未满足 JOIN，不能同时把一个请求算进多层 ancestor
组来重复获取协同资格。根 workflow 的服务账本仍只有一份。

同 workflow 多组并存时，选择拥有等待最久 ready 成员的组：

```text
group_key = (-max(q_i for ready i in group), group_id)
```

组内按下面的 key 排列，第一名为本轮不可被挤掉的 anchor：

```text
member_key = (r_i, -q_i, restore_cost_i, arrival_i, request_id)
```

先给在本 JOIN 中实际启动调用更少的分支一个机会；相同时先等待久的，
再偏向恢复成本低的。取 anchor 和随后最多三个同组成员形成候选池。
最多四个候选意味着最多枚举 `2^3=8` 个含 anchor 的集合；不是强制只
执行四个 child，后续准入轮次仍可推进其他成员。

**已有 sibling 在 running batch 中就继续执行。**不重新给它申请一个槽位，
也不因为本轮只有一个新增成员就称整组没有并行。尚在工具中或没有提交
请求的 sibling 不进入候选池，不能拖住 ready 成员。

### 4.3 集合如何比较

对每个包含 anchor 的候选 `S`，先检查实际资源：

\[
\operatorname{slots}(S)\le\operatorname{remaining\_slots},
\quad M(B_0\cup B\cup S)\le \operatorname{usable\_capacity},
\]

其中 `B0` 为已运行请求，`B` 为本轮已经选中的请求。`M` 使用原生有效
前缀和真实物理页并集，加入当前输入/chunk、新输入增长及 decode 预留。
共享页只计算一次；FULL、Mamba 若存在则分别核算，当前 FULL-only 模型
不能要求不存在的 Mamba 池。还须满足输入 token/chunk 预算、锁、Host
副本、在途传输及原生 admission 的其余约束。

在可行集合中最大化：

\[
\operatorname{score}(S\mid B)=
\frac{|\operatorname{distinct\ ready\ branches}(S)|}
     {C(S\mid B)}
\]

\[
C(S\mid B)=
\widehat T_{\rm restore}(S\mid B)
+\Delta\widehat T_{\rm prefill}(S\mid B)
+16\,\Delta\widehat T_{\rm decode}(S\mid B).
\]

分母是毫秒单位的短窗口成本；decode 项为每步边际成本。
这是“单位新增成本能推进几个 ready 分支”的启发式，不是 JOIN 完成
时间估计，也不保证选到未来的关键 child。16 步是成本比较窗口，
**不是执行 16 步后强制抢占的时间片**。

实际成本估计接口必须使用相同模型的固定校准表，按 batch、上下文长度、
输入/chunk 及实际缺失页建模。恢复部分应按共享缺失页/extent 并集规划，
只计新增恢复，不能把同一共享页的多个请求恢复耗时简单相加。成本表
尚未实现或无覆盖时只保留 anchor，沿原生路径执行，不编造零恢复成本
或为了凑组同步运行昂贵规划。

同分依次选择：分支更多、恢复更便宜、物理占用更小、请求 ID 字典序更前。
先有保留 anchor，再考虑集合效率，避免一个高恢复成本的 anchor 永远被
便宜 sibling 挤掉；但整组或 workflow 的可容纳性限制仍可能导致饥饿。

每次遍历每个 workflow 最多贡献一个集合，再继续下一个 workflow；
全部遍历后若仍有资源才开始下一遍。这样既允许同组联合准入，也不会在
一个 workflow 内无限循环、完全不检查其他 ready workflow。
组更宽时确实会暂时占用更多槽位，不能把共享记账说成严格等份服务保证。

### 4.4 何时记账，何时重试

原生物理检查拥有最终决定权。proposal 中的两名成员只实际准入一名时，
只能记录一次实际服务，不能记为全组成功。预算预留与请求 attempt 绑定；
拒绝、取消及 native requeue 必须撤销未消费预留。

真实请求完成、容量/资格释放、JOIN 更新和新请求提交会触发重新检查；
采用已有有界重试入口，不逐 decode 步重扫失败集合。本候选自身不引入
额外周期 tick，也不继承一个必须等待五秒才重排新请求的门槛；若现有
逻辑资格仍卡住同一请求，必须把那段等待单独统计，不能声称新排序已生效。

## 5. 可核对的伪代码

```python
def propose_admission(snapshot):
    selected = []
    workflows = sorted(snapshot.ready_workflows, key=workflow_key)

    while snapshot.has_available_slots():
        made_progress = False
        for w in workflows:                 # 每一遍：每 workflow 最多一个集合
            reqs = individually_fitting_ready_requests(w, snapshot)
            if not reqs:
                continue
            g = min(nearest_observed_join_groups(reqs), key=group_key)
            pool = sorted(g.ready_requests, key=member_key)[:4]
            anchor = pool[0]
            choices = subsets_containing_anchor(pool)
            feasible = [s for s in choices if native_plan_fits(s, snapshot)]
            if not feasible:
                continue
            s = best_score_or_anchor_fallback(feasible, selected, snapshot)
            reserve_provisional_union_pages_and_slots(s, snapshot)
            selected.extend(s)
            remove_selected_pending_requests(s, snapshot)
            made_progress = True
        if not made_progress:
            break

    return selected                         # 只是 proposal

def on_native_service(request, actual_work):
    root_workflow(request).a += actual_work
    root_workflow(request).ready_without_service_since = None
    if first_forward_of_this_logical_call(request):
        nearest_observed_join_branch(request).served_calls += 1
```

原生请求不属于 JOIN 时，只更新对应单成员的本地调用记录。线上实现还需
限制一次候选检查的总窗口，并复用当前 native 选批的前缀与容量快照；
本例的八个子集上界不等于整控制器开销已经有了固定时间上界。

## 6. 把数字代入：五个确定的选择

基础快照：两个空闲请求槽位，八份抽象 KV 容量。A/B/C 档位为 low/mid/high，
已获服务和 ready 等待均为零。A.a、A.b 属于一个真实 ALL-JOIN，每个需
三份 KV；B、C 各需四份。anchor=A.a。成本为人工构造值：

```text
prefill(A.a)=10ms, prefill(A.b)=4ms
decode_cost(width)=3+width ms，width=0 时为 0
restore=0
```

| 集合 | 占用 | 窗口成本 | 组内分支数 / 成本 |
| --- | ---: | ---: | ---: |
| `{A.a}` | 3 | `10+16*4=74ms` | `1/74=0.0135` |
| `{A.a,A.b}` | 6 | `10+4+16*5=94ms` | `2/94=0.0213` |

因此本轮选两名 sibling。不是看到有两个 child 就强制把它们一起调度。

| 场景变化 | 确定选择 | 原因 |
| --- | --- | --- |
| 基础快照 | A.a + A.b | 组合可容纳，分支推进/增量成本更高 |
| 可用 KV 只有 5 | A.a | 两人集合不可行，剩余 2 也放不下 B/C |
| 只有 1 个槽；A.a 已启动 2 次，A.b 为 0 | A.b | 组内 anchor 按调用服务机会排序 |
| C 连续 ready 无服务 1802s | C，然后 A.a | C 先走老化通道；剩余 4 份容量只放一名 child |
| A.b 新增恢复成本 300ms | A.a，然后 B | pair 成本 394ms，`2/394<1/74`，不为凑组搬入 |

最后一条也暴露局限：A.b 如果恰是未来的关键分支，局部省恢复成本可能
推迟 JOIN。这个评分目前只是可执行的候选，不是已经正确解决关键路径。

以上五种选择由
[examples_r2.py](../../experiments/reports/workflow_size_join_frontier_20261029/examples_r2.py)
实际执行并断言，
[examples_r2.json](../../experiments/reports/workflow_size_join_frontier_20261029/examples_r2.json)
保存完整候选、成本、排序及选中结果。
这个参考只覆盖空闲槽位、抽象页容量及人工成本，不包含真实 native
锁、chunk、输入增长、异步 DMA 或预测器。页占用按并集去重，恢复成本
按请求人工给定；这些互不共享的示例**没有验证共享传输的成本建模**。

## 7. 时间线：收益与代价一起展示

![计算所得的时间线](../../experiments/figures/workflow_size_join_frontier_20261029/execution_timeline_r2.png)

同样的五段固定工作：A.a/A.b 各需 2 个时间单位，二者结束后 A.r 汇总需 1；
B 需 6，C 需 10。两个理想执行槽位、KV 容量为 8、全部 t=0 到达、迁移
时间为零。FIFO 的请求次序为 B、A.a、C、A.b；A.r 只能在 JOIN 解锁后出现。

| 政策 | A JCT | B JCT | C JCT | 平均 JCT | 最后完成时刻 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 请求 FIFO | 9 | 6 | 12 | 9 | 12 |
| 本文候选规则 | 3 | 8 | 13 | 8 | 13 |

A 提前 6，B 推迟 2，C 推迟 1；平均 JCT 降低 11.1%，makespan 增加
8.3%，固定工作总量下的全程吞吐也下降。这不是 GPU 实验，更不是预计
系统能提升 11.1%；也没有分离“短任务排序”和“组内协同”的独立贡献。
它说明若研究目标同时包括提高吞吐，不能仅凭平均 JCT 改善接受该政策。

## 8. 相对已有实现，哪些是真正待做的

[现有 sibling 共同准入](reference_join_siblings_20261027_zh.md)已经支持
可容纳子集、实际同组选批和容量释放后的有界重试。它们不是本文发现的新
缺失。本候选新增讨论的是残余工作量三档预测、根共享服务排序，以及
明确的组内 anchor/短窗口集合成本选择；尚未接入已有实现。

Agentix ATLAS 已有 program 级服务继承及并行调用分组；附录 A.3 还明确
讨论了通过共同调度避免 straggler。Justitia 已有文本向量与小型 MLP 的
资源成本预测和组级完成目标。因此“轻量模型 + 短任务 + sibling 成组”
这一组合还不足以直接声称新的论文贡献。应证明动态已观察 JOIN、预测
纠错和 KV 容量约束的具体机制带来超出这些机制的净收益。

对应原始论文：

- Agentix，USENIX NSDI 2026，正文服务继承及附录 A.3。
  `https://www.usenix.org/system/files/conference/nsdi26/nsdi26spring_luo_prepub.pdf`
- Justitia，arXiv:2510.17015v2，2026-03-14。
  `https://arxiv.org/html/2510.17015v2`

## 9. 当前不能承诺的部分及验证顺序

当前源轨迹只有 18 个项目、72 个 JOIN，其中 52 个单成员、20 个双成员，
没有嵌套。它不足以支持跨任务泛化或大 fanout 场景的结论。初始输入排除
代码后，不同项目可能拥有相似任务指示却需要完全不同的工作；该信息
限制不会因把模型命名为“语义预测器”而消失。

先完成三个 CPU 诊断，再考虑另行登记 GPU 比较：

1. 在完整固定轨迹上分别使用真实未来剩余工作三档和在线可得的三档，
   检查跨 workflow 排序空间及预测失误代价。先知标签仅用于诊断，不是
   在线方案，也不是理论 JCT 上界。
2. 统计两个及以上 sibling 同时 ready 的机会、联合实际 KV 需求、可用
   槽位，以及最后完成分支的 ready 等待。不能用总 JOIN 数代替这些机会。
3. 按项目/模板留出检查排序正确率、长任务误报 low、预测更新稳定性和
   全控制器 CPU 成本；包含最大候选窗口、失败规划和共享页场景。

未来性能比较保持完整 token、依赖、工具时间、到达时间、容量和正常
尾部一致。报告完整平均/P95 JCT、固定工作全程吞吐、workflow ready
服务间隔、长任务退化、最后 child 等待、JOIN 解锁至 parent 首次服务，
同时报告 H2D/D2H、重算、实际同组服务及控制 CPU 成本。

若队列主要被不可抢占的长 decode 占满，本候选可能很少获得干预机会；
若成本评分长期绕开真正关键分支，或模型排序无效，就需要修改或放弃，
不能以联合准入次数或某个短任务更早结束来宣称成功。

两幅 R2 图由
[draw_policy_r2.py](../../experiments/reports/workflow_size_join_frontier_20261029/draw_policy_r2.py)
读取算例 JSON 生成，含
[准入图 SVG](../../experiments/figures/workflow_size_join_frontier_20261029/admission_decision_r2.svg)
和
[时间线 SVG](../../experiments/figures/workflow_size_join_frontier_20261029/execution_timeline_r2.svg)。
旧版 `policy_overview` 保留为被替换的草案图，本文不再使用。

## 10. 历史 R3：完成时间前瞻及其反例

以下完成时间推演已退出默认在线方案。保留算例，说明仅凭短/中/长
及 sibling 关系不能达到 oracle；当时的 R4 方案见第 13 节，
最新 R5 推荐设计见第 14 节。

### 10.1 三种情况先固定相同的工作量

![容量变化与离线 oracle：历史反例](../../experiments/figures/workflow_size_join_frontier_20261029/capacity_oracle_cases.png)

只给 A=low、B=mid 和空位数量，无法唯一确定 oracle。下面给一个可以
逐步核对的完整例子：

- A 的两个 child：a1、a2 各需 3；二者结束后 parent 汇总还需 1。
- A 总工作为 7，B 是一段需 8 的串行工作，因此 A 确实比 B 短。
- 全部在 t=0 到达。每段工作占一份 HBM 容量，执行速率独立，非抢占，
  没有工具、传输或客户端延迟，完成后立即释放该份容量。
- 初始只有一份可用容量；另一执行者在已声明的 tau 时刻释放第二份。
  这个外部释放不因选择 A/B 而改变。其完成时间在所有安排中相同，
  比较目标仅列 A/B，省略这个相同的常数。

这是用户所说“一份/两份 agent 空间”的理想化模型。真实 HBM 容量不等于
独立 GPU 核；多请求共同 decode 的服务速率会变化，必须另行建模。

### 10.2 第一种：a1 执行中释放第二份容量

令 a1 已在 0--3 执行，第二份容量在 t=2 释放。比较此时真正可以改变的
决策，而不是回到 t=0 重选已经发生的工作：

| t=2 的选择 | 后续安排 | A 完成 | B 完成 | 平均 JCT |
| --- | --- | ---: | ---: | ---: |
| 补 a2 | a2:2--5；B:3--11；汇总:5--6 | 6 | 11 | 8.5 |
| 先 B | B:2--10；a2:3--6；汇总:6--7 | 7 | 10 | 8.5 |

二者均是本状态下的 oracle。共同推进 A 让 A 提前 1，却使 B 推迟 1，
平均目标完全相同。实际系统可用更少恢复、更高缓存局部性打破平局。

不能把“a1 正在运行时出现空位”概括成“必定优先 a2”。额外反例把
a1/a2/汇总/B 改为 5/1/1/8，第二份容量在 t=4 释放：A 仍只有 7，
但从 t=0 先 a1 的最佳完成时间总和为 19，先 B 的最佳总和为 18。
这是初始选择的比较，不能用于撤销已经实际运行的 a1。

### 10.3 第二种：先 B，恰好在结束时得到两份容量

令第二份容量在 t=8 释放：

| 初始安排 | 完整顺序 | A 完成 | B 完成 | 平均 JCT |
| --- | --- | ---: | ---: | ---: |
| 严格优先 A | a1:0--3；a2:3--6；汇总:6--7；B:7--15 | 7 | 15 | 11 |
| 先 B | B:0--8；a1/a2:8--11；汇总:11--12 | 12 | 8 | 10 |

离线穷举验证第二行在这个模型中全局最优。上一版即使正确知道 A=7、B=8，
也会因为固定先选 low 而错过它，所以问题并非仅来自预测错误。

这不证明“只要未来能并行 A，就必须先 B”。设每个 child 为 p、汇总为 r，
B 为 b，且外部释放时刻 tau=b。在 `2p+r<b` 的条件下，上述两个候选
完成时间总和分别为：

```text
先 A：4p + 2r + b
先 B：2b + p + r
```

比较这两个候选，先 B 只有在 `b<3p+r` 时更好；B 过长则先 A 更好。
该不等式比较这两个候选，不替代一般任务图的全局最优证明。

### 10.4 第三种：始终串行

此时将没有外部阻塞、没有新到达的 A 看成需 7 的整体串行作业：

| 顺序 | A 完成 | B 完成 | 平均 JCT |
| --- | ---: | ---: | ---: |
| A 的全部剩余工作，然后 B | 7 | 15 | 11 |
| B，然后 A 的全部剩余工作 | 15 | 8 | 11.5 |

因此应先完成 A。两个 child 内部先 a1 还是 a2 在该例中不影响目标。
一般情况下加入工具阻塞、不同到达时间、共享页、服务速率变化或预测错误，
就不能继续套用这个简单串行结论。

### 10.5 固定两级优先级缺少什么

严格先选 workflow 会在比较其他安排之前锁定赢家；组内“分支数/成本”
也没有直接测量完成一个 workflow 的收益。即使放宽为两级近似，至少
还需要知道：

| 信息 | 作用 |
| --- | --- |
| 总剩余工作 W | 串行处理时需要付出多少模型工作 |
| 当前可利用的并行度及汇合后的串行尾部 | 同样工作量在一份/两份容量下可能差别很大 |
| 正在运行分支的剩余工作 | 一个新空位应补齐已推进的 JOIN，还是交给 B |
| 未来安全容量释放的时间分布 | 比较“现在做 A”与“先 B、再并行 A” |
| 实际缺失 KV、迁移与批服务速率 | 防止逻辑上更好的安排制造更大的恢复/计算成本 |

对这个 fork，理想完成曲线是 `T_A(1)=a1+a2+r`、
`T_A(2)=max(a1,a2)+r`。一般 DAG 的 `max(W/k,critical_path)` 只能作
简化参考，不能直接当成实际完成时间；HBM 份额也不提供 k 倍算力。

### 10.6 推荐决策：少量候选安排的滚动比较

保留 low/mid/high 作为低成本候选筛选和退化策略，取消其对最终选择的
绝对决定权。最终比较同一资源快照上的候选安排 P：

\[
P^*=\arg\min_{P\in\mathcal P(x)}
\sum_\xi p(\xi\mid x)\sum_{w\in\mathcal W}
\widehat C_w(P,\xi).
\]

`x` 只含当前观测；`xi` 是相互一致的任务剩余工作、服务速率及容量释放
情景；每个 workflow 等权计一次。`W` 包含可能被安排影响的已到达未完成
workflow，不能只给新选中的 A 计收益而遗漏被延迟的 B。只要各政策使用
同一组固定到达时间，`sum(C_w-arrival_w)` 与 `sum(C_w)` 只差常数，
最小化完成时间总和等价于最小化平均 JCT。

候选生成必须至少保留三类互相竞争的选择：

1. 当前工作集/原有政策会选的可行安排，作为保留连续性的参照。
2. 补齐正在推进的真实 ALL-JOIN，包括单成员及可容纳的共同准入集合。
3. 先执行另一 workflow，再用预计释放容量推进该 JOIN。

可容纳时也比较跨 workflow 混合的集合。不能先固定 low 的 anchor，
否则第 3 类安排仍然进不了比较，第二种反例也就没有修复。

首个工程原型可把候选上限定为 8，向前展开最多两个容量/模型调用完成
事件，每个节点只保留相同的有限候选；至多 64 个两步候选组合/情景。
这些是待验证的工程上限，并非最优参数或已测得的低开销保证。
到窗口末尾仍未完成的 workflow 必须加上模型估计的尾部完成成本；
不能只计窗口内已经完成者，否则会奖励把长任务推到统计窗口之外。
此段的完整两层展开是初始研究上限，不再推荐默认放入同步热路径；
第 11 节按实际小宽度选择给出更轻的首版执行方式。

现有 1800 秒兜底已选定、且当前可以安全容纳的请求应作为候选约束保留，
只比较剩余资源上的选择，不能因平均完成时间评分再次把它无限延后。
这仍不赋予越过物理容量检查或强行驱逐正在服务请求的权限。

候选只决定当前能安全提交的第一个动作，后续在真实请求完成、容量释放、
工具返回或 JOIN 更新时重算。正在执行的请求固定，不为了预测变化强制
换出；未来释放只进入估计，不能在实际释放前当成可分配物理页。
线上首版保持可行时推进 ready 工作，不为了猜测某个未来并行窗口空等；
离线 oracle 额外枚举空等，用于测清这项限制的损失。

### 10.7 预测错误如何进入决策

不仅给一个预测秒数，还需要经过留出集校准的剩余工作和释放时间分布。
相关变量应联合采样：同一 GPU 上的 batch 变慢会同时影响多个分支，
不能任意组合它们各自的独立 P50/P90 当成真实概率情景。

仍用第二种例子，假设只有两种真实状态：

- 概率 q：第二份容量在 t=8 可用。
- 概率 1-q：整个比较期间没有第二份容量。

从枚举得到：

```text
先 A 的期望完成时间总和 = q*22 + (1-q)*22 = 22
先 B 的期望完成时间总和 = q*20 + (1-q)*23 = 23 - 3q
```

因此 `q>1/3` 才选 B；例如 q=.8 时先 B 的期望平均 JCT 为 10.3，
先 A 为 11；q=.2 时应先 A，先 B 的期望平均 JCT 为 11.2。
这展示了同一个明确目标如何处理预测不确定性；真实 q 还没有训练得到，
这些人为概率不是实际预测准确率。

初始文本模型的三档输出不足以给出这些联合分布。后续若实现本候选，
需要增加当前前沿请求/分支的剩余工作区间、root 串行尾部及可安全释放
容量的估计；不要求预知完整未来 spawn 图。当前随机工具时长继续用
可观测类别和已等待时间的条件分布回退，不能读取隐藏的合成计时。

预测区间过宽、缺乏校准或成本表未覆盖时，采用第 1 类连续性安排。
预计改进须超过控制开销及验证集测得的决策误差余量才替换；控制造成的
延迟要以 workflow 完成时间的同一单位计入，不能把 CPU 毫秒直接与多
workflow 的累计完成时间相减。并列方案优先保留已有驻留、减少迁移。
这只能降低错排风险，不构成任意预测错误下的 oracle 近似保证。

### 10.8 伪代码及与当前实现的关系

```python
def on_admission_opportunity(observed_state):
    snapshot = native_resource_snapshot(observed_state)
    continuity = current_policy_plan(snapshot)
    plans = bounded_cross_workflow_plans(
        continuity=continuity,
        include_ready_join_extensions=True,
        include_other_workflow_first=True,
        max_candidates=8,
    )
    forecasts = calibrated_joint_forecasts(observed_state)
    if not forecasts.usable:
        return continuity
    costs = rollout_sum_workflow_completion(
        plans, snapshot, forecasts, depth=2, include_unfinished_tail=True,
    )
    winner = lowest_expected_cost(costs)
    if not clears_decision_error_and_control_margin(winner, continuity):
        return continuity
    return native_validate_and_submit_first_action(winner)
```

现有 sibling 共同准入仍是有限组内重排和可容纳子集，不是上述求解器。
R2 的三档预测/组内成本评分本身也只有 CPU 参考，不能将它说成正在运行
的系统已经实现了这个两级政策。R3 尚未接入运行时，1800 秒的原生/参考
暂停恢复路径及当前冻结实验未在此次设计工作中修改。

现有 `join_sibling_service.py:order()` 另有请求等待 10 秒的局部保护：
阻止冷 sibling 越过久等请求。它不使整个 workflow 全局越级，也不撤销
其他 owner 的资格，与已撤回的 R2 10 秒 workflow 老化不是同一机制。
不能为了统一 1800 秒而把所有出现的 `10` 一并替换；该现有保护保持原样。

### 10.9 已执行证据和下一步

[oracle_capacity_cases.py](../../experiments/reports/workflow_size_join_frontier_20261029/oracle_capacity_cases.py)
对四个固定、非抢占任务枚举每个事件点的全部可行子集及空等，保存所有
约束下的最佳续排和检查的完整安排数量。其已执行输出为
[oracle_capacity_cases.json](../../experiments/reports/workflow_size_join_frontier_20261029/oracle_capacity_cases.json)；
包含三种容量、第一种的两个受约束续排、预测概率门槛及额外反例。
在本例的固定时长、单调增加容量及独立服务假设下，固定任务次序后
尽早可行开工不会损害完成时间，因此事件时点枚举足以覆盖最优安排。
图由
[draw_oracle_cases.py](../../experiments/reports/workflow_size_join_frontier_20261029/draw_oracle_cases.py)
生成，另有
[SVG](../../experiments/figures/workflow_size_join_frontier_20261029/capacity_oracle_cases.svg)。

这些穷举结论只对上述理想模型成立，不证明真实 GPU 收益。下一步应先
在独立 CPU 轨迹模型中检查预测误差、真实变 batch 速率、HBM 字节和迁移
成本下的决策后悔，再决定是否接入；不能把读取完整未来图/工具时长的
离线 oracle 当成在线策略。正式实验仍验收平均/P95 JCT、吞吐、长任务
退化、迁移、重算和整个控制器成本。

## 11. agent 剩余工作量与控制成本细化

本节的 agent 工作分解和低成本更新可作后续参考。当前首版只要求
workflow 剩余模型工作三档，不要求这里所有时间/分布预测头上线。

### 11.1 预测 agent 当前 invocation 的剩余工作

执行选择应使用每个 agent 此刻尚未完成的工作，而非任务初始总工作量。
统计单位为当前串行 invocation，从当前时刻到它实际 RETURN；持久 context
未来被重新唤起是另一个问题。优化目标仍是 workflow 平均 JCT，不能把
所有 agent 直接按各自短长排序后宣称优化了 workflow。

首版分别维护：

| 预测输出 | 含义 | 调度用途 |
| --- | --- | --- |
| `R_current` 的均值/区间 | 当前模型调用尚需的必要 prefill/decode 工作 | 近期服务与完成事件预测 |
| `p_return` | 当前调用结束后，本 invocation 自然 RETURN 的概率 | 区分工具轮次和真正终态 |
| `R_after` 的条件均值/区间 | 当前调用不 RETURN 时，此 agent 后续自己的模型工作 | 多轮任务的剩余工作估计 |
| 当前工具剩余等待分布 | 已观察类别/已等待时间条件下的外部等待 | 与模型工作分开推进时间线 |

对自己的模型工作，有明确分解：

\[
E[R_i\mid x_t]=E[R_{\rm current}\mid x_t]
+(1-p_{\rm return})E[R_{\rm after}\mid x_t,\neg{\rm RETURN}].
\]

该分解用于存在当前调用的状态。工具/JOIN 等待且没有在途模型调用时，
当前调用项为零，直接根据已观察等待状态估计后续自身工作；不能继续
套用上一轮尚未看到工具结果时的 `p_return`。

模型工作以固定校准下的 U 为单位，或保留必要输入、输出 token 及上下文
作为成本表的输入；不把当前请求排队墙钟当成已经消耗的工作。实际 DMA、
恢复造成的额外 prefill 和 batch 服务速度由 runtime 另计。

`R_i` 首先只统计该 agent 自己的调用，不包含 descendant。已观察子树的
服务工作可沿图相加，ALL-JOIN 的等待则按各必要分支完成时刻的最大值组合，
再加 parent 后续工作。不能将已包含 child 的 parent 总量预测再与 child
预测相加。未知的未来 spawn 只能作为尚未展开尾部的不确定性，不能读取
完整未来 DAG 来补齐。

### 11.2 小模型的特征与标签

复用第 3 节的缓存任务文本投影与小型 CPU 模型，分别输出上述短期和后续
工作头，不新增一次大模型调用来估时。静态输入为该 agent 的任务指示、
root/child 角色和已知软工作目标，不包含项目代码。动态输入至少包含：

- 本调用已输出 token、必要输入增量、当前上下文长度。
- 已完成自己的调用/工具轮次、逻辑工作进度及实际模型服务量，三者分开；
  重算属于实际服务消耗，不能当成固有任务进度重复扣减。
- 最近可见输出的阶段特征、实际工具调用/最终报告结构、completion intent。
- 已观察 spawn/JOIN 轮次、未完成/已返回 child 和当前 ready/tool/JOIN 状态。

文本阶段只在新内容块或阶段事件时更新，不每个 token 重新编码全文。
已执行调用数或已花时间不能单独决定剩余工作；它们只是条件信息。
`announce_completion_intent` 是证据，不能直接把 `p_return` 设为 1。

离线标签使用从快照至真实 RETURN 的后续本 agent 固有工作，并将当前
调用剩余部分与后续调用分开。训练可以读取完整记录作标签；在线特征
不能读取 sampler 中的录制输出长度、未来 token、未来 spawn 或隐藏
合成工具计时。取消、截断和执行失败按删失/失败样本处理，不能贴成
零剩余工作或自然终态。

按项目和任务模板留出；同一 invocation 的多个快照不能拆到两侧。
现有 18 项目及相关请求快照不足以证明跨项目泛化。首版应检验短期头
是否有用、完整 invocation 工作量是否具有排序价值及区间是否校准，
不能预先承诺完整 agent 工作可以准确预测到秒。

### 11.3 快速更新不能只是初始总量减去已生成量

若当前调用总输出长度为 L，已输出 n 且尚未结束，使用条件分布：

\[
E[L-n\mid L>n,x_t],
\qquad
P(L-n>r\mid L>n,x_t)=\frac{S(n+r\mid x_t)}{S(n\mid x_t)}.
\]

例如初始 L=100/300 各一半，初始均值 200。已经输出 120 且仍在生成时，
这个例子的真实条件剩余量为 180，不能用 `200-120=80`。
已执行更多 token 有时会排除“短输出”模式，因此条件剩余量不必单调下降。

可将短头的生存曲线及条件残余统计缓存为分桶表；已输出计数变化时只做
表查询/轻量插值，随后在新阶段证据到达时重算分布。模型之外的精确进度
无需每次重新推断。观察已超出模型尾部支持时标记失配并放宽区间，
不能在请求仍运行时将剩余量截成零。

建议事件分工：

| 更新事件 | 同步动作 | 较贵的模型更新 |
| --- | --- | --- |
| 新输出进度 | 读取已有计数，查询缓存条件剩余量 | 新内容块/阶段变化才标记 dirty |
| 新调用/调用完成、工具开始/结束 | 更新身份、计数和实际服务账本 | 合并后刷新该 agent |
| spawn、child RETURN、JOIN 变化 | 更新相关图聚合与 ready 信息 | 状态真正改变的 agent 刷新 |
| 容量释放/真实准入机会 | 读取预测缓存及版本 | 不等待新模型结果 |

初始工程配置可按 64 个新增 token 合并内容，并对同 agent 模型刷新间隔
限制为至少 200ms；这只是待测上限，不是精度或时延保证。高成本更新
放在有预算的 CPU 后台，优先处理当前竞争者，其余保留缓存和 stale 标志。
后台仍消耗 CPU，须计入总成本；不能把“异步”当作“没有开销”。
工具执行或请求排队期间不扣减模型工作，外部等待单独做条件更新。

### 11.4 190 个恢复周期能够说明什么

复核
[已有完整周期审计](../research/demand_restore_readmission_result_20261028_zh.md)
及其
[结构化证据](../../experiments/reports/prefill_pipeline_20261028/periodic_admission_distribution_20261029.json)：

- 最新 ACK Full 的 461 次已观测周期中，271 次零选择、190 次非零。
- 1/2 个的周期为 83/61 次，占非零的 75.7895%。
- 至少 3 个的周期为 46 次，贡献 167/372=44.8925% 的周期恢复选择。
- 非零均值 1.957895，P90=4，最大 6。

这是五秒控制周期返回的逻辑 `_resume` 选择；其中对象可能随后再暂停，
也可能在不同周期反复出现。它不是独立 agent 数、实际 H2D 数、最终驻留
数量、所有事件触发准入或 native batch 大小。它也没有给出候选池大小、
全部被拒对象及排序间距。历史 burst 分布不能自动代表新的 warm-start。

因此适合优先优化一至两个的常见路径，但不能把全局选择硬上限设为 2。
本轮历史最大 6 同样不是系统上限。事后有零选择也不能证明事前无需规划；
应以增量维护的 ready、容量和版本等便宜条件判断是否有决策机会。

### 11.5 缩小热路径，但保留必要的前瞻

首版按实际可增量准入宽度分工，宽度仍由原生槽位、缺失页和输入增长
预算决定，不能直接由“平均选中 1.96”得到：

| 情况 | 首版计算方式 |
| --- | --- |
| 没有新 ready、资源/队列/预测版本及有效期未变，或只有唯一可行推进方式 | 复用原路径/缓存，不运行方案搜索 |
| 当前仅能新增 1 名 | 比较少量第一动作及其尾部安排，保留“先 B、随后并行 A” |
| 当前能新增 2 名 | 比较少量同组、跨组及当前政策的 pair 安排 |
| 能新增更多 | 根据同一完成时间目标逐步补齐，并有限替换尚未恢复的候选 |

常见路径先维护至多四名普通候选，并额外保留实际 ready sibling、1800 秒
兜底已选对象及当前政策方案；不是最多只让四名执行。在线只生成四至八个
具有含义的宏观安排，直接用缓存的工作/完成曲线估算；不为每个候选复制
完整 runtime 状态并展开 8x8 搜索树。完整两层展开保留为离线诊断对照。

“当前只有一个空位”也需要保留未来容量的作用；取消树搜索不等于回退
到当前最短 agent 贪心。第 10 节的先 B/后并行 A 可以作为一个固定宏观
安排，直接代入条件完成曲线及释放概率，不必逐分支复制状态。

每个候选使用缓存的上下文/缺失页摘要及近似成本，同一次决策共享图/
容量快照；最终选中的请求才走完整 native prefix/allocator/锁检查。
缓存绑定 context、epoch、checkpoint 和容量版本；校验失败保留原生
回退，不为了坚持模型选择同步重扫所有替代。真实共享页去重和增长
预留不因预测而弱化。不能将四至八个候选各重复一次完整物理规划。

并列或收益小于误差/控制余量时保持工作集连续性。维持 1800 秒兜底，
只在真实准入/资源/依赖事件重新安排；输出进度仅便宜更新缓存，
不新增逐 decode 步扫描全部 agent、比较集合或持有恢复页的循环。
本轮候选替换仅发生在尚未提交的 paused 恢复方案之间，不替换已有
active。第 12 节给出这个最新范围，旧的联合受害者选择不在当前设计内。

### 11.6 目前尚未证明收益大于开销

R3 运行时与新工作量预测器都尚未实现，没有新增路径的实际 CPU 或
端到端数据。因此目前只能限定工作范围，不能声称已经平衡两者。
“最多 8 个候选”只限制组合数，不限制每次前缀读取、图遍历和分配规划
的成本；这些可能比小 MLP 推断本身更贵。

仅作频率算术：若 461 次周期每次额外 1ms，累计为 0.461 秒；
同样 1ms 若落在十万次 decode 步上，就是 100 秒。两者均不是本方案
的测量，也不是可直接从 JCT 中扣除的时间。真实调用还包括事件触发
入口，不能拿 190 个非零周期替代完整调用总数。

验收应同时给出：

1. 快速路径与有竞争路径的调用次数、独占 thread-CPU/wall 的 P50/P99
   及累计值；后台推断、日志写入和失败规划分别归属，扣除嵌套重复计时。
2. 同一模型/形态 baseline 批次提交间隔下的增量同步预算。可先把 1%
   作为工程目标，例如 10ms 对应 100us；它不是已达到或已验证的参数。
   预算不足回到连续性方案，不能阻塞下一次提交等待预测/搜索完成。
3. 实际提交间隔与计算重叠关系，区分可隐藏的 CPU 工作与真正延迟 GPU
   服务的部分；后台处理也要测 CPU 竞争及预测缓存过期。
4. 排序错误、长任务误判短、剩余区间覆盖与相对离线参考的决策后悔，
   再验收完整平均/P95 JCT、吞吐、重算和迁移量。仅提高预测精度、
   多选恢复对象或减少某个局部函数耗时都不是最终性能验收。

本节是设计细化与既有日志口径复核；未训练新模型、热改冻结运行时、
启动新 GPU 实验或证明净收益。

## 12. 恢复选择的组合建模及作用域修订

下面保留组合优化的研究表达，在线首版以第 13 节的分档启发式代替
预计完成时间优化。原有 decay 和压力处理保持，不冻结整轮 active。

### 12.1 是组合优化，但普通背包不完整

按用户最新明确的范围，问题是带资源、依赖和恢复成本的在线组合选择：

- 从 paused 且实际 ready 的集合中选择哪些 agent 恢复为 active。
- 已有 active 是恢复选择输入；新策略不直接选择受害者，原有 decay
  和后续压力处理仍可改变 active。
- 工具、JOIN、未来安全容量释放及 parent 串行尾部改变完成时间收益。
- 同一 GPU batch 内请求服务速度相关，agent 不等价于独立计算核。
- KV 驻留可跨越工具等待；模型调用结束不等于资源占用区间结束。

经典“有限资源 + precedence + 完成时间总和”提供建模参考，但其机器/
固定处理时长假设不能直接替代动态 batch 与持续 KV 占用。
也不能把每个 child 的完成时间都加进目标，造成 fanout 更大的 workflow
获得额外权重；每个 workflow 的最终完成事件等权计一次。

普通背包默认单项收益可相加，不能表达所有 JOIN 互补收益。以“在某个
窗口内是否解锁 JOIN”的简化收益为例，若 a1/a2 都需要推进才解锁：

```text
g({}) = g({a1}) = g({a2}) = 0
g({a1,a2}) = 1
```

第二名成员的边际收益反而大于第一名，不能未经证明套用次模贪心的近似
保证。该例针对窗口解锁收益，不声称真实整个 JCT 目标总是这个四值函数。
共享页节省、不同 batch 的服务速度和缓存损失也会破坏简单单项加和。

### 12.2 决策变量只有 paused 恢复子集

在同一个观测快照 O_t 下，A_t 为现有 active，P_t 为 paused，候选为
P_t 中满足原恢复条件的对象。选择 S 时将 A_t 视为输入，不增加 active
受害者决策。下式保留为研究目标表达，不要求当前在线算法估计 C_w：

\[
S^*=\arg\min_{S\subseteq P_t^{\rm ready},\,S\in\mathcal F(A_t)}
E\left[\sum_w C_w(A_t,S)\mid O_t\right].
\]

恢复子步骤满足：

```text
active_after_resume = active_before union selected_paused
active_after_tick = original_pressure_handling(active_after_resume)
```

旧版模型中的受害者 V 和保留者 K 不再是变量。局部搜索中的替换也只是在
不同尚未提交的 S 之间替换 paused 候选，不能把已有 active 替换出去。
当前 active 后续因原有压力、完成/退出变化时，在新快照中再作选择。

逻辑预算至少满足：

\[
\sum_{i\in S}b_i\le C_{\rm original\ resume}(A_t),
\]

其中 b_i 沿用 reference 对恢复候选的完整上下文及 buffer，容量沿用
当前原策略；已开启 ACTING decay 时仍使用其恢复容量。撤回额外全额
预算门禁，不为避免原有压力处理暂停旧 owner 而关掉或削弱 decay。
原生页级共享与真实缺失恢复量另做物理检查，不能把物理共享折扣未经
证明地套进 reference 的逻辑 budget。

还须满足原生槽位、输入/chunk 与增长预留、有效 Host 状态及 DMA 依赖。
未来释放只能用于比较后续宏观顺序，不能充当本次已可分配容量；
现有逐层恢复与 native 生命周期仍保留。

新恢复会改变 batch 组成及服务速度，所以目标中仍包含已有 active
所属 workflow 的完成时间，而非只计 S 的收益。恢复、重算和带宽竞争
进入 C_w；已计入完成时间的代价不再重复添加一个任意 KV 字节惩罚项。

### 12.3 如何选择组合优化方法

| 方法 | 可借鉴的部分 | 首版用途及限制 |
| --- | --- | --- |
| 普通/多维背包 | 快照容量内选择对象 | 仅用于可加近似和候选筛选，不替代 JOIN/时间推演 |
| 配置选择 | 将同组 ready 子集作为候选配置 | 能保留互补性，但跨组排队/共享页仍须联合估计 |
| CP-SAT / MILP | 显式编码依赖、资源区间、切换和完成时间 | 离线小快照参考解；报告实际求解状态与 gap |
| 有界枚举 + 局部改进 | 固定 active，探索 paused 恢复子集 | 推荐在线入口，保留可行基线，预算耗尽即返回 |

OR-Tools 的调度示例给出了区间、先后和资源互斥建模，并支持搜索时间
限制。但示例常用 makespan，必须改成 workflow 最终完成时间之和。
一次超时返回可行解不等于证明最优；固定预测和简化 batch 下的最优也
不等于真实 GPU 或未知未来 workflow 的 oracle。

可先对小的事件窗口建立离线 CP-SAT/MILP 参考，比较本地规则漏掉了哪些
组合及损失多少；不必将全局求解器放在每次 native 选批中。
更不能把 GPU 作为一台只能串行执行单请求的机器，丢掉连续批处理行为。

在线固定 A_t，从当前政策可行的 paused 恢复方案 S0 出发，有限比较：

1. 用剩余额度恢复一名 paused ready agent。
2. 恢复可容纳的 paused JOIN sibling 配置，不等待 unready 成员；
   已 active sibling 的进度是给定条件，不再重复授权。
3. 比较不同 workflow 的 paused singleton/pair 子集。
4. 比较另一 paused workflow 先恢复、实际新增容量后再补 sibling 的顺序。
5. 有剩余容量时继续补齐，并有限替换尚未恢复的候选。

只做单成员贪心可能错过 JOIN 互补性，因此邻域至少保留同组组合动作。
当前只容纳一至两个时可对小候选窗口枚举 singleton/pair；窗口内精确
不等于全局精确。较宽场景使用有预算的增量补齐，不能把历史最大 6
或均值 1.96 当成硬限制。S0 始终作为备选；当前真实状态变化会使旧
方案失效时，先按 native 更新得到当前可行基线，而不是保留旧容量。

预估收益须跨过已校准的误差与控制余量，才采用不同的 paused 恢复子集；
并列时保留现有驻留和更少恢复。只提交第一个真实可行动作，下一资源/
依赖事件重算。完整邻域搜索、逐页读取或模型推断仍受第 11 节预算限制。

### 12.4 保留 ThunderAgent 的局部性机制

从当前 reference ThunderWorkingSet 代码确认：一次模型请求结束后，
program 进入 ACTING，但没有被标记暂停时仍保持 active；后续调用不会
因为请求身份改变而自动失去执行资格。容量压力优先暂停 ACTING，
必要时将 REASONING 标记到安全边界再暂停。
作者可选 ACTING decay 又允许较长等待者让出逻辑预算，说明其局部性
也不是永久锁定所有 KV。

本文沿用 ThunderAgent 跨调用连续性，新选择器不增加独立抢占策略：

- agent 完成一次模型调用后，默认延续资格；不要每轮请求结束就将它
  放回所有新请求一起竞争，或因为另一个 workflow 档位降低就暂停它。
- 新增选择使用原策略给定的恢复容量；不修改受害者或暂停函数，但
  恢复后的原有压力检查照常执行，其暂停结果不被新约束阻止。
- 必要的增长/容量压力与 1800 秒兜底仍按原有安全边界处理，不能永久
  锁住所有 active owner，阻止正常容量恢复和其他 workflow 的服务。
- 冷度取决于下一次使用的时间与恢复成本，不能用整个 agent 剩余工作
  量替代；剩余工作很长、频繁续调的 agent 也可以有很热的 KV。
- 恢复与实际服务消费绑定，必要保护有界；不可执行或长期排队的预测
  对象不能无限保留 HBM，也不为等 sibling 凑组而空置已有计算能力。
- 暂时释放请求槽位不等于整个 context 的 KV 可以删除；临时 agent
  的明确终态、持久 context 的实际关闭及 native 引用规则分别处理。

仅承诺“不抢占正在 decode 的请求”不足以实现上述机制，因为跨调用
频繁换人同样可以制造缓存抖动。1800 秒是防饥饿尺度，不是要求每个
program 在 HBM 中至少驻留 1800 秒。

同时必须区分逻辑资格和实际物理驻留：保留 active 不等于 native 页
被永久 pin，也不能保证与 ThunderAgent 完全相同的缓存命中率。
真实恢复集合、输入增长和执行次序改变后，仍需测量重复加载、相同
checkpoint 首次服务前再次丢失、有效前缀复用、重算和完整性能。

### 12.5 最新修订：沿用 ACTING decay 和原压力处理

作者 ACTING decay 及当前对应 reference 路径会先用折扣容量选恢复对象，
再以原始容量检查并暂停 ACTING/标记 REASONING。因此只替换 `_resume`
内的排序，不足以证明新恢复不会通过后续检查挤出旧 active。

用户最新明确要求维持原 Full 的 ACTING decay，只优化 paused→active
选择，不要求阻止 decay 导致的原有 active 暂停。因此上一版追加的
全额 active 预算复核和整轮 active 单调不减约束均撤回。

新选择器调用相同的恢复容量函数、候选资格和后续压力函数；五秒控制、
1800 秒超时兜底、物理容量/锁/DMA 权限保持。它不直接决定 active
受害者，但选择不同大小的 S 仍可能间接改变原压力处理的次数和结果，
不能声称两边局部性或最终 active 集合必定相同。
本次只修订设计，不热改冻结代码或在此运行新实验。

### 12.6 建议及证据边界

组合优化方法可继续作为离线研究工具。当前在线首版改为第 13 节的
**workflow 剩余工作分档 + 原恢复预算内排序/协同**。
这是对现有候选生成/决策的明确建模，不是组合优化形式本身的新颖性声明，
也没有从普通背包或经典 precedence 调度继承近似比保证。

设计对照同时报告受限参考解的差距与完整 JCT/吞吐，不用“更少迁移”
单独证明更好。若保持局部性后只有少量可调整空间，应公开这一限制，
不能为了制造调度机会强行驱逐热 owner。

查阅的原始资料：

- Demirci, Hoffmann, Kim，STACS 2018，
  *Approximation Algorithms for Scheduling with Resource and Precedence Constraints*。
  `https://drops.dagstuhl.de/storage/00lipics/lipics-vol096-stacs2018/LIPIcs.STACS.2018.25/LIPIcs.STACS.2018.25.pdf`
- OR-Tools 官方 Job Shop 与求解限制文档。
  `https://developers.google.com/optimization/scheduling/job_shop`
  `https://developers.google.com/optimization/cp/cp_tasks`
- ThunderAgent 作者调度器说明与实际 router。
  `https://github.com/ThunderAgent-org/ThunderAgent/blob/main/wiki/Scheduler.md`
  `https://github.com/ThunderAgent-org/ThunderAgent/blob/main/ThunderAgent/scheduler/router.py`

本节只修改同一讨论稿，未新增求解器依赖、改动运行策略、训练模型、
切换当前工作集或启动 GPU 实验。

## 13. 当前方案：剩余工作三档与原预算内恢复

本节保留 2026-10-29 的 R4 方案及其旧链接。当前推荐已更新为
[第 14 节的 R5](#r5-conditional-join-admission)，不再将最大概率档位、
类别内相邻排序或逐个扫描当作完整的新设计。

### 13.1 预测标签收敛为 workflow 剩余模型工作

预测未来尚需的固有模型工作 R_w(t)，分 short/medium/long；不预测每个
候选安排的完成秒数。标签包含 root 及此 workflow 后续 descendant
的必要模型工作，每项只计一次，排除排队、工具等待、DMA 和额外重算。
实际在线成本仍受输入长度/批次形态影响，不把标签当成精确时间。

分档阈值仅从训练 workflow 确定后冻结，例如初始总工作训练分位点，
同一阈值用于运行时残余量。长 workflow 临近完成可以降档，初始
short 也可在新证据到达后升档。更宽的档位减少精确数值预测要求，
但不保证分类一定准确或一定改善排序：

- 阈值附近很小的预测偏差仍可跨档；同档内部有大小差异。
- 初始任务指示缺少代码/项目规模时，仍可能没有足够信息。
- 大量样本都进入一个档位时，普通准确率可以很高而排序毫无帮助。
- 模型工作少不等于工具等待短，也不等于 workflow 墙钟 JCT 短。

输入为缓存任务文本投影与已观察动态状态：实际逻辑服务进度、累计
输出/调用/工具轮次、已观察 spawn/JOIN 轮次及 child 数、ready/tool
状态和收尾证据。使用共享小 CPU 模型，每 workflow 一份档位缓存，
所有 descendant 继承；不依 agent 数量给 workflow 额外身份权重。

模型在调用/工具/依赖事件后合并刷新，只刷新受影响 workflow；新输出
按已有内容块合并，避免逐 token 运行文本模型或扫描全图。不确定预测
先回到中档及原策略顺序，并记录弃权/校准状态；高 softmax 不等于
已经校准的置信度。已有 1800 秒兜底仍沿原方法运行。

当前只要求三档分类，暂不要求 agent 多头残余分布、完整分支时长和
安全容量释放概率都准确。训练仍按项目/模板留出，未来轨迹只作标签，
不读取录制输出长度或隐藏工具时长作为在线特征。

### 13.2 恢复规则的最小版本

保留原 paused 候选资格和原恢复预算。首个保守候选保留原有恢复类别
优先级，在同一类别中插入 workflow 剩余工作档位：

```text
resume_key(i) = (
    original_resume_category(i),
    remaining_work_class(workflow(i)),
    original_context_tokens(i),
    original_queue_sequence(i)
)
```

`original_resume_category` 沿用 reference 对续调、新 program 和 ACTING
后备对象的原有区分，不把“预测工作短”当成真实 ready 信号。
类别内 short→medium→long，同档沿用原上下文大小及到达序。
它是对原排序的有限修改，不等于全局 SJF 或严格最优。

组内继续复用真实 ALL-JOIN 的 sibling 共同准入/选批：同档可优先把
能共同容纳的 ready 成员相邻安排，放不下则选子集；只有一个 ready
成员也继续推进。既有队首/久等保护和物理检查保留，不新增全组门禁。
共同逻辑恢复不等于同一个 GPU batch，分别统计最终资格和实际服务。

按原循环扫描排序后的 paused 对象，以原候选大小和恢复 budget 累加；
遇到放不下的跳过并继续填充，不因等待某个 short/sibling 留出未知
容量。保留已开启的 decay，最后调用原有压力处理。无候选完成时间
推演、CP-SAT、8x8 搜索、主动挑选 active victim 或额外全额预算复核。

### 13.3 预期收益与明确代价

这个版本优先服务剩余模型工作更少的 workflow，并在实际资源允许时
共同推进其 JOIN。若 JCT 中模型排队占比较高、工作量有差异、分档具有
排序价值，就有可检验的收益空间；相对于原来的完整时间推演更容易
实现和定位问题，但当前没有性能证据。

工具等待主导、分档没有泛化能力、同档差异很大、KV 需求使排序很少
真正生效，或原其他恢复入口绕过此排序时，收益可能很小或为负。
第 10 节第二种例子也不会因改成三档而消失：A 确实较短，但先 B 仍
可能更好。此处明确接受启发式的局限，不再承诺近似 oracle 的 JCT。

离线先看真实 paused 竞争快照中的工作大小排序、macro-F1、long→short
误判及 runtime 更新价值，不只看所有快照混合后的准确率。再用同工作/
相同 decay 配置验收平均/P95 JCT、吞吐、恢复后服务、迁移和控制成本。

### 13.4 新增决策开销的界限

设 N 为本轮原 paused 候选数。档位来自 O(1) 缓存查询；原 `_resume`
已作 O(N log N) 排序和 O(N) 容量扫描。插入档位键不改变该阶数，
增量 sibling 索引/分桶可以控制为 O(N) 元数据操作，避免重复物理规划。
候选数仍不是日志中选中数 1.96，不能据选中宽度推断 N。

模型推断和文本处理按 workflow 合并在后台，恢复入口不等待；相同
workflow 的多个 child 不重复推断。最终页级准入继续走既有路径，
不为每个备选重读完整 radix prefix。不逐 decode 步全局排序。

因此可从设计上把新增工作约束在缓存查表、少量 key 比较和组内重排，
而非组合搜索树。但这不是已测耗时：原压力循环也可能因 S 不同而
做更多/更少工作，后台推断也会竞争 CPU。仍需独占 thread-CPU/wall、
调用次数、P99 和提交间隔测量，再判断端到端净收益。

本次只更新当前设计和撤回不适用要求；新的分档模型/排序未实现，
没有新增准确率、开销或 GPU 性能结果。

### 13.5 当前算法的架构与恢复算例

![当前 R4 模块架构：工作量分档与原预算内恢复](../../experiments/figures/workflow_size_join_frontier_20261029/scheduling_architecture_r4.png)

此图表示模块与数据流：灰色为已有机制，绿色虚线为待实现的分档与排序，
蓝色为原生物理准入与执行。具体的设计作用由第 13.6 节的对照图解释。

当前方案是 ThunderAgent 上的 workflow 剩余模型工作分档与 agent 恢复
选择。原 active 继续沿既有路径执行；新排序只影响 paused 候选在原恢复
预算中的相对选择。保留原 5 秒控制和既有事件入口，也保留 ACTING decay、
恢复后压力处理及 1800 秒 force-resume。后者是原 pending 超时兜底，
不是每秒持续提高 workflow 优先级的老化函数。

| 环节 | 规则 | 当前状态 |
| --- | --- | --- |
| 剩余工作分类 | 小 CPU 模型结合任务指示与已观察进度，每 workflow 一份 short/medium/long 缓存 | 尚未训练或接入 |
| 恢复选择 | 原类别、剩余工作档位、上下文大小、入队序；原预算扫描，放不下跳过 | 新增分档键尚未接入 |
| sibling 协同 | 真实阻塞 ALL-JOIN；预算允许则共同推进 ready 成员，允许子集、保留单成员推进 | 已有机制，拟沿用 |
| active 与压力 | 新选择器不指定 active 受害者；原 decay、续调和压力循环照常运行 | 已有机制，保持原样 |
| 物理准入与执行 | 原生 KV/依赖/槽位/输入增长检查，通过后才获得 GPU 服务 | 已有机制，保持原样 |

预测单位为完整 workflow，所有 descendant 共享该 workflow 当前档位，
不因多 spawn 获得额外优先级身份。分类的是剩余必要模型工作，排除未来
工具墙钟等待；工作量短不能保证墙钟 JCT 短。档位仅在事件或内容块后合并
更新，恢复热路径只查缓存；不确定时回退中档及原策略顺序。

![R4 恢复选择的具体算例](../../experiments/figures/workflow_size_join_frontier_20261029/recovery_selection_example_r4.png)

本例中全部候选处于同一原恢复类别且已 ready。A 为 short，其同一真实
阻塞 ALL-JOIN 的 a1/a2 各需 3 份预算；B 为 medium，需 4；C 为 long，
需 2。这里的预算是含原 buffer 的抽象逻辑单位，不是实测 HBM 字节。

- 原预算 7 时选 A.a1 和 A.a2，剩余 1，后续对象均放不下。
- 原预算 5 时选 A.a1，跳过放不下的 A.a2 和 B.b1，再选 C.c1 填满。

这展示了短档优先、sibling 有条件共同推进，以及跳过不可容纳对象后
继续填充。它只表示原压力处理前的选择，不保证这些对象在整轮后全部
保持 active，也不保证同 batch 或最优平均 JCT。原类别之间仍沿用原序，
例如另一类别的 short 不会仅凭档位越过本类别的 medium。

两图由
[draw_policy_r4.py](../../experiments/reports/workflow_size_join_frontier_20261029/draw_policy_r4.py)
生成，可分别查看
[架构 SVG](../../experiments/figures/workflow_size_join_frontier_20261029/scheduling_architecture_r4.svg)
和
[算例 SVG](../../experiments/figures/workflow_size_join_frontier_20261029/recovery_selection_example_r4.svg)。
图中新增部分仍属设计；本次没有修改运行时策略、训练模型或启动实验。

### 13.6 论文式设计图：依赖、容量分配与执行顺序

本节用两张具体设计图展示“为什么这样选择、选择如何影响执行”，与上节
模块架构图分别保留。两侧固定相同工作、依赖、原恢复类别和预算；对照侧
仅按上下文大小排序，候选侧使用当前 R4 的 workflow 档位与 sibling 协同。
这是说明性的规则对照，不是 ThunderAgent 实测，也不说明原有其他入口
总会作出图中选择。

第一张图在本文第 1 节展示。A 的 root 已真实阻塞于 ALL-JOIN，a1/a2 均已
ready；两者共享 A 的 short 档位，而不是各自获得一个短作业身份。b/c
上下文更小，但它们所属 workflow 的剩余工作为 medium/long。原 active X
占用 4，本轮还可恢复 6：b/c 各占 2 时，剩下 2 放不下任一占 3 的 child；
先选 a1/a2 则可共同推进 A 的两个必要分支。容量均为含 buffer 的抽象逻辑
单位，不代表连续物理地址或 allocator 碎片。

![R4 设计的时间与容量对照：JOIN 解锁与完成时间](../../experiments/figures/workflow_size_join_frontier_20261029/design_space_time_r4.png)

第二张图的横轴为时间，纵轴为逻辑工作集预算。矩形宽度表示本算例的服务
持续时间，高度表示容量占用，虚线汇聚于 ALL-JOIN 解锁点，再连接 parent
后继。X 是两侧相同的已 active workflow，全程保持 4 份预算并于 50 完成，
一并纳入平均值，不为展示新选择器收益而漏掉背景 workflow。

| workflow | 按上下文大小的完成时刻 | R4 完成时刻 | 差值 |
| --- | ---: | ---: | ---: |
| A | 35 | 10 | -25 |
| B | 20 | 25 | +5 |
| C | 40 | 45 | +5 |
| X（原 active） | 50 | 50 | 0 |
| 四者平均 | 36.25 | 32.50 | -3.75 |

对照侧 a1 在 25 返回，parent 仍需等待 a2 到 30 返回，再从 30 执行至 35。
R4 中两名 child 在 0--5 共同推进，5 时解锁 parent；parent 与 b/c 同时
获得后续预算，A 于 10 完成。两种顺序都没有为新选择暂停 X；所有对象
每个时刻均满足 10 份总预算，恢复时刻也都落在相同的 5 时间单位控制点。

这个例子说明平均完成可以改善而个别长任务被推迟；全体最后完成时刻均
为 50。两侧都假设后继立即提交，恢复和物理准入即时成功，因此图中
parent 紧接 JOIN 执行不代表已消除真实系统的客户端和数据依赖等待。
t=0 是同一调度快照，所报数字是剩余完成时间；在相同过去历史和
到达时刻下，JCT 的差值相同。它没有证明 R4 最优，也没有量化真实收益：
独立服务速率固定，工具等待、DMA、组批干扰、增长与原压力循环的变化
尚未建模。在线排序只读取档位、当前大小和入队序，未来固定服务时长仅
用于推进图中的 CPU 模拟。A 的两名成员在本例中本来就因共享 short 相邻，
因此该图也不能独立归因 sibling 机制的边际收益。

生成器和逐次恢复决策见
[draw_design_mechanism_r4.py](../../experiments/reports/workflow_size_join_frontier_20261029/draw_design_mechanism_r4.py)
及
[算例数据](../../experiments/reports/workflow_size_join_frontier_20261029/design_mechanism_r4.json)；
矢量原图为
[恢复选择 SVG](../../experiments/figures/workflow_size_join_frontier_20261029/design_selection_r4.svg)
和
[时间容量 SVG](../../experiments/figures/workflow_size_join_frontier_20261029/design_space_time_r4.svg)。
本次仅完善设计表达与可复核算例，没有改动运行时或实验。

<a id="r5-conditional-join-admission"></a>

## 14. R5：条件剩余工作分布驱动的 JOIN 组合准入

2026-10-30 按用户要求固化的推荐方案，纳入后续控制开销讨论。
目标是降低完整 workflow 平均 JCT，同时报告 P95 JCT、全程吞吐和长任务
退化。以下是待实现设计，不能作为已上线、精度提升或 GPU 收益的声明。

### 14.1 作用域与执行边界

workflow 共享一份剩余工作预测和评价权重；恢复动作以 agent invocation
为单位。新选择器只从已有机制允许的 ready-paused 对象中选择子集 S，
不主动暂停、撤销或物理驱逐旧 active 来给 S 让路。跨模型调用仍保持
active 的 agent 也属于旧工作集，不因别的 workflow 被判为更短而重竞争。

沿用原 ACTING decay、压力处理及原 1800 秒兜底。恢复子步骤可写成
`A_after_resume = A_before union S`；后续原压力处理仍可能暂停 ACTING 或在
安全边界标记 REASONING，不能把这个等式宣称为整轮 active 不减少。
不添加“旧 active 全额预算再次扣除”的门禁，也不新增强制时间片。

ready 必须来自已提交请求和已满足的真实应用依赖，不能由短任务预测、
预计工具结束或未来 trace 推导。首次到达资格、现有必要保护和 native
allocator、引用/锁、DMA、输入增长与槽位检查保留最终权限。当前 FULL-only
模型不要求不存在的 Mamba 池；其他模型存在必要状态池时分别核算。

### 14.2 条件剩余工作预测

预测目标为当前观察 O_t 条件下，完整 workflow 未来尚需的固有模型工作：

\[
R_w^*(t)=\sum_{\text{尚未完成的逻辑模型工作}}
  c_{\mathrm{ref}}(\text{必要新增输入、输出、上下文}).
\]

标签包括 root、已有 child 和未来实际产生的 descendant，每项只计一次。
剔除排队、工具等待、DMA 和缓存丢失/重试造成的额外重算。离线未来轨迹可
构造标签，线上输入只来自已经观察的前缀。当前
`0.01 * necessary_input + output` 只是逻辑工作代理；改用固定模型/硬件成本表
时，需要重新绑定标签、阈值和模型身份，不能直接称其为 GPU 秒数。

| 项目 | R5 合同 |
| --- | --- |
| 初始输入 | 原任务指示及已声明执行规则；不含项目源码、工具输出或未来执行。 |
| 动态输入 | 已获模型服务、必要输入增量、输出与完成请求数；已观察 spawn/JOIN 轮次、child 数量/状态、嵌套深度及收尾证据。 |
| 模型 | 缓存的轻量语义编码加小型条件分布头；任务文本到达时编码一次。具体编码器、维度和训练量尚待选择。 |
| 输出 | `p_short, p_mid, p_long`、观察版本、更新时间及校准/支持状态；root 与 descendant 共享该 workflow 的结果。 |
| 阈值 | 在训练数据上确定后冻结，同一阈值用于初始量和运行时残余量，不随当前队列重分三等份。 |
| 更新 | 请求完成、spawn、RETURN、JOIN 状态和工具开始/结束标记 dirty；长 decode 按较大输出块合并，初版每 workflow 最多每秒推断一次。 |

直接训练“观察到当前状态且仍未结束时，还剩多少工作”的条件分布，不用
初始均值减去已生成量。新证据可以使残余量升档，工具执行和排队期间不因
墙钟流逝而自动扣减模型工作。长 decode 的输出块阈值须单独冻结，不逐 token
编码文本或扫描全图；RETURN 和 JOIN 的精确状态立即更新，不等模型刷新。

恢复入口只读取不可变预测缓存，不等待后台推理。缺失、过期、校准不支持或
worker 出错时，撤销相应长短偏置；全部预测失效时保留原方案作为回退。
支持状态不能只由最大 softmax 概率决定，高概率错判也要在竞争样本中检查。

随机合成工具时间作为独立外部等待处理，不能从原命令或同 workflow 前一次
工具时长恢复本次隐藏采样值。本头不承诺完整墙钟 JCT。model/tool 关键路径
oracle 是独立诊断，不能将其秒数标签直接当作 R5 的模型工作标签。

训练/校准/评价按完整执行划分，不能拆分同一执行的快照。工程开发可沿用
用户允许的跨独立执行项目/任务重叠，但需声明为开发结果。重点检查真实
准入竞争中的排序、长误判短、概率校准及更新价值，避免晚期 short 快照
抬高总体准确率；更换语义编码器本身不构成精度或泛化证据。

### 14.3 统一选择器，保留入口预算

周期恢复、空余额度补入、sibling 补入拟调用同一个选择器，输入为：

```text
原方案 S0 + 合法候选窗口 + 预测快照 + 最近一层真实 JOIN 索引
          + 本入口逻辑预算 + native 资源摘要 + 状态版本 + 计算截止时间
```

输出为选中集合、参考评分、回退/拒绝原因及版本。统一的是选择逻辑，
不是把所有入口改成同一种预算：周期恢复继续使用原 decay 恢复预算；
空余额度入口使用其原有未折扣预算及授予上限。事件补位不额外触发完整
压力循环，也不放大逻辑容量。首次到达规则及既有必要保护照常运行。

完整 working-set control 初版保留 2 秒；请求就绪、工具返回和容量释放
触发有界补位，500ms 为 R5 的补位兜底设计，不重新切换整套 active。
这个配置属于待验证的 R5，不能写成当前所有 Full/Scope18 对照已经启用。

### 14.4 候选生成与组合

1. 先获得原入口正常执行会产生的 S0，并保存同一预算/状态快照。
2. 默认收集六名 ready-paused agent；存在需要比较的 sibling 组合时，按
   已声明条件扩展到八名，不能自动增长到全队列。
3. 窗口包含原方案入选者、其他竞争者及相关 ready sibling，按原队列轮转，
   避免永久只看队首。必须保护的对象先沿原规则处理并核算占用。
4. 如果无法在窗口内保留原方案及必要候选，本轮使用 S0，不悄悄截断原方案。
   候选发现和 JOIN 查找也必须有界，不能以八人枚举掩盖全图扫描。
5. 比较窗口内所有满足入口上限的子集，连同 S0 一起评价。六人至多 63 个、
   八人至多 255 个非空集合；无竞争、无新机会或原路径能直接容纳全部对象
   时走快速路径，不为搜索而搜索。

组仅来自真实观察到、parent 正在阻塞等待且尚未满足的 ALL-JOIN。
每个候选只归入最近一层 JOIN，不能重复获得多个 ancestor 奖励。
ANY、后台 spawn、推断的未来 JOIN 不形成这种组；其他请求作为单成员组。
未 ready、仍在工具或自身 JOIN 中阻塞的 sibling 不进入 S，不要求整组到齐。
已有 active 且可执行的 sibling 计入给定覆盖，不重复授予资格或申请槽位。

### 14.5 评分与明确的启发式边界

首版在可行集合中最大化：

\[
\operatorname{Score}(S)=
\frac{\sum_w q_w F_w(S)}
     {\epsilon+\widehat C(S)}.
\]

\[
q_w=p_{\rm short}+0.5p_{\rm mid}+0.25p_{\rm long},
\qquad
F_w(S)=\min\left(1,\sum_{g\in G_w}\Delta F_g(S)\right).
\]

不可用预测取消长短偏置，可采用统一中性权重 `q0=0.5`；不能把缺失结果
冒充已确认 short。成本或资源摘要不支持安全比较时返回 S0。系数、中性权重、
epsilon 和近似同分阈值均为待验证策略参数，比较前冻结，不能事后调到获胜。

对组 g：

\[
\Delta F_g(S)=
\left(\frac{a_g+s_g(S)}{n_g}\right)^2-
\left(\frac{a_g}{n_g}\right)^2.
\]

`n_g` 为当前尚未完成的成员数，包含仍被工具/依赖阻塞的成员；
`a_g` 为其中已 active 且当前可执行的成员数，`s_g(S)` 为新增选中数。
非 JOIN 单成员组使用 `n=1,a=0,s in {0,1}`。每 workflow 汇总后封顶，
不把更多 child 直接变成更多评价身份或线性收益。

两个 sibling 均未 active 时，选一个的增量为 0.25，两个一起为 1；
已有一个 active 时，补入另一个的增量为 0.75。该非线性使组内补齐
真正参与集合比较。它度量的是可执行资格覆盖代理，不是已消费的 GPU 服务，
不证明对应 child 会同时完成、未来不调用工具或 JOIN 即将解锁。

`C_hat(S)` 根据合并后的必要缺失 KV、必要 prefill 和固定短窗口 decode
成本查表，反映新 batch 相对已有工作集的增量。共享页只计一次；不能将
每个请求的独立成本简单相加来假装已建模组批。窗口是成本比较范围，
不是执行若干步后强制抢占。成本表及窗口长度须冻结；恢复与计算可能重叠，
该代理不等于完整 JCT，也不等于可以从 JCT 中扣除的传输耗时。

原安全、依赖和 1800 秒兜底保护继续有效。续调、新 program 等历史恢复
类别拟降为近似同分时的稳定偏好，避免它们永久压过工作分布和组合收益。
这是一项显式的 R5 策略变化，须与旧的“类别内排序”独立区分，不冒充
原 ThunderAgent 或当前 oracle 已有行为。同分时优先保留 S0 和既有局部性。

### 14.6 容量、恢复与真实执行

组合可行性同时检查入口逻辑预算、剩余请求槽位、prefill/chunk 输入、
必要缺失 FULL 页和输入/decode 增长预留。存在必要状态池时另行核算。
合法可回收的冷 Host-backed 状态可以进入资源摘要，不能只看 allocator
当前空闲页，也不能把所有可驱逐页当作无代价、无保护的空闲容量。

每候选每轮最多获取一次带身份/版本的前缀、缺失页和恢复成本摘要；
组合枚举只复用这些摘要。不要对 255 个集合分别遍历 radix、构造完整
物理计划、执行 native admission 或触发 DMA。候选发现/摘要构造的时间
单独记录，不能从控制成本中省略。

最终方案仍重验 request/context/epoch、Host 副本、页引用、在途依赖和
原生容量。摘要过期或 native 拒绝时有限重选或回到正常 native 路径，
不在同一调度步无界重试，不为等某个 sibling 空置已有服务能力。
授予后按原入口继续压力处理；记录原压力撤销和实际服务，不能只计 grant。
共同逻辑准入不保证同一 prefill batch，更不保证持续并行。

已有 JOIN/EOS、备份及原生需求恢复按当前配置继续运行。R5 文档不重新开启
用户关闭的工具/普通 frontier 预取、physical layer handoff 或 mixed chunk，
也不将需求恢复计入边界前预测 H2D。

### 14.7 开销预算与失效路径

| 项目 | R5 要求 |
| --- | --- |
| 搜索规模 | 默认六人/63 集合，必要时八人/255 集合，原方案始终保留。 |
| 评分计算 | 缓存概率、组索引、单候选资源/成本，增量聚合和查表；不运行全 DAG rollout。 |
| 触发频率 | 依赖/请求/容量等相关事件合并触发；缓存命中或无准入机会立即返回，不逐 decode 全局重排。 |
| 同步预算 | 新增组合选择 P99 初始目标不超过 0.5ms；循环分段检查软截止时间，超预算使用已知可行方案或 S0。 |
| 物理检查 | 对最终方案走原生重验，不把一次可能很贵的 native 调用藏在计时范围之外再宣称整轮有界。 |
| 后台处理 | 编码、条件分布推理、队列处理和日志仍消耗 CPU/GIL，统计累计开销与服务干扰。 |
| 日志 | 保存候选/版本/预算/成本表身份、S0/S 和拒绝原因、耗时及服务关联；中间集合离线重建，避免逐集合重复编码。 |

0.5ms 是新增组合计算的软预算和待测 P99 目标，不是包含候选发现、页级
检查和原压力处理在内的整轮最大耗时，也不是 Python 能硬中断 native
调用的保证。不能用已完成的八人排序微基准证明组合选择满足此目标。
会话内合成缓存元数据原型只提供量级参考，正式入口仍需来源绑定测量。

计划缓存绑定请求/epoch、预测、JOIN、资源摘要等版本；有效变化后重新评价。
不能因每步 decode 的细粒度空闲计数变化而每步搜索，也不能跳过执行前
准确的原生检查。没有有效缓存或摘要无法保守判定时走原路径。
总成本按“每次耗时乘实际调用次数”验收，不能只公开单次微秒数。

### 14.8 验收链与已知限制

依次确认：实际选中集合改变、授予在原压力处理后保留、同 attempt 后续
真实服务、sibling 分支服务覆盖/等待改善，最后才看完整 workflow JCT。
排序变更次数、grant、ACK 和 H2D 字节都不能单独作为收益证据。

至少报告以下指标：

- 真实竞争快照上的概率校准、pair 排序、long→short 错误及更新前后价值。
- 各入口 S 相对 S0 的集合变化、原压力撤销、native 拒绝和最终服务次数。
- sibling 已有 active/新增/真实服务的区分，同批与跨批并行分别核验。
- 同工作量平均/P95 JCT、完整尾部、输出吞吐及长任务退化。
- 恢复就绪到服务、重复恢复、有用重算、原生/预测迁移和实际缓存消费。
- 前台 CPU/wall 的 P50/P99/累计值、候选与组合次数、缓存命中/超时、
  后台 CPU/GIL 干扰及日志成本，避免嵌套重复计时。

工作量少不保证外部工具等待短；覆盖平方奖励不能识别未知的真正关键
child；有限窗口可能漏掉更好的跨组安排。现有 Scope18 的 97 次 JOIN
只有 19 次双成员，且没有嵌套，因此 sibling 组合的可用空间仍有限。
该方案是可解释、可验证的启发式，不承诺最优平均 JCT 或任意误预测下的保证。

比较时绑定完整轨迹、到达、工具 overlay、容量、种子、数据面、诊断开关
和控制/补位周期。若非排序设置同时变化，不能把整轮差值单独归因于预测
或组合选择；改变工作负载后不能复用旧 V230 数值作为匹配性能对照。

### 14.9 实现状态与下一步

可复用现有后台预测缓存、真实 JOIN 索引、参考恢复预算、原生容量/服务
关联和独立实验审计。R4 当前主要使用最大概率 bucket、原类别优先及组内
相邻扫描；它们不等于本节的分布评分、非线性组合和统一选择器。

下一版需完成：条件分布训练/校准与事件更新、跨入口统一选择、资源/成本
摘要缓存、软预算/回退及对应决策和服务审计。先验证真实竞争中的选择价值
及端到端控制成本，再冻结匹配候选比较。工具感知 oracle 的独立准备和
现有冻结实验保持各自身份，不由这次文档更新改写。
