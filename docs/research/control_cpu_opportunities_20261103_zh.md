# 等待 GPU 期间的控制优化

2026-11-03，Asia/Shanghai。保留 original73、Host400、20 初始加 mean70
到达及固定 18 成员协议。没有修改任务、token、工具时长、待跑预测器或
原生引擎。本文区分已验证的单次 CPU 改动和仍需筛查的机制。

## 已实现：一次 donor 检查复用一次需求

原调用链为：

```text
DemandAwareToolOffload._demand
  -> replacement.permits
       -> replacement.demand
  -> replacement.demand
```

同一个同步调用先验证 donor 的逻辑额度，然后立即重算相同消费者的
需求，重复执行身份、请求、前缀缓存读取、headroom 和输入预算检查。
前缀已有短期缓存，因此不能把两次 helper 都称为完整原生前缀扫描。

现在 `_permitted_demand` 返回刚通过相同 donor 额度检查的需求，
`permits` 保留原 bool 接口；`_demand` 直接使用这一份需求。
不建立跨调用或跨事件缓存。下一次调用仍读取当前容量；备份授权、
工具返回、锁、Host 副本、实际释放与准入之前的原生复核保持原路径。
无效许可的原 fallback 也保留，没有以停止必要检查换取省时。

### CPU 证据

相关回归 481 项通过，包括成功替换、备份 ACK、工具提前返回、consumer
变更、容量变更、共享和锁定 KV、原生安全与准入条件。新增检查验证：
有效 donor 的一次需求观察即可使用；下一次容量变化会改变判断；
consumer/donor 身份不匹配、inactive donor 额度不足和没有缺口仍拒绝。

初次 r1 的四项新增 fixture 失败保留：旧检查的末尾断言被错误置入新
测试，且一个 fixture 没有注册所引用的 hot 请求身份。修正 fixture 后
r2 全部通过；没有删除原有检查或改写 r1 报告。

局部比较使用实际 native CPU radix/allocator fixture，copy/GPU 为
stand-in。baseline 的 replacement 类和 `_demand` 方法均从冻结服务
`6f1c648b` 读取；两边共用其他 runtime 安全路径。九个 block 交替顺序，
每个 block 500 次。构造、断言和调用计数在计时之外。

| 项目 | 冻结服务 | 新实现 |
| --- | ---: | ---: |
| 单次调用线程 CPU 中位数 / us | 90.328630 | 49.461450 |
| 十次调用中的需求观察 | 20 | 10 |

中位 CPU 减少 45.242776%。两边返回相同 consumer 需求，均没有传输、
驱逐或逻辑授予。该比例只适用于这条已通过 donor 许可的局部调用，
不是完整 replacement、整轮 CPU 或 JCT 降幅。不能乘以前轮
118--132 秒累计控制 CPU；真实调用占比及暴露在 GPU 关键路径上的部分
仍未知。本次补丁不覆盖已冻结服务，不新增 GPU 后继。

## 后续优先级

| 方向 | 当前证据 | 下一步与边界 |
| --- | --- | --- |
| 区分例行 hint 刷新与决策变化 | 同一等待每次新 hint 都会清零两类 offload deadline | 新等待、真实就绪请求、ACK/释放仍立即唤醒；例行刷新保留有界退避；新预测使原来不合格的 donor 合格时仍须及时尝试 |
| 在完整前缀观察前筛查 donor | 已有逻辑额度过滤；工具预测、保护状态等仍主要在消费者观察后检查 | 先量化无可用 donor 的检查比例，只使用有界的当前窗口；不把一次失败长期缓存，也不改变候选顺序 |
| 已有 settled 副本时减少 shadow 重建 | 当前先刷新 shadow，再检查现有 backed release plan | 先查可验证的安全副本；索引缺失、未知或不足时保留原刷新；不多推进一次 cursor，也不能把旧索引当物理授权 |
| 使工作量选择影响实际 prefill 服务 | paused 入选还需经过 native 等待队列和既有 chunk owner | 先离线找出可改变实际首次服务的竞争点，再考虑未开始请求的有界排序；保留 active 连续性、JOIN 和原生准入 |

前三项适合先用 CPU 验证。最后一项可能更直接影响完整 JCT，但不能从
排序变化或首 token 提前推断已经改善完成时间；已有 chunk owner 轮换
涉及缓存生命周期，应作为单独设计。

### hint 刷新的现有规模

从两轮 R5 horizon64 的最后 runtime snapshot 读取：

| 最后观察到的累计计数 | 首轮 | 确认轮 |
| --- | ---: | ---: |
| accepted tool hint | 38435 | 40029 |
| deferred physical hint inspection | 38435 | 40029 |
| 非空候选 replacement 选择 | 26685 | 27341 |
| 未找到完整配对 | 26463 | 27121 |
| 实际授予 | 153 | 155 |

这些包含预热及背景工作，snapshot 的 `final=false`，不能替代共同窗口
或精确 shutdown 总量。两轮约 99.2% 的非空选择未找到完整配对，不等于
预测错误率，也不证明所有失败检查都可删除。新 consumer、完成、ACK
也会合法 rearm，不能把这些轮数全部归因于 hint 刷新。

直接取消所有刷新唤醒也不正确：条件剩余时间、在线支持和资源变化可能
使原来不满足门槛的等待变成可卸载。需要区分真正有新决策信息的变化，
而不是拉长固定周期或按相同工具类型认定所有预测都相同。

## 实验启动修复

检查本次等待状态时发现：r1 已在 18:05 因 native proof 的绝对补丁路径
不匹配退出，原两条进程也已真实退出，GPU 模型没有启动。
启动前的归一化比较遗漏了 registrar 的完整 state hash 检查。

修复复用已有严格 path rebinding：要求原生 tree、补丁字节和其他
metadata 相同，保存针对服务 checkout 的独立 manifest，并在 CPU
资格中实际调用该 checkout 的 `engine_evidence`。服务仍为 `6f1c648b`，
其 1346 项 CPU 及已有模型/传输证据复用；operator 的 108 项检查通过。
原 r1 状态与失败日志保持不变，重试位于同一实验的
`registration_retry_r2`，使用独立 `_r2` 注册。

新的资源等待器 `876499/787935963` 和结果观察器
`877000/787941120` 均已启动，实际存活另由
`registration_retry_r2/queued_start_review_r1.json` 核对。
当前仍被其他 GPU 任务阻塞，尚无新 JCT 结果。
本次局部需求复用仅为后续 CPU 候选，不悄悄加入该冻结队列。

## 入口与证据

```text
beliefkv/runtime/reference_tool_replacement.py
beliefkv/runtime/predicted_tool_offload.py
tests/test_tool_replacement_control_cost.py
scripts/benchmark_permitted_tool_demand_cpu.py

experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  synchronous_control_audit_20261103_r1/
    permitted_demand_reuse_cpu_r1.xml
    permitted_demand_reuse_cpu_r2.xml
    permitted_demand_reuse_benchmark_r1.json
  r5_host400_control_optimized_r1/registration_retry_r2/
    qualification.json
    native_sources_for_registration.json
    queued_start_review_r1.json
    final_results_r1/plan.json
```

hint 原始快照来自
`workflow_original73_host400_R5_horizon64_20261102_r1` 与
`workflow_original73_host400_R5_horizon64_confirmation_20261102_r1`
的封闭 `full/opportunities/admission_opportunities.jsonl`。
成本与样本边界见 `synchronous_control_cost_audit_20261103_zh.md`；
实际 prefill 的既有瓶颈分析见 `current_bottlenecks_20261103_zh.md`。
