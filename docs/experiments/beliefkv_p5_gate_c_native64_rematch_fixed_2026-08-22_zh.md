# BeliefKV P5 Gate C Native-64 长跑失败 Characterization

日期：2026-08-22

## 1. 裁决

本轮使用 prefix-rematch 修复版完整运行至 7,200 秒 workflow deadline。局部 ticket
重认证已经生效，但 P5 Gate C 仍未通过：88 个 ordinary native-fallback request
在可见后从未获得 physical start，最长等待约 674 秒；64 个 workflow 最终都由
绝对 deadline 终止，5 秒服务端取消门槛也未通过。

因此该 trace：

- 不进入训练集、Frozen GPU Replay、O0/O3 或性能 A/B；
- 只用于 admission starvation、deadline control 和 HBM/Host characterization；
- 不生成 KV 迁移时间线。

原始目录：
`experiments/raw/p5_gate_c_native64_rematch_fixed/20260821T234013Z`

## 2. 冻结配置

- Qwen3-Coder-30B-A3B-Instruct BF16，单张 H200 NVL；
- `h200_bf16_v5`，KV pool 850,000 tokens，Host pool 96 GiB；
- CUDA Graph max batch 32，SGLang max running requests 32；
- 64 个预注册 root 全部 eager 提交，client concurrency=64；
- `native_subagent_2to3`，predictor 与 predictive action 关闭；
- 每个 workflow 唯一绝对 deadline 为 7,200 秒。

## 3. Prefix Rematch 修复有效

本轮共有 1,780 个可见 LLM request，1,692 个获得 physical start。543 次
`prefix_rematch_ticket_recertified` 对应的请求全部随后获得 physical start：

- recertification 到 start P50：约 284 ms；
- P95：约 1,436 ms；
- 最大值：约 2,436 ms。

ordinary durable restore debt、ordinary capacity block 和全局 restore barrier 均为
0。说明前两轮的 restore authority 错误和 stale prefix-demand 循环已经消失。

## 4. 新暴露的 Ordinary Admission 饥饿

仍有 88 个 request 在可见后从未获得 physical start：

- 88/88 在首个 workflow 结束前已经进入 server；
- 87 个等待超过 5 秒，85 个超过 30 秒，82 个超过 60 秒；
- 最长等待约 673.72 秒；
- 每个请求都出现 `ordinary_waiting_prefix_delegated_to_native` 和
  `waiting_request_path_rebound`，但没有 ticket recertification 或 physical start。

根因是 ordinary fallback 被正确排除在 durable restore priority 之外后，stale 或
partial JointPlan 又可能长期不包含它。现有 30 秒 starvation floor 只影响普通排序，
不能保证这类请求进入 bounded seed epoch。

修复为 bounded ordinary-fallback aging：

1. 只读取有界 native-fallback side index，不创建 RestoreObligation、lease、funding
   或全局 barrier。
2. 超过 30 秒后，每个 epoch 最多提升一条最老请求进入 bounded seed。
3. native allocator/PrefillAdder 仍是最终容量权威。
4. 遇到 `NO_TOKEN` 或 ticket 未获得 service 时，以 allocator available tokens 和
   时间记录退避；同容量下让后续请求先尝试。
5. allocator 容量变化时立即重试；容量不变时经过 1 秒冷却再试，避免永久阻塞。

实现提交：`946be88 fix: bound native fallback admission starvation`。

## 5. Deadline 与控制事件活性

64/64 workflow 最终都进入 `server_terminal=true` 和 `cleanup_complete=true`，但只有
17/64 在 5 秒内完成服务端取消：

- server-terminal latency P50：12.51 秒；
- P95：约 65.56 秒；
- 最大值：160.59 秒。

50 个 workflow 的 runtime control 降级，共 167 次 ACK timeout。根因是 SGLang
idle sleeper 只监听原生 ZMQ socket，未监听 BeliefKV runtime-event UDS；scheduler
在工具等待/取消阶段可能不被控制事件唤醒。deadline controller 还曾串行执行 request、
child 和 command 取消，进一步延迟 server-terminal。

已修复：

- 将 runtime-event fd 注册到 SGLang idle poller；
- child deadline cancellation 合并为每 workflow 一个原子 control batch；
- request abort、pending task cancellation 和 active command cancellation 并发启动；
- server-terminal 独立于较慢的本地 cleanup 计时；
- terminal invocation 的迟到事件只保留在 trace，不再污染服务端 RCCG。

实现提交：`96b358a`；冻结运行 profile：`h200_bf16_v6`，提交 `2f2a8bc`。

## 6. HBM、Host 与 GPU Characterization

- gross HBM 峰值：83.558 GB；effective pressure 峰值：52.62%；
- Host 峰值：95.99995 GB；
- native D2H：5,024 次 / 201.91 GB；
- native H2D：586 次 / 415.44 GB；
- 显式 BeliefKV 策略迁移：0；deadline/terminal Host cleanup：73 次；
- GPU 平均利用率：4.10%，83.10% 样本为 0；
- GPU 利用率不低于 50% 的样本仅 2.47%。

Host 接近满载是真实 characterization，但本轮不通过 admission/deadline correctness，
不能据此评价迁移策略收益，也不先扩大 Host pool。

## 7. Agent 语义覆盖

本轮包含 64 parent、128 FRESH child、64 JOIN_WAIT、1,780 次 LLM 和 4,722 次工具
调用。共观察到 73 个 child RETURN，但只有 1 个 JOIN_SATISFIED；其余 63 个
JOIN 最终随 deadline 结束。没有 workflow 自然完成，因此不能冻结完整 agent demand。

## 8. CPU 验证与下一门槛

- Admission/restore/JointPlan/retraction/Host：254 passed，8 subtests passed；
- Deep Agents workload/deadline/runtime：149 passed；
- event channel 与 running-retraction reserve：8 passed；
- 2 个 vendored SGLang 导入测试因 CPU shell 缺少 `CUDA_HOME` 被排除，真实 GPU
  server 启动路径负责覆盖。

下一步只运行一次相同 64-root/v6 Gate C。必须满足：

- 88 类 ordinary fallback 不再系统性超过 30--60 秒；
- ordinary promotion 不创建 durable debt 或全局 barrier；
- deadline workflow 100% 在 5 秒内 server-terminal；
- waiting backlog 下不出现持续 0 ticket 或 running 排空；
- 无 orphan command、lease、transaction 或 container。

只有 Gate C 通过并出现自然 child RETURN/JOIN 后，才能冻结 demand 并进入 P6 O0/O3。
