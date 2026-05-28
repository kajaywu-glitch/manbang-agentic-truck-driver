# Agent 实现说明

本目录实现确定性滚动规划 Agent。主入口仍为官方要求的 `ModelDecisionService.decide(driver_id)`。

## 基座模型预留

- 复赛建议模型按赛题要求锁定为 `qwen3.5-flash`。
- 当前 V1 主路径不依赖模型调用，保证没有真实 API key 时也能本地跑通。
- 已预留 `llm_helper.QwenFlashHelper`，只通过官方 `SimulationApiPort.model_chat_completion` 调用模型。
- 若要启用 Qwen3.5-Flash 进行偏好结构化提示，设置环境变量：

```powershell
$env:AGENT_ENABLE_QWEN35_FLASH = "1"
```

未设置该变量时，不会发起模型请求。

## 约束

- 决策代码不得读取 `demo/server/data/cargo_dataset.jsonl` 或 `drivers.json`。
- 只能通过 `get_driver_status`、`query_cargo`、`query_decision_history` 和可选的 `model_chat_completion` 获取信息。
- 不按 `driver_id` 写死策略；偏好规则来自运行时 `preferences` 文本解析。
