# R5 工具保留档内的压力工作量 Oracle

## 为什么检查这个入口

当前 83 项目、400GB Host、20 初始、平均 70 秒到达及固定 18 个测量成员不变。
上一轮连续模型工作真值 Oracle 平均 JCT 为 1041.991464 秒，相对重复轮
非排序 Full 改善 1.018541%，尚不能证明可重复收益或学习 R5 的同等收益。

后续 CPU 重放发现，强制最短可行 workflow 只改变 14 个集合，其中新进入
替代集合的测量对象只有 4 个请求、3 个 workflow，历史服务等待区间并集
16.414 秒，另有 6 次减少准入 agent 数量。因此不为该候选新增 GPU 实验。
详见 `r5_shortest_frontier_followup_20261108_zh.md`。

另一个明确的范围差异是：当前 R5 条件分布及同选择器真值模式的
`_pressure_preference` 返回 `None`，工作量只用于恢复和组合准入。旧全局
Oracle 也会把工作量用于压力暂停：

| 完整历史运行 | 压力动作 | 使用工作量偏好 | 与记录中的参考对象不同 |
| --- | ---: | ---: | ---: |
| 连续模型工作真值，本轮 | 134 | 0 | 未在本项据此归因 |
| 旧全局 Oracle，第一轮 | 147 | 147 | 109 |
| 旧全局 Oracle，重复轮 | 116 | 116 | 78 |

当前 134 次中，124 次直接暂停安全阻塞对象、10 次仅标记后续边界暂停。
旧日志中的 reference 仍是原上下文优先对象，不是同工具保留档内禁用工作量
的完整反事实；差异含工具权重和其他历史变化，不能将 109/78 次都算成工作量
纠错，也不能据此解释旧 Oracle 的全部 JCT 收益。

## 显式候选

新增独立、不可部署的 artifact 类型：

```text
workflow_continuous_model_work_pressure_oracle_v1
admission_order = r5_combination_and_tool_band_pressure
pressure_work_order = remaining_model_work_within_tool_retention_bands_v1
```

旧三档、连续真值和旧关键路径 artifact 拒绝夹带这个字段，默认行为不变。
构造器还要求原来的 `workflow_tool_aware` 策略，不能替换成忽略工具权重的
全局暂停排序。

候选只扩展现有 D2 的压力选择范围：

1. 仍先按原工具保留档处理安全阻塞对象，保留短工具宽限、工具返回预测、
   未知工具时间的历史退避及 JOIN/恢复保护。
2. 在同一个可选档内，优先暂停剩余模型工作较长的 workflow；相同工作量
   沿用现有已知偏好路径的 KV 大小和队列次序作为平局规则。
3. 有计算状态的请求仅标记安全边界暂停，标记不产生实际容量。
4. 不改变原组合效用、六/八候选、0.5ms 预算、工具 A→B 顺序、JOIN/EOS、
   prepare_host、agent 连续性和 sibling 行为，不新增周期或 KV 遍历。
5. 继续关闭工具预取和逐层 handoff。

数据范围与使用范围分开声明：`model_work_only=true` 表示没有读取未来工具
路径；新模式的 `combination_only=false` 明确说明工作量也用于压力选择。
真值仍按观察到的完成及每 128 输出 token 进度更新，不提前释放工具依赖。

新模式记录压力现场的完整安全候选、工具状态、工作量优先值、同档参考对象
及 request/context/attempt/session key。这样下一次能够直接比较同档内
工作量排序的选择，避免继续只与原始跨档 reference 比较。

## CPU 检查和准备

首次相关检查为 183 项通过、7 项失败：新模式在挂载前访问尚未创建的 policy，
另有夹具未提供无关 agent 的状态。初始化已移到挂载阶段，夹具保留无关对象
的阻塞保护；原失败报告不改写。修正后 190 项通过，加入准备边界、真实控制
日志和更广共享路径后，551 项通过；再补充普通准入继续使用原连续权重的
回归检查，最终 552 项通过，无失败、错误或跳过。

检查覆盖默认模式保持原选择、同档长工作量暂停、短工具保留、保护/不安全对象、
未知工作量回退、计算请求只标记不释放、实际控制的完整候选 key、未来工具路径
禁止访问，以及准备器保留全部请求和图结构。552 项包含先前相关检查，不相加
声称更多独立覆盖。

实际准备保留 83 个 workflow、12743 个完整原始请求。与已完成连续真值相比：

- 环境仅改变模型路径及 SHA256 的两套别名，共 4 个字段。
- artifact 仅改变类型、准入范围和压力排序声明，共 3 个字段。
- 请求、workflow 工作总量、图延迟、成本配置与训练标尺 19144.06 逐值相同。
- 原冻结到达表和固定 18 人口重新核对通过；正式登记仍须绑定这些同一输入。
- 未放行或部署连续学习模型，其原 `decision_eligible=false` 保持。

独立 CPU 启动检查最初绕过正式启动器的环境转换，缺少
`BELIEFKV_ONLINE_TOOL_TIMING`，正确触发“工具先验必须配在线计时”保护。
单独合并原配置仍未完成该转换，两份失败记录保留。随后使用真实的
`experiment_reference` 和 `environment` 入口生成完整设置，实际独立构造器
与挂载后状态均通过。没有关闭先验保护或修改服务配置来绕过失败。

挂载时的 Device 容量对象是 CPU stand-in，只证明构造和声明，不是 GPU
池分配或传输资格。原生引擎和物理数据面没有因本项改变。

证据位于同一比较组：

```text
continuous_pressure_oracle_20261108_r1/
  focused_cpu_r1.xml
  focused_cpu_r2.xml
  focused_cpu_r3.xml
  focused_cpu_r4.xml
  actual_constructor_failure_r1.json
  actual_constructor_failure_r2.json
  actual_constructor_r1.json
  preparation_r1/
    oracle.json
    full.json
    preparation.json
    input_parity_r1.json
```

同时修正后续启动登记的 rank 字段：此前连续真值被统一写为 three-bin，
实际运行及结果报告使用的是连续值。现在登记和实际提供器共用 artifact 的
rank 定义，新压力模式也有独立名称。没有覆写历史登记、日志或性能结果。

## 尚未完成

本项尚未登记或执行 GPU，没有新 JCT、吞吐或迁移收益。现有局部检查和 CPU
构造器不能替代完整源码绑定；正式启动前还须完成保留旧测试身份的完整绑定、
干净冻结及对应的固定测量收尾/比较入口，再检查 GPU、hold、端口和输入。
完整收尾仍要求 18 个测量成员自然完成、请求守恒、新鲜 drain 和实际退出，
之后停止非测量背景。

比较应复用已完成 Full、学习 R5、连续真值、ThunderAgent acting-decay 和 native。
不能把这一新增压力策略的差异继续称为纯预测误差，也不据合成单测宣称净收益。
整体目标保持 active。
