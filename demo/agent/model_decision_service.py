"""决策服务：依赖 `simkit.ports.SimulationApiPort`，由评测进程注入具体环境。"""

from __future__ import annotations

import logging
from typing import Any

from agent.planner import DeterministicPlanner
from simkit.ports import SimulationApiPort


class ModelDecisionService:
    """官方固定入口。

    V1 uses a deterministic rolling planner so local simulation does not depend
    on a real model API key. The optional model capability remains available via
    SimulationApiPort, but is not required for normal decisions.
    """

    def __init__(self, api: SimulationApiPort) -> None:
        self._api = api
        self._logger = logging.getLogger("agent.decision_service")
        self._planner = DeterministicPlanner(api)

    def decide(self, driver_id: str) -> dict[str, Any]:
        try:
            action = self._planner.decide(driver_id)
            self._logger.info("decision output driver_id=%s action=%s params=%s", driver_id, action.get("action"), action.get("params"))
            return self._normalize_action(action)
        except Exception as exc:  # noqa: BLE001 - never let one bad decision crash evaluation.
            self._logger.exception("deterministic planner failed driver_id=%s err=%s", driver_id, exc)
            return {"action": "wait", "params": {"duration_minutes": 60}}

    def _normalize_action(self, action: dict[str, Any]) -> dict[str, Any]:
        name = str(action.get("action", "")).strip().lower()
        params = action.get("params")
        if not isinstance(params, dict):
            raise ValueError("action.params must be a dict")
        if name == "take_order":
            cargo_id = str(params.get("cargo_id", "")).strip()
            if not cargo_id:
                raise ValueError("take_order requires cargo_id")
            return {"action": "take_order", "params": {"cargo_id": cargo_id}}
        if name == "reposition":
            return {
                "action": "reposition",
                "params": {"latitude": float(params["latitude"]), "longitude": float(params["longitude"])},
            }
        if name == "wait":
            duration = max(1, int(params.get("duration_minutes", 60)))
            return {"action": "wait", "params": {"duration_minutes": duration}}
        raise ValueError(f"unsupported action: {name}")
