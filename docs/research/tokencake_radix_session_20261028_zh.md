# TokenCake 原生 session 资格准备

2026-10-28。当前 KVFlow 正式对照仍在运行，冻结应用6dc4b892与登记
保持不变。本轮只做 CUDA-hidden、nice19 的 CPU 检查和后续 GPU
资格入口准备，没有启动并行 GPU 实验。

## 实际检查范围

原 TokenCake 组件已使用真实 native Req，但没有 session。本轮新增
完整输入、原生 session_id/generation、空 extra_key/cache_salt 的
实际 Req，并通过真实 PrefillAdder 获取消费者锁回执，检查：

- 已缓存 session 前缀的实际准入、完成和容量结清；
- 两个 session 的共享前缀只计一次，同时保留两个原生所有者；
- 工具阻塞期间保存，按4/2/1/1 token分批恢复，并由同 context 后继消费；
- 工具在第一批 H2D 中途返回，消费者先等待，随后由 native demand
  恢复未上传的4 token，原生锁接管后释放可撤销保留与 Host lease。

四个新增用例均通过。生产 TokenCake 的调度、优先级、保留策略、
传输和容量实现没有改动。不能把这项兼容性验证描述为性能优化收益。
CPU 的 copy/event 是 stand-in，不提供真实 DMA 或字节恢复证明。

第一轮两项通过、两项失败：旧测试辅助只设置了简化锁字段，真实 Req
具有单独的 lock_receipt，因此安全检查正确拒绝；另一个断言混淆了
工具阻塞期间 registry 的保留与工具返回后 checkpoint 接管的 lease。
第二轮还有一个错误断言：被移除 checkpoint 对象上的 lease ID 字符串
不必清空。最终检查实际 manager 归属、预留、原生需求 H2D 和 ACK，
没有为测试放宽生产安全条件。全部原 XML 保留。

## GPU 入口扩展

`qualify_tokencake_runtime.py` 保留原六个 plain-token 场景，另外对
full-prompt native radix session 执行相同六项：完整上传、D2H中途返回、
H2D中途返回、过期、退休和压力撤销。原来的字节、独占容量、native
consumer回执、需求回退和pin清理门槛均保留。

新 session 组另外核对实际 session 请求数、三个不同的有效原生
generation，以及 native source anchor/所有权。结果验证器要求两个
组分别完整，不能用 plain-token 结果或顶层成功标记替代 session 证据。
这十二个场景尚未在 GPU 上执行，正式 TokenCake 也尚未启动。

## 合并证据

`experiments/reports/tokencake_radix_session_20261028/` 保存
`run_bound_cpu.py` 和 `bound_cpu.json/.xml/.sources.json`：
同一次执行742项全部通过、无失败或跳过，完整保留此前726个用例，
新增16个，1873个源码文件在执行前后相同。原生为未改动的 clean
common11b84f7c3198；当前 KVFlow 的 frozen tree 不受影响。

同目录 `kvflow_collecting_while_cpu.json` 是独立 live-prefix 审计，
封存2274个对照一致的完成回执；实际 watcher/server/driver 存活，
预算字段未知时保留 null，不计算部分完成 JCT 或整轮性能。
该前缀不能替代完整3674请求、正常尾部和实际退出审计。

后续先冻结当前来源，再等 KVFlow 的实际 supervisor退出、owned清理
和新鲜资源检查后执行十二个组件。组件通过后才继续正式 TokenCake
配置及入口检查；Agentix正式臂仍未启用。保持同一 V230/V311 完整
工作量、不重跑 native/V345、不启动消融，整体目标仍 active。
