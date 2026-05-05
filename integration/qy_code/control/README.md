# Control Plane

最后更新：2026-04-03
状态：可实施版（旁路，不接管现网）

## 当前交付

- `CONTROL-PLANE-DESIGN.md`
- `COORDINATOR-RUNTIME-DESIGN.md`
- `WORKER-RUNTIME-DESIGN.md`
- `coordinator-routing-policy.v1.json`
- `scripts/coordinator_cli.py`
- `examples/report-intake.example.json`

## 当前目标

给 `main` 提供一个 bus-aware 的旁路协调器原型：
- intake
- route
- task creation
- bus dispatch

## 当前边界

- 不替换当前 `main`
- 不接入现网会话
- 不改当前群聊/私聊行为
- 只作为后续把 `main` 接入 Agent Bus 的中间层基线

## 本地使用

只看路由计划：

```bash
python3 ${QYCLAW_HOME}/workspace/integration/qy_code/control/scripts/coordinator_cli.py \
  plan \
  ${QYCLAW_HOME}/workspace/integration/qy_code/control/examples/report-intake.example.json
```

创建任务并投递到旁路 task board + bus：

```bash
python3 ${QYCLAW_HOME}/workspace/integration/qy_code/control/scripts/coordinator_cli.py \
  dispatch \
  /path/to/task-board.db \
  /path/to/agent-bus.db \
  /path/to/bus-runtime \
  ${QYCLAW_HOME}/workspace/integration/qy_code/control/examples/report-intake.example.json
```

