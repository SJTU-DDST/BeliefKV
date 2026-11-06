# GPU 暂停期间的优化、配对准备与设计筛查

2026-11-06，America/Los_Angeles。继续遵守用户的 GPU 暂停指令。
本轮仅使用 CPU、已有原始轨迹和已完成实验日志，没有启动或注册 GPU 实验、
资格测试或自动恢复任务。原 73 项目配对及其结果保持原样。

## 1. 去除卸载授权中的重复消费者检查

`DemandAwareToolOffload._before_eviction` 原先先执行 `_live_consumer`，
随后 `_worth_offloading` 又执行一次相同的实时需求检查。两次检查在同一个
同步回调内连续发生，没有传输或调度动作插在中间。

现保留 `_worth_offloading` 内的检查，消费者失效时立即返回；消费者有效后
再读取工具预测。请求身份、实际队列成员、逻辑许可、原生前缀、物理容量、
工具预测和动作前原生锁检查仍然生效。没有把缓存的容量结果跨动作复用。

使用同一真实 native CPU radix、分配器和消费者，把提交 `846b6d87` 中的旧方法
通过 AST 单独绑定回来，与当前方法交替测量。固定 CPU0、nice19、隐藏 CUDA，
每种情况 15 个测量块，每块 100 次调用；copy/event 使用 CPU stand-in。

| 授权检查情况 | 旧方法 CPU 中位数 | 当前方法 CPU 中位数 |
| --- | ---: | ---: |
| 有效且受容量阻塞的消费者 | 87.474 us | 51.788 us |
| 工具已经返回 | 65.753 us | 33.596 us |
| 消费者已经离队 | 0.509 us | 1.016 us |
| 消费者 attempt 已失效 | 4.564 us | 5.409 us |

有效路径减少约 40.80%，实时消费者读取从两次降为一次。离队和失效路径仍多出
约 0.51/0.85 us 的函数调用与参数准备开销；没有隐藏这些负结果。
四类情况授权结果相同，探测未改变物理页、Host 副本或传输 pending 状态。
这些是单次授权检查成本，不能折算为完整控制线程、GPU 或 JCT 改善。

初次测量保留为 `eviction_authorization_cpu.json`。调整无消费者时的快速退出
顺序后，最终测量为 `eviction_authorization_cpu_r2.json`。
不因这一小改动单独安排 GPU 消融。

## 2. 83 项目候选的 Full / Oracle 配对

复用上一轮已生成、已校验的候选：83 个不同原项目、20 个初始 workflow、
平均 60 秒泊松到达，400 GB Host、running/decode 上限 24。
保留原 18 个按到达位置选定的测量成员，统计其完整 JCT，全部测量成员自然
完成后停止背景。旧 73 项目、平均 70 秒的结果不与此候选混算。

新增 `scripts/prepare_background_oracle_pair.py`，生成：

```text
background83_mean60/paired_oracle_prepare_r2/
  full_work.json
  oracle_work.json
  full.pending.json
  oracle.pending.json
  pair_review.json
  pair_review_r2.json
  constructor_review_r2.json
  constructor_review_r3.json
```

两份工作量 artifact 只在 `remaining_work_ranking_enabled` 上不同。
两份配置只在对应 artifact 路径、哈希和明确的排序标志上不同。
同一进度维护、工具压力选择、active 连续性、sibling、JOIN 和 KV 数据面
均保留；实际环境比较也检查了配置别名与 artifact qualification 的一致性。

两个真实 CPU runtime constructor 分别验证：

| 项目 | 非排序 Full | Oracle |
| --- | --- | --- |
| 实际控制器 | 统一 native 边界控制＋有界剩余额度补位 | 相同 |
| 普通准入边界 | 2000 ms | 2000 ms |
| 窗口到期时协调 decay 与容量 | 启用 | 启用 |
| 独立免费补位兜底时钟 | 关闭 | 关闭 |
| 工具卸载检查间隔 | 400 ms | 400 ms |
| 工具卸载成本余量 | 400 ms | 400 ms |
| JOIN 预取触发 | observed_eos | observed_eos |
| prepare_host / 工具预测卸载 | 保留 | 保留 |
| 工具预取 / 逐层 handoff | 关闭 | 关闭 |
| 剩余工作量排序 | 关闭 | 启用 |

配置中的 legacy free-dispatch 数值仍为 500 ms；在这一显式 2 秒合并路径中
它不再构成独立的周期。构造检查读取实际 controller 状态，确认不存在该
独立兜底，避免将配置中的历史字段误解释成第二套有效时钟。

模型单位到时间的系数及组合成本资料继承旧 73 项目控制，明确标为
`inherited_old_population_cost_calibration`，不称作新 83 项目实测校准。
该次 global Oracle 比较没有开启组合成本分母打分。

第一版准备因生成器没有同步 Oracle 的元数据排序标志，被自身校验拒绝；
`paired_oracle_prepare_r1` 中的未注册 artifact 保留，第二版生成器已修正。
CPU 构造探测又暴露了探测器遗漏 shell launcher 的
`BELIEFKV_ONLINE_TOOL_TIMING` 别名映射；第二次失败的 stderr 保留。
第三次探测按实际 launcher 映射 online timing 和 parent headroom 后，两臂
构造与实际控制状态均通过。这个失败不证明实际 GPU launcher 存在同样问题。

两份配置明确以 `.pending.json` 命名、`source_registration=null`，
没有复用旧 73 项目的注册来冒充新来源。新来源正式注册、当前源码完整绑定和
新 GPU 资格验证仍未完成；当前用户 GPU 暂停有效。该候选也尚未通过实际
ready 供给、容量竞争、背景尾部覆盖和状态损失检查，不称作已接受的正式负载。

## 3. 新设计筛查：备份生命周期

重用已完成的 `workflow_original73_host400_control_refresh_20261104_r1/full`，
通过 `audit_sealed_wait_prefetch.py` 固定 JSONL 完整前缀和哈希，再以
`scripts/screen_prepare_host_value.py` 对账原生 D2H receipt。
这里分析的是旧 73 项目日志，不是上节新候选的执行结果。

1811 次语义 PREPARE 均收到 ACK，其中 1571 次在可观察的 Host 回收之前
关联到后续恢复，240 次没有恢复关联。节点关联不等于字节连续性或最终
forward 使用，不能把后者直接称为浪费。

原生备份另外有 817 批、约 242.338 GB：

| 原生来源 | 批数 | 对账 D2H 字节 | 传输流累计时间 |
| --- | ---: | ---: | ---: |
| idle | 291 | 11.601 GB | 465.34 ms |
| critical parent | 318 | 2.321 GB | 156.80 ms |
| spawn | 208 | 228.416 GB | 6916.17 ms |

idle 中 147 批、4.282 GB，在所记录副本结束前没有释放或恢复关联，
传输流累计时间仅 173.46 ms；另 61 批仍含右删失节点，不能算成无用。
spawn 的 170 批有释放或恢复关联；33 批没有关联且已结束，传输流时间合计
1018.85 ms；另外 5 批右删失。

这些结果不足以把“减少 idle 备份”列为优先的第三个性能设计：
可观察的传输工作量较小，大字节备份主要来自父子依赖路径，且有大量后续
恢复关联。没有据此关闭 prepare_host、SPAWN 备份、JOIN 预取或需求卸载。

也不能把 submit-to-ACK 的较大包络当作可删除的传输时间。当前 device event
没有 wall-clock DMA 起点，累加流时间不是可暴露停顿或 JCT 上界；
Host 生命周期跨 split 的物理连续性及原生 forward 使用仍不完全可见。
本轮只筛查优化候选，没有为传输与 attention 的干扰作因果归因。

## 4. 设计选择与下一步

目前没有新证据支持把以下内容升级成独立第三贡献：

- 重复检查削减和刷新退避属于控制路径工程优化。
- Host 备份节流和重复副本回收属于 D2；已有安全镜像回收不重复实现。
- 分支余量和窗口选择属于 D1；此前分支可替换现场较少的结果仍保留。
- 工具关闭标记后再提升 decode 的现场过晚，旧双向成本筛查不支持直接实现。

继续保留“驻留集合内的实际计算推进”作为独立设计候选，但下一项有意义的
研究应先证明：一个在线可见的信号能足够早地改变 prefill/decode 服务顺序，
并在支付整个 running batch 与被推迟 prefill 的代价后仍有净收益。
不重复用未来 EOS 真值或忽略对侧代价的局部结果论证它。
本轮没有筛出足够证据支持立刻增加新的在线控制器或预测器。

GPU 恢复后的优先动作仍是合并本轮成熟改动，使用同一源码和上节配对
检查更强持续压力能否增加真正可改变选择的竞争，再评价完整 JCT、
共同窗口吞吐和实际服务传递。不能靠选择性统计受益成员制造收益，
也不为上述每个 CPU 小改动单独消耗完整 GPU 实验。

## 证据入口

本轮 CPU 产物都放在原配对的具名子目录，没有消耗新的主实验版本：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  host400_registered_copy_pressure_r1/capacity_review_correction_r1/
    arrival_pressure_followup_r1/background83_mean60/
      cpu_preparation_20261106_r1/
        eviction_authorization_cpu.json
        eviction_authorization_cpu_r2.json
        eviction_cpu.xml
        pair_eviction_cpu_r2.xml
        prepare_value_cpu.xml
        final_cpu.xml
        prepare_lifecycle.json.gz
        prepare_lifecycle_summary.json
        prepare_value_screen.json
      paired_oracle_prepare_r2/
```

最终 `final_cpu.xml` 中 234 项相关 CPU 检查通过，无失败或跳过，
覆盖工具卸载授权和事件返回、
真实 native 页与锁、配对配置与别名一致性、备份 receipt 和右删失分类。
CPU 证据不建立新 GPU 正确性、完整共享回归或 JCT 收益。

后续已完成[83 项目来源绑定与三臂准备](background83_source_binding_20261106_zh.md)：
完整 CPU 验证通过 8188 项与 23 个子测试，原 7132 个身份全部保留，
新组合来源已注册，Full/Oracle/ThunderAgent acting-decay 配置已接入。
GPU 实验仍未注册，原始 `.pending.json` 和本节当时的准备状态作为历史证据保留。
