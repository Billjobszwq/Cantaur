# Cantaur (QYclaw Mode Release)

本项目基于openclaw魔改。

## 发行说明（当前版本）

- 新增：多 Agent 生命周期主链，支持 `trigger/run/retry/terminate/timeout-scan` 全流程治理。
- 新增：A2A 协议总线，任务与结果采用结构化消息协作，支持审计与重试。
- 新增：记忆与知识闭环，支持结果回流、候选评审与蒸馏收敛。
- 增强：维护与门禁链路，支持 P1/P3/P4 自动巡检、放行与回退控制。
- 增强：本地控制面板与统一命令入口，支持状态查看、诊断与任务触发。

QYclaw-style packaged release for local deployment with:
- one-command install
- lifecycle orchestration scripts
- local dashboard
- agent/model/skill configuration templates

## One-line local deploy

```bash
QYCLAW_HOME=$HOME/.qyclaw bash ./install.sh
```

## Start / status

```bash
$HOME/.qyclaw/workspace/bin/qyclaw start
$HOME/.qyclaw/workspace/bin/qyclaw status
$HOME/.qyclaw/workspace/bin/qyclaw panel
```

## Run a fusion task

```bash
$HOME/.qyclaw/workspace/bin/qyclaw run --title "Cross-functional report" --goal "Generate a full report"
```

## Configure API/Model/Agents/Skills

1. Edit `$HOME/.qyclaw/.env`
2. Edit `$HOME/.qyclaw/qyclaw.json`
3. Place skills under `$HOME/.qyclaw/workspace/skills/`
4. Verify with:

```bash
$HOME/.qyclaw/workspace/bin/qyclaw doctor
```

## 发布说明

- 仓库不包含私人凭据。
- 运行数据与日志通过 `.gitignore` 排除。
- 代码中不保留个人绝对路径。

## Publishing

- GitHub publishing and privacy checklist: `docs/GITHUB-PUBLISH.md`
