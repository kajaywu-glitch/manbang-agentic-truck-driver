# Agent 实现说明

本目录是当前比赛项目的主要修改面。官方入口仍是 `ModelDecisionService.decide(driver_id)`，外部评测进程会注入 `SimulationApiPort`。

## 当前架构

- `model_decision_service.py`：入口、动作归一化、异常兜底。
- `planner.py`：确定性滚动规划，负责紧急约束、查询货源、候选评分、等待和空驶。
- `preference_rules.py`：把运行时 `preferences` 文本解析为 `PreferencePolicy`。
- `state_tracker.py`：从 `query_decision_history` 重建累计接单数、休息段、空驶里程、到访天数等。
- `geo.py`：时间和地理计算工具。
- `llm_helper.py`：可选 Qwen3.5-Flash 偏好结构化接口。

## 基座模型

复赛建议基座模型已锁定为 `qwen3.5-flash`。当前策略默认不依赖模型，保证没有真实 API key 时也能完成本地仿真。用户已明确要求：下一轮不能只满足于离线确定性策略，必须把 `qwen3.5-flash` 作为真实参与迭代的基座模型层加入。

启用模型时只通过官方端口：

```python
SimulationApiPort.model_chat_completion(payload)
```

启用方式：

```powershell
$env:DASHSCOPE_API_KEY = "你的新APIKey"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
```

模型只允许做偏好结构化或少量候选裁决提示，最终动作仍必须由本地代码校验。

## 下一轮 Qwen3.5-Flash 迭代要求

今天尚未开始实现，下一位模型接手后应优先补上：

- `preference_hints` 不能只打日志，应把 Qwen 输出保守合并到 `PreferencePolicy`。
- 增加候选复审接口，让 Qwen 在本地已生成、已校验的候选动作中选择 index。
- 模型不能直接输出任意 `take_order/wait/reposition`，不能绕过本地合法性检查。
- Qwen 主要处理偏好文本歧义和高风险候选判断，例如 home-night、家事、每日连续休息、指定熟货、必访点。
- 设置 `AGENT_ENABLE_QWEN35_FLASH=1` 后，短测日志应能确认模型实际被调用。
- 需要增加 `AGENT_QWEN_MAX_REVIEWS` 或类似机制控制调用次数，避免 token 和运行时间失控。
- 完整 31 天评测必须同时记录确定性基线和启用 Qwen 后的结果差异。

## 当前策略流程

1. `get_driver_status(driver_id)` 获取位置、时间、偏好。
2. `query_decision_history(driver_id, -1)` 重建运行时状态。
3. 解析偏好文本，得到休息、禁行、距离、家事、熟货、必访点等规则。
4. 先执行紧急约束：家事、熟货定位、每日回家、禁行窗口、整天休息/不接单、必访点、临近休息。
5. 若无紧急动作，调用 `query_cargo` 查询当前位置候选货源。
6. 评估接单候选：装货窗、车型、成本、收益、偏好风险、目的地机会价值。
7. 同时生成等待候选和保守空驶候选。
8. 选择最高分合法动作；异常时由入口兜底为 `wait(60)`。

## 当前完整评测

最近完整 31 天结果在本地忽略目录：

```text
D:\竞赛\demo\results\history\20260528_210701\
```

摘要：

- `failed_driver_count = 0`
- 无 `validation_error`
- `total_token_usage = 0`
- `total_net_income_all_drivers = 122844.69`
- `total_preference_penalty = 30945`

罚分集中点：

- D009：每日 23 点前到家/夜间静止，15 次违规，罚分 13,500，且净收入为负。
- D010：家事窗口缺席 1129 分钟，必访点 4/5 天，每日休息 7 次违规。
- D002/D008/D006/D001：连续休息仍有罚分。

## 下一步修改入口

- D009：优先检查 `planner._best_cargo_plan()` 中指定熟货的高优先级路径。若 `_evaluate_cargo()` 因 home-night 或其他硬约束返回 `None`，当前逻辑仍可能强制接单，应改为只在真正可行时接单，或只对明确非硬约束原因做例外。
- D010：检查 `planner._family_action()` 和 3 月 10 日到 3 月 13 日动作日志。到家后应不再查询货源，直接 wait 到 `stay_until_minute`。
- 必访点：`planner._urgent_action()` 中 required visit 逻辑需要更早安排，不能只在剩余天数紧张时抢救。
- 连续休息：把休息判断尽量前移到查询货源之前，避免 `query_cargo` 消耗时间切碎休息窗口。
- 空驶：`state_tracker.market_heat` 目前不是跨步持久记忆。若继续使用市场热度，需要在 Planner 实例中增加安全缓存，并控制 home-night 司机的远距离空驶。

## 验证命令

编译：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

短测：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

完整 31 天仿真需要本地 `demo/server/config/config.json` 中：

```json
"simulation_duration_days": 31
```

完整仿真：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py
```

收益计算：

```powershell
cd D:\竞赛\demo
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

## 禁止事项

- 不要读取 `demo/server/data/cargo_dataset.jsonl`。
- 不要读取 `demo/server/data/drivers.json`。
- 不要按 `driver_id` 写死策略。
- 不要提交真实 API key、`demo/server/config/config.json`、`demo/results/`。
