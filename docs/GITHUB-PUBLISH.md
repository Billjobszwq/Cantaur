# GitHub 发布与安全检查

本文件用于把当前 QYclaw（OpenClaw Mode）发布到 GitHub，并确保不上传私人信息。

## 发布前检查

在仓库根目录执行：

```bash
rg -n "zhangweiqi|/Users/|sk-|api[_-]?key|token|secret|password|ou_[a-zA-Z0-9]+" --hidden
```

要求：
- 不应出现真实密钥、真实账号 ID、真实本机绝对路径。
- 示例字符串（如 `<API_KEY>`）允许保留。

## 缺失条件（必须满足）

发布到你的 GitHub 前，当前环境还缺少：
- `gh` 登录状态（未登录）。
- 目标仓库信息（仓库名、可见性、所属账号/组织）。

先完成登录：

```bash
gh auth login
gh auth status
```

## 创建并推送仓库

示例（公开仓库）：

```bash
gh repo create qyclaw-openclaw-mode --public --source . --remote origin --push
```

示例（私有仓库）：

```bash
gh repo create qyclaw-openclaw-mode --private --source . --remote origin --push
```

如果你已提前建好空仓库：

```bash
git remote add origin <YOUR_REPO_URL>
git push -u origin main
```

## 打标签（可选）

```bash
git tag -a v1.0.0 -m "QYclaw OpenClaw-mode v1.0.0"
git push origin v1.0.0
```

## 部署步骤（给使用者）

```bash
git clone <YOUR_REPO_URL>
cd qyclaw-openclaw-mode
OPENCLAW_HOME=$HOME/.openclaw bash ./install.sh
```

启动与检查：

```bash
$HOME/.openclaw/workspace/bin/qyclaw start
$HOME/.openclaw/workspace/bin/qyclaw doctor
$HOME/.openclaw/workspace/bin/qyclaw panel
```

## 安全发布建议

- 仓库启用 Secret Scanning（GitHub Advanced Security 可选）。
- `.env` 只保留在本地，仓库仅提交 `.env.example`。
- 所有个人日志、运行库、数据库继续通过 `.gitignore` 排除。
