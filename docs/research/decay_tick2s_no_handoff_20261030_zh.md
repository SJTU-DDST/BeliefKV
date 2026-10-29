# 两秒控制 tick 与关闭逐层 handoff

日期：2026-10-30，Asia/Shanghai。用户最新要求：确认 acting decay，
适度缩短其控制周期，并在后续实验关闭逐层 handoff。

## 三个不同的时间参数

| 参数 | 原值 | 本次 FULL 候选 | 作用 |
|---|---:|---:|---|
| acting decay 半衰期 | 1s | 1s | 按真实 ACTING 已等待时间折扣 resume 容量估计 |
| 完整工作集控制 tick | 5s | 2s | 应用衰减、尝试 resume、核对未折扣容量和压力暂停 |
| 免费逻辑额度补位周期 | 0，另有历史 500ms 候选 | 0 | 只使用完整剩余额度补位，不暂停 peer、不复制 KV |

500ms 是补位检查周期，不是完整调度 tick，也不是 JOIN ticket 有效期。
当前 Full 由 `ReferenceThunderAdmission` 继承真实
`ThunderAgentDecayAdapter`，其 `ThunderActingDecayWorkingSet` 在 resume
时使用 `tokens * 2**(-elapsed_seconds)`，半衰期一直为一秒。
五秒是检查机会的间隔，不是五秒才给 token 乘一次固定系数。

本次只使控制 tick 更频繁，不改变衰减公式或压力计算：周期性 resume
可使用衰减估计，随后未折扣的容量检查仍生效；真实物理容量由原生
allocation、锁与 ACK 决定。不能把衰减的逻辑额度算作已释放 HBM。

## 代码与配置

新增显式 `AGENT_CONTROL_INTERVAL_MS` /
`BELIEFKV_AGENT_CONTROL_INTERVAL_MS`，只允许 FULL 的
`thunderagent_reference` 组合使用非默认值，范围 500–5000ms。
默认仍为 5000ms，paper/native/其他 control 的环境明确恢复为
5000ms。没有设置新 timer 线程；既有 tick 的提前返回检查使用实例间隔。
实际 runtime、agent status、CPU preflight 和完整结果 validator
共同核对声明值。

新的声明配置：
`configs/migration/predictive_decay_tick2s_no_handoff_20261030.json`。

| 开关 | 候选值 |
|---|---|
| 完整工作集 tick | 2000ms |
| 500ms 补位候选 | 关闭 |
| `LAYER_HANDOFF` / `BELIEFKV_LAYER_HANDOFF` | 0 / 0 |
| `BELIEFKV_LAYER_HANDOFF_FREE_EXTENT` | 0 |
| JOIN/EOS 预取、JOIN 阻塞预算交接 | 保留 |
| 工具预测卸载、prepare_host | 保留 |
| 原生需求恢复流水线 | 保留 |
| 工具、普通 frontier 预取 | 关闭 |
| prefill / mixed / running | 2048 / 关闭 / 24 |

逻辑 JOIN 阻塞资格交接与物理逐层 D2H/H2D handoff 是不同机制。
用户关闭的是后者；普通 HiCache 和原生需求恢复的数据依赖没有被删除。
原生树继续使用已验证的 `434c35dd66211d0a230717c88bf1452056769a40`，
本次没有修改 native copy、allocator、sampler 或逐层 fence。

## 队列替换边界

原 2048 ThunderAgent 工具衰减对照仍由冻结 `f502f87d` 运行在 18881，
保留作者五秒控制周期。不修改其输入、计时、数据面或已取得的工作。

旧 `prefill2048_mean120_20261029/queue_r2` 的 CPU watcher1446109
已根据当前用户要求发送 SIGTERM，并独立确认实际退出。该动作没有
发送给 GPU server、scheduler 或 replay driver。停止时原 FULL 的
position 与正式 registration 均未创建，记录在旧队列目录的
`user_superseded_stop_r1.json`。原状态快照保留，不用陈旧存活字段
代替实际 PID/start-ticks 检查。

新 `queue_full.py` 将等待既有 ThunderAgent 的完整 3,674 个请求、
新鲜空闲排空及实际 owned 退出，然后执行当前源码的 fresh position/
token/session/native-stop 与 sampler1/8/24 标定，再只登记本次 FULL。
新端口 18883，独立命名 `decay_tick2s_no_handoff_20261030_r1`；
不复用旧的未执行 FULL 身份，不放松同策略 `after-registration`
约束，不重启对照或重采 native。

两臂共享原始完整 V230/V311、18 个不同项目、10 个初始 root 和
8 个 mean120/seed21 泊松后续到达、工具样本、token、容量与正常尾部。
本轮同时取消 handoff 并调整控制 tick，完整净收益不能单独归因于
其中某一项。若后续两秒候选有效，仍需保留它与原五秒 paper 参数的
差异，必要时另行做相同 tick 的调参对照，不能包装为新的衰减算法。

## 验证与待测项

447 项调度/配置针对性检查已通过。首个 focused report 保留一次测试
fixture 容量估算错误和未设置 native 测试环境产生的七个 skip；
第二次使用原生 checkout 后全部通过。配置检查另发现 free-extent
声明为 1 会被 disabled path 规范化为 0，已要求队列同时核对原始声明
和实际有效值，保留该次失败。

完整 CPU 回归 `cpu_r1/evidence.json` 实际通过 4,859 项测试和 6 项子测试，
保留此前全部 4,810 个测试身份，无失败、错误或跳过；1,962 份绑定输入
及原生 checkout 在执行期间保持一致。新 GPU 资格、完整性能尚待完成。
验收同时看完整平均 JCT、全程输出、
ready 服务间隔、H2D/D2H、已服务前缀重算及工作集切换；更频繁的 tick
可能增加迁移，不能仅凭更早 resume 判为正收益。

当前已从独立冻结 `175d6925` 登记 CPU 队列：
`experiments/reports/decay_tick2s_no_handoff_20261030/queue_r1/registration.json`。
实际 watcher1459481、nice0，通过全部冻结源码与输入校验；
`queue_live_start_r1.json` 独立确认旧 watcher 已死、原对照仍在正常
运行、新 FULL 尚未越过完整对照门槛，并重新核对未来有效值为
control2000ms、layer handoff0、free extent0。正式 FULL 的 GPU
资格和性能仍未据此宣称完成。实现提交为 `234de993`，冻结根
`175d6925` 只额外移除了误暂存的生成 bytecode，不改变绑定源码。
