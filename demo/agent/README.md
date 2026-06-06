# Agent 实现说明

最后更新：2026-06-06 17:40 +08:00

## 当前架构

- `model_decision_service.py`：入口、动作归一化、异常兜底、进度显示。
- `planner.py`：Risk-Gated MPC 确定性滚动规划，负责紧急约束、查询货源、候选评分、等待和空驶。
- `preference_rules.py`：把运行时 `preferences` 文本解析为 `PreferencePolicy`。
- `state_tracker.py`：从 `query_decision_history` 重建累计接单数、休息段、空驶里程、到访天数、连续工作时长等。
- `geo.py`：时间和地理计算工具。
- `llm_helper.py`：Qwen 偏好结构化、货源排序、候选建议和领域特化约束验证接口。

## 算法策略：Risk-Gated MPC + qwen3.5-flash 推理增强

### 核心思路

1. 确定性 Planner 做硬约束检查 + 候选生成 + 短视滚动评分。
2. **qwen3.5-flash** 在三层参与：
   - `rank_cargos`：约束感知货源重排（top-3，10 步冷却）
   - `suggest_decision`：隐藏确定性分数，独立约束上下文选优（5 步冷却）
   - `verify_constraints`：home_night/rest/family 安全审核
3. 安全底线：模型失败时完全回退确定性逻辑。

### Qwen 集成架构（冠军版本已验证，审阅修正待 CC 完整复验）

- **模型**：`qwen3.5-flash`（推理模型，启用 reasoning）
- **配置**：`AGENT_ENABLE_QWEN35_FLASH=1`, `AGENT_QWEN_MAX_REVIEWS=20`
- **审阅后默认配额**：每司机最多 2 次、rank 总计最多 4 次；同一步只允许一次模型调用，风险校验优先。
- `preference_hints`：仅 `forbidden_cargo_names`（纯罚分改善，避免过度收紧）
- `rank_cargos`：约束感知 prompt，top-3 货源评分
- `suggest_decision`：隐藏 deterministic_score，基于约束上下文选优
- `verify_constraints`：三种领域特化 prompt（home_night/rest/family）

### 有效改动

| 改动 | 影响 |
|------|------|
| `net_per_hour` 0.25→0.5 | +2,837 确定性净收入 |
| qwen3.5-flash 推理模型 | rank_cargos + suggest_decision 启用 |
| 按比例休息预触发 `max(240, rest_minutes)` | D002 小幅改善 |

## 评测结果

### 最终成绩（qwen3.5-flash, max_reviews=20, 2026-06-06）

| 司机 | 净收入 | 罚分 |
|------|------:|-----:|
| D001 | 12,016 | 300 |
| D002 | **23,483** | 1,800 |
| D003 | 830 | 0 |
| D004 | 15,024 | 0 |
| D005 | 17,052 | 0 |
| D006 | 19,079 | 200 |
| D007 | 18,675 | 0 |
| D008 | 21,661 | 3,200 |
| D009 | 10,464 | 900 |
| D010 | 18,689 | 6,555 |

**总计：净收入 156,973，罚分 12,955，token 46,149，耗时 431s**（旧冠军）

### 新冠军（上午主动休息 + 休息兼容性奖励，2026-06-06 17:40）

| 司机 | 净收入 | 罚分 |
|------|------:|-----:|
| D001-D010 | **153,541** | **8,355** |

**罚分 -35.5%（12,955→8,355），距 8,000 目标仅差 355。净收入 153,541（-2.2% vs 旧冠军）。**

以下表格是早期 Qwen 方案的历史对照，不代表当前冠军版本：

| 指标 | 确定性基线 | Qwen 模式 | 变化 |
|------|----------:|----------:|-----:|
| 净收入 | 152,769 | 142,895 | -9,874 |
| 罚分 | 12,070 | 12,070 | 0 |
| Token | 0 | 39,241 | +39,241 |
| 耗时 | 143s | 371s | +228s |

### 已知限制

1. **D010 家事迟到**（3,570 罚分）：家事偏好在 3/10 10:00 才可见，但司机在之前已接长单，无法提前规避。这是偏好可见性窗口的固有限制。
2. **D002/D008 休息违规**：仍有 8/7 天未满足连续休息。休息被跨日长单切碎，需要更智能的跨日休息预测。
3. **Qwen 必须受控介入**：早期版本存在 suggest 无差异化、rank token 过高和调用集中于单司机的问题；审阅修正已增加分差门控和公平配额，待 CC 完整复验。
4. **market_heat 跨步记忆**：当前只在当前决策步内累积，不做跨步持久化。

## 启用 Qwen

```powershell
$env:DASHSCOPE_API_KEY = "your-api-key"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
$env:AGENT_QWEN_MAX_REVIEWS = "20"
$env:AGENT_QWEN_MAX_REVIEWS_PER_DRIVER = "2"
$env:AGENT_QWEN_MAX_RANKS = "4"

cd demo/server
python main.py
```

注意：156,973 是审阅修正前的冠军成绩；新配额和上下文修正需要由 CC 的下一次完整仿真重新确认。

## 禁止事项

- 不读取 `demo/server/data/cargo_dataset.jsonl`。
- 不读取 `demo/server/data/drivers.json`。
- 不按 `driver_id` 写死策略。
- 不提交真实 API key。
