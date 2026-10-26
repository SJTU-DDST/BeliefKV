# 已空闲页的 H2D 依赖候选

本轮没有启动 GPU。预约仍截至北京时间 2026-10-27 11:00。
新 engine 在独立 worktree 开发并提交；不改变冻结实验树或中止结果。
本候选补齐预取与计算重叠的一项底层条件，尚未证明 JCT/吞吐收益。

## 为什么先做这一项

v235 的完整控制计数中，工具预取扫描 2553 次，资格范围外计数 12，
无恢复目标计数 1394，时间窗口外计数 1420，条件尾部分布不支持计数
985，工具预取实际发行零。以上是可重复触发的控制分支计数，不能
相加当作互斥工具调用数。此来源没有证明 inactive warming 是主要
损失，因此保留其原有门槛，不为了增加预取而驱逐 ready agent。
证据为 `experiments/reports/v239_tool_warming_gate_audit_20261026.json`。

原 engine 在 H2D 提交前统一调用
`host_to_device_stream.wait_stream(load_fence_stream)`，
其中 fence 指向 forward stream。如果目标页早已空闲，这会把
当前已经提交、使用其他页的计算也包含进等待，可能损失重叠窗口。
这里识别的是代码依赖，尚未用 GPU 时间线证明它对 v235 的实际贡献。

## 安全证明与实现

每次真正释放 token 页、分组发布释放或 pool clear，递增
`TokenToKVPoolAllocator.beliefkv_free_generation`。同一 generation 内，
分配只缩小空闲集合。scheduler 在下一次 forward 提交前记录一个
不可重新录制的 GPU event，保存 allocator 身份及 generation。

如果 load 分配时两者仍匹配，目标页必定来自记录 event 时的空闲集合；
H2D 等待该 event 即覆盖旧 writer，无需等待当前使用其他页的 forward。
已有 target 分配后的其他释放不会改变它已经取得的证明。
每次 generation 变化创建新的 event，旧 operation 保持旧 event 引用。
generation 不变时重复检查直接返回，不逐 decode 步创建 event，
也不扫描 free list 或逐页维护 Python 元数据。

一个合并 load 的所有子 operation 必须携带同一个证明，并且没有
额外 pool transfer，否则整个 load 保留原 forward-stream fence。
新回收而未重新建立 boundary 的页、未证明页、其他 allocator、
Mamba/额外 pool、非 overlap 和 speculative 执行路径继续回退。
producer start event、逐层完成依赖、ACK、共享锁及原生分配权不变。
没有新增容量、ready-agent 驱逐或普通 execution-frontier 恢复权限。

默认 `BELIEFKV_FREE_PAGE_H2D_FENCE=0`。显式实验参数为
`FREE_PAGE_H2D_FENCE=1`，在比较各 arm 中共同传递。
**同样的证明用于 native demand H2D 和 controlled prefetch。**
不会让 baseline 承担事件开销却无法使用已证明的优化。
因此启用后的正式比较需重新运行同样开关的 ThunderAgent，
旧 v232 仍保留，但不作为此次共同开启后的对照结果。
不需要重新采集 v230，也不额外重放完整 native。

## 来源与验证

Native base commit：`fa5cb52afaeaad1f59f6d3eb2c5cf124f72bb491`，
tree 为已验证的 `6437d7deeacc533f0d795ba166dfd5256b3f44b2`。
候选 commit：`d3f9f37648be64af598fb936b7631fa8e6c215ba`，
tree 为 `11b84f7c31986782be3fbe699bae2574ef092389`。
补丁为 `patches/sglang_v0520_free_page_h2d_fence_20261026.patch`，
只在 base tree 之上应用，已逐字核对其等于提交差异。
完整声明为 `configs/migration/predictive_free_page_fence_20261026.json`，
尚未登记 GPU 作业。

CPU 使用真实 native allocator、HiCache/Hybrid controller、CPU KV 张量，
配合有时序的 event/stream 替身。覆盖旧 writer、当前无关 forward、
真实页复用、分组释放的两个阶段、排序释放、clear 防 ABA、同/异证明
的合并、callback 拒绝与 native demand。提取实际 scheduler 绑定和
run_batch 前导语句，验证 event 先于新 forward，并覆盖不支持模式。
实际 CUDA 时间线与物理异步行为仍待 GPU 验证。

共享报告 `v239b_common_free_page_fence_shared_cpu_20261026.xml`：
1815 项及四个子测试，无失败/跳过，完整保留上一轮 1739 项。
Native 报告 `v239b_common_free_page_fence_native_engine_cpu_20261026.xml`：
726 项及 57 个子测试，无失败/跳过。
早期 v239 限制为 controlled transfer 的中间版本及其 CPU 报告保留。
原始合并 invocation 的 45 个 application 用例因跨 pytest root 失去
classname；单独从 application root 重跑这 45 项并全部通过。
`v239b_free_page_fence_native_engine_bound_cpu_20261026.xml` 组合其正确绑定
与原始 681 个 native 用例，逐项确认保留旧报告全部 726 个身份；
原始报告不改写，绑定与 hash 依据另存
`v239b_free_page_fence_engine_projection_cpu_20261026.json`。

严格 AST projection 只移除已审阅的三个 helper、两个 load 绑定、
allocator 代次、scheduler 绑定/前导以及 fixture 的方法选择扩展。
剩余五个文件必须与 source 完全一致，其他文件变化拒绝复用资格。
它证明修改边界和 default-off 投影，不证明 GPU 正确性或数值等价。
运行时记录 requested/enabled/supported 及真实 boundary、早事件和
原 fence 批次数；operator 拒绝声明启用但没有执行 boundary 的结果。

`v239b_free_page_fence_control_cpu_20261026.json` 在不变空闲集合上每档
测量 30 万次实际 Python guard，含循环开销：关闭均值 0.066 微秒，
开启均值 0.173 微秒。开启档只有一次 fixture event 记录。
不包含真实 CUDA event 创建/record、allocator、driver、copy 或 GPU
时间，不能把这组数字当作完整控制开销或加速比。

## 后续验收

预约结束后先在独占设备做有旧 writer 的页污染/恢复测试，确认所有层
K/V 字节、ACK 和 allocator 容量，并验证真实无关 forward 与 H2D 重叠。
再完成 unforced session-bound 模型数值检查；forced-token 同工作量
重放不能替代该项。通过后再按需要进行 Full/ThunderAgent 完整比较，
保持同请求、token、工具时长、容量、种子和传输协议，保留普通长尾。
同时检查提前 copy 是否增加计算竞争或重复迁移。
若暴露等待占比很小，不应继续堆叠 allocator 改动以追逐很小的窗口；
原有平均 JCT 和全程吞吐同时改善的目标仍然必须满足。
