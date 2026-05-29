# Mimo + Codex 协作工作流

这份文档给 Mimo、Claude Code、Codex 和用户共同使用。目标是让 Mimo 可以持续构建，Codex 可以随时审阅，而不会互相覆盖代码或把未验证改动直接推到 `main`。

## 先读顺序

每次新会话开始，先按这个顺序阅读：

1. `WORKFLOW_MIMO_CODEX.md`：协作规则和 Git 流程。
2. `CLAUDE.md`：当前项目状态、环境、最近评测结果、下一步优先级。
3. `demo/agent/README.md`：Agent 内部结构和 Qwen3.5-Flash 接入任务。
4. `项目总设计方向.md`：赛题约束、总体算法路线和审阅关注点。

## 角色分工

Mimo 负责实现：

- 在独立分支上做代码改动。
- 小步提交，每个提交只解决一个明确问题。
- 每轮结束更新交接说明，说明改了什么、测了什么、还剩什么。
- 不直接把不稳定代码合并到 `main`。

Codex 负责审阅：

- 拉取 Mimo 分支，检查 diff、规则合规性、潜在 bug 和测试结果。
- 必要时在单独的 `codex/...` 分支上提交 review fix。
- 给出审阅结论：可继续、需修复、或建议合并。

用户负责调度：

- 指定 Mimo 当前要做的分支或任务。
- 在 Mimo push 后叫 Codex 审阅。
- 决定是否合并到 `main`。

## 分支规则

`main` 只放稳定同步点。不要在 `main` 上直接做大改。

推荐分支命名：

```text
mimo/qwen-integration
mimo/fix-d009-home-night
mimo/fix-d010-family-task
mimo/rest-policy-tuning
codex/review-qwen-integration
codex/fix-after-review
```

如果接手时发现工作区已经不干净：

```powershell
git -C D:\竞赛 status --short --branch
```

不要直接覆盖。先判断这些改动是谁做的：

- 如果是 Mimo 当前工作，继续在当前分支提交。
- 如果误在 `main` 上改了，先新建分支保存：

```powershell
git -C D:\竞赛 switch -c mimo/current-work
git -C D:\竞赛 add demo/agent
git -C D:\竞赛 commit -m "Save current Mimo work"
git -C D:\竞赛 push -u origin mimo/current-work
```

不要使用 `git reset --hard`、`git checkout -- 文件` 等会丢弃别人改动的命令，除非用户明确要求。

## Mimo 开发流程

从最新远程开始：

```powershell
cd D:\竞赛
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c mimo/任务名
```

实现时遵守：

- 主要修改 `demo/agent/`。
- 保持 `ModelDecisionService.decide(driver_id)` 官方入口不变。
- 决策代码只通过 `SimulationApiPort` 访问状态、货源、历史和模型接口。
- 禁止读取 `demo/server/data/cargo_dataset.jsonl`、`demo/server/data/drivers.json` 或复制文件。
- 禁止按 `driver_id` 写死策略。
- 不提交真实 API key、`demo/server/config/config.json`、`demo/results/`。

每个小步完成后至少运行：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

重要策略改动后运行短测：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

阶段性完成后运行完整 31 天评测，并计算收益：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py

cd D:\竞赛\demo
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

提交和推送：

```powershell
git -C D:\竞赛 status --short
git -C D:\竞赛 diff --stat
git -C D:\竞赛 add demo/agent CLAUDE.md demo/agent/README.md
git -C D:\竞赛 commit -m "简短说明本轮改动"
git -C D:\竞赛 push -u origin mimo/任务名
```

只提交本轮相关文件。不要顺手提交本地配置、结果目录或无关临时文件。

## Mimo 交接格式

每次 push 后，在 `CLAUDE.md` 或给用户的消息里写清楚：

```text
分支：
最新 commit：

本轮目标：
已修改文件：
主要实现：
验证命令：
验证结果：
当前收益/罚分变化：
仍未解决：
希望 Codex 审阅重点：
```

## Codex 审阅流程

用户可以这样叫 Codex：

```text
请审阅 GitHub 上 mimo/任务名 分支的最新改动，重点看是否违反赛题约束、是否有 bug、测试结果是否可信。
```

Codex 审阅命令：

```powershell
cd D:\竞赛
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c codex/review-任务名 origin/mimo/任务名
git diff main...HEAD --stat
git diff main...HEAD
```

审阅重点：

- 是否直读官方数据文件。
- 是否按 `driver_id` 硬编码。
- 动作 JSON 是否始终合法。
- Qwen3.5-Flash 是否只通过 `SimulationApiPort.model_chat_completion` 调用。
- 无 key、模型失败、超时时是否能降级。
- 是否过度调用 `query_cargo` 或模型，导致时间/token 失控。
- D009 home-night、D010 家事、连续休息、必访点、熟货等高罚分规则是否更好。
- 是否运行了足够验证。

Codex 如果只给审阅意见，不直接改 Mimo 分支。若需要修复，另开分支：

```powershell
git switch -c codex/fix-任务名
```

然后提交并推送，让用户决定是否让 Mimo 合并。

## Qwen3.5-Flash 协作要求

下一轮不能只做离线确定性调参，必须把 Qwen3.5-Flash 作为真实参与迭代的基座模型层。

原则：

- 确定性 Planner 负责候选生成、硬约束、收益估算和兜底。
- Qwen3.5-Flash 负责偏好结构化、高风险候选复审、调参解释。
- 模型不能自由生成未经校验的最终动作。
- 模型只能输出 JSON，或从本地候选 index 中选择。
- 失败时必须自动回退确定性策略。
- 需要记录 token、耗时、收益、罚分，与未启用模型基线对比。

启用环境变量：

```powershell
$env:DASHSCOPE_API_KEY = "你的新APIKey"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
```

建议增加或使用调用上限：

```powershell
$env:AGENT_QWEN_MAX_REVIEWS = "300"
```

验收标准：

- 不设置 key 时完整仿真仍可跑通。
- 设置 key 后日志能确认模型实际参与。
- `monthly_income_202603.json` 中 token 用量不为 0，但远低于复赛上限。
- 启用 Qwen 后必须和确定性基线对比，不只看单次结果。

## 合并到 main 的条件

满足以下条件后，才建议合并：

- `compileall` 通过。
- 至少一次短测通过。
- 重要策略改动有完整 31 天评测结果。
- 无 `validation_error`。
- 无仿真崩溃。
- 无真实 API key 或本地配置文件进入 Git。
- Codex 审阅没有阻塞问题。

合并方式建议使用 GitHub Pull Request。若本地合并：

```powershell
git -C D:\竞赛 switch main
git -C D:\竞赛 pull --ff-only origin main
git -C D:\竞赛 merge --no-ff mimo/任务名
git -C D:\竞赛 push origin main
```

## 给用户的最短操作口令

让 Mimo 开始：

```text
请先阅读 D:\竞赛\WORKFLOW_MIMO_CODEX.md 和 D:\竞赛\CLAUDE.md，然后从 main 新建 mimo/qwen-integration 分支。按文档要求小步实现 Qwen3.5-Flash 真实参与迭代，完成后 push 并写交接说明。
```

让 Codex 审阅：

```text
请审阅 origin/mimo/qwen-integration 的最新改动，按 WORKFLOW_MIMO_CODEX.md 的审阅规则检查并给出结论。
```
