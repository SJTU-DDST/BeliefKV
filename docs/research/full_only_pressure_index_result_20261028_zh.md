# FULL-only 索引修复完整结果

2026-10-28。冻结应用 `a5164fe3` 的完整 FULL 已结束，未达到明确、
可重复的双指标收益目标。平均 JCT 1651.727199 秒、全程输出吞吐
235.457746 token/s；相对完整 V345 工具衰减 ThunderAgent 改善
2.70128% / 0.89383%，但比准入前卸载 FULL 多 20.532555 秒。
局部 CPU 索引加速没有建立整轮 JCT 改善；本轮还包含祖先回收修复，
不能独立归因于其中任何一项。

## 完整比较

| 完整系统 | 平均 JCT/s | 全程输出 token/s | decode worker token/s |
| --- | ---: | ---: | ---: |
| 准入前卸载 FULL | 1631.194643 | 238.499008 | 346.325953 |
| 上一轮控制诊断 FULL | 1642.310207 | 235.033300 | 348.245457 |
| 本轮索引修复 FULL | 1651.727199 | 235.457746 | 348.574006 |
| 复用 V345 工具衰减 ThunderAgent | 1697.583645 | 233.371801 | 340.158312 |

本轮和对照均完成 18 个不同项目的 18 workflow、3674 请求、
145565966 输入和 549671 输出 token、72 JOIN，原生停止和全部正常
尾部保留。工具时间仍为固定 V311，Device805198/Host200GB、
running/decode24、seed21、流式测量不变。工具预取、优先窗口、
普通恢复绑定及 profiling 均关闭。

本轮 native 为 `cdd58a4acbb5`，V345 和准入前卸载为
`11b84f7c3198`。上一诊断同为 `cdd58a4acbb5` 但 profiling 开启。
这些差异保持公开，各配置只有一次完整结果，不能当作独立重复或
单模块消融。两次新鲜 idle 快照、退出零及实际 owned 进程退出通过。

相对 V345 有 15 个 workflow 更快、3 个更慢：fintech_trading
+73.658365 秒，web_blog +2.012000 秒，web_cms +2.046480 秒。
workflow TTFT 均值 136.817300 秒 versus 139.028403 秒；每 workflow
最大 post-first ready-output gap 的均值为 79.022491 秒 versus
73.449107 秒，后者更差。这些回退均保留。

## 服务后时间仍然增加

本轮与准入前卸载的 18 条实际关键请求链逐项相同：

| 全部关键请求分量/s | 相对准入前卸载的增量 |
| --- | ---: |
| 提交至首次服务 | -9.361696 |
| 首次服务至 native 完成 | +29.911890 |
| 其中自己的 prefill worker 区间 | -1.354393 |
| 其中自己的 decode worker 区间 | +16.888229 |
| 其中请求未参与的其他区间 | +14.378054 |
| 最终平均 JCT | +20.532555 |

原始问题的 +24.981494 秒属于旧分组 `913e3238` 的关键工具后继，
不能替换成上表全部关键请求的 +29.91 秒。对同一组 2973 个关键工具
后继再次按实际阶段核对：本轮其他 prefill-only 区间比准入前卸载多
16.683404 秒，其他 decode-only 少 0.115054 秒，跨阶段及所有
worker 并集之外均为零。

这些工具后继仍有 464588 次有效 decode 成员观测，时间加权实际 batch
为 10.471511，batch 平均上下文为 59513.970622 token。所有成员
区间并集和关键路径守恒通过。它们仍是带运行时和依赖等待的 worker
区间，不能用来排除 CPU 控制、证明纯 kernel 变慢或直接扣减 JCT。

下一步优先检查其他 prefill 插入时机、批次组成及其恢复依赖；继续
增加卸载次数或提高工具后继优先级没有收益依据。历史增量控制 CPU
仍未知，本轮关闭 profiling，也没有建立暴露 CPU 延迟的计量。

## 实际机制

H2D/D2H 为 2735.615312/1281.826849 GB，相对准入前卸载增加
20.927840/149.461598 GB。原生 untagged demand、请求 Host 命中及
handoff 字节完全对账。两个实际 8/4 handoff 恢复 2.776302 GB，
约占 H2D 的 0.10%；不能把全部迁移增量归因于这两个交接。

88 次 JOIN 物理尝试有 62 次无安全 donor、15 次已无缺失、4 次消费者
失效、3 次发备份等 ACK、2 次容量已经足够、2 次实际释放。
两条释放日志共 226652 token，与计数一致。735 次 Host-only extent
遍历不是 735 个机会；累计释放不代表同时空闲容量或消费者页分配证明。

708 条 JOIN 命令均验证复用，共 154.898792 GB；63 个已观察预取
JOIN 事件中，18 个提前启动并复用，共 62.677844 GB。源轨迹的
72 JOIN 和这 63 个已观察预取事件属于不同分母。工具预取命令为零。

20 个并行 JOIN 的 1062 条 sibling 服务日志、1075 个请求准入尝试
均绑定实际 native batch 和完成 session，包括 13 个多成员 prefill
批次。250 次逻辑 grant 对应 255 个 program 服务关联：252 个验证、
3 个 native identity 仍未知；不把日志次数当作独立 JOIN 或收益分母。

本轮 34 个恢复等待样本合计 4090.837ms，准入前卸载 27 个合计
4272.785ms，V345 18 个合计 2662.835ms。cohort 不同，均不外推、
不相减解释整轮收益。最终同 session 旧输入探针的 140952652 个
共同 token 中缺失量为零；这不是所有 kernel 重算量的证明。

## 证据与后续

完整算子、JSON、原字节 gzip 归档位于
`experiments/reports/full_only_pressure_index_20261028/`：
`complete_review`、`mechanism_review`、`complete_jct_paths`、
`complete_service_interleaving`、`complete_wait_prefetch`、
`complete_sampled_restore_waits` 及 `final_index`。

54 个 EOS 触发均有真实最终 RETURN 证明，四个已知工具/EOS 反例均
通过。原 guard 算子因为要求同一 engine 和完全相同 overlay 路径而
拒绝本轮；其原始拒绝及 traceback 保存在 `complete_guard_cases.json`。
新的 `known_guard_review.py` 只显式绑定已审核 engine pair 及同字节
工具时间路径，复用原 classifier，保持其他工作量和 serving 检查；
生产 guard 和原算子均未修改。

三幅完整趋势、分布和 geometry 图已实际打开核对，位于
`experiments/figures/full_only_pressure_index_20261028_r1/`。保留真实尾
bin、不在结束后补零。原始 CRLF CSV 以同名 `.csv.gz` 保存，提交的
CSV 仅规范为 LF，已逐字节核对。GPU 和 CPU 绘图进程均已退出。

复用完整 V345，没有启动其他 baseline、消融或下一项 GPU 实验。
明确、可重复、所有 baseline 的最终目标保持未完成。
