# Agent 实现说明

最后更新：2026-06-07 00:25 +08:00

## 2026-05-29 数据版本迁移

当前官方版本只公开 D001/D002，正式评测还有未公开司机。旧版按固定句式解析的策略已不足：

- 新 D001 仅“每日 8 小时连续休息”能被当前解析器识别，其余 4 条漏识别。
- 新 D002 的 7 条偏好当前全部漏识别。
- 偏好包含口语化禁货/禁区域、绝对日期任务、整日停驶、区域接单天数和多站顺序。

最终实现应以 `ConstraintSpec` 通用中间表示替代继续增加司机专用字段。每条规则必须保留：

- 类型、作用域、罚款金额与封顶；
- 每日或绝对时间窗口；
- 地点/半径/城市关键词；
- 目标次数或天数与当前进度；
- 对 take_order/wait/reposition 的满足或违规影响；
- 解析置信度和原始文本。

确定性解析不能可靠覆盖时，在偏好首次可见或文本变化时调用一次 Qwen 输出严格 JSON，校验并缓存。模型不直接生成动作。详细路线见仓库根目录 `最终冲刺与泛化方案.md`。

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

### Qwen 集成架构（低罚分版本已验证）

- **模型**：`qwen3.5-flash`（推理模型，启用 reasoning）
- **已验证结果配置**：总复审 15、每司机最多 5 次、rank 总计最多 5 次
- **当前代码默认配额**：总复审 15、每司机最多 5 次、rank 总计最多 5 次；同一步只允许一次模型调用，风险校验优先。
- `preference_hints`：实验功能，默认关闭；设置 `AGENT_ENABLE_QWEN_PREFERENCE_HINTS=1` 才启用。已验证运行中 10 次调用均因输出截断失败，未贡献决策。
- `rank_cargos`：约束感知 prompt，top-3 货源评分
- `suggest_decision`：隐藏 deterministic_score，基于约束上下文选优
- `verify_constraints`：三种领域特化 prompt（home_night/rest/family）

### 有效改动

| 改动 | 影响 |
|------|------|
| `net_per_hour` 0.25→0.6 | 恢复低罚分版本的部分净收入 |
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

### 当前低罚分基线（2026-06-06 21:41）

| 司机 | 净收入 | 罚分 |
|------|------:|-----:|
| D001-D010 | **154,297** | **8,355** |

**罚分 -35.5%（12,955→8,355），距 8,000 目标仅差 355。净收入 154,297，Token 52,976。结果位于 `demo/results/history/20260606_214230`。**

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
3. **Qwen Token 仍超目标**：已验证结果 52,976；默认关闭无效 preference hints 后需由 CC 确认是否低于 50,000。
4. **D009 home-night 校验异常已修复并验证**：最新完整日志不再出现 `HomeNightRule` 属性错误。
5. **market_heat 跨步记忆**：当前只在当前决策步内累积，不做跨步持久化。

## 启用 Qwen

```powershell
$env:DASHSCOPE_API_KEY = "your-api-key"
$env:TIANCHI_MODEL_API_KEY = $env:DASHSCOPE_API_KEY
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
$env:AGENT_QWEN_MAX_REVIEWS = "15"
$env:AGENT_QWEN_MAX_REVIEWS_PER_DRIVER = "5"
$env:AGENT_QWEN_MAX_RANKS = "5"
$env:AGENT_ENABLE_QWEN_PREFERENCE_HINTS = "0"

cd demo/server
python main.py
```

注意：154,297 / 8,355 / 52,976 是关闭无效 preference hints 之前的已验证基线；下一轮重点确认 Token 是否降到 50,000 以下。

## 禁止事项

- 不读取 `demo/server/data/cargo_dataset.jsonl`。
- 不读取 `demo/server/data/drivers.json`。
- 不按 `driver_id` 写死策略。
- 不提交真实 API key。
