# JOIN Host 尾节点上方的私有前缀回收

2026-10-28。这是 `JoinPhysicalRecovery.advance()` 的有界祖先遍历修复，
没有修改运行中的冻结应用 `fe0ce6b2`、native `cdd58a4acbb5` 或调度策略。

真实 native CPU 树反例将 donor 的 32 token 拆为 16-token 前缀和尾部，
两段均有 Host 副本，仅尾部已从 Device 释放。前缀仍满足原生
`_can_beliefkv_full_demote(..., full_session_ref=1)`。旧控制器遇到尾部
`value is None` 即停止，因此即便真实空闲只有 4、消费者需要 16，
也无法推进此合法回收。

修复允许经过已经验证身份、Host 副本、锁和 DMA 状态的非驻留节点。
session 标签位于 native session 叶节点；从该已验证锚点向上，私有
内部节点的 `session_ids` 可以为 None，但 `session_ref` 必须仍为 1。
显式其他 owner、共享引用、Host/Device 锁、在途 load/write 均继续拒绝。
实际释放仍由原生 demote 及分配器决定；遍历本身不产生容量额度。
八名 donor、64 节点、200ms 退避和 consumer/donor 重新验证保持原样。

正例现能释放祖先 16 token，将真实 free slots 从 4 增至 20；
随后正常 native H2D 消费这 16 token，free slots 回到 4，独立热 peer
索引保持不变。全 Host 路径不会虚增容量，扫描上限仍会阻止越界遍历。
这些是 CPU copy/event 替身的集成验证，没有建立新的 GPU 或 JCT 收益。

证据保留于 `experiments/reports/control_plan_diagnostics_20261028/`：

- `join_host_tail_red_cpu.xml`：原始已确认反例；
- `join_host_tail_targeted_cpu.xml`：首次修复仍漏掉内部节点标签语义，
  同时保留 shared-prefix 测试夹具的 None 集合错误；
- `join_host_tail_targeted_r2_cpu.xml`：新增在途 load 安全反例；
- `join_host_tail_targeted_r3_cpu.xml`：159 项相关检查通过；
- `join_host_tail_consumer_cpu.xml`：追加实际正常消费者恢复后 20 项通过；
- `join_host_tail_shared_cpu.xml/.sources.json/.evidence.json`：一次完整
  3705 项及六个子测试通过，保留之前全部 3683 项并新增 22 项，
  三个实际构造器及 reference 核心继承隔离通过。

诊断 FULL 的 73 次节点检查有 69 次记录非驻留节点；这不能证明其中
存在 69 个可回收祖先，也不能将它们当作可消除的恢复机会。历史频率、
额外控制成本及完整 GPU 性能效果仍待独立验证。
