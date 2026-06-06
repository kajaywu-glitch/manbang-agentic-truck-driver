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

### Qwen 集成架构（低罚分版本已验证，异常修正待 CC 下一轮确认）

- **模型**：`qwen3.5-flash`（推理模型，启用 reasoning）
- **低罚分结果实际配置**：总复审 20、每司机最多 5 次、rank 总计最多 5 次
- **当前代码默认配额**：总复审 15、每司机最多 5 次、rank 总计最多 5 次；同一步只允许一次模型调用，风险校验优先。
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

### 旧净收入冠军（qwen3.5-flash, max_reviews=20, 2026-06-06）

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

### 低罚分候选（上午主动休息 + 休息兼容性奖励，2026-06-06 17:40）

| 司机 | 净收入 | 罚分 |
|------|------:|-----:|
| D001-D010 | **153,541** | **8,355** |

**罚分 -35.5%（12,955→8,355），距 8,000 目标仅差 355。净收入 153,541（-2.2% vs 旧冠军），Token 64,567（超过 50,000 目标）。**

以下表格是早期 Qwen 方案的历史对照，不代表当前冠军版本：

| 指标 | 确定性基线 | Qwen 模式 | 变化 |
|------|----------:|----------:|-----:|
| 净收入 | 152,769 | 142,895 | -9,874 |
| 罚分 | 12,070 | 12,070 | 0 |
| Token | 0 | 39,241 | +39,241 |
| 耗时 | 143s | 371s | +228s |

### 已知限制

1. **连续休息仍是主要罚分源**：D002/D006/D008/D010 分别仍有 8/6/4/8 天违规，需要统一的跨日休息可行性模型。
2. **D010 家事基本解决**：家事罚分已降至 155，但仍有 31 分钟不在家窗口，可继续压缩。
3. **Qwen Token 超目标**：最新结果 64,567；模型调用集中在 D001/D006/D010 的风险复核，应按边际收益进一步收紧。
4. **D009 home-night 校验异常已修复**：日志中的 `HomeNightRule.home_lat` 属性错误已改为正确的 `lat/lng`，等待下一轮结果确认。
5. **market_heat 跨步记忆**：当前只在当前决策步内累积，不做跨步持久化。

## 启用 Qwen

```powershell
$env:DASHSCOPE_API_KEY = "your-api-key"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
$env:AGENT_QWEN_MAX_REVIEWS = "15"
$env:AGENT_QWEN_MAX_REVIEWS_PER_DRIVER = "5"
$env:AGENT_QWEN_MAX_RANKS = "5"

cd demo/server
python main.py
```

注意：153,541 / 8,355 是当前低罚分候选成绩；D009 异常修正后的结果由 CC 下一轮运行确认。

## 禁止事项

- 不读取 `demo/server/data/cargo_dataset.jsonl`。
- 不读取 `demo/server/data/drivers.json`。
- 不按 `driver_id` 写死策略。
- 不提交真实 API key。
