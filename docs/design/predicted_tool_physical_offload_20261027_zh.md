# 基于工具剩余时间预测的主动物理卸载

## 原有实现与本次补齐

原有 `_long_tool_wait` 已用于工具等待期间的增量备份、原生容量短缺时的
冷页候选，以及 agent 逻辑预算借出。它并非完全没有工具卸载能力，
但“预测很长”并不保证在容量短缺前实际释放 HBM；逻辑资格改变、
D2H 提交和物理驱逐不能互相替代。

新增 `beliefkv/runtime/predicted_tool_offload.py`，复用现有工具预测与
原生 PREPARE，补齐“预测 → 增量备份 → 原生 ACK → 安全释放 Device 页”。
候选配置为 `configs/migration/predictive_tool_offload_20261027.json`，
在 V286 Full 配置上显式增加 `PREDICTED_TOOL_OFFLOAD=1`。
旧配置保持默认关闭；V286/V295 冻结执行不受开发区代码修改影响。

## 触发与执行

只有当前已观察到的单个工具仍在执行、上下文身份有效，且预测满足

`P(工具剩余时间 > 1000ms | 已观察执行时间与历史) >= 0.9`

时，主动卸载才有资格执行。独立的 `_predicted_long_tool_wait` 不接受
“有紧迫 JOIN 需要容量”作为工具时长证据。现有按实际容量需求回收的
路径仍保留自身的权限，两种原因分别记录。

在线预测继续使用当前 run 已完成的同类工具历史，按 workflow 与全局
作用域回退；冷启动历史不足、尾部缺少支持、并行工具组不支持时不进行
本次主动卸载。V269 覆盖表中的未来时长仍只在私有 replay 侧使用。
这不是对旧数据训练精度或跨工作集精度的重新认证。

控制器复用原生增量备份选择，不重复复制已有的 Host extent。备份提交
必须保留物理事务预登记；在事件 drain 之后和真正 enqueue 之前，再次
核对本次预测授权。Host 空间不足时推迟新增备份，不为预测性动作驱逐
其他 Host 状态，也不使用全局 `cache.evict()` 或同步写回屏障。

物理释放只处理已确认 Host 副本完整且写入已结束的原生私有 Device
叶子，复核 session generation、上下文 epoch、实际 owner、原生锁和
在途 D2H/H2D。共享的 parent/child 页、ready/运行对象的页不能借此释放。
混合模型中若独立 Mamba 状态仍有 Device 依赖，则沿用原生 FULL-only
释放接口保留该状态，不临时同步写回 Mamba。

工具返回、上下文切换或预测失效后，尚未执行的物理驱逐取消；已提交的
原生 D2H 按自身 ACK/锁协议收尾。正在恢复或已获得预取驻留跟踪的
上下文不再主动卸载，避免把刚预取的 KV 再次换出。

Device 释放不直接取消 agent 活跃资格。逻辑预算继续由 retained agent
策略及已有的完整 Host 证明、真实等待和实际竞争需求决定是否借出。
不能把物理释放字节数当作逻辑准入额度。

## 控制范围与证据

- 复用现有 `dispatch_tool_prepare`，兼容 post-forward 和非 overlap 入口。
- 200ms 节流，每次至多检查 8 个预测上下文。
- 一轮最多对一个上下文提交备份或实际释放 Device 状态。
- 每个上下文最多执行 16 次候选节点检查；轮转节点位置，避免长分段
  前缀的内部节点反复占满驱逐扫描预算。记住已释放叶子的下一段
  ancestor 身份，在后续轮次优先继续；该身份表最多 256 项。
- 原生 PREPARE ACK 可重新唤醒卸载检查；不在 ACK 回调里直接释放页。
- `predicted_tool_offload_backup` 只证明提交；
  `predicted_tool_kv_evicted` 单独记录实际释放 units/bytes。
- 运行时状态、启动记录、CPU constructor preflight 与最终比较验收均
  检查开关，paper baseline 和仅调度 arm 不继承本功能。

CPU 测试使用实际原生树、分配器、备份提交、事务账本和 ACK 发布路径；
拷贝、完成事件及 Host 索引仍为明确的 CPU stand-in。测试覆盖独立
FULL-only、FULL/Mamba checkpoint、返回竞态、enqueue 前预测变化、
共享/锁/在途拒绝及超过单轮扫描预算的分段前缀。

最终共享 CPU 回归 V307 在同一次执行中通过 2523 个用例和四个 subtest，
无失败或跳过，保留 V294 的全部 2376 个用例。V305 发现的分段扫描
继续推进失败保留在原报告，修复后才完成这次全量回归，不用局部重试
拼接为通过结果。

这些证据不代替真实 GPU DMA、模型正确性或完整工作量 JCT/吞吐验证。
新配置在完成独立物理资格验证前不得被标记为性能已接受。
