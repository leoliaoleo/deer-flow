# 基线:单实例 Gateway + SQLite + mock LLM

日期:2026-11-06 · 提交:fe603e7 · 压测脚本:bench/load.py

## 环境
- Windows 10 + WSL2 (Ubuntu 24.04),WSL 内存上限 6 GB、8 核;压测脚本、mock LLM、Gateway 同机运行
- 虚拟磁盘在 USB 外接 SSD 上
- DeerFlow 生产模式(make start-daemon),单个 Gateway worker,SQLite,run_events=memory
- 压测配置:标题、建议、摘要、记忆全部关闭,仅保留 mock 模型(DeepSeek 已移除)
- mock LLM:首包 300 ms,60 个词元,每词元 20 ms(单次约 1.6 s)
- 每轮先预热 3 次

## 结果
| 并发 | 运行数 | 成功 | 吞吐(次/秒) | 总耗时 p50 | p95 | p99 | Gateway RSS |
|---|---|---|---|---|---|---|---|
| 1 | 50 | 50 | 0.53 | 1.85 s | 1.99 s | 2.00 s | 289 → 303 MB |
| 5 | 100 | 100 | 1.98 | 2.37 s | 3.40 s | 3.54 s | 322 → 345 MB |
| 10 | 200 | 200 | 2.15 | 4.65 s | 6.07 s | 6.53 s | 345 → 367 MB |

## 观察
1. 吞吐在并发 5 到 10 之间基本不再增长(1.98 → 2.15),延迟翻倍,说明网关在并发 5 左右饱和;原因尚未定位。
2. SQLite 增长约 180 KB/次运行(350 次运行 3.5 MB → 66 MB),原因待查。
3. Gateway 内存随运行数缓慢增长(约 20 MB/100 次),尚不能区分预热与泄漏。
4. 日志出现大量 `LookupError: thread row does not exist yet`,所有请求仍成功,待查。

## 局限
- 只有单次、单机测试;压测与服务同机,互相抢占 CPU 与内存
- mock 不触发工具调用,沙箱路径未被覆盖
- ttft(首词元)指标不可信,未采用
- 未覆盖多实例、Postgres、Redis
