# Agent 实现说明

最后更新：2026-06-06

## 当前架构

- `model_decision_service.py`：入口、动作归一化、异常兜底、进度显示。
- `planner.py`：Risk-Gated MPC 确定性滚动规划，负责紧急约束、查询货源、候选评分、等待和空驶。
- `preference_rules.py`：把运行时 `preferences` 文本解析为 `PreferencePolicy`。
- `state_tracker.py`：从 `query_decision_history` 重建累计接单数、休息段、空驶里程、到访天数、连续工作时长等。
- `geo.py`：时间和地理计算工具。
- `llm_helper.py`：Qwen 偏好结构化 + 领域特化约束验证接口（v2：从候选选择改为约束验证）。

## 算法策略：Risk-Gated MPC + Qwen 约束验证

### 核心思路

1. 本地 Planner 先做硬约束可行性检查：家事、home-night、连续休息、熟货、必访点、禁入区。
2. 对剩余候选做短视滚动评分。
3. 确定性选择产生后，Qwen 以**约束验证器**角色检查是否违反 home_night/rest/family 硬约束。
4. Qwen 返回 `{safe, risk, concern, suggestion}` — 本地代码决定是否调整。
5. **Qwen 不再在候选中选择**（v1 的 suggest_decision 已被 replace）。

### Qwen v2 集成（2026-06-06，待测试）

- **模型切换**：`qwen3.5-flash` → `qwen-plus`（非推理模型，消除 ~5000 reasoning token/call）
- **可配置**：`AGENT_QWEN_MODEL`（默认 `qwen-plus`），`AGENT_QWEN_MAX_REVIEWS`（默认 `10`）
- **三种领域特化验证 prompt**：
  - `home_night`：检查 23:00 前能否到家（16:00+ 触发）
  - `rest`：检查接单后剩余时间能否完成连续休息
  - `family`：检查操作是否与家事窗口冲突（48h 前瞻）
- `preference_hints()`：偏好结构化，每 driver 一次并缓存。**保持不变**。
- `rank_cargos()`：**已禁用**（代码保留）。
- `suggest_decision()`：**已替换**为 `verify_constraints()`。
- 安全降级：模型调用失败时完全回退确定性逻辑。

### 跨天休息追踪修复（2026-06-06）

- `state_tracker.py`：`longest_rest_for_day()` 现在跨午夜合并连续的 wait 区间
- `planner.py`：移除休息等待的 `day_end` 上限；新增清晨（06:00 前）休息续接检查
- 预期 D002（8 次违规）和 D008（7 次违规）罚分大幅下降

## 评测结果

### 确定性基线（无 Qwen，2026-06-05）

| 司机 | 净收入 | 罚分 | 主要约束 |
|------|------:|-----:|---------|
| D001 | 11,039.21 | 300 | 每日连续休息 8h（深圳范围） |
| D002 | 18,909.98 | 1,600 | 每日连续休息 4h + 无成交日 |
| D003 | 829.60 | 0 | 月度空驶 ≤100km |
| D004 | 15,023.86 | 0 | 首单 ≤12:00 + 每日 ≤3 单 |
| D005 | 17,051.64 | 0 | 装卸距离 ≤100km |
| D006 | 19,079.24 | 200 | 每日连续休息 5h + 完全不出车日 |
| D007 | 19,720.82 | 0 | 23-04 不接单 + 无成交日 |
| D008 | 21,235.62 | 2,800 | 平日连续休息 4h + 完全不出车日 |
| D009 | 10,526.21 | 900 | 每日 23:00 前到家 + 熟货 240646 |
| D010 | 19,353.10 | 6,270 | 家事 3/10-3/13 + 每日休息 3h + 必访点 |

**总计：净收入 152,769.28，罚分 12,070，失败司机 0，token 0**

### Qwen 模式结果（AGENT_QWEN_MAX_REVIEWS=10，2026-06-06）

| 指标 | 确定性基线 | Qwen 模式 | 变化 |
|------|----------:|----------:|-----:|
| 净收入 | 152,769 | 142,895 | -9,874 |
| 罚分 | 12,070 | 12,070 | 0 |
| Token | 0 | 39,241 | +39,241 |
| 耗时 | 143s | 371s | +228s |

### 已知限制

1. **D010 家事迟到**（3,570 罚分）：家事偏好在 3/10 10:00 才可见，但司机在之前已接长单，无法提前规避。这是偏好可见性窗口的固有限制。
2. **D002/D008 休息违规**：仍有 8/7 天未满足连续休息。休息被跨日长单切碎，需要更智能的跨日休息预测。
3. **Qwen 不适合当前场景**：suggest_decision 无差异化，rank_cargos token 过高。需要领域特化 prompt 或非推理模型。
4. **market_heat 跨步记忆**：当前只在当前决策步内累积，不做跨步持久化。

## 启用 Qwen

```powershell
$env:DASHSCOPE_API_KEY = "your-api-key"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
$env:AGENT_QWEN_MAX_REVIEWS = "10"

cd demo/server
python main.py
```

注意：当前 Qwen 模式会降低净收入。仅用于测试和研究，不建议用于正式评测。

## 禁止事项

- 不读取 `demo/server/data/cargo_dataset.jsonl`。
- 不读取 `demo/server/data/drivers.json`。
- 不按 `driver_id` 写死策略。
- 不提交真实 API key。
