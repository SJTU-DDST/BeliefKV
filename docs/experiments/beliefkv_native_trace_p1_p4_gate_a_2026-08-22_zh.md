# BeliefKV Native Trace P1-P4 Gate A

日期：2026-08-22

## 1. 背景

2026-08-21 的 H200 64-root native-subagent trace 达到 100% HBM pressure 和
95.996 GB Host 占用，但只完成 18 个 system-valid workflow。主要问题不是迁移
次数少，而是：

- workflow deadline 没有可靠取消 descendants 和在途请求；
- 高优先级 waiting request 可能没有可执行 HBM，却不产生显式 reclaim 需求；
- Host 饱和后缺少非终态、可重算 KV 的安全退出路径；
- execution working set、residency reclaim 和 workflow fairness 使用了不一致
  的目标。

本轮先修 observed P5 下界，不启用 FrontierBelief 或预测动作。

## 2. P1：Workload 与 Deadline

native_subagent_2to3 现在要求第一轮委派 2--3 个独立 child。每次 JOIN 后由
同一个 parent 汇总；只有仍存在至少两个独立调查项时才允许下一轮 2--3 child，
不固定总轮数，也不为满足 fan-out 人为拆分任务。

一个 workflow 只创建一个绝对 ActivationDeadline。parent、child 和 summary
共享该 deadline；到期后停止新提交，取消 active request、pending task 和 active
command，向 SGLang 发送 abort，并记录
deadline_expired -> abort_sent -> server_terminal -> cleanup_complete。

## 3. P2：HBM 物理活性

Admission compiler 对下一执行片段计算：

    fixed startup
    + bounded prefill chunk
    + 16-token decode quantum
    + allocator guard

若该片段超过 bounded HBM，compiler 发布 ReclaimRequirement，而不是只记录
普通 skip。JointPlanner 先选能立即产生 GPU work 的 beneficiary，再将 parked
victim 的 reclaim 与该 beneficiary 绑定。ACK 后 beneficiary priority 一直保留到
首次真实 GPU service。

超过 force-progress 时间且持续 HBM 不可行时，系统只允许一笔 admission rescue：
已有 running batch 继续前进，普通新 admission 暂停；回收容量由 allocator-backed
reservation 隔离；只有 beneficiary 获得真实 service 才算成功。rescue 不创建新的
全局 restore barrier。

## 4. P3：Host 到 Recompute

Host pool 不扩容，继续使用 96 GiB，并采用 95%/85% hysteresis：

1. 优先对 DUAL_CLEAN 执行 Host shadow drop，GPU copy 保持不变。
2. 无冗余 shadow 时，才考虑 CPU_ONLY。
3. CPU-only owner 必须全部来自显式保证 full-prompt replay 的 runtime 路径。
4. replay certificate 绑定每个 owner 的 context epoch。
5. active obligation、engine lock、reader、semantic pin、in-flight transfer、
   unsealed 或非 leaf extent 一律拒绝。
6. ACK 后 context 标记为 recompute_required；下一次请求由 native cache miss
   走完整 prefill，首个 service 清除该标记。

该策略是 Host-tier 生命周期控制，不是第二套 HBM victim planner。

## 5. P4：Causal MaxWeight

正常调度的层级为：

    30 秒 starvation floor
    -> causal class / JOIN straggler / blocking depth
    -> unlock-weighted short GPU work / immediate HBM envelope
    -> resident reuse / startup cost
    -> workflow fairness tie-break

一个 workflow 可以在同一 epoch 获得多个 ticket。HBM pressure 只开启
replacement/reclaim，不降低 native GPU-ready target。prefill chunk 自适应裁剪；
decode 每个 scheduler safe point 是有限服务片段，HBM admission 使用 16-token
增长保护。resident_service_window_ms=5000 作为可续租 residency lease。

fairness revision 变化不再使无关的执行或 KV 动作失效；fairness 仅在 30 秒防饿
和最终同分时生效。

## 6. Gate A 结果

| 测试组 | 结果 |
|---|---:|
| 核心 control/policy/runtime/trace replay | 777 passed |
| Deep Agents workload/runtime/collection | 117 passed |
| H200 frozen runtime profile | 14 passed |
| 附加 subtest | 8 passed |

两项测试被排除，因为它们导入 vendored SGLang 时需要本机完整 CUDA toolkit；
该服务器的 GPU launch path 将覆盖真实导入。没有发现 P1-P4 的 CPU
correctness 回归，git diff --check 通过。

## 7. 尚未关闭

- 尚未证明 GPU 上出现 COMMIT_CPU -> ACK -> beneficiary service。
- 尚未证明 DROP_HOST_CONTEXT -> recompute -> service。
- 尚未测量 deadline 后 5 秒内的服务端清理比例。
- 尚未测量 GPU utilization、batch size、workflows/hour 或 JCT 改善。
- full-prompt replay 保证目前只对正式 Deep Agents SWE-bench runtime 显式开放；
  其他 runtime 默认 fail-closed。

下一步只运行一次 4--8 root Gate B。Gate B 通过后，再以 predictor-off、
Host=96 GiB 和真实动态 workload 运行 64-root Gate C。
