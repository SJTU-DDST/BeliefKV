# 补齐随机源后的 V108/EOS 消融接续

日期：2026-10-22，北京时间。

用户取消重复的第3、8、9项消融。V125已完成schedule_only和kv_only；
正在运行的no_tool_continuity于19:52停止，driver原始退出码143。
旧监督器在缺少被取消项终态summary时保存stopped_for_investigation及
原始错误；不改写它冒充正常完成。旧watcher、driver及其所属实验进程
均已退出，原trace与取消记录保留。

## 随机源

服务端random_seed和每次模型请求的sampling_seed此前已固定为21。
本次补齐客户端Python random、NumPy全局随机源、Torch CPU generator；
在Python启动前导出PYTHONHASHSEED=21，并在启动回执、client manifest中
记录实际值。客户端不为设置随机种子初始化CUDA。

root和child工具沙箱统一传入PYTHONHASHSEED及
BELIEFKV_SANDBOX_RANDOM_SEED。已有sitecustomize仅初始化Python
标准random，不为每次短工具导入NumPy/Torch。独立generator、任务显式
重设种子、OS熵、并发工具与动态batch浮点差异仍可能改变轨迹。
UUID继续作为唯一标识，不作为工作量完全一致的承诺。

## 接续顺序

入口：scripts/run_eos_pair_after_modules.py。四项消融均相对同一最新版
Full移除一个模块，之后运行Full与native_matched：

| 顺序 | 实验 | 相对最新Full的改动 |
| --- | --- | --- |
| 1 | no_join_prefetch | 关闭JOIN/EOS主动恢复，保留工具期预取 |
| 2 | no_background_prepare | 关闭idle/spawn后台备份，保留紧急容量备份 |
| 3 | no_tool_duration_model | 关闭工具时长模型及其更新，也关闭依赖该模型的工具预取 |
| 4 | no_child_reclaim | 关闭自定义终态child回收 |
| 5 | full | 最新V108/EOS配置的完整系统 |
| 6 | native_matched | 同任务、容量、种子和前端工程设置的native |

保留36 roots同时到达、running/decode8、256K上下文、95%压缩、
16384输出上限、200GB Host和80:20池比例。Full开启工具期预取、
resident-first、阶段优先、增量后台备份、完整恢复及可在压力下撤销的
额外驻留保护。JOIN只接受实际EOS；普通frontier、handoff、短工具续接、
child剩余工作预测与跨轮复用subagent profile关闭。

新冻结应用工作区复用V125已合格且未修改的引擎，启动前检查CPU源码
资格、旧进程身份与退出证据、GPU空闲、Host NUMA预检及新暂停/预约。
运行时检查有效开关、已关闭模块的动作计数和种子回执。
登记与进度分别为../ops/v126_eos_seeded_modules_pair_20261022.registration.json
及其registration.state.json，以实际文件为准。

CPU资格为V2821：保留V2811的全部4150项测试，新增60项，最终4210项
及2个subtest全部通过，无失败、无跳过。V2822绑定完整应用源码；
已有引擎及其独立CPU资格不变。V2820的172项聚焦检查和随后21项队列
检查用于开发验证，不与完整资格重复累加。

## 评价

逐组保留原始退出码、guard与完成资格、完整JCT和普通长尾、吞吐、
迁移和后台备份审计。全部六组结束后生成pair_comparison、
pair_busy_window和paired_realized_work。此前旧配置的两项消融保留为
历史结果，不冒充新配置下的单模块收益。

V2818对历史逐workflow的工作量审计表明，V124b/native虽总量接近，
配对任务输出、请求、输入的对称相对差中位数仍约37.58%、38.69%、
32.87%；36项中没有任务在这三项同时达到10%以内。新比较必须保留
逐任务明细，不能只以总量接近、JCT/token或挑选有利子集定论。
固定种子、一次运行或CPU通过均不构成性能目标已经实现的证据。
