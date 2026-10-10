# V16 中止诊断与 V16b 冷启动

日期：2026-10-10。V16 在 13:55 CST 因实现错误停止，没有运行 native
侧，不能作为完成的性能对照。原冻结提交为 `1c0a3ea`；原计划和 trace
继续保留在 `experiments/raw/qwen35_native_predictive_replenished_108plus48_20261010_v16`。

## 确认的错误

workflow `django__django-11239` 的 child
`deepagents-invocation:86e700cb37f89c3c` 在 epoch14 的同一模型请求中连续
执行两条 grep。第一条工具 END 后，第二条 START 改变 invocation revision，
context epoch、session 和 checkpoint 都未改变。旧的租约判断却要求 revision
保持一致，使两条预取命令在 ACK 后约14ms以 `wait_episode_changed` 释放。

命令为 `beliefkv-prefetch-bf149538cad7464a8b99f3a3dfa5eb24` 和
`beliefkv-prefetch-f64ed4fa3d934fe8bc194c8857b521f6`。ACK 时间分别为
1791611324122.324/1791611324122.3323ms，释放为
1791611324135.5798/1791611324135.544ms。下一条 epoch15 请求
`beliefkv:01a1245b-474b-7432-be2c-d28a46b7637b` 的两条 FULL 首次复用证明
均为 false。后一节点还观察到首次服务前原生 reload；旧合并批次的分池
证据仍不能推导精确重复分配字节。记录证明错误释放与随后未复用，不能
单凭这条轨迹断言修正后必然节省多少 JCT。

修复删除同 epoch 工具进度的 revision 相等要求，保留 epoch、session/
generation、节点代际、终态、物理驻留、有效期及容量边界。未扩大租约。
相关249项检查通过；新的分段等待审计19项检查再次通过。

## 工具边界与等待

旧的采样 active-tool 集合会漏掉第二条连续工具，从而将第一条 END
误认为整轮完成。现按同epoch原始请求到下一请求的半开区间收集全部
START，要求每条都有 END；缺边界或END保持未知。该故障的完整工具边界
为1791611324187.5745ms，共2条工具。事件审计确认提前且实际复用 FULL 为0。

| 间隔 | 本条请求 |
| --- | ---: |
| 完整工具轮次结束→客户端 LLM_SUBMIT | 60.97ms |
| 客户端 LLM_SUBMIT→原生请求到达记录 | 6210.00ms |
| 原生到达记录→匹配 worker 首次服务 | 10244.46ms |
| 最后 ACK→匹配 worker 首次服务 | 16580.67ms |

客户端到原生到达包含请求准备、传输及 serving 前端工作，现有日志无法
分摊到具体函数。worker 服务采样与节点首次复用回执是不同观测点；两者
不能混用，也不能把上表总等待当作 H2D 传输耗时。

## 停止快照

`predictive_h2d/prefetch_lifecycle_stopped.json` 保存重新审计的逐命令与
独立事件证据。JOIN/tool分别26/17条已ACK命令，完整独立事件提前且
复用FULL分别102.25664/25.70240MB，共127.95904MB。需求handoff3787条，
已知FULL传输15.41132GB、确认复用15.40325GB；停止时未完成消费证明的
16条保持未知。handoff不能记作预测提前覆盖。JOIN/tool仍有10--28秒的
ACK到服务长尾，不能宣称恢复与准入问题已经解决。

该轮中止时只有1条workflow写出result，其他中断不计为模型incomplete。
先保存相对base commit的二进制patch及所有untracked文件（含ignored），
再移除108个workspace、107个精确匹配原审计和挂载路径的容器。归档位于
各workflow的 `stopped_workspace`，清单为
`predictive_h2d/stopped_workspace_cleanup.json`。原始trace、回执和
冻结计划保留。目录由约20GB降为620MB，可用磁盘由约184GB升至203GB。

## 继续执行

新目录V16b保持156任务、108+48/3600秒到达、running48、
Host200GB80:20、HBM Mamba/FULL0.9、context131072/completion8192、
graph2048/reserve32、workflow14400秒、工具硬上限180秒、
`native_in_graph_2to4`、seed21/temperature0及既有模型产物。先predictive，
再冷启动同版本native。规范引擎patch保持
`68109d51ceb77f92fcbc2d17b52cfe76717b4ad26c47fd3642923b43ed0ca615`。

继续以繁忙窗口服务吞吐、GPU利用率、配对JCT、控制独占耗时、独立事件
提前FULL消费及原生残余恢复判定目标；全程工具长尾、ACK数和字节数不
作为目标达成证据。
