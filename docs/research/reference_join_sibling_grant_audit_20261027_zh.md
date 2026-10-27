# 共同准入的实际执行证据

当前 FULL 仍从冻结 525c7cc66e1f 运行，注册为
`ops/reference_join_siblings_20261027_r1.registration.json`。本次增加的
`scripts/audit_reference_join_grants.py` 只做离线读取，没有更改正在运行的
调度器、配置、native 引擎、请求或工具时间，也没有登记新的 GPU 任务。

## 审计方法

新的授权日志提供完整 request/workflow/invocation/context/epoch/
attempt/session/generation。计算样本只有部分身份，不能仅凭 agent ID
或授权计数推断已经恢复和执行：

- 先检查每个逻辑授权的完整上下文加 100-token buffer、组内唯一 owner、
  同 workflow、授权前后未衰减预算，保留每次授权。
- sibling 绑定该次实际提交的 request；parent 通过实际 JOIN 后继绑定
  下一请求，不能随便找这个 parent 的某个未来请求。
- 结合原生完成回执核对 request、四元上下文身份、session、generation
  及服务 clock domain。服务样本没有 attempt 字段，明确不声称逐 attempt
  验证。
- 只用 native server wall time 计算授权到服务间隔。客户端 JOIN 事件
  仅用于选择后继身份，不与 server 时间相减。服务入口是原生
  `run_batch` 的记录，不是 CUDA kernel 开始。
- 对同 owner 的后续授权建立前次关联的上界，避免把同一次未来服务或
  复制重复算给多次授权。已经在授权前服务的请求另作标记。
- 可选读取同 arm 的已封存 prefetch report，继承其已有文件前缀，
  结合 version-2 实际 FULL 复用证明关联复制；其他字节保持未知。
  逻辑 Host hit 不直接折算为单次 DMA。
- 没有完成、身份或边界证据的记录保留为未知，不删除。两个新授权成员
  的服务间隔只代表这两个成员，不代表所有 sibling 同时开始/结束。

各 JSONL 单独按完整行封存并记录 SHA256。已有 prefetch 前缀不能因文件
继续增长而扩展；该报告未读取的完成回执由审计器另外封存。因此不同
审计执行的 receipt 前缀可能更长，不声称获得了原子系统快照。

## 已观察到的前缀

`collecting_pipeline_with_grants.json` 独立核对 1722 个完成请求与完整
V345 的 token/hash/原生停止一致。它不验证剩余请求，不将前缀吞吐与
完整 V345 比较；reference 只公布聚合预算，逐 agent token-time 仍未知。

`collecting_prefetch_mechanisms.json.gz` 中有 30 个独立 JOIN ticket，
其中 29 个观察到完成；26 个在完成前有正 FULL 计划，3 个实际提前
提交，其中 2 个已确认提前传输后的复用，合计 6.5691648 GB。
另一个的使用证据在该前缀中尚未齐备，不能补算成成功或失败。

`collecting_grant_service_validated.json` 在这些封存输入上观察到：

| 观察 | 数量或区间 |
| --- | ---: |
| parent free-JOIN 新授权 | 2 次 |
| sibling 授权 | 16 次、19 个请求 |
| 合计有完整身份和后续服务证据的请求 | 21 个 |
| sibling 授权至下一原生服务入口 | 22.242--166.213 ms |
| 两个 parent 授权至后继服务入口 | 127.280 / 246.217 ms |
| 同一次授权两个 sibling 的次数 | 3 次 |
| 上述三个新授权组的服务入口差 | 0 / 0 / 137.904 ms |
| 与 parent 授权关联且确认复用的 FULL 复制 | 12.524519424 GB |

最后一项包含 JOIN 完成后的传输，不能与提前复用的 6.569 GB 混用。
服务入口差为零表示同一个原生 batch 入口，不证明 CUDA 同时执行。
这些请求都在本次授权前没有观察到服务；仍不能据此推断与没有授权的
反事实相比加速多少，更不能当作完整 JCT、吞吐或新算法贡献。

审计还保留了一次提前恢复约 0.403 GB 后失去保护的观察，以及最长
ACK 到首次服务约 127.6 秒的记录；它们属于这个前缀的其他恢复路径。
新增授权的快速接入不能替所有已有资格的预取证明及时消费。

## CPU 边界验证

最终 104 项定向 CPU 测试全部通过，包含：

- 原生 session/generation/epoch/clock/request 不符时不能取得服务证明；
- parent 只绑定真实 JOIN 后继，不绑定其他相同 context 请求；
- 重复授权/复制不会重复计数，部分组的服务未知值保留；
- 旧版复用证明、不相关 JOIN/请求和授权前复制不取得授权关联字节；
- 错误预算、重复 owner、缺损/重复 service 序列拒绝；
- 已导入相对路径的 prefetch 前缀在文件追加后仍保持原有边界。

初稿 4 个失败及后来 1 个重复服务样本失败分别保留在
`grant_audit_initial_cpu.xml` 和 `grant_audit_service_guards_initial_cpu.xml`。
这些是新离线审计器的开发反例，不是运行中的 FULL 失败。
最终报告为 `grant_audit_final_cpu.xml`；早期
`collecting_grant_service.json` 保留，最终经过完整边界验证的报告另存，
两次授权/服务/组结果一致，完成回执的封存时刻不同。

本次没有重新执行整个 shared suite，也没有更改已通过 3174 项共享
验证的运行时。当前任务仍需完整结果判断平均 JCT、全程输出吞吐、
正常长尾和额外迁移，再决定后续优化。
