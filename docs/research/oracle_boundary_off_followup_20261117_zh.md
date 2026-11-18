# 工具边界抢占关闭与 Oracle 后续降本

本记录落实用户的新顺序：完成当前 Oracle 后继续既有 D3 四组实验，
若 Oracle 无明显净收益，后续关闭额外工具边界抢占，保留普通预测式
工具卸载；在 D3 运行期间继续以 CPU 检查和既有日志定位 Oracle 问题。
不插入一次新的 GPU 消融，也不热改当前或待启动的绑定源。

## 当前完整测量结果

注册为 `workflow_original133_host400_mean60_simple_path_oracle_20261117_r4`。
133 个原项目、Host 400 GB、20 个初始 workflow、平均 60 秒到达、
fixed18 完整自然 JCT 均保持原定义，客户端读取超时为 3600 秒。
实际停止按 fixed18 完成触发，背景任务按原协议删失。

| 指标 | 匹配 Full | 当前 Oracle |
| --- | ---: | ---: |
| 完整测量 workflow 数 | 18 | 18 |
| 精确测量请求数 | 2991 | 2991 |
| 测量输出 token 数 | 362157 | 362157 |
| 平均完整 JCT，秒 | 1274.833468 | 1276.770573 |
| 共同窗口输出 token/s | 342.209295 | 333.094148 |

平均 JCT 回退 0.151950%，共同窗口输出吞吐下降 2.663618%。
11 条测量 workflow 改善、7 条回退；不能删掉长尾后重新定义均值。
Oracle 的 `cohort_termination/completion.json` 已确认完整测量原生工作、
fresh idle drain 和实际 owned 退出。回放日志的 KeyboardInterrupt 对应
测量完成后停止背景的既定动作，原失败标记保留，由独立完成证书分类。
协调器已完成完整比较并实际退出，后续只读诊断已开始；
D3 已接续到新匹配 campaign，不因 Oracle 性能未达标而阻止。

共同窗口已有证据见 `simple_path_oracle_live_20261117_zh.md`：
工具替换检查线程 CPU 为 Full 约 24.833 秒、Oracle 约 43.345 秒；
平均 decode batch 为 15.514 和 14.996，每批 worker 区间基本不变。
这是控制成本增加和服务组合变化的证据，不能把嵌套计数相加，
也不能直接换算为可移除的 JCT。

历史 83 项目 Oracle 的注册代码为 `631911fb`，本轮为 `61a55c95`。
普通排序已回到含模型与工具等待的剩余关键路径、贪心装入；
仍有必要的后续修复、事件路径 sibling 相邻处理、独立执行兜底，
且任务人口和到达不同。因此，关闭额外抢占是当前负结果下的保留决策，
不能把历史差异严格归因于工具边界机制一个变量。

## 后续默认

`prepare_simple_path_oracle.py` 默认不再写入
`tool_boundary_preemption`；只有显式传入 `--tool-boundary-preemption`
才重建该历史候选。关闭时直接删除整个字段，构造器不会实例化
`ToolBoundaryPreemption`。当前已运行的配置和证据不重写。

只关闭额外的工具边界抢占，继续保留：

- 普通预测式工具卸载、prepare_host 和实际消费者配对。
- 剩余模型与工具关键路径排序、原两秒普通窗口、active 连续性。
- 活体 JOIN/EOS 恢复、ready sibling 支持和原生容量、锁与 ACK 权限。
- 独立 1800 秒连续 ready 无实际服务兜底，客户端 3600 秒读取超时。

禁用用例分别验证长工具预测允许时仍能原生释放并准入短候选，
短工具预测不足时拒绝额外抢占。两个配置的工作量 artifact 除删除
`tool_boundary_preemption` 外完全一致，重复准备也不能继承旧字段。

## 无效排序降本

普通工具替换原先在 donor 资格检查前查询并排序消费者，即使同一有界
donor 窗口没有任何合法对象，仍支付 Oracle 路径查询或预测读出成本。
现将原有逻辑容量筛查与 donor 预检查放到排序前，只在存在可行对象时
调用原排序函数；同一次调用复用预检查，下一次尝试重新检查。

额外抢占显式开启时保持它原有的选择顺序。普通路径实际释放、备份、
授权和授予前仍使用原生复核；工具在排序或消费者检查期间返回，
不得凭预检查继续卸载。没有新增等待时间、老化分数、容量信用、
候选上限或迁移策略。

`order_prefilter_observed_r1.xml` 记录修复前的 7 项失败、1 项通过：
不合格 donor 和不可能的逻辑配对仍会调用排序。修复后的相关回归见
`experiments/reports/terminal_service_integration_20261117/` 中的
`order_prefilter_related_cpu_r2.xml`：最终 413 项实际执行全部通过，
无失败或跳过，16 条环境警告保留。范围包括默认关闭、历史显式开启、
普通工具替换、JOIN/sibling、原生回调、执行兜底与 D3 协作。
初次 412 项通过报告、缺失测试文件导致未执行的命令和修复前报告均保留；
不将各次执行相加成一次更大的验证。这是独立候选的 CPU 证据，
当前 GPU 结果不包含此改动，也不据此声称整轮 JCT 已改善。

后续优先检查实际选择到服务的传递、短长候选的 KV 组合及 decode 宽度、
重复替换和恢复成本。增删机制应有具体机会与成本证据，不恢复连续老化、
组合效用加总、JOIN 覆盖评分或主动积累容量。

## 完整服务审计与观测修复

Oracle 完成后，原只读周期审计记录 489 次跨 workflow 逻辑容量竞争；
全局 Oracle 局部反事实重放改变 258 次集合，290 个新选请求均保留资格、
获得精确关联的服务，270 个在下一次控制或同 agent 干预前获得有效服务。
这证明选择可以传递到实际服务，不能直接证明替代轨迹的完整 JCT。
完整输出保留在原 Oracle 的 `complete_diagnostics_r2/`。

原事件准入审计严格拒绝一条 JOIN 恢复 choice，原退出 1 与日志保留。
独立诊断 `unmatched_free_choice_r2.json` 完整封存 190511 条原 journal：
失败位于第 72649 条，实际授予使用 data_lake parent 后继请求、
epoch 15、session generation 11；choice 的 selected key 却仍为该 owner
旧请求、epoch 14、相同 session generation。原逻辑授予使用显式
`grant_keys`，观测函数没有接收该映射。不能放宽身份匹配后把它当作
已验证的实际 ready 竞争，也不重写原始数据。

后续 producer 将同一 `grant_keys` 交给 choice 观测，selected 身份与实际
授予一致；尚未原生可见的新请求继续记为 warming，不取得 ready 标签。
原生许可、实际选择、latest、物理恢复与容量计算均沿用原逻辑。
新增用例验证新 key 原生不可见、latest 仍为旧 key 时，严格审计可以
绑定精确授予，同时将其排除出 ready 无保护竞争。
`join_choice_identity_cpu_r1.xml` 的 181 项相关用例实际全部通过，
无失败或跳过，16 条环境警告保留；这是单独的观测修复检查，
不与前述 413 项相加，也不代表历史 journal 已重新通过全部事件审计。
