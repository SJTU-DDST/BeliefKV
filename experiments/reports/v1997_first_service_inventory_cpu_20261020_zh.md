# 首次服务记录的库存分类与后续 CPU 重点

日期：2026-10-20（北京时间）。基线 `a346b6a`。
新 GPU 验证最早仍为北京时间 06:00，本轮只执行 CPU 工作。

## 修改范围

`_record_prefetch_first_service()` 原先无论库存是否为空，都创建候选列表，
遍历两次同一份库存，分别寻找匹配命令和超过下一 epoch 的过期命令。
该方法在 native 新请求首次服务时也会调用，不能假定每次都有预测回执。

现在保留原 context/epoch 字段读取，空库存立即返回；非空库存一次遍历
分类匹配/过期命令。仍先发布过期记录，再按原顺序处理匹配命令。其他
context、未来 epoch 和不属于当前消费者的命令继续保留。

没有修改 token 快照构造、原生节点/创建时间/分配身份、物化前缀、FULL
复用证明、Mamba COW/forward 证明或 ACK 前记录的暂存与核准。
早期数组字节复制原型未显示稳定收益，已撤回，不在本次生产差分中。

## 实际轨迹范围

V85d 的 `server/physical_action_use.jsonl` SHA256：
`cc017a55a79012f6b4ebe5442eb01476c3655940a858d9b70449dada1f86d460`。

172 条首次服务命令记录属于 30 个首次服务请求，单请求最多 42 条命令。
96 份 token 诊断快照累计 7709290 token，最长 131072；76 条记录没有
这种 token 快照。没有快照不等于无法根据原生分配身份证明 FULL 复用。
同请求内公共 token 的重复位置为 6409589，不是新增 KV 复用或节省时间。

这些记录没有给出每次回调时完整库存的人口分布。下述 0/1/8/42/128
库存场景为受控比较，不能按旧轨迹计数计算累计节省或加权 JCT。

## CPU 对照

V1998 为最终对照；每组 151 个样本，10 次预热，新旧次序交替。
构造、库存重置、记录比较和实际轨迹读取在计时外。固定墙钟使用普通
函数，不引入 Mock 调用历史。

17 组库存覆盖空、其他 context、未来 epoch、全部过期及混合库存。
其完整返回值、逐条记录、过期发布顺序和剩余命令一致。
单位为线程 CPU us，P50：

| 库存场景 | 基线 | 候选 |
| --- | ---: | ---: |
| 空库存 | 1.148 | 0.611 |
| 42 条其他 context | 7.289 | 3.944 |
| 42 条未来 epoch | 10.031 | 6.721 |
| 42 条过期 | 21.985 | 18.928 |
| 42 条混合 | 37.680 | 34.585 |
| 128 条其他 context | 20.170 | 10.440 |

17 组库存均改善，降幅 8.2%–48.2%。这只是库存分类及其记录方法的范围，
不是整个原生 on_launch 或调度器。

另外 32 组完整方法覆盖 32768/131072-token、1/42 条恢复命令、完整/
首部/中部/尾部分歧、list/native array 输入。逐条记录、消费命令和返回
Mamba 候选相同。输入与 issuance 快照仍各自独立；两个版本读取同一份
只读缓冲区，最后核对内容未变。

30 组 CPU P50 更低，2 组小幅回退，均为 131072-token、单命令、尾部
分歧：list 多 1.971us，native array 多 0.072us。保留这些结果，不宣称
大前缀核验普遍显著加速。大目标仍由转换、比较及原有证明工作主导。

V1993 是单样本基准检查。初次完整 V1994 给两个版本独立分配快照，
出现三项单节点长数组回退约 5.8–19.0us，明显大于库存逻辑差分。
最终改为共享只读缓冲区地址；保存每侧记录列表的副本，防止清空另一
侧输出而掩盖差异。V1994 保留，不能只报告初次较好的场景，也不能把
改进基准后的变化当作新增生产优化。

基准使用 CPU 原生节点/分配身份的替身，没有真实 allocator、DMA、CUDA、
并发 writer I/O 或端到端 GPU 服务，不支持吞吐/JCT 结论。

## 检查与导入来源

扩大检查时，`test_v0520_native_telemetry.py` 中的原生 ComponentType 导入，
早于 `test_waiting_full_tail_backup.py` 的 fixture 修改 sys.path。
初次 V1995 因此使用了 site-packages 的 SGLang，16 个原生 fixture
在 Mamba 引用前提上报错；其余 2242 项通过。

仅设置 `SGLANG_SOURCE_CHECKOUT` 不能替换已经缓存到 sys.modules 的包。
改在 Python 启动前显式配置：

```bash
CUDA_VISIBLE_DEVICES=99999 \
SGLANG_SOURCE_CHECKOUT=/home/longhao/experiment/.worktrees/sglang-v88-idle-control-cpu-20261019 \
PYTHONPATH=/home/longhao/experiment/.worktrees/sglang-v88-idle-control-cpu-20261019/python:/home/longhao/experiment/BeliefKV \
/home/longhao/miniconda3/envs/beliefkv-next/bin/python -m pytest ...
```

启动前检查实际 `sglang` import 来源；V1996 在相同生产代码、冻结 V88
源码下 2258 项全部通过，无 skip。保留 V1995 失败记录，没有跳过原生
检查、改引擎或削弱生产所有权约束。新检查还覆盖混合过期/匹配的记录
顺序、其他消费者保留和空库存下的身份字段验证。

## 后续重点

V1999 绑定 V85d 的 `native_telemetry_status.json`，SHA256：
`19c4c6c4d21b67bf9910d1e983736ba0fdca82b923fe05e37dc220522ee5c3fe`。
writer 没有错误、丢弃或失败记录。已记录后台处理样本如下：

| 类别 | 已见记录 | 样本 | 样本 CPU 均值 | 样本 CPU 最大值 |
| --- | ---: | ---: | ---: | ---: |
| Host block eviction | 1393 | 22 | 6.285ms | 20.294ms |
| request probe | 1647 | 26 | 6.343ms | 15.820ms |
| context prefix probe | 1728 | 27 | 1.187ms | 3.350ms |
| served context prefix | 1728 | 27 | 0.464ms | 1.241ms |

这是每 64 条记录采样的实际处理时间，排除队列等待和状态发布；不乘 64
推算全量，不据此断言 GIL 争用或吞吐因果。下一步优先 profile Host
驱逐/重访 writer 的前缀构造、哈希和匹配，保留 namespace、HMAC、匹配、
计数及失效语义。当前库存局部改善不足以说明最终性能目标已完成。

V88 引擎、补丁及 24-root 配置未改。main 提交后更新原 06:00 等待进程
的合格代码和冻结执行目录；GPU 空闲后先 predictive、后 native。
旧 V85d native 不恢复，最终仍以去长尾吞吐、平均 JCT 和有效迁移验收。

- [V1998 最终对照](v1998_first_service_inventory_shared_fixture_cpu_20261020.json)。
- [V1996 合格来源下的 CPU 检查](v1996_first_service_inventory_qualified_checks_20261020.xml)。
- [V1995 初次来源混用检查](v1995_first_service_inventory_cpu_checks_20261020.xml)。
- [V1994 初次独立快照对照](v1994_first_service_inventory_cpu_20261020.json)。
- [V1993 基准检查](v1993_first_service_inventory_fixture_check_20261020.json)。
- [V1999 后台采样优先级](v1999_v85d_native_writer_sample_priority_20261020.json)。
