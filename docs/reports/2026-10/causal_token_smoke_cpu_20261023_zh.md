# 原生 token GPU 检查的 CPU 准备

日期：2026-10-23，北京时间。应用基线 `dc89d56`。当前 V129 的
冻结采集未修改；本次增加有界数据路径检查入口，不能视为性能实验。

## 预先固定的范围

`scripts/run_causal_token_smoke.py` 按源轨迹的创建时间取前 8 个 root，
每个 root 取最先 3 次模型请求，共 24 次；不同 root 并发、同 context
串行。缺少约定数量就拒绝，不改选较容易通过的对象。

检查保留精确输入输出、原生停止条件、采样 seed、session 和 namespace；
沿用已实现的 HTTP 后端及原生 terminal receipt。它不执行原工具/JOIN
等待，不向策略发送 live control 事件，也不计算 workflow JCT。
清理会关闭本次持有的 session；失败会取消其他并行任务并 abort
已提交请求。原生 stop 证据缺失不能静默通过。

默认最多 8 context、每个最多 3 轮，避免资格检查演变成另一轮完整
native 实验。完整因果回放仍使用另一个入口，保留整个原依赖图、
工具持续时间和正常长尾。这个检查本身永远不独自赋予 GPU/源码资格
或性能验收。

## 最新真实源前缀

V2949 封存 V129 中当时已写出结果至少五秒的全部 21 个 workflow：
2193 次模型请求、209951250 输入 token、792700 输出 token、
24 个 JOIN、48 次 spawn、2821 对工具，以及 1 次内部 CALL/压缩。

完整子集的依赖图和精确 token/停止参数通过 CPU 核查。五秒只用于
正在采集时避开刚结束但后台文件尚未刷新的边界，不是性能筛选。
报告保留独立 JSONL 完整前缀的长度和 SHA-256；不是跨文件原子快照，
也不是整个 36-workflow 源采集已经通过。

## 回归与剩余检查

V2946 的 8 项聚焦检查通过。V2947 的 143 个模块、4681 项与原有
2 个 subtest 通过，无失败、错误或 skip；完整保留上一轮 4673 项，
增加 8 项。沿用一个真实 Docker progress 测试的原范围外 deselect。
V2948 绑定最终应用、测试、配置和 canonical patch 的源码。

后续 GPU 执行还需要完整源采集和审计完成、原进程身份核实退出、
GPU/端口/磁盘与最新 hold 检查、冻结源码与真实 GPU 进程身份记录。
GPU 检查沿用相同模型、seed21、256K context、running8、Host200GB、
80:20 split 和 NUMA preferred1；只激活辅助回放桥接和原生强制输出。

当前仍未取得该入口的真实 GPU 结果。同轨迹 Full/native/简单调度
比较及 KV 管理的增量收益尚未证明，目标继续 active。
