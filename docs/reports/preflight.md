# IncidentPilot 宿主机与仓库预检

日期：2026-07-15（Asia/Shanghai）  
任务：M0.0 宿主机与仓库预检

## 结论

- 本地 Git 仓库已按用户授权初始化为 `main` 分支；没有远程仓库、提交或推送。
- Docker CLI 与 Docker Desktop 均未发现，因此 M1 保持 `BLOCKED_BY_HOST`。责任人：用户完成 Docker Desktop（含 Compose v2）安装、启动与首次初始化。
- Node.js 为 `v24.16.0`，npm 为 `11.17.0`；项目要求 Node 22 LTS。该项在 M5 开始前由用户切换或安装后复检。
- WSL 命令存在，但 `wsl --status`、`wsl --version` 与 `wsl --list --verbose` 均返回帮助文本并以退出码 1 结束，尚不能确认 WSL2 已就绪。虚拟化宿主存在；需要在 Docker Desktop 安装前由用户完成 WSL2 更新或安装。
- 目标端口中仅 `127.0.0.1:8080` 被 PID `4276` 占用；其余预检端口未发现监听。启动 OTel Demo 前必须再次检查该端口以避免冲突。

## 实际预检输出摘要

| 项目 | 命令 | 实际结果 |
|---|---|---|
| 工作目录 | `Get-Location` | `E:\IncidentPilot` |
| 工作区 | `Get-ChildItem -Force` | 初始化前仅有 `AGENTS.md` 与 `IMPLEMENTATION_MASTER.md` |
| 磁盘可用空间 | `Get-PSDrive -Name E` | 约 117.4 GiB 可用 |
| Python | `& 'D:\software\ana\envs\tx_agent\python.exe' --version` | `Python 3.12.13` |
| Git | `git --version` | `git version 2.54.0.windows.1` |
| Git 仓库 | `Test-Path .git`、`git status --short --branch` | 初始化前不存在；初始化后为 `## No commits yet on main`，工作区含两份既有文档 |
| Docker / Compose | `docker --version`、`docker compose version` | `docker` 命令未找到 |
| Docker Desktop | `Get-Command 'Docker Desktop'` | 未找到 |
| Node.js / npm | `node --version`、`npm --version` | `v24.16.0` / `11.17.0`，不符合 Node 22 LTS 基线 |
| WSL | `wsl --status`、`wsl --version`、`wsl --list --verbose` | 命令存在但不支持所需查询，返回帮助文本；WSL2 状态未确认 |
| WSL 安装工具 | `winget --version` | `v1.29.280` 可用 |
| 虚拟化 | `Get-CimInstance Win32_ComputerSystem` | `HypervisorPresent=True` |
| 端口 | `Get-NetTCPConnection -State Listen`（目标端口集合） | 仅 `127.0.0.1:8080` / PID `4276` 正在监听 |

## Docker Desktop + WSL2 后续检查

此任务未安装软件或修改 PATH。用户完成安装、UAC 或重启后，在新的 PowerShell 中执行：

```powershell
wsl --status
wsl --version
wsl --list --verbose
docker --version
docker compose version
docker context ls
docker run --rm hello-world
```

预期为 WSL2 可用、Docker Desktop daemon 正在运行、Compose v2 可用，且 `hello-world` 成功结束。Docker Desktop 应使用 WSL 2 backend，并为后续完整 OTel Demo 预留至少 8 GB 内存和约 14 GB 磁盘空间。

## Node 22 LTS 后续检查

在 M5 前由用户安装或切换到 Node 22 LTS；重新打开 PowerShell 后执行：

```powershell
node --version
npm --version
npx -y @modelcontextprotocol/inspector@latest --version
```

## Host prerequisite remediation (2026-07-15)

With the user's explicit authorization, `wsl --install --no-distribution` and the official Docker Desktop 4.82.0 silent installer were run. The initial Docker installation enabled `Microsoft-Hyper-V` and required a reboot. After reboot, Docker Desktop started successfully with the WSL2 backend.

| Item | Verification command | Actual result |
|---|---|---|
| WSL2 | `wsl --version` | WSL `2.7.10.0`; kernel `6.18.33.2-2` |
| Docker Engine | `docker version` | Docker Desktop `4.82.0 (233772)`; Engine `29.6.1`, `linux/amd64` |
| Compose v2 | `docker compose version` | `Docker Compose version v5.3.0` |
| Docker context | `docker context ls` | `desktop-linux` is the active context and connects to the Docker Desktop Linux engine |
| Real container | `docker run --rm hello-world` | Pulled, created, ran, and removed successfully; output included `Hello from Docker!` |

Conclusion: Docker Desktop, Compose v2, and the WSL2 backend now satisfy the M1 host prerequisite. M1 has not started. Node `v24.16.0` still does not satisfy the Node 22 LTS prerequisite for M5 and was not changed early.

记录实际 Node/npm 版本，并复核 MCP Inspector 的 `engines` 要求。当前 Node 24 不能作为项目通过基线。
