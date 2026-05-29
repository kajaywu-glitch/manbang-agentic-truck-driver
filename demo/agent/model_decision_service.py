"""决策服务：依赖 `simkit.ports.SimulationApiPort`，由评测进程注入具体环境。"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from agent.geo import minutes_to_wall_time
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
        self._progress_stderr = os.environ.get("AGENT_PROGRESS_STDERR", "").strip().lower() in {"1", "true", "yes", "on"}
        self._step_count = 0

    def decide(self, driver_id: str) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            action = self._planner.decide(driver_id)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self._logger.info("decision output driver_id=%s action=%s params=%s", driver_id, action.get("action"), action.get("params"))
            normalized = self._normalize_action(action)
            if self._progress_stderr:
                self._emit_progress(driver_id, normalized, elapsed_ms)
            return normalized
        except Exception as exc:  # noqa: BLE001 - never let one bad decision crash evaluation.
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self._logger.exception("deterministic planner failed driver_id=%s err=%s", driver_id, exc)
            fallback = {"action": "wait", "params": {"duration_minutes": 60}}
            if self._progress_stderr:
                self._emit_progress(driver_id, fallback, elapsed_ms, error=True)
            return fallback

    def _emit_progress(self, driver_id: str, action: dict[str, Any], elapsed_ms: int, *, error: bool = False) -> None:
        self._step_count += 1
        action_name = action.get("action", "?")
        params = action.get("params", {})
        reason = params.get("cargo_id", "") if action_name == "take_order" else ""
        qwen = self._planner._qwen_review_count
        try:
            status = self._api.get_driver_status(driver_id)
            sim_min = int(status.get("simulation_progress_minutes", 0) or 0)
            sim_wall = minutes_to_wall_time(sim_min)
        except Exception:
            sim_min = 0
            sim_wall = "?"
        tag = "ERROR" if error else "PROGRESS"
        line = (
            f"[AGENT_{tag}] driver={driver_id} step={self._step_count} "
            f"sim={sim_wall} sim_min={sim_min} action={action_name} "
            f"reason={reason} qwen_reviews={qwen} elapsed_ms={elapsed_ms}\n"
        )
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:
            pass

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
