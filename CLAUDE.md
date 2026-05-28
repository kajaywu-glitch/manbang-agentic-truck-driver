# Claude/Codex 项目交接说明

这份文档是给下一次接手的模型优先阅读的项目状态说明。目标是让新会话不用重新摸索环境、赛题约束和当前策略问题，就能直接继续修改 `demo/agent/`。

## 当前结论

- 仓库：`D:\竞赛`
- 远程：`https://github.com/kajaywu-glitch/manbang-agentic-truck-driver.git`
- 分支：`main`
- 当前 Agent 已能完整跑 31 天本地仿真，最近一次完整结果无崩溃、无 `validation_error`、10 名司机都有动作日志。
- 当前策略仍不是最终版：总净收入约 `122,844.69`，总偏好罚分 `30,945`，最需要继续降罚分的是 D009、D010、D002、D008、D006、D001。
- 当前主路径是确定性滚动规划；`qwen3.5-flash` 目前只是可选偏好解析 hook，默认不调用模型。用户明确要求：下一轮必须把 `qwen3.5-flash` 作为真实参与迭代的基座模型层，而不是只满足于离线确定性策略。

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

## 关键文件

- `demo/agent/model_decision_service.py`：官方入口，调用 Planner，并在异常时返回合法 `wait`。
- `demo/agent/planner.py`：核心确定性滚动规划器，包含紧急规则、接单评分、等待、空驶和 Qwen hook。
- `demo/agent/preference_rules.py`：偏好文本解析，生成 `PreferencePolicy`。
- `demo/agent/state_tracker.py`：从官方 `query_decision_history` 重建司机状态。
- `demo/agent/geo.py`：距离、时间、区间工具。
- `demo/agent/llm_helper.py`：可选 `qwen3.5-flash` 偏好结构化接口。
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
- Qwen3.5-Flash 预留完整，但当前结果是确定性策略跑出的，token 用量 0。

## 下一轮必须加入的 Qwen3.5-Flash 迭代任务

今天不开始实现，但下一位模型必须把这件事作为最高级工程任务之一：在保持确定性降级的前提下，让 `qwen3.5-flash` 真实参与本地迭代，而不是只做离线/无模型策略。

建议目标架构：

1. 确定性 Planner 继续负责合法候选生成、硬约束过滤、收益估算和兜底。
2. Qwen3.5-Flash 作为基座模型层，参与“偏好结构化 + 高风险候选复审 + 调参解释”。
3. 模型不能自由生成未校验动作，只能在本地候选动作中选择，或输出结构化偏好/风险 JSON。
4. API key 缺失、模型超时、返回非 JSON、返回候选越界时，必须自动回退确定性策略。
5. 模型调用必须通过 `SimulationApiPort.model_chat_completion`，显式使用 `model: "qwen3.5-flash"`。
6. 必须记录 token、调用次数、运行时间、收益和罚分对比，评估“启用 Qwen”是否真的改进。

建议第一版实现入口：

- 在 `demo/agent/llm_helper.py` 增加 `choose_candidate(...)`，让模型只从本地候选 index 中选择。
- 在 `demo/agent/preference_rules.py` 增加 `apply_qwen_hints(...)`，把模型结构化偏好保守合并进 `PreferencePolicy`；只能新增或收紧约束，不能放松规则。
- 在 `demo/agent/planner.py` 中，当候选分数接近、存在 home-night/family/rest/required-cargo/required-visit 等高风险偏好时，调用 Qwen 做候选复审。
- 增加环境变量 `AGENT_QWEN_MAX_REVIEWS` 控制每次仿真的候选复审次数，避免 token 和运行时间失控。

验收标准：

- 不设置模型 key 时，短测和 31 天评测仍能跑通。
- 设置 `AGENT_ENABLE_QWEN35_FLASH=1` 后，日志能看到真实模型调用或候选复审记录。
- `monthly_income_202603.json` 中 token 用量不再全为 0，但不能接近复赛每司机 500 万 token 上限。
- 启用 Qwen 的完整 31 天结果要和确定性基线对比：总罚分、D009/D010 罚分、运行时间、token 用量。
- 不允许把真实 API key 写入任何提交文件。

需要注意：

- `state_tracker.DriverMemory.market_heat` 目前由当前决策里的 `query_cargo` 结果临时写入；`build_memory()` 不会从历史恢复 market heat。因此它不是跨步长期记忆。若要真正做在线市场学习，需要在 `DeterministicPlanner` 内增加实例级缓存，并保证不读取结果文件或原始数据。
- `query_cargo` 会消耗仿真时间。休息、家事、回家这类硬约束最好在查询前的 urgent 阶段就返回 `wait/reposition`，否则查询消耗的分钟可能继续切碎连续休息或迟到窗口。
- `planner._best_cargo_plan()` 对指定熟货给了极高优先级；如果 `_evaluate_cargo()` 返回 `None`，当前逻辑仍可能强制接单。下一步排查 D009 时要优先处理这个路径，避免它绕过 home-night 或其他硬约束。

## 最近完整评测

最近完整结果位于被 `.gitignore` 忽略的本地目录：

```text
D:\竞赛\demo\results\history\20260528_210701\
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
| completed_steps | 1959 |
| simulate_time_seconds | 683.03 |
| failed_driver_count | 0 |
| total_token_usage | 0 |
| total_net_income_all_drivers | 122,844.69 |
| total_preference_penalty | 30,945 |

司机结果：

| 司机 | 净收入 | 罚分 | 当前主要问题 |
| --- | ---: | ---: | --- |
| D001 | 9,453.86 | 900 | 每日连续休息 8h 有 3 天未满足 |
| D002 | 17,098.78 | 2,200 | 每日连续休息 4h 有 11 天未满足 |
| D003 | 829.60 | 0 | 合法但收益过低，空驶限额已压住 |
| D004 | 15,023.86 | 0 | 暂无罚分 |
| D005 | 17,051.64 | 0 | 暂无罚分 |
| D006 | 12,894.67 | 1,200 | 每日连续休息 5h 有 6 天未满足 |
| D007 | 19,720.82 | 0 | 暂无罚分 |
| D008 | 17,740.78 | 2,400 | 平日连续休息 4h 有 6 天未满足 |
| D009 | -2,989.33 | 13,500 | 每日 23 点前到家/夜间静止有 15 次违规，空驶成本也高 |
| D010 | 16,020.01 | 10,745 | 家事窗口缺席 1129 分钟、必访点只完成 4/5 天、休息违规 7 天 |

本轮检查已运行：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

结果：通过。

## 下一步优先级

### 高优先级：降罚分

1. D009 home-night 是最高优先级。先看 `actions_202603_D009_*.jsonl` 中 23:00 前没回家的那些天，确认是接了无法回家的单，还是熟货/空驶路径绕过了 `_evaluate_cargo()`。优先限制 `required_cargo` 强制接单路径和 speculative reposition。
2. D010 家事第二优先。当前 `sequence_ok=true`，说明配偶流程已达成，但 `minutes_not_home_in_window=1129`。需要检查 3 月 10 日 22:00 到 3 月 13 日 22:00 期间是否有查询、接单或空驶打断了在家等待。到家后应直接 wait 到 `stay_until_minute`，且不要在此期间再查询货源。
3. D010 必访点第三优先。只完成 4/5 天，需要更早、更主动地安排到访点，最好在月初低机会成本时做，而不是月底抢救。
4. 每日连续休息问题。D001/D002/D006/D008/D010 都有不同程度罚分。重点检查连续休息是否被查询耗时、短 wait、夜间回家动作切碎。更好的方案是在当天剩余时间不足前，直接生成完整 `wait(required_rest_minutes)` 或等到天末。
5. D009 净收入为负。除了罚分外，空驶成本很高。需要限制 home-night 司机的空驶半径，优先家附近短单，避免被 market heat 牵引到远端。
6. 与以上规则修复并行，把 Qwen3.5-Flash 纳入迭代链路。不要只跑纯确定性离线基线。

### 高优先级：Qwen3.5-Flash 模型集成

`llm_helper.py` 已写好三个模型接口方法，但尚未集成到 `planner.py` 决策流程中：

- `rank_cargos()`：对候选货源打分（0-100），返回 `{cargo_id: score}`。用于替代或增强 `_evaluate_cargo` 的确定性评分。
- `suggest_decision()`：从候选动作中选最优，返回索引。用于在 `take_order/wait/reposition` 候选间做最终选择。
- `preference_hints()`：偏好结构化提示（已有，仅日志记录未实际使用）。

集成方案：

1. 在 `decide()` 中，当 `self._qwen.enabled` 为 True 时，调用 `rank_cargos` 获取模型评分。
2. 将模型评分与确定性评分加权融合：`final_score = alpha * model_score + (1-alpha) * det_score`。初始 alpha=0.4。
3. 在候选动作选择时，调用 `suggest_decision` 做最终决策，但仅当模型返回的索引对应候选的确定性分数 > 某阈值时才采纳（防止模型选低分候选）。
4. 保持 fallback：模型调用失败时完全回退到确定性逻辑。
5. 需要真实 `DASHSCOPE_API_KEY` 才能调用模型。运行前设置环境变量。

### 低优先级

6. D003 收益过低（净收入仅 830）。空驶限额限制了接单能力，但可能需要在限额内更精准选择高价值订单。
7. market_heat 跨步记忆：当前 `market_heat` 只在当前决策步内累积，`build_memory()` 不恢复。需要在 `DeterministicPlanner` 内增加实例级缓存。

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
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

短测：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

检查结果：

```powershell
Get-Content D:\竞赛\demo\results\history\20260528_210701\monthly_income_202603.json -Raw
```

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
