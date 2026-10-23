# Prepare 作用与 JOIN 轨迹稳定性

日期：北京时间 2026-10-23。

## 统计口径

此前的“50 个”是完成前有正 FULL 恢复计划的 JOIN 等待，
不是全部 JOIN。V128 完整封存证据为：

| 指标 | 关闭后台 prepare | 同版 Full |
| --- | ---: | ---: |
| Harness JOIN 总数 | 75 | 66 |
| 控制器关联已完成等待 | 74 | 66 |
| 完成前有正 FULL 计划 | 50 | 61 |
| 完成前启动并确认后继复用 | 38 | 61 |
| 上一行确认复用 FULL，GB | 2.784768 | 9.906258 |

Full 的后台 prepare 为 5558 批、170.431754 GB FULL，
平均每批 30.66 MB。后台加 spawn 的 5602 批中，1241 批关联后续
容量释放，2711 批关联后续恢复。关联既不等于独占容量，也不等于
已经避免关键路径等待。

这些证据支持 prepare 提供了更多可恢复副本和可直接回收的容量。
即使关闭后台组的 JOIN 更多，其完成前恢复量仍更少。
但两组逐 workflow 的工作量差异明显，36 对中只有 1 对的输出、
请求数、输入三项同时在 10% 工程筛选线内。
不能将两组字节比或原始 JCT 比解释为 prepare 的因果加速。
关闭后台 prepare 也未关闭 spawn、工具预测或紧急备份路径。

## JOIN 为什么波动

同版 Full、关闭后台、关闭工具模型、关闭 child 回收的 JOIN
总数分别为 66、75、92、84。固定 seed、任务、prompt 和
temperature=0 仍未固定动态 batch 的数值计算及并发工具的反馈顺序。
少数任务的轨迹变化会放大总数差异：关闭后台与关闭工具模型两组中，
一个 system automation 任务的 JOIN 从 4 轮变为 18 轮，
贡献总计 17 次差异中的 14 次。首个分歧原因尚未逐 token 定位，
不能将其笼统归因于某一个模块或单个随机种子遗漏。

动态端到端运行继续由模型决定 spawn、嵌套、回访和自然结束，
不以 JOIN 下限拒绝 final，也不增加固定 DAG。
需要严格隔离系统模块时，使用预先登记的真实动态轨迹进行辅助
因果回放，固定原始请求 token、工具耗时和依赖，后继根据本轮实际
前驱完成解锁，而非沿用原始墙钟提交时刻。
未来 token 只进入隔离的数据面，不作为策略输入。

## 当前验证边界

V129 已完整收集 36 个 workflow、8165 个原生模型请求、
73 次 JOIN、123 个动态 subagent、9846 对工具事件。
其中一个 workflow 有 guard 干预，完整保留并单列；
采集不能作为新的性能基线或独立正确性评分。

V2958/V2959 已完成全源依赖和精确 token 的 CPU 审计。
V130c 启动真实原生 GPU 服务，预先指定前 8 个 root 的前 3 次请求，
用于 token、停止条件和 session 的数据面检查。
该检查不含完整工具/JOIN 因果执行或 live policy 对照，
不能宣称完整回放、prepare 净收益或性能目标已验证。

独立重启服务或调整调度顺序会改变原生全局递增的
`session_generation`，因此客户端应按原会话生命周期绑定本轮实际
generation，并拒绝同一生命周期内编号变化；session ID、namespace、
完整 token、停止条件和本轮完成时钟仍严格校验。
服务端冻结快照保持不变；客户端修复另行测试、冻结和登记。

## 本轮实际 GPU 检查

服务端冻结 `552b09d`，客户端冻结 `4d0b32b`，
客户端与服务端只有已登记的四个客户端/测试文件不同；
原生 receipt、sampling、clock、token capture 模块的哈希一致。
引擎为登记的 `cc9772c6c5c4` 树。

- V2963 的 67 项聚焦 CPU 检查通过。
- V2964/V2965 的 4703 项、原有 2 个 subtest 通过，
  保留 V2960 全部 4694 项；真实 Docker progress 的既有范围外
  deselect 保持不变。V2962 为文件名错误导致的空测试调用，
  原报告保留，不计入通过资格。
- V2966：预先指定的 24/24 个真实 GPU 请求全部通过。
  原生输入共 3538312 token，输出共 8841 token，
  单请求输入为 114454–208158 token，24 次均保留原生 stop 结束。
  另以原生 telemetry 对齐 24 个 submit 和 24 个 result 及全部输出
  token；writer 失败记录为零。
- 8 个原生 session 完成 HTTP close；其中 5 个 session 的本轮
  generation 与源不同，生命周期内保持一致。没有将 close 的
  HTTP 回执当作物理 KV 释放 ACK。

V2966 单独验证了实际 GPU 进程、来源和参数；
microcheck 自身的摘要仍保留 `gpu_process_verified=false`，
避免一个通用 HTTP 客户端自行签发外部 GPU 资格。
两处的 `gpu_replay_qualified` 与 `performance_accepted` 均为 false。
本轮不是完整因果运行，没有 live policy，也没有测量输出强制和
delayed sampling 的共同观测成本，不能用于 JCT 或吞吐归因。

数据面检查完成后已正常关闭本轮服务及其已核实归属的子进程。
最终复核无 GPU compute process，GPU 空闲为 143142 MiB，
端口 18552 已释放。操作记录为 V130c 原始目录中的
`qualification_cleanup.json`；没有恢复每日停机规则。

主要证据：

- `v128_full_complete_20261023_zh.md`：Full 完整结果及 V2915–V2917。
- `v128_no_tool_prepare_findings_20261023_zh.md`：关闭后台及工具模型组。
- `v129_exact_source_requalified_20261023_zh.md`：真实源及 guard 资格。
- V2958/V2959：完整源依赖与精确 token 审计。
- V2963–V2966：session 修复、保留全部旧检查的 CPU 资格及实际 GPU
  数据面检查。原始请求与回执位于 V130c 的 `smoke/`。

工作目标仍未验收，需要同工作量的 Full、native 和最强简单调度对照。

## 同工作量对照的后续进展

V131b 已使用上述 V129 的真实动态源启动 Full、native_matched、
JOIN-only、关闭后台 prepare 四组因果对照。所有组共同固定 73 个 JOIN、
8165 个请求及逐请求完整输入输出 token，后继按本轮实际前驱完成解锁；
调度策略不能访问未来 DAG 或输出。它用于隔离系统收益，仍保留实际
动态端到端运行及其工作量、轨迹和资格差异，不用回放声称模型决策
在不同调度下天然稳定。源中的一个 guard workflow 继续保留并单列。

V3000 对仍在执行的 Full 独立封存输入前缀，已关联完成 71 个 JOIN，
其中 63 个在完成前有正 FULL 计划，56 个在完成前启动并确认后继复用，
FULL 为 10058895360 字节。该前缀不是完整 73 个 JOIN，也不是多个
文件的原子快照，不与另一组的完整结果计算加速比。当前仍需要等待
同工作量对照完成，联合核对 JCT、吞吐、备份成本及实际暴露恢复等待。
