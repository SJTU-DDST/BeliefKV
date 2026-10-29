# 逐层交接组合私有 donor 与真实空闲页

2026-10-29。与新的 warm-start ThunderAgent 对照并行开发的候选。
现已完成 4713 项 CPU 检查、103 项原生检查、四个真实 GPU 组件场景及
11 次真实模型调用；同到达表的 Full 已启动，完整性能结果仍待收集。

## 处理的限制

原实现要求一条未备份、可安全卸载的私有 donor 路径，单独覆盖消费者
全部缺失 Host 前缀。假设消费者需要恢复 16 个 token，donor 私有尾部
只有 14 个，另外真实空闲 4 个，其中 2 个必须留给输入与增长：资源
实际上足够，但原协议仍然拒绝。共享前缀不能算作 donor 的私有容量。

新候选允许：

```text
完整恢复 token <= 私有 donor token + 真正空闲 token - 输入与增长预留
```

从消费者的完整缺失路径计算需求；只扫描现有的有界私有路径。
额外所需页必须通过原生 allocator 立即分配，分配之后仍满足输入与
增长预留。消费者的索引前缀来自 donor，后缀来自本次分配；二者合为
同一段索引张量。低层接口要求显式、精确的新增容量数量，并保留两方向
索引前缀的存储别名检查。

如果现有空闲页已足够，新增候选不制造 donor 备份，而是回到正常需求
恢复。已有 Host 副本仍直接 demote，不为了增加 handoff 数量制造 D2H。
本改动不增加 donor 资格，不暂停 ready agent，不改变 ThunderAgent
的 program 容量或 JOIN 逻辑优先级。

## 传输与所有权

继续使用已有 D2H8/H2D4 层组。每个 H2D 层组在覆盖其所有重用层的
D2H 事件之后发起；新页后缀也与本组一起加载，消费者的层就绪事件
覆盖整个完整前缀。复制仍在现有两条流执行，没有额外逐层调用或新的
后台扫描。较小 donor 可能减少新增 D2H 及后续 donor 恢复，但不保证
H2D 一定赶得上计算。

Host 目的容量只按实际 donor 私有范围申请。身份、session generation、
共享页、Host/Device 锁、在途拷贝、单事务、索引上传及旧 forward
栅栏继续生效。Host 分配、成本授权或新增 Device 页分配拒绝时，不发布
缓存状态、不提交复制，并退还已分配的 Host 目的页。异常提交仍按原有
致命错误处理，不能在 DMA 可能在途时回收目的页。

Device 记账中，消费者获得 donor 重用页和新分配页；这两部分均不进入
free pool。donor 超出消费者范围的尾部仍仅在真实 D2H ACK 后释放。
更早收到消费者 ACK 不解除 donor 备份依赖。未改变全部层 KV 共用
token 索引的布局，仍不支持多 donor 的单一逐层事务。

## 接入与验证

开关 `BELIEFKV_LAYER_HANDOFF_FREE_EXTENT` 默认 0，仅 Full 可使用，
依赖已有 layer handoff。attach 阶段要求新 native 明确声明支持，
旧引擎拒绝；paper controls 清除此开关。preflight 与在线状态单独
核对它，不能将请求配置当作实际生效证明。

`layer_handoff_reused_tokens` 仅计 donor 重用范围；
`layer_handoff_free_extent_tokens` 计新分配页；
`layer_handoff_restored_tokens` 计实际完整 H2D 范围，避免把全部恢复量
都归为 donor 交接。

原生分支 `beliefkv-layer-free-extent-20261029`，提交 `c5ad39a1e128`，
基于原 `d3da19c1` / tree `729aa0caa7a1`。
可重建差分在
`patches/sglang-layer-free-extent-delta-20261029.patch`。
这是基于该明确父提交的差分，不是替换通用 staging patch 的累计补丁。

首轮定向测试保留为失败：测试 helper 错误地为一个已存在的共享前缀
重新分配整段页，在小池中耗尽容量。改用实际 native session registration
引用已经存在的前缀后，127 项定向检查通过；没有为此放松生产容量保护。
后续联合回归和原生 seal 位于
`experiments/reports/layer_free_extent_20261029/`。

联合回归实际通过 **4694 项和 6 项子测试**，保留前一份 Full 及本次
ThunderAgent CPU 报告的全部 4627 个并集身份；绑定 1910 份输入，检查
前后应用与原生源码不变，无失败、错误或跳过。新增分组用例同时覆盖
8/4、4/8、5/3、3/5，核验不同方向粒度及所有被覆盖层的依赖。
原生提交树为 `434c35dd66211d0a230717c88bf1452056769a40`，
seal 核验精确五文件差分及反向 apply 检查，未将此树加入正式启动白名单。
该句保留首次 CPU seal 时点的边界。后续累计补丁
`sglang-layer-free-extent-runtime-20261029.patch` 将相同精确树接入
`layer_free_extent` flavor，保留默认关闭及新组件、模型、位置/采样检查。
累计补丁 SHA256 为
`c3a287b69d8f400f722996930fb6aad6ddabaf1f8859fc98e794f556887b1e15`。

CPU 用例使用真实原生 radix、allocator、session 和锁，拷贝与完成事件
使用替身。它们不能证明 GPU 字节、真实跨流并发、非强制模型数值或
完整 workflow 性能。后续真实 GPU 与模型证据单独记录在
`../research/free_extent_gpu_qualification_20261029_zh.md`，
不将 CPU 替身结果当作实际 DMA 结果。

模型 R3 通过了独立 namespace/session 中的五组生成结果比较，生成 token
和 logprob 均完全相同；原始 11 对请求/响应已再独立核对。
新的 24 请求位置/停止检查及 batch1/8/24 采样检查通过后，正式
`warm_start_free_extent_20261029_r1` 从冻结 `e5f77eff` 启动，端口 18861。
使用与新 ThunderAgent 相同的完整工作、工具时间、10+8 到达表和容量。
它仍是组合系统比较，不是单独 handoff 的性能归因；负收益继续按用户
要求关闭或回退。
