# 最新73项目的短Workflow Oracle机会与NUMA控制

本次仅执行CPU审计，未启动GPU、修改在线调度或移动物理页面。
使用已完成的background-substitution Full及原固定18测量组，
不更改73个原项目、到达表、工具样本、token、JOIN或截断协议。

结论：存在局部短workflow恢复机会，但当前证据不足以预期明显的平均JCT收益。
不建议把原样剩余路径排序作为下一项优先GPU优化。
应先修正NUMA实验控制，再决定是否进行最多一轮纯排序诊断。
这不是否定全部大小感知调度，也不是全系统最优调度收益的上界。

## 周期恢复入口的实际机会

原非排序Full已通过11项workload诊断检查。
人口模型工作量P80/P20=2.934261，保留用户放宽后的最低值，
未重新要求大于等于3。负载通过不代表短任务优先一定有益。

复用源码未变的两个审计器，重新核对冻结配置、完整原始journal、
固定测量组完成、新鲜drain和实际owned退出：

- `scripts/audit_control_oracle_opportunities.py`
- `scripts/audit_control_oracle_service_order.py`

证据位于
`experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/`
`original73_background_substitution_full_review_r1/`：

- `oracle_opportunity_screen_r1.json`
- `oracle_service_order_screen_r1.json`
- `oracle_revisit_assessment_r1.json`
- `oracle_free_choice_screen_r1.json`

| 相同真实边界上的局部重放 | 完整观察跨度 | 固定共同吞吐窗口 |
| --- | ---: | ---: |
| 实际周期控制 | 1,805 | 772 |
| 跨workflow、单独放得下而不能全部放下 | 27 | 7 |
| 拟议oracle改变候选顺序 | 272 | 105 |
| 拟议oracle改变最终恢复集合 | 15 | 6 |
| 竞争时拟议剩余路径比值至少2 | 25 | 7 |

Oracle重放保留实际候选、预算、100-token buffer、
ready JOIN保护和已观察的sibling邻接。
剩余路径分数来自控制日志中的模型/工具关键路径代理；
知道未来图和工具时间不意味着知道反事实下精确GPU服务成本或最优JCT。
本次没有模拟后续状态变化、额外迁移或物理分配。

15次集合变化中，4次减少所选agent数。
例如原集合选择35,767和41,054 token的两个agent，
剩余路径代理为224.16和391.24秒；
排序后只选择66,741 token、剩余路径145.05秒的一个agent。
它是更短的单体，但原两个对象的并行推进被推迟，
不能仅用单体剩余路径较短证明平均JCT降低。
上述例子的三个workflow都不属于测量组。

全部新增对象去重后为8个agent/8个workflow，
其中仅2个属于固定18测量组。
按原始request/context/attempt/session等八元身份进一步核对，
新增对象涉及11个不同原生请求，其中测量组3个请求。
它们从拟议决策到原运行下一次新发起的实际服务，
去重后每请求最大等待为30.908、24.669、39.893秒，合计95.470秒。
这些是原执行中的等待暴露量，既不能相加为可消除JCT，
也不构成全局oracle收益上界；背景选择还可能改变后续状态。
测量组身份只用于离线诊断，未提供给调度器。

同集合但顺序改变的169次观察中，156次能在下一控制或已记录干预前
确认所有相关workflow的实际服务；其余13次保持未知。
156次中130次已经同一服务波次推进，142次最短workflow已在首个波次；
只有14次最短workflow较晚获得服务，最大额外间隔837.475毫秒。
这些观察不是独立随机机会，不能累加为JCT收益。

## 事件入口的证据边界

免费准入审计核对12条竞争grant记录、12个去重决策：
5条存在受保护JOIN，5条是尚未应用就绪的warming目标，
只有2条属于无JOIN保护且已ready的普通竞争。
12条全部位于共同吞吐窗口之后；共同窗口内该观察器未记录竞争。
不能为短任务排序取消JOIN/EOS保护以扩大这个数量。

原最后一个runtime状态没有final标记，因此审计器的instrumentation
仍为`observed_live_prefix`，不是已核对最终累计计数。
`--complete`确认的是固定测量组协议完成、封存journal及实际退出，
不把缺失的final状态补成存在。
该观察器还有候选窗口边界，未覆盖所有带donor的替换和压力暂停。
完整跨度记录117次预测工具替换与39次压力选择，
本次不把它们假定为117或39次可由oracle改善的竞争。

## Host池没有全部固定在NUMA1

`nvidia-smi topo -m`和两臂原现场证据均确认GPU0本地节点为NUMA1。
两臂原Host池都为200,000,077,824 bytes，CPU允许列表也相同，
但实际采用`prefer:1`：

| 物理页面位置 | Full | ThunderAgent |
| --- | ---: | ---: |
| NUMA1 | 152,791,285,760 bytes | 195,563,122,688 bytes |
| NUMA0 | 47,208,792,064 bytes | 4,436,955,136 bytes |
| 远端比例 | 23.6044% | 2.2185% |

原证据分别为同父目录下
`original73_background_substitution_full_live_r1/host_numa.json`和
`original73_background_substitution_thunder_live_r1/host_numa.json`。
这回答的是实际页面位置，不是按DMA实际字节加权的远端传输比例。
尚未测量它对本次JCT的因果成本，不能直接对已有收益作数值修正，
也不能保证修正后收益只会增大。

具体实现缺口：

- `scripts/run_causal_comparison.py`的环境构造直接写入
  `HOST_NUMA_MEMORY_POLICY=preferred`。
- `scripts/launch_qwen35_native_v0520.sh`本身支持`bind`，
  但仅设置外层环境变量无法绕过上述回放入口的覆盖。
- 旧position入口的Host容量预检也直接使用`preferred`。

Linux NUMA策略资料：
`https://docs.kernel.org/admin-guide/mm/numa_memory_policy.html`。
Preferred允许落到其他节点；单节点Bind约束后续分配范围。
因此后续应同时控制CPU及Host池分配策略，在新池实际分配、触碰和注册后，
按完整Host共享映射检查`numa_maps`，再开始测量。
严格本地分配不足时应保留失败或等待可用容量，不能默默改回preferred。
不要在原已完成对照中修改策略或重标实际页位置。

## 可回收缓存与容量预检

本机只读快照显示NUMA1约132 GiB空闲，
Active(file)与Inactive(file)合计约61 GiB，
另有约38 GiB Shmem；不能把全部FilePages都当成可回收文件缓存。
这些是瞬时状态，既不是历史分配失败的原因证明，
也不是200GB严格本地池当前必能分配的证明。

当前`preflight_qwen35_host_pool.py`已排除Shmem、Dirty、Writeback，
计入全部clean inactive file、25% active file和50%可回收slab。
只读`--memory-policy bind --node 1 --size-gb 200`预检返回：
要求194.26 GiB（200GB池加8GiB预留），保守估计159.37 GiB。
它不是一次真实分配失败，也不能由此推断必须扩充内存或跨NUMA。
内核可能回收更多活跃文件缓存，但是否足够仍需实际严格分配核验。
Linux VM说明：`https://docs.kernel.org/admin-guide/sysctl/vm.html`。

## 后续比较要求

若再进行一次oracle诊断，应先有相同严格本地放置条件下的Full对照，
保留同workload、到达、工具样本和固定18完整JCT。
只切换已声明的全局剩余路径排序，保留active连续性与JOIN/EOS，
不增加人为等待或抢占来制造机会，不同时切换MoE或组合评分。
按既有诊断目标观察至少5%的平均JCT改善及不低于98%的共同窗口吞吐，
同时核对长尾、实际执行集合、decode宽度和迁移。
达不到时保留负结果并结束原样排序试验；本次不登记GPU后继。
