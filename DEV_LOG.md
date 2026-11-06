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
