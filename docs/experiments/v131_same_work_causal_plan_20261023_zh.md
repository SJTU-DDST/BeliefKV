# V131 同工作量因果对照计划

本轮用真实动态轨迹补齐系统模块的同工作量对照，目标仍是改善完整
workflow 平均 JCT 和吞吐，并证明 KV 管理相对简单调度的增量。
仅完成回放工具或增加传输量不构成性能验收。

## 固定内容

源为预先登记的 V129 原生动态轨迹：
36 个 workflow、8165 个原生模型请求、602782233 输入 token、
2352172 输出 token、73 次 JOIN、123 个动态 subagent。
所有源 workflow 和正常长尾保留；源中一个有 guard 干预，
既报告完整总体，也单列源资格一致的 35 项。
回放不构成独立任务正确性评分。

保持原始完整输入输出 token、原生停止条件、session 生命周期、
工具等待和依赖。后继根据本轮真实前驱完成解锁。
未来 token 只进入隔离的数据面，调度器不能使用未来 DAG 或工具结束时刻。
这属于模型服务与 KV 系统的辅助因果对照；真实动态端到端实验仍保留。

共同配置为 Qwen3.5-35B-A3B BF16、256K、running/decode8、seed/hash21、
FULL/Mamba 独立池、Host200GB、80:20、NUMA preferred1、
mem_fraction_static0.94、chunk4096、原生 overlap。
每组独立冷启动，并对实际 server_info、容量 census、进程归属和
GPU context 进行核对。

## 四组顺序

1. Full：V108/EOS 路线，工具期预取、resident-first、阶段优先、
   spawn/后台增量备份及安全容量回收；普通 execution-frontier restore
   和短工具排队连续性关闭。保留该路线原有恢复保护配置。
2. 工程对齐 native：相同容量与原生 HiCache，不启用自定义调度和 KV 管理。
3. JOIN-only：保留有界 JOIN parent 优先及原有防饥饿规则，
   不启用自定义 KV 管理。
4. 关闭后台 prepare：其余 Full 控制保持一致，spawn、工具和紧急备份
   路径继续保留。该组用于隔离后台备份的增量。

不得因某组 token 较少或 JOIN 较少而解释为系统收益。
原生请求数、token 总量、每请求输入输出 SHA 和停止原因必须逐组相同；
任一组未完成、容量变化、控制配置错误或工作量变化时停止队列并保留证据。

## 回放共同成本

原生 custom-logit processor 的 mask `nonzero` 与索引可能同步 GPU/CPU。
新增微基准测量真实原生入口和 greedy argmax，分别覆盖 batch1/8，
并与直接 forcing、greedy 路径比较。所有路径包含相同 logits reset。
该组件测量不含模型 forward、delayed sampling、HTTP、receipt、KV DMA
或 workflow JCT；不将测量差值从端到端结果中扣除。

V2971 已在 H200 上完成 11 个采样、每个 64 步的组件测量，
词表为模型真实的 248320，logits 为 float32：

| 平均每步墙钟，ms | batch1 | batch8 |
| --- | ---: | ---: |
| greedy，包含共同 reset | 0.017448 | 0.017253 |
| 直接 forcing + greedy | 0.050959 | 0.057382 |
| 原生 forcing 入口 + greedy | 0.184215 | 0.261965 |
| 原生 forcing 相对 greedy 的增量 | 0.166766 | 0.244712 |

原生入口存在可测成本，但没有测量其与 forward 的实际重叠，
因此不由上表推导 JCT 加速比或整体吞吐损失。
本轮保持采样实现不变。微基准首个导入尝试缺少 CUDA_HOME，未进入
测量；补齐与模型启动脚本相同的 CUDA 13 环境后成功，
失败记录另行保留，不能计作一次有效采样。

精确输出使用 `SGLANG_ENABLE_DELAY_SAMPLE=1`，与源采集默认不同，
因此它和输出强制必须在所有回放组共同使用，不能归因于 KV 设计。
V130c 已在真实 GPU 上验证 24 次长上下文请求的精确 token、原生 stop
和 session，但尚未验证完整 live-policy 回放。
回放驱动和监督进程也显式设置 hash seed21，
每组摘要必须回报实际的驱动 hash seed 才能通过结果校验。

## 监督与结果解释

监督器在启动前和运行中检查用户暂停/一次性 GPU hold，
不恢复已删除的每日宵禁。只清理 PID 与 start_ticks 匹配、归属已验证
的服务和驱动；保留原始退出码、失败文件、完整尾部及工作量差异。
Full、native 和 JOIN-only 的 JCT、batch 利用、暴露恢复等待、
重复恢复及忙时吞吐需要在收集后联合审查。
队列收集完成只能标为待性能复核，不能自动宣布目标达成。
