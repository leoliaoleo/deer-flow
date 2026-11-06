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
