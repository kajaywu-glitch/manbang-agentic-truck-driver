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
cd D:\竞赛
.\scripts\load_local_env.ps1
```

真实 key 填在 `D:\竞赛\.env.local`。该文件已被 `.gitignore` 忽略，不能提交；加载脚本不会打印 key，只会提示 `key_present=True/False`。必须在同一个 PowerShell 终端里加载后再运行仿真。

模型只允许做偏好结构化、货源评分或少量候选裁决提示，最终动作仍必须由本地代码校验。

## Qwen3.5-Flash 集成状态

当前分支已完成主流程接入：

- `preference_hints()` 会请求结构化偏好，`apply_qwen_hints()` 只允许新增或收紧规则。
- `rank_cargos()` 可对候选货源给模型评分，并与确定性分数融合。
- `suggest_decision()` 可在高风险或分数接近时从本地候选动作中选择 index。
- `AGENT_QWEN_MAX_REVIEWS` 控制模型调用次数，默认值见 `planner.py`。
- 模型失败、超时、坏 JSON 或越界 index 时，必须回退确定性策略。

尚未完成：

- 本机 `.env.local` 已由用户填写真实 key，格式脱敏检查通过；该文件被 Git 忽略，不能提交。
- 需要在删除 D010 hardcode 后，用真实 key 跑 `AGENT_ENABLE_QWEN35_FLASH=1` 的短测。
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

最近已计算的 31 天结果来自 `mimo/fix-d010-family-task` 的无模型运行，结果文件在本地忽略目录：

```text
D:\竞赛\demo\results\
```

摘要：

- `failed_driver_count = 0`
- 无 `validation_error`
- `total_token_usage = 0`
- `total_net_income_all_drivers = 115570.25`
- `total_preference_penalty = 16945`

注意：这个结果不能直接作为可合并成绩，因为当前分支为了修 D010 在 `planner.py` 中按 `driver_id == "D010"` 硬编码注入了 `FamilyTask`，违反赛题约束。下一步必须先删除 hardcode，改成完全基于运行时 `preferences` 的通用家事处理。

罚分集中点：

- D009：每日 23 点前到家 10 次违规，罚分 9,000，净收入仅 514。
- D010：家事罚分 1,645 + 休息 600，净收入 -6,567；当前低罚分来自违规 hardcode，需合规重做。
- D001/D002/D006/D008/D010：连续休息仍有罚分；D008 另有 1 次食品饮料软偏好罚分。

## 当前审阅阻塞点

- `planner.py` 不能保留 `if driver_id == "D010"` 这类策略分支。
- D010 家事偏好并非完全不可见：在 2026-03-10 10:00 后，`get_driver_status()` 会把该偏好放入 `preferences`，现有 `parse_preferences()` 能解析出 `FamilyTask`。
- 家事修复方向应是增强通用逻辑：偏好可见后立即执行接配偶、回家、等待到 `stay_until_minute`，并在 `_evaluate_cargo()` 中拒绝会覆盖已知家事窗口的订单。
- `_family_action()` 后续应使用 `home_deadline_minute` 判断 22:00 前进家门的风险，而不是只等到 `stay_until_minute`。
- 家事、home-night、连续休息都应尽量在 `query_cargo` 前返回动作，避免查询货源消耗仿真时间后再补救。

## 下一步修改入口

- D010：先删除 hardcode，保留并加强 `preference_rules._parse_family_task()` + `planner._family_action()` 的通用路径。重新验证 D010 家事 sequence、到家时长和收益。
- D009：继续定位未回家日期，避免远距离接单/空驶导致 23 点前无法回家；注意不要因为修 D010 让 D009 继续恶化。
- 必访点：`planner._urgent_action()` 中 required visit 逻辑需要更早安排，不能只在剩余天数紧张时抢救。
- 连续休息：把休息判断尽量前移到查询货源之前，避免 `query_cargo` 消耗时间切碎休息窗口。
- Qwen 验证：用真实 key 跑短测和完整评测，记录 token、耗时、收益、罚分与无模型基线差异。
- 空驶：`state_tracker.market_heat` 目前不是跨步持久记忆。若继续使用市场热度，需要在 Planner 实例中增加安全缓存，并控制 home-night 司机的远距离空驶。

## 下一轮算法策略

优先实现 **风险门控滚动规划（Risk-Gated MPC）+ 稀疏 Qwen 顾问**：

1. 本地 Planner 先做硬约束：家事、home-night、连续休息、熟货、必访点、禁入区。
2. 候选评分从单步收益升级为短视滚动后果：接单/等待/空驶后，是否还能满足回家、休息、家事、必访点。
3. 给每个候选算 `penalty_risk` 和 `preference_progress_bonus`。会破坏高罚分偏好的候选直接 invalid。
4. Qwen 只在 `risk_level` 高或 `score_gap` 很小时从本地候选 index 中选；普通接单不要频繁 `rank_cargos`。
5. 限制 Qwen 输入 top 3-5 候选、短 timeout、小 `max_tokens`，失败立即 fallback。

这比“每一步让 Qwen 给货源打分”更可能提高最终成绩，因为主要收益来自降低高罚分和避免模型超时。

## 内置进度显示计划

旁路脚本 `scripts/watch_progress.ps1` 已能看日志，但下一轮需要在 Agent/本地仿真启动时内置实时进度输出。

建议接口：

```powershell
$env:AGENT_PROGRESS_STDERR = "1"
$env:AGENT_PROGRESS_EVERY_STEPS = "1"
```

实现约束：

- 只输出到 `stderr` 或 logging，不改变 action JSON。
- 每次 `decide()` 后输出一行：driver、历史 step、仿真时间、action、reason、Qwen 调用类型、token、本轮耗时。
- 默认不输出密钥、prompt、完整货源列表。
- 适合放在 `model_decision_service.py` 外层或新增 `progress.py`，避免污染核心规划逻辑。
- 如果选择改 `demo/server/bench`，只能增加本地 runner heartbeat，不能改评分逻辑。

## 真实 Key 验证流程

必须先确保没有 `driver_id` hardcode，再验证 Qwen：

```powershell
cd D:\竞赛
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\load_local_env.ps1
$env:AGENT_QWEN_MAX_REVIEWS = "20"
```

短测：

```powershell
cd D:\竞赛\demo\server
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python main.py --max-steps 200
```

收益与 token 检查：

```powershell
cd D:\竞赛\demo
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python calc_monthly_income.py
```

短测通过标准：无崩溃、无 `validation_error`、日志有模型调用、`monthly_income_202603.json` 中 token 大于 0。不要直接跑完整 Qwen 31 天；先把上限提高到 `50` 做较长短测，并确认没有频繁 60 秒超时。完整评测前必须收紧 Qwen 触发条件，避免普通接单步骤频繁调用 `rank_cargos`。

进度观察：

```powershell
cd D:\竞赛
.\scripts\watch_progress.ps1
```

单次查看：

```powershell
.\scripts\watch_progress.ps1 -Once
```

跑完整仿真或 Qwen 短测时建议同时开这个脚本。它只读日志，不影响仿真。

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
