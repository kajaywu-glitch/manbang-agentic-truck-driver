# 满帮 Agent 算法大赛项目

最后更新：2026-05-29 19:42 +08:00

本次更新：同步 `mimo/risk-gated-mpc` 审查修正与合并状态，说明已加入 Risk-Gated MPC、仿真进度 heartbeat、Qwen 输出限长和调用收紧；下一轮重点是完整 31 天确定性基线、真实 Qwen 小上限短测、以及“当前跑到第几天/完成几个司机/阶段结果”的评测进度显示。

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

真实 API key 只放在本机 `D:\竞赛\.env.local`，该文件已被 Git 忽略；运行模型验证前用 `.\scripts\load_local_env.ps1` 注入当前 PowerShell 进程。不要把真实 key、`.env.local` 或含 key 的截图推送到 GitHub。

## 当前状态（截至 2026-05-29 19:42 +08:00）

截至 2026-05-29 19:42 +08:00，当前稳定主线为 `main`，本次合并包含 `mimo/risk-gated-mpc`：D010 家事逻辑仍保持运行时 `preferences` 解析；新增 Risk-Gated MPC、`AGENT_PROGRESS_STDERR` heartbeat、Qwen 候选触发收紧，并经 Codex 审查补充了 `max_tokens` 限制、rank 候选数 5、默认 `AGENT_QWEN_MAX_REVIEWS=20` 和家事 deadline 修正。已通过 `compileall` 和关闭 Qwen 的 `--max-steps 50`。

Agent 已实现确定性滚动规划，并已接入 `qwen3.5-flash` 的偏好增强、货源评分与候选复审接口；默认不开模型，开启需设置 `AGENT_ENABLE_QWEN35_FLASH=1`。下一步优先重跑合并后的 31 天确定性基线，然后用真实 key 做小上限 Qwen 短测，确认 token 和耗时可控。下一位模型请优先阅读 `WORKFLOW_MIMO_CODEX.md`、`CLAUDE.md` 和 `demo/agent/README.md`。
