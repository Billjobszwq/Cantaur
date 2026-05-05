# QYclaw x QY_Codex 协同结构入口

最后更新：2026-04-03
状态：sidecar 原型已落地，现网最小接入已开始（shadow mode）

## 目标

本目录用于承载 QYclaw 与 QY_Codex 的协同设计、协议草案、目录骨架与后续实施文档。

当前原则：
- 先做结构化设计，不直接改生产运行链路
- 先建立可扩展的协作内核，不重写现有 QYclaw 底座
- 先抽能力边界，再决定哪些模块需要真正开发

## 当前目录结构

- `QYCLAW-CLAUDECODEX-INTEGRATION-BLUEPRINT.md`
  - 总体协同蓝图
- `QYCLAW-CLAUDECODEX-MODULE-MAP.md`
  - QY_Codex 能力与 QYclaw 宿主映射
- `QYCLAW-CLAUDECODEX-ROADMAP.md`
  - 分阶段实施路线图
- `QYCLAW-ORIGINAL-VS-INTEGRATED-MODULE-COMPARISON.md`
  - 原始 QYclaw 与当前整合版的模块对照
- `NEXT-STAGE-EVOLUTION-ROADMAP.md`
  - 下一阶段演化建议路线
- `QYCLAW-CLAUDECODEX-PHASE0-TODO.md`
  - Phase 0 契约层清单
- `GLOSSARY.md`
  - 统一术语表
- `AGENT-BUS-TOPOLOGY.md`
  - 7-agent 调用拓扑与协作边界
- `MODULE-DESIGN-INDEX.md`
  - 全部模块设计总览
- `IMPLEMENTATION-PRIORITY-CUT.md`
  - 实施优先级裁剪
- `FOUNDATION-ARCHITECTURE-UPGRADE.md`
  - 底层架构优化建议
- `protocols/`
  - 协议与 schema 草案
- `registries/`
  - agent/skill registry 草案
- `control/`
  - coordinator / worker 运行模型草案
- `state/`
  - task board / mailbox / audit / blackboard 草案
- `prompt/`
  - prompt governance 与 overlay 设计
- `remote/`
  - 远程接入与 bridge 设计
- `bus/`
  - Mailbox / Bus MVP 旁路实现
- `memory-fusion/`
  - 旁路记忆协同与记忆包输出
- `live-link/`
  - 现网最小接入层、shadow 派单、registry sync、runtime bootstrap
- `runtime/`
  - sidecar 独立运行态

## 建议实施顺序

1. Phase 0：术语、边界、接口、目录结构
2. Phase 1：协议草案与 message envelope
3. Phase 2：agent registry / skill registry
4. Phase 3：task board 与状态机
5. Phase 4：bus mailbox MVP

## 当前实施决策补充

当前建议优先级：
1. Protocol Plane
2. Registry Plane
3. Task Board
4. Mailbox / Bus MVP
5. `main` 协调器增强
6. Memory Fusion
7. Remote / Bridge

当前建议优先优化的底层点：
- 组织事实外置
- 任务主键化
- 状态层外置
- mailbox 旁路化
- 审计层补齐

## 当前红线

- 不改 `qyclaw.json` 主生产路由
- 不改 Feishu 主通道逻辑
- 不调整现有 workspace 隔离结构
- 不调整现有 memory db 生产路径
- 不让本目录中的设计直接接管现网行为

## 当前阶段完成定义

当以下内容齐备时，说明“协同结构设计 + sidecar 原型”已经成立：
- 蓝图明确
- 分阶段路线明确
- 模块映射明确
- 模块设计完整
- 术语统一
- 调用拓扑明确
- 协议草案可读
- 手册和长期记忆可索引
- sidecar runtime 可初始化
- 现网组织事实可同步到 registries
- 真实任务可通过 shadow mode 旁路注入 task board / bus / memory-fusion
