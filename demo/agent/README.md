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

复赛建议基座模型已锁定为 `qwen3.5-flash`。当前策略默认不依赖模型，保证没有真实 API key 时也能完成本地仿真；设置 `AGENT_ENABLE_QWEN35_FLASH=1` 后，Qwen3.5-Flash 已可参与偏好结构化、货源评分和候选动作复审。

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

模型只允许做偏好结构化、货源评分或少量候选裁决提示，最终动作仍必须由本地代码校验。

## Qwen3.5-Flash 集成状态

当前分支已完成主流程接入：

- `preference_hints()` 会请求结构化偏好，`apply_qwen_hints()` 只允许新增或收紧规则。
- `rank_cargos()` 可对候选货源给模型评分，并与确定性分数融合。
- `suggest_decision()` 可在高风险或分数接近时从本地候选动作中选择 index。
- `AGENT_QWEN_MAX_REVIEWS` 控制模型调用次数，默认值见 `planner.py`。
- 模型失败、超时、坏 JSON 或越界 index 时，必须回退确定性策略。

尚未完成：

- 需要用真实 API key 跑 `AGENT_ENABLE_QWEN35_FLASH=1` 的短测。
- 需要比较“不开模型”和“启用 Qwen”的完整 31 天结果、token 与耗时。

## Hybrid Agent 启动标准

不要无限继续纯确定性调参。满足以下条件后，就可以开始 Hybrid Agent 构建：

- 31 天完整仿真可跑完。
- 无 `validation_error`。
- 无仿真崩溃。
- 动作日志非空。
- 无 key 时确定性策略可独立运行。
- 主要罚分来源可解释。
- 本地候选生成和 fallback 已稳定。

无需等待所有罚分清零。D009/D010 仍有罚分也可以开始 Hybrid，因为 Qwen3.5-Flash 正应该参与这类高风险偏好理解和候选复审。

Hybrid 第一版只做受控接入：

1. Qwen 结构化偏好，结果只允许收紧规则。
2. 确定性 Planner 生成本地合法候选。
3. Qwen 只在高风险或分数接近时从候选 index 中选择。
4. 本地代码复核模型选择，失败则回退确定性首选。
5. 先短测，再完整 31 天，和不开 Qwen 的基线对比。

禁止事项：

- 不让 Qwen 直接输出最终动作。
- 不把全量货源或原始数据文件交给模型。
- 不因为接入 Qwen 破坏无 key fallback。
- 不把 Qwen 调用扩展到每一步、每条货源，除非 token 和耗时已被证明可控。

## 当前策略流程

1. `get_driver_status(driver_id)` 获取位置、时间、偏好。
2. `query_decision_history(driver_id, -1)` 重建运行时状态。
3. 解析偏好文本，得到休息、禁行、距离、家事、熟货、必访点等规则；启用 Qwen 时再保守合并模型 hints。
4. 先执行紧急约束：家事、熟货定位、每日回家、禁行窗口、整天休息/不接单、必访点、临近休息。
5. 若无紧急动作，调用 `query_cargo` 查询当前位置候选货源。
6. 评估接单候选：装货窗、车型、成本、收益、偏好风险、目的地机会价值；启用 Qwen 时可融合模型货源评分。
7. 同时生成等待候选和保守空驶候选。
8. 选择最高分合法动作；启用 Qwen 且命中复审条件时让模型在本地候选中选 index；异常时由入口兜底为 `wait(60)`。

## 当前完整评测

最近已计算的 31 天结果来自当前分支的无模型运行，结果文件在本地忽略目录：

```text
D:\竞赛\demo\results\
```

摘要：

- `failed_driver_count = 0`
- 无 `validation_error`
- `total_token_usage = 0`
- `total_net_income_all_drivers = 138156.88`
- `total_preference_penalty = 25445`

罚分集中点：

- D009：每日 23 点前到家/夜间静止，9 次违规，罚分 8,100，净收入已转正。
- D010：家事窗口缺席 1129 分钟，必访点 4/5 天，每日休息 7 次违规。
- D001/D002/D006/D008：连续休息仍有罚分；D008 另有 1 次食品饮料软偏好罚分。

## 下一步修改入口

- D009：home-night 已从 15 次降到 9 次，但仍需继续定位未回家日期，避免远距离接单/空驶导致 23 点前无法回家。
- D010：检查 `planner._family_action()` 和 3 月 10 日到 3 月 13 日动作日志。到家后应不再查询货源，直接 wait 到 `stay_until_minute`。
- 必访点：`planner._urgent_action()` 中 required visit 逻辑需要更早安排，不能只在剩余天数紧张时抢救。
- 连续休息：把休息判断尽量前移到查询货源之前，避免 `query_cargo` 消耗时间切碎休息窗口。
- Qwen 验证：用真实 key 跑短测和完整评测，记录 token、耗时、收益、罚分与无模型基线差异。
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
$env:PYTHONIOENCODING = "utf-8"
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

## 禁止事项

- 不要读取 `demo/server/data/cargo_dataset.jsonl`。
- 不要读取 `demo/server/data/drivers.json`。
- 不要按 `driver_id` 写死策略。
- 不要提交真实 API key、`demo/server/config/config.json`、`demo/results/`。
