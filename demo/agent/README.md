# Agent 实现说明

最后更新：2026-06-04

## 当前架构

- `model_decision_service.py`：入口、动作归一化、异常兜底、进度显示。
- `planner.py`：Risk-Gated MPC 确定性滚动规划，负责紧急约束、查询货源、候选评分、等待和空驶。
- `preference_rules.py`：把运行时 `preferences` 文本解析为 `PreferencePolicy`。
- `state_tracker.py`：从 `query_decision_history` 重建累计接单数、休息段、空驶里程、到访天数、连续工作时长等。
- `geo.py`：时间和地理计算工具。
- `llm_helper.py`：可选 Qwen3.5-Flash 偏好结构化、货源评分和候选复审接口。

## 算法策略：Risk-Gated MPC + 稀疏 Qwen 顾问

### 核心思路

1. 本地 Planner 先做硬约束可行性检查：家事、home-night、连续休息、熟货、必访点、禁入区。
2. 对剩余候选做短视滚动评分：`expected_net - deadhead_cost - time_cost - penalty_risk + preference_progress_bonus`。
3. 增加 `risk_level`：只有高风险偏好且候选分数接近时，才允许 Qwen 介入。
4. Qwen 只做偏好结构化、货源评分和候选裁决提示，最终动作仍由本地代码校验。

### Risk-Gated MPC

- `_estimate_penalty_risk()`：对每个接单候选估算罚分风险。
- 检查 home-night 回家可达性、家事窗口侵占、休息时间不足、必访点影响。
- 罚分风险 >= 500 的候选直接拒绝。

### 约束处理

- **D009 home-night**：16:00 后限卸货点距家 60km，18:00 后限 30km，20:00 后不接单。15:00/17:00/19:00 主动 reposition 回家。
- **D010 家事**：48 小时前瞻预警（60% 阈值），6 小时强制前往接人点，通用完成时间检查（完成+赶路 > 家事开始-2h → 拒绝）。家事窗口内不接单。
- **连续休息**：提前 4 小时触发休息，使用完整休息时长确保连续性。硬性截止：当天剩余时间不足 → 拒绝接单。空驶时如果需要休息则跳过空驶。
- **必访点**：月度前瞻，剩余天数紧张时更积极安排。

### Qwen3.5-Flash 集成

- `preference_hints()`：结构化偏好文本，`apply_qwen_hints()` 只允许收紧规则。
- `rank_cargos()`：对候选货源评分，与确定性分数融合（alpha=0.35）。
- `suggest_decision()`：在高风险或分数接近时从本地候选中选择。
- 冷却期：`rank_cargos` 至少间隔 10 步，`suggest_decision` 至少间隔 5 步。
- 安全降级：模型调用失败时完全回退确定性逻辑。

## 评测结果

### 确定性基线（无 Qwen，2026-06-04）

| 司机 | 净收入 | 罚分 | 主要约束 |
|------|------:|-----:|---------|
| D001 | 11,069.08 | 300 | 每日连续休息 8h（深圳范围） |
| D002 | 18,713.61 | 1,800 | 每日连续休息 4h + 无成交日 |
| D003 | 829.60 | 0 | 月度空驶 ≤100km |
| D004 | 15,023.86 | 0 | 首单 ≤12:00 + 每日 ≤3 单 |
| D005 | 17,051.64 | 0 | 装卸距离 ≤100km |
| D006 | 18,086.57 | 400 | 每日连续休息 5h + 完全不出车日 |
| D007 | 19,720.82 | 0 | 23-04 不接单 + 无成交日 |
| D008 | 21,661.16 | 3,200 | 平日连续休息 4h + 完全不出车日 |
| D009 | 10,526.21 | 900 | 每日 23:00 前到家 + 熟货 240646 |
| D010 | 19,658.14 | 6,270 | 家事 3/10-3/13 + 每日休息 3h + 必访点 |

**总计：净收入 152,340.69，罚分 12,870，失败司机 0，token 0**

### 已知限制

1. **D010 家事迟到**（3,570 罚分）：家事偏好在 3/10 10:00 才可见，但司机在之前已接长单，无法提前规避。
2. **D002/D006/D008 休息违规**：Agent 倾向于连续接单后安排短休息，而非在接单间插入完整休息。
3. **market_heat 跨步记忆**：当前只在当前决策步内累积，不做跨步持久化。

## 启用 Qwen

```powershell
$env:DASHSCOPE_API_KEY = "your-api-key"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
$env:AGENT_QWEN_MAX_REVIEWS = "20"

cd demo/server
python main.py
```

## 禁止事项

- 不读取 `demo/server/data/cargo_dataset.jsonl`。
- 不读取 `demo/server/data/drivers.json`。
- 不按 `driver_id` 写死策略。
- 不提交真实 API key。
