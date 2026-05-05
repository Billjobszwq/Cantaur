# QYclaw 发行与部署标准（对齐 QYclaw / Unique 主流体验）

- 版本：`v1.0`
- 日期：`2026-05-04`
- 目标：实现“一行命令本地部署 + 启动后配置 agent/api/skill + 本地控制面板 + 命令说明 + 代码保护策略”。

---

## 1. 目标体验（发布标准）

参考主流 QYclaw / Unique 的使用体验，QYclaw 发布标准定义为：

1. 一行命令完成本地部署（安装依赖、初始化目录、生成配置模板、启动核心进程）。
2. 启动后 5 分钟内完成核心配置（Agent、模型 API、Skill 路径）。
3. 提供本地控制面板，可查看运行状态、任务、健康检查、维护日志。
4. 提供 CLI 命令体系与命令手册（可检索、可复制、可脚本化）。
5. 发布包分级：开源核心 + 可选受保护增强包。

---

## 2. 一行部署标准

## 标准命令（对外）

```bash
curl -fsSL https://raw.githubusercontent.com/<org>/qyclaw-core/main/install.sh | bash
```

## 本地开发等价命令（仓库内）

```bash
bash ./scripts/install_qyclaw_local.sh
```

## install.sh 必须完成的动作

1. 环境检查：`python3`、`node`、`qyclaw`、`sqlite3`。
2. 创建目录：`runtime`、`logs`、`config`、`workspace`（缺失时自动创建）。
3. 生成模板配置：
   - `.env.example`
   - `config/qyclaw.example.json`
   - `config/agents.example.yaml`
4. 初始化默认 agent 清单：`main/dev/content/ops/law/finance/research`。
5. 启动服务：gateway + lifecycle runner + dashboard。
6. 输出下一步命令（`qyclaw init`、`qyclaw doctor`、`qyclaw panel`）。

---

## 3. 启动后配置标准

## 3.1 Agent 配置

```bash
qyclaw agent list
qyclaw agent enable main dev content ops law finance research
qyclaw agent bind --channel feishu --account <accountId> --agent main
```

## 3.2 模型/API 配置

```bash
qyclaw model set --provider deepseek --base-url https://api.deepseek.com --api-key <KEY>
qyclaw model primary deepseek-v4-pro
qyclaw model fallback add deepseek-v4-flash
qyclaw model fallback add deepseek-reasoner
qyclaw config validate
```

## 3.3 Skill 配置

```bash
qyclaw skill list
qyclaw skill add ./skills/<skill-name>
qyclaw skill trust set <skill-name> --level allow
qyclaw skill doctor
```

---

## 4. 本地控制面板标准

当前仓库已有控制面板基础（`dashboard/server.py`），发布版要求如下：

1. 默认端口：`127.0.0.1:8899`
2. 页面：`/`（实时状态）
3. API：
   - `/data`：运行态聚合
   - `/doctor`：健康诊断
   - `/restart`：受控重启
4. 页面最小指标：
   - gateway 状态
   - agent 在线与会话数
   - cron/maintenance 状态
   - 模型主备链状态
   - memory/knowledge 更新状态
   - 最近告警

## 面板命令

```bash
qyclaw panel start
qyclaw panel stop
qyclaw panel status
qyclaw panel open
```

---

## 5. 命令体系标准（CLI 手册最小集）

| 命令组 | 作用 | 必须子命令 |
|---|---|---|
| `qyclaw init` | 初始化 | `local`, `reset-template` |
| `qyclaw start/stop/restart` | 服务控制 | `all`, `gateway`, `runner`, `panel` |
| `qyclaw doctor` | 健康检查 | `quick`, `full`, `json` |
| `qyclaw agent` | Agent 管理 | `list`, `enable`, `disable`, `bind`, `status` |
| `qyclaw model` | 模型配置 | `set`, `primary`, `fallback`, `test` |
| `qyclaw skill` | Skill 管理 | `list`, `add`, `remove`, `trust`, `doctor` |
| `qyclaw maint` | 维护任务 | `run`, `status`, `logs`, `schedule` |
| `qyclaw panel` | 控制面板 | `start`, `status`, `open` |

---

## 6. 与 QYclaw / Unique 对齐点

1. 单命令可启动（降低部署门槛）。
2. 配置声明式（模型、agent、skill 均模板化）。
3. 多 Agent 可观察（面板与 CLI 双通道）。
4. 维护任务可调度（memory 每日、knowledge 每 3 天）。
5. 异常可降级（主模型失败自动切备）。

---

## 7. “开源核心代码加密”可行性说明（关键）

这里需要明确一个事实：

- **严格意义的开源（OSI）要求源码可读、可修改、可再分发。**
- “核心代码加密后仅可运行不可读”与严格开源定义冲突。

因此有 3 种可执行路径：

## 路径 A（推荐）双轨发布

1. `qyclaw-core`：真正开源（可读源码）。
2. `qyclaw-enterprise-pack`：受保护增强包（可加密/混淆/许可证校验）。

适用：你要社区生态 + 商业保护并存。

## 路径 B（源码可见但限制）

- 使用 Source-Available License（非 OSI 开源）。
- 对关键模块做编译混淆，仅暴露接口。

适用：强调商业控制，不强调“纯开源”。

## 路径 C（全加密发布）

- 仅二进制/加密包发布。
- 不能宣称“开源”。

适用：内部发行或商业私有化交付。

---

## 8. 代码保护技术建议（增强包）

针对 Python 模块建议：

1. 构建层：`Nuitka` 生成二进制（优先于纯混淆）。
2. 混淆层：`PyArmor`（仅用于少量关键模块）。
3. 许可证层：离线 license 文件 + 机器指纹（可选）。
4. 完整性校验：启动时校验模块签名与哈希。

不建议：
- 全仓库一刀切混淆（维护成本极高，调试困难）。

---

## 9. 发布物清单（v1 标准）

1. `install.sh`（一行安装入口）
2. `scripts/install_qyclaw_local.sh`（本地等价安装）
3. `config/qyclaw.example.json`
4. `.env.example`
5. `docs/COMMANDS.md`（命令手册）
6. `docs/DEPLOY.md`（部署手册）
7. `docs/PANEL.md`（控制面板手册）
8. `docs/SECURITY.md`（安全与密钥策略）

---

## 10. 落地里程碑（最快节奏）

## M1（1 天）
- 完成 install 脚本 + 配置模板 + CLI 包装命令。
- 统一 `QYCLAW_HOME`，移除绝对路径硬编码入口。

## M2（1-2 天）
- 接入并固化 dashboard（服务化启动 + 指标统一）。
- 完成命令手册初版。

## M3（2-3 天）
- 完成双轨发布：开源核心仓 + 受保护增强包打包流程。
- 完成 `v0.1.0` 对外发布。

---

## 11. 你可以直接拍板的决策项

1. 发布形态：`A 双轨发布`（推荐） / `B 源码可见限制` / `C 全私有加密`。
2. 控制面板入口：沿用当前 `:8899` 还是迁移到统一端口。
3. 首发范围：仅 `core` 还是 `core + 部分 skills`。

