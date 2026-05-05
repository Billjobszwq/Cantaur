# Cantaur (QYclaw Mode Release)

本项目基于openclaw魔改。

## 先决环境

需要先安装：

- `bash` 4.0+
- `git`
- `curl`
- `rsync`
- `python3` 3.10+
- `node` 20+
- `sqlite3`

## 适配系统与依赖安装命令

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

### RHEL / Rocky Linux

```bash
sudo dnf update -y
sudo dnf install -y git curl rsync python3 python3-pip nodejs npm sqlite
```

### Windows 11 (WSL2 + Ubuntu)

```bash
wsl --install -d Ubuntu-24.04
```

进入 WSL 后，执行 Ubuntu / Debian 依赖安装命令。

## 一键部署

```bash
git clone https://github.com/Billjobszwq/Cantaur.git
cd Cantaur
QYCLAW_HOME=$HOME/.qyclaw bash ./install.sh
```

## 启动与验证

```bash
$HOME/.qyclaw/workspace/bin/qyclaw start
$HOME/.qyclaw/workspace/bin/qyclaw status
$HOME/.qyclaw/workspace/bin/qyclaw doctor
$HOME/.qyclaw/workspace/bin/qyclaw panel
```

## 首次配置

1. 编辑 `$HOME/.qyclaw/.env`
2. 编辑 `$HOME/.qyclaw/qyclaw.json`
3. 放置 skills 到 `$HOME/.qyclaw/workspace/skills/`

## 任务触发示例

```bash
$HOME/.qyclaw/workspace/bin/qyclaw run --title "Cross-functional report" --goal "Generate a full report"
```

## 详细部署文档

- `docs/DEPLOY.md`
- `docs/PROJECT-TRACKS.md`（项目双线状态手册）

## 发布说明

- 仓库不包含私人凭据。
- 运行数据与日志通过 `.gitignore` 排除。
- 代码中不保留个人绝对路径。

## Publishing

- GitHub publishing and privacy checklist: `docs/GITHUB-PUBLISH.md`
