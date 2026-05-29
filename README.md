# 满帮 Agent 算法大赛项目

本仓库用于「基于 Agentic AI 的卡车司机连续找货决策」赛题的本地开发、设计沉淀与后续审阅。

## 当前目录

- `CLAUDE.md`：Claude Code/Codex 使用的本地环境与运行命令说明。
- `项目总设计方向.md`：交给实现模型的总体设计与算法路线。
- `docs/`：官方赛题说明、数据说明、评测规则、提交方式与快速开始。
- `demo/`：官方离线仿真工程，后续主要修改 `demo/agent/`。
- `demo/server/data/`：已解压的公开数据，供本地仿真使用；决策代码运行时禁止直读。
- `standord_mus_tread/`：原始要求截图。
- `demo_docs_release_20260509.zip`：官方公开数据与 demo 压缩包原件。

## 开发原则

Agent 决策代码必须通过 `SimulationApiPort` 获取状态、货源与历史，禁止读取 `demo/server/data/cargo_dataset.jsonl`、`demo/server/data/drivers.json` 或其复制文件。正式提交以官方说明为准：初赛提交 `demo/agent/` 与必要 `demo/results/`，复赛至少提交 `demo/agent/`，不要把 `data/` 打进提交包。

## 当前状态

当前 Agent 已实现确定性滚动规划，并在 `mimo/fix-home-night-rest-family` 分支接入了 `qwen3.5-flash` 的偏好增强与候选复审接口；默认不开模型，无 key 时仍走确定性 fallback。最新已计算的 31 天本地结果无崩溃、无 `validation_error`，总净收入 `138,156.88`、总罚分 `25,445`、token 用量 0。下一位模型请优先阅读 `WORKFLOW_MIMO_CODEX.md`、`CLAUDE.md` 和 `demo/agent/README.md`。
