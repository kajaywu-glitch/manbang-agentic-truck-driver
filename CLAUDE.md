# Claude/Codex 项目交接说明

这份文档是给下一次接手的模型优先阅读的项目状态说明。目标是让新会话不用重新摸索环境、赛题约束和当前策略问题，就能直接继续修改 `demo/agent/`。

**重要：先读 `D:\竞赛\WORKFLOW_MIMO_CODEX.md` 了解协作工作流和分支规则，再读本文档。**

## 当前结论

- 仓库：`D:\竞赛`
- 远程：`https://github.com/kajaywu-glitch/manbang-agentic-truck-driver.git`
- 当前工作分支：`mimo/fix-d010-family-task`
- 当前 Agent 已能完整跑 31 天本地仿真，最新已计算结果无崩溃、无 `validation_error`、10 名司机都有动作日志。
- 当前策略仍不是最终版：`mimo/fix-d010-family-task` 的无模型结果总净收入 `115,570.25`，总偏好罚分 `16,945`，但该结果来自含阻塞问题的分支，不能直接作为可合并成绩。
- 当前主路径是确定性滚动规划；`qwen3.5-flash` 已集成到 `planner.py` 主决策流程（rank_cargos、suggest_decision、apply_qwen_hints），但默认不启用（需设置 `AGENT_ENABLE_QWEN35_FLASH=1`）。
- Codex 审阅结论：`mimo/fix-d010-family-task` 不建议原样合并到 `main`。主要阻塞点是 `planner.py` 中按 `driver_id == "D010"` 硬编码家事规则，违反赛题约束；应改成完全基于运行时 `preferences` 的通用处理。

## Windows 环境

项目在 Windows 下开发。不要使用 `python3`，它容易命中 Microsoft Store 占位符。

可用 Python 环境：

- Conda 安装位置：`C:\Users\20689\miniconda3`
- 项目虚拟环境：`mus-tread`
- Python 版本：`3.11.15`
- 已安装依赖：`numpy`、`requests`

推荐运行方式：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

运行单个脚本：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python D:\竞赛\demo\calc_monthly_income.py
```

也可以先进入环境：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe activate mus-tread
```

如果 `activate` 在当前 shell 不可用，就继续用 `conda.exe run -n mus-tread python ...`。

## 机密和配置

- 不要提交真实 API key。
- 不要提交 `demo/server/config/config.json`。
- 不要提交 `demo/results/`。
- 如果用户曾经把 key 发到聊天里，视为已泄露，必须建议用户在百炼控制台删除/禁用旧 key 后新建。

本地模型环境变量示例：

```powershell
$env:DASHSCOPE_API_KEY = "你的新APIKey"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
```

推荐本地做法：把真实 key 填到仓库根目录的 `D:\竞赛\.env.local`，该文件已被 `.gitignore` 忽略，不要提交。Mimo/CC 在运行仿真前，在同一个 PowerShell 终端执行：

```powershell
cd D:\竞赛
.\scripts\load_local_env.ps1
```

如果 PowerShell 拦截脚本执行，先在当前终端放开本进程策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\load_local_env.ps1
```

加载脚本只打印 `key_present=True/False`，不会打印真实 key。之后再运行 `demo/server/main.py`，子进程才能读到 `DASHSCOPE_API_KEY`、`TIANCHI_MODEL_API_KEY` 和 `AGENT_ENABLE_QWEN35_FLASH`。

CMD 写法不同：

```cmd
set DASHSCOPE_API_KEY=你的新APIKey
set TIANCHI_MODEL_API_KEY=%DASHSCOPE_API_KEY%
set AGENT_ENABLE_QWEN35_FLASH=1
```

不开启 `AGENT_ENABLE_QWEN35_FLASH` 时，当前 Agent 不会发起模型调用，token 用量为 0。

## 必守约束

- 主要修改 `demo/agent/`。
- 保持官方入口 `ModelDecisionService.decide(driver_id)` 不变。
- 决策代码只能通过 `SimulationApiPort` 获取状态、货源、历史和可选模型能力。
- 决策过程禁止直接读取 `demo/server/data/cargo_dataset.jsonl`、`demo/server/data/drivers.json` 或复制文件。
- 禁止按 `driver_id` 写死策略；可以解析运行时返回的 `preferences` 文本并生成通用规则。
- 做完代码修改后至少运行一次 `compileall`，重要策略改动后跑 31 天仿真并计算收益。

## 本轮审阅发现（2026-05-29）

这些是 `mimo/fix-d010-family-task` 相对 `main` 的审阅结论，给下一位模型优先处理。

### 阻塞问题

1. `demo/agent/planner.py:87-100` 按 `driver_id == "D010"` 注入 `FamilyTask`，违反“禁止按 driver_id 写死策略”。该分支不能原样合并。
2. 硬编码注释称“仿真 API 不返回该偏好”不准确。实测在 2026-03-10 10:00 以后，`get_driver_status("D010")` 会返回家事偏好，`parse_preferences()` 也能解析出 `FamilyTask(start_minute=13560, home_deadline_minute=14280, stay_until_minute=18600)`。
3. 硬编码中的 `home_deadline_minute=17880` 与偏好文本“2026年3月10日22:00前进家门”不一致，正确值应为 `14280`。当前字段暂时未被 `_family_action()` 使用，但这是后续扩展时会埋雷的错误。

### 需要优化的代码方向

1. **D010 家事修复必须通用化**：删除 `driver_id` 分支，只依赖 `status["preferences"]` -> `parse_preferences()` 得到的 `family_task`。如果需要测试，写基于偏好文本的单元/调试脚本，不把司机号写进策略。
2. **家事动作应使用 deadline**：`_family_action()` 现在只看 `start_minute` 和 `stay_until_minute`，没有利用 `home_deadline_minute` 做“最晚回家”判断。应在可见家事偏好后立即评估：先接配偶，再回家；如果距离导致 22:00 前回家风险高，禁止查询货源和接单，直接执行家事路径。
3. **家事窗口内禁止查询货源**：家事、回家、连续休息这类硬约束应在 `query_cargo` 前完成决策，避免查询耗时切碎连续等待或造成迟到。
4. **接单评估要避免覆盖未来已知硬约束**：如果 `family_task` 已经可见，`_evaluate_cargo()` 需要拒绝会延伸到接人、回家或 stay 窗口内的订单；但不能用隐藏的原始数据提前预知未来偏好。
5. **D009 home-night 分支有回退风险**：当前分支把 D010 罚分降下来了，但 D009 从上一轮 8,100 变成 9,000，收益也明显下降。后续修复不能只看总罚分，必须逐司机对比 D009/D010/D001-D008。
6. **连续休息仍需前移**：D001/D002/D006/D008/D010 仍有连续休息罚分。优先检查 `query_cargo` 扫描耗时、短 `wait`、夜间回家动作是否把连续休息切碎。
7. **Qwen 验证不能替代合规修复**：真实 key 验证应继续做，但模型不能掩盖 hardcode。Qwen 只可用于结构化偏好、候选评分和候选复审，最终动作仍由本地合法候选产生。

### 下一轮推荐优化策略

采用 **风险门控滚动规划（Risk-Gated MPC）+ 稀疏 Qwen 顾问**，这是当前阶段最值得做的一轮效果优化。

核心做法：

1. 本地 Planner 先做硬约束可行性检查：家事、home-night、连续休息、熟货、必访点、禁入区。任何会导致高罚分窗口不可达的候选直接判 invalid。
2. 对剩余候选做 1-3 步短视滚动评分：`expected_net - deadhead_cost - time_cost - penalty_risk + preference_progress_bonus`。重点把“接这单后还能不能回家/休息/到家事点”算进 `penalty_risk`。
3. 增加 `risk_level`：只有 `home_night`、`family`、`rest`、`required_cargo`、`required_visit`、候选分差很小或本地评分不确定时，才允许 Qwen 介入。
4. 默认关闭普通场景的 `rank_cargos()`，避免每步都问模型。Qwen 优先做 `suggest_decision()`，只在本地合法候选 index 中选择。
5. Qwen 输入只给 top 3-5 个候选摘要，限制 `max_tokens`，超时 10-15 秒立即 fallback。`AGENT_QWEN_MAX_REVIEWS` 短测从 20 开始。
6. 偏好结构化 `preference_hints()` 必须缓存，同一份 preferences 不重复调用。

预期收益：先压低 D009/D010/连续休息这类高罚分，再让 Qwen 处理少数冲突决策；同时显著减少模型调用、超时和 token。

## 关键文件

- `demo/agent/model_decision_service.py`：官方入口，调用 Planner，并在异常时返回合法 `wait`。
- `demo/agent/planner.py`：核心确定性滚动规划器，包含紧急规则、接单评分、等待、空驶和 Qwen hook。
- `demo/agent/preference_rules.py`：偏好文本解析，生成 `PreferencePolicy`。
- `demo/agent/state_tracker.py`：从官方 `query_decision_history` 重建司机状态。
- `demo/agent/geo.py`：距离、时间、区间工具。
- `demo/agent/llm_helper.py`：`qwen3.5-flash` 偏好结构化、货源评分和候选复审接口。
- `项目总设计方向.md`：总体设计和赛题约束。
- `demo/agent/README.md`：Agent 目录内实现说明和下一步调参入口。

## 当前代码状态

已实现能力：

- 三类动作：`take_order`、`wait`、`reposition`。
- 候选货源收益估算：空驶到装货点、装货窗等待、干线耗时、总成本、单位时间收益。
- 货源过滤：车型、装货窗、月度终点、城市边界、禁入区、最大干线距离、最大赴装货点空驶、禁运品类、安静窗口。
- 偏好解析：休息、禁接/尽量不接、每日时间窗、整天不接单/不出车、距离限制、禁入圆区、必访点、每日回家、临时熟货、家事。
- 休息/不接单/不出车前瞻：尽量避免把月度休息日拖到最后。
- D003 月度空驶限额已经明显修好，最近完整结果中空驶 `99.93km`，罚分 0。
- D009 指定熟货 `240646` 已能接到，熟货罚分 0。
- D010 家事任务在当前审阅分支通过硬编码注入后 `sequence_ok=true`，但该做法违规，必须改成运行时偏好解析路径。
- Qwen3.5-Flash 已接入主流程，但当前最新结果仍是未启用模型跑出的，token 用量 0。

## Qwen3.5-Flash 集成状态

已完成代码集成（`planner.py` + `llm_helper.py` + `preference_rules.py`）：

- `rank_cargos()`：在 `_best_cargo_plan()` 中调用，alpha=0.35 融合模型评分与确定性评分。
- `suggest_decision()`：在 `decide()` 中候选分数接近或高风险偏好时调用，安全检查防止模型选低分候选。
- `apply_qwen_hints()`：偏好解析后调用，只能收紧约束不能放松。
- `AGENT_QWEN_MAX_REVIEWS`（默认 500）：控制每次仿真调用次数。
- 安全降级：模型调用失败时完全回退确定性逻辑。

验收状态：

- [x] 不设置 key 时短测和 31 天评测仍能跑通。
- [x] 本机 `D:\竞赛\.env.local` 已配置真实 key，`scripts/load_local_env.ps1` 脱敏检查通过：`key_present=True`、key 形态为 `sk-...`、`.env.local` 被 Git 忽略。不要把 key 写入任何提交或文档。
- [ ] 设置 `AGENT_ENABLE_QWEN35_FLASH=1` 后，日志能看到真实模型调用。
- [ ] `monthly_income_202603.json` 中 token 用量不再全为 0。
- [ ] 启用 Qwen 的完整 31 天结果要和确定性基线对比。

真实 key 验证计划：

1. **先修合规阻塞**：删除 `planner.py` 中 D010 `driver_id` hardcode，保留通用 `preferences` 解析路径。Qwen 验证不能在违规策略上作为可合并依据。
2. **环境检查并限制模型调用**：

```powershell
cd D:\竞赛
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\load_local_env.ps1
$env:AGENT_QWEN_MAX_REVIEWS = "20"
```

3. **短测**：在同一终端运行 200 步，确认无崩溃、日志出现 `model_chat_completion ok` 或 Agent Qwen 日志。

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

4. **短测后收益检查**：确认 `monthly_income_202603.json` 无 `validation_error`，token 大于 0。若 token 仍为 0，先查环境变量是否在同一终端、`AGENT_ENABLE_QWEN35_FLASH` 是否为 `1`、模型触发条件是否没有命中。

```powershell
cd D:\竞赛\demo
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

5. **不要直接完整 Qwen 评测**：2026-05-29 已实测，真实 Qwen 完整 31 天在 D001 阶段就超过 20 分钟，并出现 60 秒读取超时。短测通过后先把上限调到 `50` 做较长短测，并收紧触发条件；只有模型调用频率和超时可控，再跑完整 31 天。
6. **完整 31 天评测**：记录无模型 vs 启用 Qwen 的总净收入、总罚分、D009/D010 罚分、运行时间、token 用量和模型调用次数。

需要注意：

- `state_tracker.DriverMemory.market_heat` 目前由当前决策里的 `query_cargo` 结果临时写入；`build_memory()` 不会从历史恢复 market heat。因此它不是跨步长期记忆。若要真正做在线市场学习，需要在 `DeterministicPlanner` 内增加实例级缓存，并保证不读取结果文件或原始数据。
- `query_cargo` 会消耗仿真时间。休息、家事、回家这类硬约束最好在查询前的 urgent 阶段就返回 `wait/reposition`，否则查询消耗的分钟可能继续切碎连续休息或迟到窗口。
- `planner._best_cargo_plan()` 对指定熟货给了极高优先级；如果 `_evaluate_cargo()` 返回 `None`，当前逻辑仍可能强制接单。下一步排查 D009 时要优先处理这个路径，避免它绕过 home-night 或其他硬约束。

## 最近完整评测

最近已计算结果位于被 `.gitignore` 忽略的本地目录：

```text
D:\竞赛\demo\results\
```

对应文件：

- `run_summary_202603.json`
- `monthly_income_202603.json`
- `actions_202603_D001_*.jsonl` 至 `actions_202603_D010_*.jsonl`

最近完整仿真摘要：

| 指标 | 值 |
| --- | ---: |
| 仿真月份 | 2026-03 |
| 仿真天数 | 31 |
| completed_steps | 2292 |
| simulate_time_seconds | 149.98 |
| failed_driver_count | 0 |
| total_token_usage | 0 |
| total_net_income_all_drivers | 115,570.25 |
| total_preference_penalty | 16,945 |

司机结果：

| 司机 | 净收入 | 罚分 | 当前主要问题 |
| --- | ---: | ---: | --- |
| D001 | 10,942 | 1,200 | 每日连续休息 8h 有 4 天未满足 |
| D002 | 21,015 | 1,600 | 每日连续休息 4h 有 8 天未满足 |
| D003 | 830 | 0 | 合法但收益过低 |
| D004 | 15,024 | 0 | 暂无罚分 |
| D005 | 17,052 | 0 | 暂无罚分 |
| D006 | 14,399 | 1,200 | 每日连续休息 5h 有 6 天未满足 |
| D007 | 19,721 | 0 | 暂无罚分 |
| D008 | 20,461 | 2,600 | 平日连续休息 4h 有 6 天未满足 + 1 次食品饮料 |
| D009 | 514 | 9,000 | 每日 23 点前到家 10 次违规 |
| D010 | -6,567 | 2,245 | 家事 1,645 + 休息 600；家事窗口占 3.5 天导致收益为负 |

本轮检查已运行：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

结果：通过。

## 下一步优先级

### 高优先级

1. **移除 D010 hardcode**：这是当前分支合并前的阻塞项。删除 `driver_id == "D010"` 注入，证明运行时 `preferences` 可解析，并重新跑完整 31 天。
2. **通用家事执行器**：优化 `_family_action()` 与 `_evaluate_cargo()`，用 `home_deadline_minute`、pickup wait、stay window 做通用约束；确保偏好可见后不再查询货源、不再接会覆盖家事窗口的订单。
3. **D009 home_night**（罚分 9,000）：10 次 23:00 前未到家。核心问题是白天接远单后赶不回家。当前 home_night 约束效果有限，需要更强的白天定位策略（如下午主动 reposition 回家方向）。
4. **每日连续休息**：D001(1,200)、D002(1,600)、D006(1,200)、D008(2,400)、D010(600) 仍有罚分。检查休息是否被查询耗时切碎。
5. **用真实 API key 验证 Qwen3.5-Flash**。`.env.local` 格式已检查通过；合规修复后按上面的真实 key 验证计划先跑 `--max-steps 200`，确认日志中有模型调用和 token > 0，再跑 31 天完整评测。

### 已知问题

1. **D010 收益为负**（净收入 -6,567）：家事窗口占 3/10-3/13 共 3.5 天，导致收益大幅下降。合规修复后再优化家事窗口外的接单效率。
2. **D003 收益过低**（净收入仅 830）：空驶限额限制了接单能力。
3. **market_heat 跨步记忆**：当前只在当前决策步内累积，需增加实例级缓存。

### 低优先级

1. D003 空驶限额内的收益优化：更精准选择高价值订单。
2. market_heat 跨步记忆：当前 `market_heat` 只在当前决策步内累积，`build_memory()` 不恢复。需要在 `DeterministicPlanner` 内增加实例级缓存。

## 调试命令

清理 Python 缓存：

```powershell
Get-ChildItem -LiteralPath D:\竞赛\demo -Directory -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath D:\竞赛\demo -File -Recurse -Filter *.pyc | Remove-Item -Force
```

完整 31 天评测前，确认本地 `demo/server/config/config.json` 中：

```json
"simulation_duration_days": 31
```

运行仿真：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py
```

收益计算：

```powershell
cd D:\竞赛\demo
$env:PYTHONIOENCODING = "utf-8"
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

短测：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

检查结果：

```powershell
Get-Content D:\竞赛\demo\results\monthly_income_202603.json -Raw
```

实时看仿真进度：

```powershell
cd D:\竞赛
.\scripts\watch_progress.ps1
```

单次查看当前最新进度：

```powershell
.\scripts\watch_progress.ps1 -Once
```

该脚本只读取 `demo/results/logs/simulation_orchestrator.log`，会显示最近的 driver、step、仿真时间、动作和 token。完整 Qwen 测试时必须同时开一个进度窗口，避免长时间不知道跑到哪里。

## Git 工作流

- 当前要求是每轮有效改动后 commit 并 push 到 GitHub。
- 提交前检查：

```powershell
git -C D:\竞赛 status --short --branch
git -C D:\竞赛 diff --stat
git -C D:\竞赛 diff --cached --stat
```

- 不提交：真实 key、`demo/server/config/config.json`、`demo/results/`、`.claude/*.local.json`。
- 如果只改文档，至少跑 `compileall` 已足够；如果改 Agent 策略，必须重新跑仿真和收益脚本。
