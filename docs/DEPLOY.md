# Cantaur 安装部署说明

本项目基于openclaw魔改。

## 1. 环境要求

- `bash` 4.0+
- `git`
- `curl`
- `rsync`
- `python3` 3.10+
- `node` 20+
- `sqlite3`
- 可选后端命令：`qyclaw-core`（用于 gateway/cron/doctor）

## 2. 适配系统

- macOS 13+
- Ubuntu 22.04+ / Debian 12+
- RHEL 9+ / Rocky Linux 9+
- Windows 11 + WSL2 (Ubuntu)

## 3. 各系统依赖安装命令

### macOS (Homebrew)

```bash
brew update
brew install git curl rsync python node sqlite
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y git curl rsync python3 python3-venv python3-pip nodejs npm sqlite3
```

### RHEL / Rocky

```bash
sudo dnf update -y
sudo dnf install -y git curl rsync python3 python3-pip nodejs npm sqlite
```

### Windows (WSL2 Ubuntu)

```bash
wsl --install -d Ubuntu-24.04
```

安装完成后进入 WSL，执行 Ubuntu 依赖命令。

## 4. 项目安装命令

```bash
git clone https://github.com/Billjobszwq/Cantaur.git
cd Cantaur
QYCLAW_HOME=$HOME/.qyclaw bash ./install.sh
```

## 5. 启动与检查

```bash
$HOME/.qyclaw/workspace/bin/qyclaw start
$HOME/.qyclaw/workspace/bin/qyclaw status
$HOME/.qyclaw/workspace/bin/qyclaw doctor
$HOME/.qyclaw/workspace/bin/qyclaw panel
```

## 6. 首次配置

- 编辑：`$QYCLAW_HOME/.env`
- 编辑：`$QYCLAW_HOME/qyclaw.json`
- skills 放置目录：`$QYCLAW_HOME/workspace/skills/`
