# DEV_LOG

## 2026-11-05 环境搭建与首次跑通(M0 前半)
- 环境:Windows 10 + WSL2 Ubuntu 24.04(装在 E 盘),Docker Desktop(数据盘在 E 盘)。
- 发现:E 盘是 USB 外接 SSD,不是机内盘。WSL、Docker 数据都放在上面,拔线或休眠有损坏风险。页面文件不应放在 E 盘。
- 网络:出国代理节点只有约 4 到 24 KB/s;PyPI 直连约 20 KB/s,清华镜像快约 70 倍,但只能用于自己的小项目(DeerFlow 的 uv.lock 锁定 pypi.org)。Docker Hub 直连不通。
- 选择:放弃 Docker 方式(要拉 Docker Hub 和 ghcr.io),改用本地模式。Node 22 装在 ~/tools,沙箱镜像(13 GB)走火山引擎北京仓库,拉取顺利。
- 内存:开发模式(make dev)下 Next.js 热重载吃内存,Windows 可用内存掉到 0.8 GB,整机卡死。WSL 内存上限调到 6 GB,暂停 Dify,改用 make start(生产模式),稳定。
- 已知问题:make stop 没有杀掉残留的 next-server,下次启动提示 3000 端口被占用,需手动结束。
- 首次对话:一句话提问,输入 12.1K token、输出 23 token。固定开销很大,后续做成本方向值得研究。
- 教训:带 read 的命令不能和后续命令一起粘贴,会被读成输入。API Key 只能在本机终端输入。

## 2026-11-05 晚 mock LLM
- 新增 bench/mock_llm/server.py(OpenAI 兼容、支持流式和 usage、延迟可配,默认首包 300ms、每词元 20ms)。
- 验证:首包 0.31s,60 词元总计 1.6s,与设定一致。
- 局限:只返回普通文本,不触发工具调用,测不到沙箱路径。每次对话后还有记忆更新等额外 LLM 调用,统计请求数需计入。
- 虚拟环境放在仓库外(~/venvs/mockllm),依赖用清华镜像安装。

## 2026-11-06 配置与认证调研
- 默认模型:多处调用在没指定模型时取 models[0](agent.py、subagents、摘要中间件),所以 mock 放在第一位。
- 后台调用:title、suggestions、memory(debounce 30s)、summarization(32000 token)默认开启,会产生额外 LLM 请求,压测时需计入或关闭。
- tiktoken 预热超时,token 计数退回按字符估算,用量与摘要阈值都是近似值。
- 单实例默认值:run_events.backend=memory,run_ownership.heartbeat_enabled=false,deployment.multi_instance=false。
- token_budget 默认关闭,按每次运行计;按用户或租户的配额未发现,待读代码确认。
- 认证:PAT 需会话创建,前端无入口;登录接口 /api/v1/auth/login/local;文档称无细粒度 RBAC(待核实)。
- extensions_config.json 是示例,MCP 全部未启用,my_package 警告为占位符噪音。

## 2026-11-06 瓶颈定位(进行中)
- py-spy(--gil):并发 10 下约 43% 的 GIL 时间在 assemble_lead_agent(其中约 31% 是 build_assembly_descriptor,对每个工具做描述和 JSON Schema 哈希);Gateway 进程 CPU 约 65% 到 75%,压测脚本与 mock 的 CPU 均低于 4%。
- 原因线索:runtime/runs/worker.py:1425 在每次运行组装时进入 capture_assembly(),使 capture_requested() 恒为真,_complete_assembly 的"无观察者快速通路"不生效。
- 性质:这是有意的"组装证据"(审计)功能,不应当作缺陷;待做对照实验量化其开销,并评估"工具集与策略不变时复用描述符"的可行性。
- 待核实:worker.py:1425 外层是否有开关;证据是否写入数据库(可能与 SQLite 每次运行约 180 KB 的增长有关)。

## 2026-11-06 数据库增长取证
- 数据库 228.6 MB,约 1480 次运行;checkpoints 35,347 行(checkpoint 列合计 158.3 MB,约占 70%),writes 44,214 行。
- 约每次运行 24 个检查点、每个约 4.7 KB;当前为 checkpoint_channel_mode: full,所以 checkpoint_delta.snapshot_frequency 未生效。
- 上游已有 delta 模式与留存契约草案(backend/docs/checkpoint-retention-contract.md):检查点构成父链,删除可能悄悄破坏分支/恢复,契约接受前不应实现清理。因此不自行写清理脚本。
- 下一步:读 delta 模式说明,用同一压测对比 full 与 delta 的空间与吞吐;压测数据视为一次性,用独立数据库。

## 2026-11-06 晚 多轮实验作废与教训
- 多轮对话(100 个对话 x 8 轮,并发 10)实验:客户端显示 800/800 成功,但服务端 runs 表中 631(full)和 约 631(delta,日志 762 行错误)个运行状态为 error,错误均为 sqlite3.OperationalError: database is locked(langgraph sqlite aio.aput 写检查点)。
- 因此该实验的体积与吞吐数据全部作废(检查点数异常偏少是失败运行导致)。单轮实验日志错误数为 0,数据有效。
- 教训:客户端 "HTTP 200 + end 事件" 不能代表服务端成功;服务端把存储错误作为正常结束的事件返回。压测汇总现在必须检查 runs 表状态,存在非 success 即标记 INVALID。
- 线索(未验证):同一对话上相邻两轮的收尾写入与下一轮写入争抢 SQLite 写锁;上游文档称 SQLite 适用于单节点,多 worker 用 Postgres。
- 待做:降低并发复测(并发 3),确认错误是否消失;再决定在 Postgres 上重做多轮实验。

## 2026-11-06 深夜 多实例环境跑通
- 依赖:backend 同步 asyncpg 0.31.0、psycopg 3.3.3、psycopg-binary 3.3.3、psycopg-pool 3.3.0、langgraph-checkpoint-postgres 3.1.3(5 个包,uv sync --locked,走官方 PyPI,约 5 分钟)。
- scripts/dev_multi_instance.sh up --skip-install:Postgres 17-alpine、Redis 7-alpine(与上游默认一致)、Gateway A(8001)、Gateway B(8011)、nginx(2027)均在约 5 秒内就绪。
- check --with-llm:8 项全部 PASS,0 FAIL,0 SKIP(健康/会话/内部令牌/对话可见/上传/Skill 开关/nginx 轮询/SSE 加入加 Last-Event-ID 续传)。
- 注意:本次 base config 用的是原 config.yaml(含 deepseek),因为新终端没有带 DEERFLOW_MI_BASE_CONFIG;lead_agent 取 models[0](mock)故未产生费用。压测前必须确认只用 bench 配置。
- 上游把"stop b --kill 时 A 的运行不得变 error""跨实例取消约 10 秒内生效"列为"手工检查"(MANUAL_CHECKS),未自动化也未量化,这是我们可补的方向。
- 示例配置中的 my_package 拦截器路径导致每个 Gateway 启动时打印 ImportError 调用栈(无功能影响),疑似可清理的小问题,暂不处理。

## 故障注入预测(运行前写下)
- B 被 SIGKILL 后,B 上进行中的运行不会被 A 接管续跑,最终状态为 error(依据常量 LEASE_ORPHAN_RECOVERY_ERROR 与 STARTUP_ORPHAN_RECOVERY_ERROR;对账函数尚未读)。
- 变成 error 的耗时约为租约 30 s + 宽限 10 s + 对账周期(未知),预计 40 s 以上。
- A 上的运行不受影响,全部 success。
- B 的客户端流会立即断开。

## 故障注入第二轮预测(运行前写下)
- 试验间随机等待 0 到 30 s 后,B 的运行转为 error 的耗时应铺开在约 30 到 70 s 之间,不再聚集在 52 s。
- 若仍聚集在 52 s 左右,说明相位理论错误,需要重新读对账与心跳代码。
- A 的运行仍应全部 success,B 的运行仍应全部 error。

## 故障注入第二轮结果(对照预测)
- 打散相位后 6 次试验:62.4 / 55.9 / 60.1 / 41.7 / 55.8 / 65.1 s,不再聚集,预测成立(样本 6 个)。A 全部 success、B 全部 error。
- 12 次合并:最小 38.4、中位 54.3、最大 65.1 s。中位高于理论中心约 50 s,原因未明。
- 待验证:A 日志中对账事件时间戳 mod 30 s 是否集中(固定节拍检验)。

## 缩短租约实验预测(运行前写下)
- lease_seconds=15、grace_seconds=5:心跳每 5 s,对账周期约 15.1 s。
- 预测 B 的运行转为 error 的耗时:范围 15 到 35 s,均值约 25 s,B 全部 error、A 全部 success。
- 若观测均值显著偏离 25 s,说明对账周期或资格时间的模型有误。

## join 延迟假设(事后提出,运行前写下)
- 观察:lease 15 的试验 3、5、7(等待 15.6/15.7/16.4 s)join 的 end 比状态变更晚约 13 s(约 35.0 s);其余试验在 1 s 以内。该关联是事后发现(偶然概率约 1/35)。
- 预测:试验间固定等待 5 s,4 个有等待的试验里至少 3 个出现 join_end_s - b_recovered_s 大于 8 s;固定等待 30 s,至多 1 个出现。
- 若不成立,说明此前的关联是巧合;同时记录 join 流中的事件序列,用于看清 end 之前收到了什么。

## join 延迟假设的检验结果与新假设 H
- 上一条假设(短等待导致 join 延迟)被证伪:固定等待 5 s 时 4 个有等待的试验中只有 1 个延迟 > 8 s(预测 ≥ 3);固定等待 30 s 时有 3 个(预测 ≤ 1)。
- 事后规律(n=18,lease 15):恢复 18.5 到 19.3 s 时 join 在约 20 s 结束;恢复 20.2 到 26.5 s 时 join 在约 35 s 结束(9 次);恢复 ≥ 27.4 s 时 join 跟随恢复(7 次)。join_end 聚在 join 起点(被杀后约 4.7 到 5.0 s)加 15 s 的整数倍。
- 日志中恢复后常出现 "Failed to publish recovered run stream end"。
- 新假设 H(事后提出):恢复早于约 27 s 时补写 end 失败,join 靠 15 s 空闲检查结束;晚于约 27 s 时补写成功。
- 预测(运行前写下):A 日志 18 个恢复事件中,恢复耗时 < 27 s 的 11 次各有 4 行失败发布,≥ 27 s 的 7 次为 0 行。顺序:前 8 次 0,0,4,0,4,4,4,0;等待 5 s 的 5 次 4,4,4,0,0;等待 30 s 的 5 次 4,4,0,4,4。

## H 的日志检验结果与脚本人为因素更正
- H 的日志预测在 18 个恢复事件上全部吻合(失败发布 0/4 行序列与预测一致,共 44 行):恢复耗时 18.5 到 26.5 s 的 11 次各 4 行失败发布,27.4 到 32.8 s 的 7 次为 0 行;失败原因为 redis.py _xadd_without_active_producer 抛出 "another active producer exists"(经 publish_end,由 deps.py 的 _publish_recovered_run_stream_end 调用)。27 s 门限的来源未读代码,未解释。
- 更正:fault_inject.py 先等 A 的 4 个流结束(约 5 s)才开始 join,所以 join 起点约 4.8 s 是脚本造成的,join_end 的 20 s / 35 s 取决于该起点,不是系统性质。
- 预测(运行前写下):让 join 在杀 B 的同一时刻开始(join 起点约 0 到 2 s)。早恢复试验 join_end 在约 29.5 到 33 s;晚恢复试验 join_end 在恢复时刻 ±1.5 s 内。若早恢复试验的 join_end 仍稳定在约 35 s,则"空闲检查从连接时刻起算"的模型不成立。
