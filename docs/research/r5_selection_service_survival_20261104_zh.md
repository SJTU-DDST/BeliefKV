# R5 改选后的资格保留与实际服务

2026-11-04，America/Los_Angeles。GPU 继续由用户暂停。
本轮复用最新完整 R5 执行和既有精确服务审计，不重跑 GPU、不修改任务或到达表。

## 结论

当前没有发现 R5 新选择被后续控制撤销、重新授予后才执行的缺口。
既有 66 个新选中 episode 都有精确后续服务；57 个在原来限定的下一次控制或干预
之前完成有用服务，另外 9 个越过这个观察边界。补查后，这 9 个期间的控制都保留
同一请求为 active，没有实际暂停、物理卸载暂停或另一次授予记录。

因此不能把“超过下一次全局控制才开始服务”写成“排序失效”。
普通准入与完整控制合并能修复错过容量协调的问题，但不等于解决这些已经 active
的请求所面临的原生服务等待。

本轮还修复了未来遥测中的一个真实缺口：普通 decode 样本此前没有
attempt/session 身份，只有 prefill 样本记录了这些字段。现在两阶段都直接记录
当前原生 handle 的 attempt_id、session_id 和 session_generation；缺失仍写 null。
这是共同记录能力修复，不是新调度机制或已测性能收益。

## 固定输入与人群

使用原服务审计：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  r5_host400_control_optimized_r1/control_refresh_r3/final_results_r1/
    actual_admission_service.json
```

对应 original73、400GB Host、20 初始加平均 70 秒持续到达、固定 18 测量成员，
完整测量为 2991 个请求、362157 输出 token。原来的 4.807688% JCT 配对改善仍是
单轮观察，不因本读器变成可重复或因果归因结果。

66 个 episode 指相对各自决策现场 S0 新选中的 agent 请求，不是 66 个不同
workflow；包含 41 个周期授予、25 个免费准入授予。其他候选、保留的 S0 成员、
工具 A→B 单 donor 排序等，不在此人群。

新增读器先校验原分析的静态指纹，使用原封闭字节边界重新读取
424726938 字节机会日志和 932582791 字节原生日志，并再次校验相同 SHA256。
不把当前工作区代码当作历史冻结运行的实现。

## 原观察边界与真实撤销分开

原服务审计为保守归因，在下一次全局控制或同 agent 干预时结束观察窗。
这并不意味着下一次控制一定撤销 active。新读器从实际 grant 出发，一直核对到
第一份精确的新有用服务：

- 后续周期记录是否仍有同一个完整请求身份，且前后均 active。
- 是否出现实际 pressure pause、full-offload pause、工具替换 donor 暂停。
- 是否出现同 agent 的另一次免费、JOIN 或工具替换授予。
- 精确服务之前是否有兼容但缺 attempt/session 的正进度、未知进度。

planned pause 不按已暂停处理；缺字段或其他已知身份冲突不补写、不混用。
原生 launch 早于授予的工作也不能消费该次授予。

| 同一 66 个 episode | 数量 |
| --- | ---: |
| 原观察窗内有用服务 | 57 |
| 第一份精确服务越过原观察窗 | 9 |
| 精确服务前观察到 inactive 控制 | 0 |
| 精确服务前有显式实际暂停记录 | 0 |
| 精确服务前有同 agent 重新授予 | 0 |
| 中间控制的完整请求身份未知 | 0 |
| 精确服务前有兼容的部分身份正进度 | 0 |
| 精确服务前有兼容的未知进度 | 0 |

66 个授予至第一份精确有用服务的等待 P50 为 398.135ms、P90 为 1416.081ms、
最大为 26562.881ms。这是授予 episode 的等待分布，不是 request TTFT、流式
首 token 或 workflow TTFT；不相加为可消除 JCT。
没有记录到暂停和若干 active 快照也不能证明每个瞬间的连续物理驻留。

## 较长等待期间在执行什么

对原来 9 个越过观察边界的 episode，与相同原生 worker 区间求交，
检查全部区间无超过 0.01ms 的重叠。每份 worker 区间在一个 episode 中只计一次，
不按一个 batch 内的请求数重复分配时间。

| 请求所属项目 | 授予至首次精确服务 | 中间同身份 active 控制 | prefill worker 重叠 | decode worker 重叠 |
| --- | ---: | ---: | ---: | ---: |
| `javascript_api_graphql_medium_079` child | 26.563s | 11 | 26.277s | 0.286s |
| `python_api_rest_easy_006` child | 19.013s | 8 | 19.013s | 0 |
| `javascript_blockchain_defi_medium_070` root | 5.088s | 2 | 5.088s | 0 |
| `python_ml_inference_hard_088` root | 2.507s | 1 | 2.507s | 0 |

最长两段共同反复观察到 prefill 请求
`beliefkv:01a18e6c-95df-7370-9684-6d413a0574aa`，分别出现于 39 和 27 个重叠
prefill batch。其余较短越窗等待为 0.396--1.322 秒，说明越窗与等待至少两秒
不是同一个条件。

这里的重叠是 worker 区间，包含 CPU、数据依赖、恢复等待和模型执行，
不能当作纯 GPU busy 或纯 attention 时间。计数也没有证明这一 prefill 请求是
唯一阻塞原因，没有证明待执行的 R5 请求当时具有足够物理容量或可提前运行。
不能将两个请求的相关等待相加为收益。

下一步应在这些固定请求上核对已提交 chunk、原生队列次序与真实容量拒绝，
而不是再次调短全局控制周期或为已 active 的请求增加逻辑 grant。
只有存在各自可执行但次序可改变的消费者时，才考虑服务层排序修改。

## 普通 decode 身份记录修复

`beliefkv/runtime/v0520_native_telemetry.py::on_launch` 原来仅在
`phase == "prefill"` 时加入 attempt_id、session_id、session_generation。
这会使只有普通 decode 正进度的历史现场无法匹配完整请求身份。

现在每份已启用的服务样本直接读取当前原生请求上的三个标量。
读取发生在 launch 时，不从此前 prefill、工作流角色、最终 receipt 或
未来执行状态推断。之后即使 request handle 被更新，已提交样本的值保持原快照；
下一次 launch 读取新的原生身份。mixed 中原有 prefill 几何字段保持原行为。

修复不改变 grant、KV、prefill/decode 次序、token 或工具执行。
它会增加普通 decode 日志字段与体积，尚无完整运行下的新增 writer CPU 测量。
后续所有比较臂应共用相同遥测代码；不为此单独运行 GPU 实验。
本轮检查也表明：在上述固定 66 个授予中，旧身份缺失没有掩盖更早的兼容正进度，
所以不据此改写原来的实际等待数据。

## 验证与证据

原生遥测、精确服务和身份相关 CPU 检查 223 项通过；新增 worker 区间边界与
共享 batch 去重后，读器自身 15 项检查通过。覆盖身份快照、原生身份改变、缺身份、
重叠 completion 的输出守恒、外来 attempt、无进度、已在途工作、真实暂停、
planned pause、重新授予和不可重复计费区间。

证据位于同一父实验内：

```text
r5_host400_control_optimized_r1/prediction_analysis_r1/
  actual_selection_service_survival_r1.json
  actual_selection_service_survival_r2.json
```

r1 保留首次资格/身份核对；r2 增加等待期间的 worker 分解，最终读器指纹以 r2 为准。
两份分析使用相同原始封闭输入，没有修改前一份结果或消耗新主实验版本号。
CPU 运行采用 CPU0、nice19、CUDA hidden、OMP/OpenBLAS/MKL 单线程。
未注册 GPU 实验或自动后继，完整目标仍未达到可重复性能验收。
