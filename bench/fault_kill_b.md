# 故障注入:SIGKILL Gateway B(双 Gateway + Postgres + Redis)

脚本:bench/fault_inject.py · 结果:bench/results/*_fault_kill_b.json

## 环境
- Windows 10 + WSL2(5 GB)、Postgres 17-alpine、Redis 7-alpine、Gateway A(8001)与 B(8011),由上游 scripts/dev_multi_instance.sh 拉起
- run_ownership:lease_seconds=30、grace_seconds=10、heartbeat_enabled=true;基础配置仅含 mock 模型(无真实模型调用)
- mock:60 个词元 x 100 ms,每次运行约 6 s,保证被杀时 B 上有进行中的运行

## 方法
每次试验:A 与 B 各启动 4 个运行并等待输出开始,`stop b --kill`(SIGKILL),随后轮询 A 上 B 的运行状态,同时在 A 上 join 这些运行,记录客户端流结局、终态与耗时。试验间自动重启 B。

## 结果(6 次试验,共 24 个 B 运行、24 个 A 运行)
| 项 | 结果 |
|---|---|
| A 上的运行 | 24/24 success |
| B 上的运行 | 24/24 error,stop_reason=orphan_recovered |
| B 的客户端流 | 全部立即断开(RemoteProtocolError) |
| 转为 error 的耗时 | 38.4 s(1 次),52.1 到 56.0 s(5 次,中位 52.5 s) |
| 在 A 上 join 拿到 end 的耗时 | 与上一行相差不到 1 s |

## 解读(谨慎)
- 被杀实例上的运行不会被存活实例接管续跑,而是在租约到期、宽限期过后由对账标记为 error;存活实例上的运行不受影响。
- 同一次试验里 4 个运行同时被同一次对账认领,所以每次试验只算 1 个样本。
- 理论窗口约 30 到 70 s(租约 20 到 30 s + 宽限 10 s + 对账周期 0 到 30 s),来自阅读注释,未读实现。观测值落在窗口内,但 5 次中有 4 次聚集在 52 s 附近,试验首尾相接导致相位可能近似固定,尚不能说明完整分布。

## 局限
- 只有 6 个样本,相位未打散;mock 工作负载,无工具调用、无沙箱
- 直连两个 Gateway,没有经过 nginx;没有测 Idempotency-Key 重试;没有测取消延迟
- 只测了 lease=30 / grace=10 一组参数,没有评估缩短租约的收益与误判风险
- 未验证 join 收到的 end 事件是否携带错误信息(只检查了事件名)

## 后续
1. 试验间加入随机等待,打散相位,验证窗口理论
2. 改变 lease_seconds,测恢复耗时与高负载下的误接管
3. 经 nginx、带 Idempotency-Key 的客户端重试
