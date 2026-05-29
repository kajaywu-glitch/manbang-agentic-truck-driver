# Mimo + Codex 协作工作流

最后更新：2026-05-29 16:43 +08:00

本次更新：把 `mimo/fix-d010-family-task` 的审阅状态改为已合并到 `main@ec2f92c`，更新给 Mimo/Codex 的下一轮操作口令，并要求文档更新必须带时间戳。

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

每次改文档时，必须同步写明更新时间和本次更新内容；如果某段旧结论已经被新提交修正，要删除或明确标记为历史记录，不要让下一位模型误以为仍是当前状态。

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

当前 `mimo/fix-d010-family-task` 的审阅与合并结论（截至 2026-05-29 16:43 +08:00）：

- 截至 2026-05-29 16:43 +08:00，该分支最新提交 `ec2f92c` 已删除 D010 `driver_id` hardcode，并已 fast-forward 合并到 `main`。
- 已验证：D010 家事偏好在 2026-03-10 10:00 后会通过 `get_driver_status()` 出现在运行时 `preferences` 中，现有 `parse_preferences()` 能解析出 `FamilyTask`。
- Codex 已检查 `demo/agent`，未发现 D010 hardcode 残留，也未发现决策代码直读 `cargo_dataset.jsonl` / `drivers.json`。
- `compileall` 通过；确定性 `--max-steps 200` 短测通过。完整 31 天确定性基线需要在 `main@ec2f92c` 上重跑。
- 删除 hardcode 前的旧 31 天结果只能作为历史对比，不再作为可合并成绩。

Codex 如果只给审阅意见，不直接改 Mimo 分支。若需要修复，另开分支：

```powershell
git switch -c codex/fix-任务名
```

然后提交并推送，让用户决定是否让 Mimo 合并。

## Qwen3.5-Flash 协作要求

当前分支已经开始 Hybrid Agent 接入：Qwen3.5-Flash 的偏好 hints、货源评分和候选复审接口已进入 `demo/agent/`。后续不能退回无限纯确定性调参，必须继续验证并收紧这条模型参与链路。

原则：

- 确定性 Planner 负责候选生成、硬约束、收益估算和兜底。
- Qwen3.5-Flash 负责偏好结构化、高风险候选复审、调参解释。
- 模型不能自由生成未经校验的最终动作。
- 模型只能输出 JSON，或从本地候选 index 中选择。
- 失败时必须自动回退确定性策略。
- 需要记录 token、耗时、收益、罚分，与未启用模型基线对比。

启用环境变量：

```powershell
cd D:\竞赛
.\scripts\load_local_env.ps1
```

真实 key 放在 `D:\竞赛\.env.local`。该文件已被 Git 忽略，只能留在用户本机；Mimo 不得把 `.env.local`、真实 key、截图或控制台明文 key 提交到仓库或贴进交接文档。脚本只把 key 注入当前 PowerShell 进程，随后在同一终端运行仿真即可。

如果脚本被执行策略拦截：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\load_local_env.ps1
```

建议增加或使用调用上限：

```powershell
$env:AGENT_QWEN_MAX_REVIEWS = "20"
```

注意：2026-05-29 已实测，一上来用真实 Qwen 跑完整 31 天会非常慢。D001 还未跑完时就已耗时 20 分钟以上，日志中还出现 `Read timed out (read timeout=60.0)`。因此真实 Qwen 验证必须先低上限短测；不要直接开完整 31 天。

验收标准：

- 不设置 key 时完整仿真仍可跑通。
- 本机 `D:\竞赛\.env.local` 已由用户填写真实 key，`scripts/load_local_env.ps1` 可脱敏加载；任何人不得提交该文件或在文档/聊天中泄露 key。
- 设置 key 后日志能确认模型实际参与。
- `monthly_income_202603.json` 中 token 用量不为 0，但远低于复赛上限。
- 启用 Qwen 后必须和确定性基线对比，不只看单次结果。
- 若启用 Qwen 后罚分上升或耗时失控，优先减少触发场景或降低 `AGENT_QWEN_MAX_REVIEWS`，不要扩大调用范围。

真实 key 短测/完整评测顺序：

1. 先消除合规阻塞，尤其是删除 D010 `driver_id` hardcode。
2. 在同一个 PowerShell 终端加载本地 key：

```powershell
cd D:\竞赛
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\load_local_env.ps1
```

3. 跑短测：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

4. 计算收益并检查 token：

```powershell
cd D:\竞赛\demo
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

5. 短测满足无崩溃、无 `validation_error`、token > 0 后，先把 `AGENT_QWEN_MAX_REVIEWS` 提高到 `50` 再跑较长短测；只有确认模型调用没有频繁 60 秒超时，才允许完整 31 天评测。交接说明中必须记录：总净收入、总罚分、D009/D010 罚分、运行时间、token 用量、是否出现模型 fallback。
6. 完整 Qwen 评测前，Mimo 必须先收紧触发条件：不能在普通接单步骤频繁 `rank_cargos`，应只在 home-night、家事、连续休息、指定熟货、必访点或候选分数接近时触发。

## Hybrid Agent 启动门槛

Mimo 不需要把确定性策略调到完美后才开始 Hybrid Agent。只要满足下面门槛，就应停止大规模纯规则调参，进入 Qwen3.5-Flash 受控接入阶段。

可以开始 Hybrid Agent 的条件：

- 完整 31 天仿真能跑完。
- `monthly_income_202603.json` 中没有 `validation_error`。
- `failed_driver_count = 0`，或失败原因已经定位为非 Agent 主逻辑问题。
- 10 名司机都有非空动作日志。
- `ModelDecisionService.decide(driver_id)` 在异常时有合法 fallback。
- 确定性 Planner 已能生成本地合法候选：`take_order`、`wait`、`reposition` 至少有可解释来源。
- 主要高罚分点已经能从结果文件定位到规则类别，例如 D009 home-night、D010 家事、连续休息、必访点。
- 无 key、模型失败、模型返回坏 JSON 时，当前确定性路径仍能独立跑通。

不必等待的条件：

- 不必等所有司机罚分为 0。
- 不必等 D009/D010 完全修好。
- 不必等收益调参稳定到最优。
- 不必等 market_heat 或长期记忆完全成熟。

还不能开始 Hybrid Agent 的情况：

- 仿真会崩溃。
- 动作 JSON 偶发非法。
- 存在直接读取 `cargo_dataset.jsonl` / `drivers.json` 的代码。
- 存在按 `driver_id` 写死策略，包括为了修 D010/D009 临时写 `if driver_id == ...`。
- 没有模型失败降级路径。
- Qwen 调用不是通过 `SimulationApiPort.model_chat_completion`。

如果已经满足启动门槛，Mimo 应明确切换任务目标：

```text
停止继续纯确定性调参。保留当前确定性 Planner 作为 fallback，从现在开始进入 Hybrid Agent 阶段。
```

## Hybrid Agent 实施计划

阶段 0：冻结确定性底座

- 不再大规模重构确定性规则。
- 只修会导致崩溃、非法动作、明显绕过硬约束的 bug。
- 记录当前不开模型的 31 天基线：净收入、总罚分、D009/D010 罚分、运行时间、token=0。

阶段 1：偏好结构化接入（当前分支已有代码，仍需真实 key 验证）

- 启用 `preference_hints()`。
- 增加或完善 `apply_qwen_hints()`。
- Qwen 输出只能新增或收紧规则，不能放松正则解析出的约束。
- 输出必须是 JSON；解析失败直接忽略。
- 对比启用前后规则对象是否合理，不急着让模型选动作。

阶段 2：候选复审接入（当前分支已有代码，仍需真实 key 验证）

- 确定性 Planner 先生成本地合法候选。
- Qwen 只能在候选 index 中选择，不能自由生成动作。
- 只在高风险场景调用：home-night、家事、连续休息、指定熟货、必访点、候选分数接近。
- 模型选择的候选必须再次经过本地校验。
- 若模型选择低质量或高风险候选，本地策略可以拒绝并使用确定性首选。

阶段 3：受控评测

- 先跑 `--max-steps 200`，确认模型调用、fallback、日志和 token 统计。
- 再跑完整 31 天。
- 产出对比表：不开模型 vs 启用 Qwen。
- 至少记录：总净收入、总罚分、D009 罚分、D010 罚分、运行时间、token 用量、模型调用次数。

阶段 4：小步调参

- 只调 Qwen 触发条件、候选摘要、拒绝阈值、调用上限。
- 不把全量 100 条货源原样塞给模型。
- 不让模型每一步都调用，优先高风险/高不确定性步骤。
- 每次只改一个变量，保留评测记录。

## 下一轮算法优化：Risk-Gated MPC

下一轮不要继续让 Qwen 对普通货源高频 `rank_cargos`。推荐改成 **风险门控滚动规划 + 稀疏 Qwen 顾问**：

- 本地先生成合法候选，并对每个候选做硬约束可行性检查。
- 用短视滚动评分估算候选后果：收益、空驶、时间、休息债、home-night 回家可达性、家事窗口可达性、必访点进度。
- 候选会破坏高罚分偏好时直接 invalid，而不是只扣一点分。
- 计算 `risk_level` 和 `score_gap`。只有高风险或分差接近时调用 Qwen。
- Qwen 只从本地候选 index 中选择，不能自由生成动作。
- 普通接单场景默认不调用 `rank_cargos`；如果保留，也只能给 top 3-5 个候选摘要，并设置短 timeout 与小 `max_tokens`。
- `preference_hints` 对同一份 preferences 必须缓存，避免每步重复调用。

这套策略的目标是先降高罚分，再少量用 Qwen 处理冲突决策。它比“每步让 Qwen 给货源打分”更符合比赛运行时和 token 约束。

## 进度显示要求

完整仿真或 Qwen 短测必须让用户能看到进度。

最低要求：另开一个 PowerShell 窗口运行：

```powershell
cd D:\竞赛
.\scripts\watch_progress.ps1
```

单次查看：

```powershell
.\scripts\watch_progress.ps1 -Once
```

该脚本会从 `demo/results/logs/simulation_orchestrator.log` 读取最近的 driver、step、仿真时间、动作和 token。不要再只用 `tail -50` 包住完整仿真，因为它会让用户长期看不到中间进度。

可选增强：如果后续需要改服务端日志，优先加非侵入式 heartbeat，而不是改评测逻辑。heartbeat 至少包含：driver、step、sim_clock、elapsed_seconds、tokens_total、qwen_call_count、last_action。

下一轮必须实现的内置进度能力：

- 增加启动时可开启的实时输出，例如：

```powershell
$env:AGENT_PROGRESS_STDERR = "1"
$env:AGENT_PROGRESS_EVERY_STEPS = "1"
```

- 输出位置用 `stderr` 或 logging，不能改变 `ModelDecisionService.decide(driver_id)` 的 action JSON。
- 每条 heartbeat 建议格式：

```text
[AGENT_PROGRESS] driver=D001 step=12 sim=2026-03-04 14:18 action=take_order reason=best_cargo qwen=rank_cargos tokens=5745 elapsed_ms=48720
```

- 不得输出 API key、完整 prompt、完整货源列表或 `.env.local`。
- 若在 `demo/agent/` 内实现，建议放在 `model_decision_service.py` 或独立 `progress.py`，只包裹决策输入/输出和耗时统计。
- 若在 `demo/server/bench/` 本地 runner 实现，必须保持评分与动作执行逻辑不变，只增加可选日志。
- 完整评测时，Mimo 交接说明必须写明进度输出是否可用、如何开启、最后跑到哪个 driver/step。

## Hybrid Agent 约束

- Qwen3.5-Flash 是基座模型，但不是无约束动作生成器。
- 本地代码仍是安全边界：候选生成、硬约束、动作合法性、fallback 必须在本地。
- 模型请求必须显式写 `model: "qwen3.5-flash"`。
- 模型输入只给必要摘要，不给原始全量数据文件。
- 不提交真实 API key。
- 不提交 `demo/server/config/config.json`。
- 不提交 `demo/results/`。
- 保持复赛 token 上限意识：每司机不超过 500 万 token，总运行时不超过 4 小时。
- 若启用 Qwen 后罚分上升或运行时间失控，优先收紧触发条件，不要扩大调用范围。

## 合并到 main 的条件

满足以下条件后，才建议合并：

- `compileall` 通过。
- 至少一次短测通过。
- 重要策略改动有完整 31 天评测结果。
- 无 `validation_error`。
- 无仿真崩溃。
- 无按 `driver_id` 写死策略。
- 无为追分绕过运行时 `preferences`、`query_cargo`、`query_decision_history` 的逻辑。
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

让 Mimo 开始下一轮优化（口令更新时间：2026-05-29 16:43 +08:00）：

```text
请先阅读 D:\竞赛\WORKFLOW_MIMO_CODEX.md、D:\竞赛\CLAUDE.md 和 D:\竞赛\demo\agent\README.md。当前 main 已合并 ec2f92c，D010 driver_id hardcode 已删除。请从最新 main 新建分支，先重跑确定性 31 天基线并计算收益；随后优先实现 Risk-Gated MPC、内置 AGENT_PROGRESS_STDERR 进度输出、D009/D010 通用高罚分约束优化，以及稀疏 Qwen 顾问触发收紧。完成后 push 并写清验证结果。
```

让 Codex 审阅（口令更新时间：2026-05-29 16:43 +08:00）：

```text
请审阅 Mimo 新分支的最新改动，按 WORKFLOW_MIMO_CODEX.md 的审阅规则检查是否违反赛题约束、是否有 hardcode、结果是否可信、Qwen 调用是否受控，以及是否可以合并。
```
