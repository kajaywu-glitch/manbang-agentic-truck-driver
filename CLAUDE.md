# Claude/Codex 项目交接说明

这份文档是给下一次接手的模型优先阅读的项目状态说明。目标是让新会话不用重新摸索环境、赛题约束和当前策略问题，就能直接继续修改 `demo/agent/`。

**重要：先读 `D:\竞赛\WORKFLOW_MIMO_CODEX.md` 了解协作工作流和分支规则，再读本文档。**

## 当前结论

- 仓库：`D:\竞赛`
- 远程：`https://github.com/kajaywu-glitch/manbang-agentic-truck-driver.git`
- 当前工作分支：`mimo/fix-home-night-rest-family`
- 当前 Agent 已能完整跑 31 天本地仿真，最新已计算结果无崩溃、无 `validation_error`、10 名司机都有动作日志。
- 当前策略仍不是最终版：最新无模型结果总净收入 `138,156.88`，总偏好罚分 `25,445`，最需要继续降罚分的是 D010、D009、D008、D002、D006、D001。
- 当前主路径是确定性滚动规划；`qwen3.5-flash` 已集成到 `planner.py` 主决策流程（rank_cargos、suggest_decision、apply_qwen_hints），但默认不启用（需设置 `AGENT_ENABLE_QWEN35_FLASH=1`）。
- 最新分支 `mimo/fix-home-night-rest-family` 已将 D009 home-night 罚分从 13,500 降到 8,100，总罚分从 30,945 降到 25,445；D010 家事/必访点仍是最大风险。

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
- [ ] 设置 `AGENT_ENABLE_QWEN35_FLASH=1` 后，日志能看到真实模型调用（需真实 API key 测试）。
- [ ] `monthly_income_202603.json` 中 token 用量不再全为 0（需真实 API key 测试）。
- [ ] 启用 Qwen 的完整 31 天结果要和确定性基线对比。

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
| completed_steps | 1975 |
| simulate_time_seconds | 476.78 |
| failed_driver_count | 0 |
| total_token_usage | 0 |
| total_net_income_all_drivers | 138,156.88 |
| total_preference_penalty | 25,445 |

司机结果：

| 司机 | 净收入 | 罚分 | 当前主要问题 |
| --- | ---: | ---: | --- |
| D001 | 10,941.82 | 1,200 | 每日连续休息 8h 有 4 天未满足 |
| D002 | 21,015.35 | 1,600 | 每日连续休息 4h 有 8 天未满足 |
| D003 | 829.60 | 0 | 合法但收益过低，空驶限额已压住 |
| D004 | 15,023.86 | 0 | 暂无罚分 |
| D005 | 17,051.64 | 0 | 暂无罚分 |
| D006 | 14,399.01 | 1,200 | 每日连续休息 5h 有 6 天未满足 |
| D007 | 19,720.82 | 0 | 暂无罚分 |
| D008 | 20,461.05 | 2,600 | 平日连续休息 4h 有 6 天未满足，另有 1 次食品饮料软偏好 |
| D009 | 2,693.72 | 8,100 | 每日 23 点前到家/夜间静止还有 9 次违规 |
| D010 | 16,020.01 | 10,745 | 家事窗口缺席 1129 分钟、必访点只完成 4/5 天、休息违规 7 天 |

本轮检查已运行：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

结果：通过。

## 下一步优先级

### 高优先级：降罚分

1. 用真实 API key 验证 Qwen3.5-Flash 路径。先跑 `--max-steps 200`，确认日志中有模型调用，再跑 31 天完整评测，对比无模型基线。
2. D010 家事仍是最高罚分风险。当前 `sequence_ok=true`，但 `minutes_not_home_in_window=1129`，需要检查 3 月 10 日 22:00 到 3 月 13 日 22:00 期间是否被查询、接单或空驶打断。
3. D010 必访点只完成 4/5 天，需要更早、更主动地安排到访点。
4. D009 home-night 已改善但仍有 9 次违规。继续定位 23:00 前没回家的日期，限制远距离订单/空驶。
5. 每日连续休息问题。D001/D002/D006/D008/D010 仍有罚分，重点检查连续休息是否被查询耗时、短 wait、夜间回家动作切碎。

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
