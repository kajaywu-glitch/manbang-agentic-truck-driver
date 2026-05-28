# Claude Code 项目环境说明

本项目在 Windows 下开发，Python 环境已经由 Codex 配置好：

- Conda 安装位置：`C:\Users\20689\miniconda3`
- 项目虚拟环境：`mus-tread`
- Python 版本：`3.11.15`
- 已安装依赖：`numpy`、`requests`
- 依赖来源：`demo/server/requirements.txt`

## 运行 Python

不要使用 `python3`。Windows 上 `python3` 会命中 Microsoft Store 占位符，导致：

```text
Python was not found; run without arguments to install from the Microsoft Store
```

请使用下面的命令运行项目 Python：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python
```

常用验证命令：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python -m compileall -q D:\竞赛\demo
```

如果需要执行某个脚本，使用：

```powershell
C:\Users\20689\miniconda3\Scripts\conda.exe run -n mus-tread python path\to\script.py
```

## 开发约束

- 主要修改目录是 `demo/agent/`。
- 决策代码必须通过 `SimulationApiPort` 获取状态、货源与历史。
- 禁止直接读取 `demo/server/data/cargo_dataset.jsonl`、`demo/server/data/drivers.json` 或其复制文件。
- 做完代码修改后，至少运行一次 `compileall` 验证。

## 项目进度（2026-05-28）

### 赛题概述
满帮 Agent 算法大赛：10名司机（D001-D010）在2026年3月进行31天货运仿真。目标：最大化月度净收入，同时满足每位司机的个性化偏好规则（违反有罚分）。

### 架构
- 事件驱动仿真，3种动作：`take_order`（接单）、`wait`（等待）、`reposition`（空驶转移）
- 滚动时域规划（MPC思路），确定性规划器 + 可选 Qwen3.5-Flash 模型辅助
- 关键文件：`demo/agent/planner.py`（核心规划器）、`demo/agent/preference_rules.py`（偏好解析）、`demo/agent/state_tracker.py`（状态跟踪）

### 当前成绩（31天仿真）
| 司机 | 净收入 | 罚分 | 主要问题 |
|------|--------|------|----------|
| D001 | 9,454 | 900 | 3天休息不足8h |
| D002 | 17,099 | 2,200 | 休息违规（已大幅改善） |
| D003 | 830 | 0 | 已修复空驶限额 |
| D004 | 15,024 | 0 | 完美 |
| D005 | 17,052 | 0 | 完美 |
| D006 | 12,895 | 1,200 | 休息违规（已大幅改善） |
| D007 | 19,721 | 0 | 完美 |
| D008 | 17,741 | 2,400 | 平日休息不足4h |
| D009 | -2,989 | 13,500 | home_night 违规（23点前未到家） |
| D010 | 16,020 | 10,745 | 家事序列+必访点+休息 |
| **总计** | **122,845** | **30,945** | |

### 已完成的修复
1. **D003 空驶限额**：reposition 增加月度空驶限额检查（`max_month_deadhead_km`）
2. **D002/D006 休息解析**：修复"连着"vs"连续"关键词匹配问题
3. **D009 必接熟货**：`_best_cargo_plan` 中优先搜索 required_cargo，score=99999
4. **D010 家事**：修复 deadline regex、强制先接配偶再回家
5. **休息主动规划**：`must_rest_today_proactive()` 月度前瞻，提前触发休息
6. **每日休息保障**：`_evaluate_cargo` 中过滤侵占休息时间的订单
7. **空驶策略**：基于 market_heat 的目的地选择，4小时冷却
8. **必访点**：更灵活的时间窗口（14:00前紧急、10:00前常规）

### 未解决问题（下次继续）
1. **D009 home_night（13,500罚分）**：`_evaluate_cargo` 中已加入 home_night 检查（finish > today_deadline → reject），但仿真结果未生效。需要排查：可能是 `_best_cargo_plan` 中 required_cargo 优先级绕过了检查，或 `__pycache__` 缓存问题。
2. **D010 家事序列（5,645罚分）**：配偶已接取，但到家后缺席1129分钟。需要确保到家后 wait 直到 stay_until。
3. **D010 必访点（3,000罚分）**：只访问了4天，需要5天。需要更积极地安排必访点。
4. **D001/D008 休息（900/2,400罚分）**：D001 需要8h连续休息，D008 平日4h。需要更早触发休息。
5. **D009 净收入为负**：大量空驶转移导致成本过高。需要限制 D009 的空驶范围。

### 调试技巧
- 清除缓存：`rm -rf D:/竞赛/demo/agent/__pycache__ D:/竞赛/demo/server/bench/__pycache__ D:/竞赛/demo/simkit/__pycache__`
- 仿真运行：`cd D:/竞赛/demo/server && DASHSCOPE_API_KEY="dummy_key_for_testing" AGENT_ENABLE_QWEN35_FLASH=1 python main.py`
- 收入计算：`cd D:/竞赛/demo && python calc_monthly_income.py`
- 检查结果：`demo/results/monthly_income_202603.json`
