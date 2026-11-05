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
