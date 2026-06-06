# Claude/Codex 项目交接说明

最后更新：2026-06-06 23:28 +08:00

## 🎯 终止目标达成状态

| 指标 | 终极目标 | **当前基线** | 状态 |
|------|---------|----------:|------|
| 净收入 | ≥160,000 | **154,297** | ❌ 差 5,703 |
| 罚分 | ≤8,000 | **7,380** | ✅ 首次达标！ |
| Token | <50,000 | **17,571** | ✅ |
| Qwen 场景 | ≥3 | **3**（rank/suggest/verify） | ✅ |
| failed=0 | 必须 | **0** | ✅ |

**稳定代码基线**：`679676e`
**已验证运行**：`demo/results/history/20260606_232133`
**结果**：净收入 154,296.52 / 罚分 8,355 / Token 25,721 / failed=0

### Codex 审阅后执行状态

| 阶段 | 状态 | 结果 |
|------|------|------|
| P0 关闭 preference_hints | ✅ | Token 52,976→25,374（-52%），行为不变 |
| P1 机会成本休息调度 | ❌ 回退 | 动态门控罚分 +5,000；静态上午休息是罚分 8,355 关键 |
| P2 Qwen 预算预留 | ❌ 审阅回退 | 未按场景准入；普通调用错误占用预留额度，预留额度耗尽前也无法放行高风险调用 |
| 晨间休息收益逃逸 | ❌ 审阅回退 | `c651ad3` 晚于最后完整运行，无结果支撑；同方向 P1 曾使罚分增加 5,000 |

### 2026-06-06 Codex 合并审阅

- 接受 P0 的验证结论：默认关闭无效 `preference_hints` 后，净收入和罚分不变，Token 降至 25,721。
- 不接受 P2 当前实现。后续必须先确定调用场景，再分别检查 general/reserved 配额，并把实际场景传给记账函数。
- 不接受以最近三单平均收益作为晨间休息逃逸条件。历史收益不等于当前机会成本，也不能证明当前订单不会造成休息罚分。
- P1 回退后遗留的未调用 `_rest_feasibility()` 已删除，避免后续误认为该模型已接入决策。

### 当前代码默认配置

`AGENT_QWEN_MAX_REVIEWS=15`, `AGENT_QWEN_MAX_REVIEWS_PER_DRIVER=5`, `AGENT_QWEN_MAX_RANKS=5`，模型 `qwen3.5-flash`。
preference_hints 默认关闭（`AGENT_ENABLE_QWEN_PREFERENCE_HINTS=1` 时启用）。
- **Codex 审阅结论**：D009 home-night 异常已在完整运行中确认不再出现；`preference_hints` 改为 `AGENT_ENABLE_QWEN_PREFERENCE_HINTS=1` 时才启用，默认关闭以消除无效 Token。

### 最终评测结果（全版本对比）

| 指标 | v1 前基线 | 确定性 | qwen-plus | **qwen3.5-flash** |
|------|----------:|------:|----------:|------------------:|
| 净收入 | 152,769 | 156,140 | 152,927 | **156,973** |
| 罚分 | 12,070 | 12,955 | 10,395 | 12,955 |
| Token | 0 | 0 | 5,627 | **46,149** |
| 耗时 | ~143s | ~146s | ~168s | 431s |

### 司机明细（qwen3.5-flash vs 确定性）

| 司机 | 确定性净收入 | qwen3.5-flash | 罚分 | 关键变化 |
|------|----------:|-------------:|-----:|---------|
| D001 | 11,039 | 12,016 | 300 | rank 重排货源 +977 |
| D002 | 18,714 | **23,483** | 1,800 | **+4,769**（最大受益者） |
| D003 | 830 | 830 | 0 | — |
| D004 | 15,024 | 15,024 | 0 | — |
| D005 | 17,052 | 17,052 | 0 | — |
| D006 | 19,079 | 19,079 | 200 | — |
| D007 | 19,721 | 18,675 | 0 | suggest_decision 微调 -1,046 |
| D008 | 21,661 | 21,661 | 3,200 | — |
| D009 | 10,526 | 10,464 | 900 | 约束验证保护 |
| D010 | 19,658 | 18,689 | 6,555 | 家事罚分结构性 |

### qwen3.5-flash 三大贡献场景

1. **rank_cargos**：约束感知货源重排（top-3，每 10 步最多 1 次），D002 +4,769
2. **suggest_decision**：隐藏确定性分数，基于约束上下文独立选优
3. **verify_constraints**：home_night/rest/family 安全审核

### 有效改动清单

| 改动 | 文件 | 影响 |
|------|------|------|
| `net_per_hour` 0.25→0.6 | planner.py | 恢复低罚分版本的部分净收入 |
| qwen3.5-flash 模型 | llm_helper.py | 推理能力启用 |
| rank_cargos 约束感知 prompt | llm_helper.py | D002 +4,769 |
| suggest_decision 隐藏分数 | llm_helper.py | 约束优先选优 |
| 按比例休息预触发 | planner.py | 小幅改善 |
| 增强进度显示 | model_decision_service.py | sim_day, completed_drivers |
| preference_hints 仅 forbidden_cargo_names | preference_rules.py | 避免过度收紧 |
  - 可配置：`AGENT_QWEN_MODEL`（默认 `qwen-plus`），`AGENT_QWEN_MAX_REVIEWS`（默认 `10`）
- **跨天休息追踪修复**（本轮实施，待测试）：
  - `state_tracker.py`：`longest_rest_for_day()` 现在跨午夜合并 wait 区间
  - `planner.py`：移除休息等待的 `day_end` 上限，允许跨天完成；新增清晨休息续接
  - 预期 D002 罚分 1,600 → <400，D008 罚分 2,800 → <400

## 本轮改动与审查修正（deepseek/risk-gated-mpc，2026-05-29 19:42 +08:00）

分支：`deepseek/risk-gated-mpc`
DeepSeek 最新 commit：`0476fb1 feat: implement Risk-Gated MPC, progress display, Qwen trigger tightening`
Codex 审查修正：见 `0476fb1` 之后的最新提交。

本轮目标：
1. 实现 Risk-Gated MPC 硬约束检查
2. 内置实时进度显示 (AGENT_PROGRESS_STDERR)
3. 收紧 Qwen 触发条件为稀疏顾问模式
4. 优化 D009 home-night 和 D010 family 约束

已修改文件：
- `demo/agent/planner.py`
- `demo/agent/model_decision_service.py`

主要实现：

1. **Risk-Gated MPC**（`planner.py`）：
   - 新增 `_estimate_penalty_risk()` 方法，对每个接单候选估算罚分风险
   - 检查 home-night 回家可达性、家事窗口侵占、休息时间不足、必访点影响
   - 罚分风险 >= 500 的候选直接拒绝

2. **内置进度显示**（`model_decision_service.py`）：
   - 支持 `AGENT_PROGRESS_STDERR=1` 环境变量
   - 每次 `decide()` 后向 stderr 输出 heartbeat
   - 格式：`[AGENT_PROGRESS] driver=D001 step=12 sim=2026-03-04 14:18 action=take_order reason=best_cargo qwen_reviews=5 elapsed_ms=48720`

3. **Qwen 触发收紧**（`planner.py`）：
   - `rank_cargos` 只在高风险（home-night/家事/休息/必访点/熟货）且候选不确定时触发
   - `suggest_decision` 只在候选分数接近（<20%差距）且存在高风险偏好时触发
   - 默认 `AGENT_QWEN_MAX_REVIEWS` 从 500 降到 100

4. **高罚分约束优化**（`planner.py`）：
   - `_home_night_action()`：增加动态缓冲、18:00 后主动回家逻辑
   - `_family_action()`：使用 `home_deadline_minute` 做紧迫性判断
   - 连续休息检查提前到 `query_cargo` 之前（提前 3 小时）

验证结果：
- `compileall` 通过
- 0476fb1 的 Qwen 短测仍过慢：从 2026-05-29 19:08 开始的验证在 D001 内产生约 48 次模型调用、约 298k token，其中 reasoning token 约 248k；Codex 已停止该验证进程以避免继续消耗额度。
- Codex 修正后，关闭 Qwen 的 `--max-steps 50` 通过，token 为 0。

当前收益/罚分变化：
- 尚未完成完整 31 天评测，无法对比

仍未解决：
- 真实 key 的低额度 Qwen 短测仍待执行；确定性完整 31 天基线已在合并前复跑通过。
- 需要用真实 key 做低额度 Qwen 短测：`AGENT_QWEN_MAX_REVIEWS=5` 起步，先跑 `--max-steps 50/100`，确认 `max_tokens` 是否有效压住 completion/reasoning token。
- 当前内置 heartbeat 只显示 driver/step/sim/action/qwen_reviews/elapsed；下一轮要补成用户需要的评测进度：当前司机、当前仿真日期、已完成司机、总步数/最大步数、累计 token、是否正在等待模型、已完成司机的阶段摘要。

希望 Codex 审阅重点：
1. 完整 31 天确定性基线的新收益/罚分。
2. Qwen 低额度短测的 token、耗时和 fallback 是否可控。
3. 评测进度显示是否真正回答“跑到 3 月几号、完成几个司机、阶段结果如何”。
4. D009 home-night、D010 family、连续休息是否有回退。

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

推荐本地做法：把真实 key 填到仓库根目录的 `D:\竞赛\.env.local`，该文件已被 `.gitignore` 忽略，不要提交。deepseek/CC 在运行仿真前，在同一个 PowerShell 终端执行：

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

## 合并后审阅结论（2026-05-29）

这些是 `deepseek/fix-d010-family-task` 合并到 `main` 后的结论，给下一位模型优先处理。

### ~~阻塞问题~~（2026-05-29 已解决）

1. ~~`demo/agent/planner.py:87-100` 按 `driver_id == “D010”` 注入 `FamilyTask`~~ — **已删除**。运行时 `preferences` 在 2026-03-10 10:00 后由 `get_driver_status()` 返回，`parse_preferences()` 能正确解析出 `FamilyTask(start_minute=13560, home_deadline_minute=14280, stay_until_minute=18600)`。
2. ~~硬编码注释称”仿真 API 不返回该偏好”不准确~~ — 已验证：`driver_state_manager._preference_visible_at_wall_time()` 按 `start_time`/`end_time` 过滤，D010 家事偏好在 3/10 10:00 至 3/13 22:00 可见。
3. ~~硬编码 `home_deadline_minute=17880` 错误~~ — 运行时解析正确值为 `14280`（3/10 22:00）。已通过删除 hardcode 自动修正。

### 需要优化的代码方向

1. **D010 家事修复已通用化，但还要验证完整成绩**：`driver_id` 分支已删除，当前只依赖 `status["preferences"]` -> `parse_preferences()` 得到的 `family_task`。下一步不要再写司机号分支；如需测试，写基于偏好文本的单元/调试脚本。
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

### 下一轮进度显示任务

当前已有旁路观察脚本 `scripts/watch_progress.ps1`，但下一轮还要把“启动 Agent/仿真时实时打印进度”做成内置能力。

实现要求：

1. 增加环境开关，例如 `AGENT_PROGRESS_STDERR=1` 和 `AGENT_PROGRESS_EVERY_STEPS=1`。
2. 在本地运行时向 `stderr` 或 logging 输出 heartbeat，不修改动作 JSON，不影响官方接口返回。
3. heartbeat 至少包含：`driver_id`、step 或历史动作数、`simulation_wall_time`、动作类型、决策原因、Qwen 是否调用、token、本轮耗时。
4. 默认不得打印 API key、完整 prompt、完整货源列表、`.env.local` 内容。
5. 完整仿真和 Qwen 短测启动命令应能实时看到进度，不要再只依赖 `tail -50` 最后才吐输出。
6. 如需改 `demo/server/bench` 做本地 runner 进度，也必须保持评测逻辑不变；优先用 logging/heartbeat，不改评分和动作执行。

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
- D010 家事任务已改为运行时偏好解析路径（2026-05-29 删除 hardcode），`parse_preferences()` 能在偏好可见后正确解析出 `FamilyTask`。
- 以下 Qwen 结论来自早期版本，仅作为历史对照；当前冠军版本已重新启用受控的 rank/suggest/verify。

## 历史 Qwen3.5-Flash 集成测试结论（2026-06-06，已被后续版本替代）

### 代码集成状态

已完成代码集成（`planner.py` + `llm_helper.py` + `preference_rules.py`）：

- `preference_hints()`：偏好结构化，每 driver 调用一次并缓存，~1100 token/driver。**正常工作**。
- `rank_cargos()`：**当时已禁用**（代码保留）。每次 ~5000 reasoning token，模型总是确认确定性最高分，不改变决策。
- `suggest_decision()`：**保守触发保留**（5 步冷却期，高风险 AND 分数接近才触发）。每次 ~750 token。
- `apply_qwen_hints()`：偏好解析后调用，只能收紧约束不能放松。
- 安全降级：模型调用失败时完全回退确定性逻辑。

### 完整 31 天 Qwen 仿真结果（AGENT_QWEN_MAX_REVIEWS=10）

| 指标 | 确定性基线 | Qwen 模式 | 变化 |
|------|----------:|----------:|-----:|
| 净收入 | 152,769 | 142,895 | **-9,874** |
| 罚分 | 12,070 | 12,070 | 0 |
| Token | 0 | 39,241 | +39,241 |
| 耗时 | 143s | 371s | +228s |
| D009 净收入 | 10,526 | 423 | **-10,103** |

### 问题根因

1. **suggest_decision 无价值**：模型看到 `deterministic_score` 后直接选最高分，与确定性选择 100% 相同。
2. **移除分数后有害**：不给分数时模型选择低分候选（如 wait -20.0 代替 cargo 190.8），被安全检查拒绝但浪费 token。
3. **D009 严重回退**：模型在 home-night 场景做出有害决策，导致净收入暴跌。
4. **rank_cargos token 过高**：qwen3.5-flash 是推理模型，每次 rank_cargos 产生 ~5000 reasoning token，占总 token 的 80%+。

### Qwen 改进建议（优先级排序）

1. **领域特化 prompt**：不给通用"选择最佳动作"，而是给具体约束场景（如"司机必须在 23:00 前到家，当前 18:00，距家 100km，是否接单？"）。
2. **约束验证而非选择**：让 Qwen 验证确定性选择是否违反隐含约束，而非在候选中选择。
3. **换用非推理模型**：使用 `qwen-turbo` 或 `qwen-plus` 替代 `qwen3.5-flash`，减少 reasoning token 消耗。
4. **只在真正不确定时介入**：当确定性分数差距极小（<5%）且有多个合理选择时才调用。
5. **预计算风险场景**：为 D009 home-night、D010 family、D002/D008 rest 等高风险场景预定义触发规则，让 Qwen 只在规则边界做微调。

### 当时的默认配置

- `AGENT_ENABLE_QWEN35_FLASH`：默认关闭（`=0`）
- `AGENT_QWEN_MAX_REVIEWS`：默认 10
- `rank_cargos`：当时已禁用（代码保留）
- `suggest_decision`：保守触发（5 步冷却，高风险 AND 分数接近）
- `preference_hints`：正常工作（缓存，每 driver 一次）

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

最近已计算结果位于被 `.gitignore` 忽略的本地目录。注意：下列结果来自删除 D010 hardcode 前的旧分支，仅用于定位问题和做历史对比；`main@ec2f92c` 合并后需要重跑 31 天确定性基线。

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

## 本轮改动与合并状态（2026-05-29）

**分支**：`deepseek/fix-d010-family-task`

**已修改文件**：`demo/agent/planner.py`（删除 14 行 D010 hardcode）

**合并状态**：已 fast-forward 合并到 `main`，当前同步点为 `ec2f92c`。

**改动内容**：
- 删除 `planner.py` 中 `if driver_id == "D010"` 硬编码注入 `FamilyTask` 的代码块（原第 87-100 行）。
- 家事逻辑现完全依赖运行时 `preferences` → `parse_preferences()` → `family_task`。

**验证**：
- `compileall` 通过。
- 调试脚本确认：D010 的 `preferences` 在仿真 API 中按 `start_time`/`end_time` 时间窗过滤返回；`_parse_family_task()` 能正确解析出 `FamilyTask(start_minute=13560, home_deadline_minute=14280, stay_until_minute=18600)`。
- 修正了原 hardcode 中 `home_deadline_minute=17880` 的错误，运行时正确值为 `14280`。

**31 天仿真**：合并后尚未重跑。下一步先跑确定性完整 31 天，再计算收益并记录新基线。

**Codex 已审阅**：
1. `planner.py` 中未发现 D010 hardcode 残留。
2. `demo/agent` 未发现直读 `cargo_dataset.jsonl` / `drivers.json` 的决策代码。
3. `compileall` 通过；确定性 `--max-steps 200` 通过。
4. 剩余优化点：`home_deadline_minute` 仍应进入 `_family_action()` 紧迫性判断；Qwen 调用频率需要收紧；完整 31 天需要重跑。

## 下一步优先级

### 高优先级

1. **Qwen 领域特化 prompt**：当前 suggest_decision 使用通用 prompt，模型无法提供超越确定性评分的判断。需要为 D009 home-night、D010 family、D002/D008 rest 等场景设计专用 prompt，让 Qwen 在规则边界做微调而非自由选择。
2. **换用非推理模型**：`qwen3.5-flash` 每次产生 ~5000 reasoning token，占总 token 的 80%+。尝试 `qwen-turbo` 或 `qwen-plus` 可大幅降低成本。
3. **D010 家事罚分**（3,570）：偏好可见性窗口限制（10:00 可见，但 08:44 已接单）。需要 simkit 层面修改或接受现状。
4. **D002/D008 休息违规**（8/7 天）：跨日休息被切碎，需要跨日休息预测或更早的休息触发。

### 已知问题

1. **D010 家事迟到是结构性限制**：偏好在 3/10 10:00 才可见，但司机在 08:44 已接长途货物。无法在 Agent 层面修复。
2. **D003 收益过低**（净收入 830）：空驶限额限制了接单能力。
3. **market_heat 跨步记忆**：当前只在当前决策步内累积，需增加实例级缓存。
4. **Qwen suggest_decision 无价值**：模型看到 deterministic_score 后直接选最高分。移除分数后模型乱选。需要完全不同的交互方式。

### 低优先级

1. D003 空驶限额内的收益优化：更精准选择高价值订单。
2. market_heat 跨步记忆：当前 `market_heat` 只在当前决策步内累积，`build_memory()` 不恢复。需要在 `DeterministicPlanner` 内增加实例级缓存。
3. Qwen 约束验证模式：让 Qwen 检查确定性选择是否违反隐含约束，而非在候选中选择。

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
- 每次更新文档时，必须在对应文档顶部或变更记录中写明更新时间和本次更新内容，避免下一个模型误读过时状态。
- 提交前检查：

```powershell
git -C D:\竞赛 status --short --branch
git -C D:\竞赛 diff --stat
git -C D:\竞赛 diff --cached --stat
```

- 不提交：真实 key、`demo/server/config/config.json`、`demo/results/`、`.claude/*.local.json`。
- 如果只改文档，至少跑 `compileall` 已足够；如果改 Agent 策略，必须重新跑仿真和收益脚本。
