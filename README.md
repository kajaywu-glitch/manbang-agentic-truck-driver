# 满帮 Agent 算法大赛项目

最后更新：2026-06-07 00:25 +08:00

本次更新：已接入官方 `demo_docs_release_20260529.zip`。新版只公开 D001/D002，正式评测包含未公开 D003/D004；旧数据集成绩不再代表当前基线。最终路线改为运行时偏好编译、通用约束日程和罚款感知收益优化，详见 `最终冲刺与泛化方案.md`。

本仓库用于「基于 Agentic AI 的卡车司机连续找货决策」赛题的本地开发、设计沉淀与后续审阅。

## 当前目录

- `CLAUDE.md`：Claude Code/Codex 使用的本地环境与运行命令说明。
- `项目总设计方向.md`：交给实现模型的总体设计与算法路线。
- `最终冲刺与泛化方案.md`：新数据集下保持高分与隐藏司机泛化的最终方案。
- `docs/`：官方赛题说明、数据说明、评测规则、提交方式与快速开始。
- `demo/`：官方离线仿真工程，后续主要修改 `demo/agent/`。
- `demo/server/data/`：已解压的公开数据，供本地仿真使用；决策代码运行时禁止直读。
- `standord_mus_tread/`：原始要求截图。
- `demo_docs_release_20260509.zip`：旧版官方包，仅作历史参考。
- `demo_docs_release_20260529.zip`：当前官方公开数据与 Demo 原件。

## 开发原则

Agent 决策代码必须通过 `SimulationApiPort` 获取状态、货源与历史，禁止读取 `demo/server/data/cargo_dataset.jsonl`、`demo/server/data/drivers.json` 或其复制文件。正式提交以官方说明为准：初赛提交 `demo/agent/` 与必要 `demo/results/`，复赛至少提交 `demo/agent/`，不要把 `data/` 打进提交包。

真实 API key 只放在本机 `D:\竞赛\.env.local`，该文件已被 Git 忽略；运行模型验证前用 `.\scripts\load_local_env.ps1` 注入当前 PowerShell 进程。不要把真实 key、`.env.local` 或含 key 的截图推送到 GitHub。

## 当前状态（截至 2026-06-07 00:25 +08:00）

当前 `main` 已同步 2026-05-29 官方服务端和评测框架。公开数据为 500,000 条货源、31 个品类，`query_cargo(k)` 上限为 600。现有 Agent 对新版口语化偏好的解析覆盖不足，D002 的 7 条规则当前全部漏识别，因此不得直接在旧策略上调权重。

下一位模型应先阅读 `最终冲刺与泛化方案.md` 和 `.claude/read.txt`，一次性完成通用偏好编译器、约束日程器、自适应查询和新版完整评测。
