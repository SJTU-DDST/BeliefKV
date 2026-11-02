# 严格本地Host池的CUDA注册修复

本次沿用73个不同原项目、原始token及依赖、12个初始workflow、
平均70秒到达和固定18个测量workflow。
用户已放宽的模型工作量最低值保持2.93141753477007；
实际2.9342612329874664，不能写成已经达到3或4。
保留JOIN/EOS、预测工具卸载、prepare_host、agent连续性和sibling支持；
工作量排序、组合准入、工具预取及逐层handoff仍关闭。

## 已完成的CPU检查

`strict_numa_prefill_shared_bound_cpu_r1`保留一次失败：
6,921项通过、4项因新干净工作树未绑定历史忽略夹具而失败。
四项都是缺文件，未修改测试断言、任务或应用源码。
重新绑定与上一轮完全相同、哈希固定的四个夹具路径后，
`strict_numa_prefill_shared_bound_cpu_r2`通过6,925项及23个子用例，
保留此前6,834个字面用例身份、2,451个源码和367个外部输入。
零失败、错误或跳过，执行期间所有输入及原生引擎未变。

这些证明只对应`1c8fd2fc`源码，不能用来覆盖本节后续新增的注册修复。
新增修复的第一轮针对性检查另有267项通过，包括真实Linux线程策略切换、
原生HookRegistry接入、注册错误回滚和准确buffer/worker证据检查。

## 实际模型启动失败

`workflow_original73_strict_numa_prefill_position_20261101_r1.registration.json`
使用干净`1c8fd2fc`及原生引擎树`434c35dd66211d0a230717c88bf1452056769a40`，
模型与GPU KV池完成加载，但200GB Host池初始化阶段worker 122061被SIGKILL。
该次position校验状态保留`stopped_for_investigation`；
没有24请求校验结果、采样校验结果或正式Full性能结果。
关联的CPU完成观察器也已保留失败并实际退出，不能将它写成正在服务。

通过只读内核日志确认同一PID被OOM killer终止。
调用栈经过`nv_dma_map_alloc`、`osIovaMap`和`memdescMapIommu`，
随后明确报告`Failed to allocate page array for DMA mapping`。
OOM时worker的shmem-rss约195,329,308 KiB，
NUMA1可用Normal页仅51,996 KiB。
这说明此前完整CPU mmap分配成功，不足以证明CUDA锁页注册也能完成；
本次失败发生在驱动映射阶段，尚未进入测量请求。
内核日志的换算墙钟与应用日志有偏差，关联依据为PID和调用路径，
不把二者的墙钟直接相减得到耗时。

资源快照还显示：

- NUMA1约38 GiB Shmem，不能作为可回收的干净文件缓存计入。
- `/dev/shm`约49 GiB使用量主要在另一个用户所有的
  `yunyi-escrow-baselines-20261014`目录。
- 未删除、迁移、压缩或更改其他用户的共享内存、归档和进程。
- 没有执行全局`drop_caches`，也没有缩小200GB Host预算。

这里没有逐文件测定该归档的物理NUMA分布；
不能将整个目录字节数等同于NUMA1的实际驻留字节数。

## 修复边界

严格绑定仍用于Host KV的分配和预触碰。
新选项`HOST_NUMA_REGISTRATION_POLICY=preferred`仅在实际原生
`_cuda_host_register`回调期间，把调用线程的策略从`bind:1`
临时改为`preferred:1`，让驱动新增分配存在其他节点的回退机会。
正常返回或异常都恢复并核实原始`bind:1`。
当前修复默认关闭，其他线程的已有策略不主动更改。

Linux文档说明线程策略仅控制随后发生的分配；
已经触碰的页面保留原位置。实现没有调用迁移接口，
但仍必须在真实注册之后检查完整Host映射，不能仅依赖该文档推断成功。
CUDA注册本身继续调用原生SGLang实现，保持其注册范围、分块与回滚逻辑；
不改变KV布局、容量、拷贝kernel或在线调度。

入口使用已有原生插件的AROUND hook。
调用方必须原先处于准确的单节点bind，否则拒绝执行该修复。
启动审计记录实际buffer地址/字节数、PID/启动身份、
策略切换前/期间/恢复后状态及实现SHA。
声明该选项的position、正式回放及完成验证，均要求该完整buffer的成功记录，
同时要求全部200,000,077,824 bytes仍驻留NUMA1且远端为零。

## 后续验证

新增源码需要重新保留全部CPU用例和外部绑定，并冻结干净工作树。
随后先运行`qualify_local_host_registration.py`：
实际分配相同200GB、通过原生插件执行完整CUDA注册，
检查有限位置的H2D/D2H读写、完整本地驻留、策略恢复及原生注销和映射释放。
该组件不加载模型，不验证实际迁移kernel，也不是工作流JCT结果。

组件通过后，才能用新身份重做24-token/session/native-stop及1/8/24采样校验，
然后执行Full与相同条件下的ThunderAgent acting-decay。
两臂必须共用注册选项、MoE配置、容量、任务、工具样本、到达及固定测量组。
保留本次失败，不重标旧preferred结果，不将NUMA与共同MoE变化当成独立消融。

参考：
`https://docs.kernel.org/admin-guide/mm/numa_memory_policy.html`；
`https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__MEMORY.html`。
