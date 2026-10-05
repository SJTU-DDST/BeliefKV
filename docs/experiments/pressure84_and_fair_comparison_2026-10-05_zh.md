# 84-root 压力探索与公平对照

## 当前配置

2026-10-05 用户批准将下一轮扩大到 84 个 root，单波提交；
server running 仍为48，Host仍为200 GB、NUMA node 1，HBM
FULL/Mamba和Host比例不变。不改为64+20延时到达，不人工驱逐，
不取消自然语言终态，不新增agent guard或canary。

使用相同manifest前84项、模型、prompt、seed、工具环境、deadline、
graph、原生缓存和完成通知设置，两侧独立冷启动。备份和压力回收
规则共享，只有predictive开启提前H2D；完整算法与纯native baseline
的消融需要另外列出，不能混称为同一个baseline。

v4原生恢复量不是不到10 GB：predictive侧未标记为预测动作的
原生H2D为121个batch、15.832 GB；reactive为94个batch、12.288 GB。
这些是FULL/Mamba合计的控制器payload，不是独立agent或请求数。
84-root是用户指定的压力探索，不预设增压必然增加有效收益。
记录Host churn、失效副本、有效恢复、HBM冷热空间与可归因重算，
同时保留归因probe溢出情况；池填满不等于有用状态满池。

下一pair采用reactive先、predictive后，与v4顺序相反。
`SAMPLING_SEED`、`REPETITION_ID`及实际源码/权重SHA进入启动计划。
当前仍temperature=0、seed=21；一次pair只作探索，不报告为
无轨迹差异的系统因果收益或正式论文最终结果。

## 为什么固定seed不够

当前已经是greedy生成。调度会改变batch、请求执行先后和共享
workspace的修改/读取次序；固定seed不会冻结工具结果、自然终态
或完整请求序列。SGLang有batch-invariant确定性模式，但当前
Qwen3.5的FlashInfer GDN prefill路径不支持与该模式组合，需换
Triton等执行设置。不得只开启一侧或把换内核后的结果拼到旧baseline。
相关限制以当前pinned源码的gdn_backend.py及deterministic hooks
为准；是否覆盖全部MoE/状态缓存路径也需验证，不能凭一个开关
宣称整个agent workflow确定性。

## 当前实验要求

用户在本轮讨论后进一步明确：改进阶段只执行当前一个84-root
pair，不排额外重复；正式实验再多轮取平均。固定需求GPU回放
不作为前置条件，也不继续投入主线实现。下文回放内容仅保留为
已讨论的可选设计，不覆盖这一最新要求。

### 固定逻辑需求回放（可选、暂停主线投入）

预先冻结一批来自独立workflow的逻辑轨迹，包括请求的prompt/
输出token、工具结果及外部执行时间、spawn/JOIN依赖和上下文转换。
两侧复现相同逻辑需求，但GPU排队、batch、Host/device驻留、
eviction和transfer必须由各自真实策略重新执行，不能按原始墙钟
时间强行发请求或重放原始physical free-list。

相同prompt和max_tokens不够：输出token、EOS与MoE路由若不同，
后续前缀和工作量仍会漂移。需要真实服务中的强制输出/teacher
forcing等兼容适配，不用sleep冒充GPU执行，也不把HTTP mock
的结果称为端到端serving收益。外部工具的冻结时间只作为受控
需求，真实工具竞争另在live实验中评估。

采集多套冻结轨迹，来源覆盖不同项目和两种live策略，不按效果
选最有利的一套。预测器只能看到当前已送达的正文/token/通知，
不能访问未来RETURN、完整输出或oracle标签。历史oracle GPU
replay和只读policy replay不能直接当作当前Qwen3.5完整迁移后的
基准；该固定逻辑需求GPU适配尚未实现。

主指标为相同需求下makespan、workflow JCT、实际暴露的恢复等待、
首次FULL/Mamba复用和重算。ACK和提前量作机制解释，不代替收益。

### 真实agent的多轮配对比较（仅后续正式实验）

先固定任务集合、到达、池、模型/runtime和预算；每pair两侧fresh
workspace和cache，禁止中途换权重、prompt或代码。预先规划至少
四个独立pair，顺序R-P / P-R / R-P / P-R，同一pair参数相同。
重复当前temperature=0实验时，换seed不代表增加独立生成样本；
独立运行和顺序平衡才是这里的重复单位。

报告每pair原始完成率、makespan、吞吐和全任务JCT分布，再汇总
run-level变化及不确定性。存在共享GPU/工具竞争时，不能把84个
workflow当作84次独立整轮实验来缩窄吞吐置信区间。若方差仍与
收益同量级，增加预先约定的重复或明确报告未能检出稳定收益。
不只保留两侧都完成的任务，不删除轨迹分歧大的任务；超时/
取消/预算收尾按censor或intervention报告。

逐workflow列出LLM/工具调用、prompt/output token、children/
JOIN rounds和首次观察到的请求指纹/结果元数据分歧。提交顺序
改变不一定意味着语义改变，元数据相等也不证明逐token相等。
这些用于解释工作量与路径变化，不用于事后除以token数“修正”
JCT，或筛选更容易显示收益的matched subset。

工具/输出token吞吐及单请求等待可作辅助指标，不取代workflow
完成、任务质量和长尾。共享workspace的读写竞争须单独诊断；
不为强行保持轨迹一致而固定GPU调度，否则会改变研究对象。

## 当前已落地的部分

两层启动器已允许84-root单波，旧的>64必须两波规则不再适用于
本次用户批准的84-root。pair初始化校验manifest足够，记录实际
84个task，不静默截短。报告新增realized workload balance以及
全workflow请求序列诊断，保持原始吞吐/JCT不变。

当前开发只执行现有单pair。正式多轮汇总留待主线稳定，具体
重复数届时预先冻结；本轮计划中的四pair建议字段不是执行队列。
GPU replay和batch-invariant新内核属于可选研究，不是当前阻塞。
