"""决策服务：依赖 `simkit.ports.SimulationApiPort`，由评测进程注入具体环境。"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from agent.geo import DAY_MINUTES, minutes_to_wall_time
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
        self._driver_step_counts: dict[str, int] = {}
        self._completed_drivers: set[str] = set()
        self._driver_start_times: dict[str, float] = {}

    def decide(self, driver_id: str) -> dict[str, Any]:
        t0 = time.monotonic()
        if driver_id not in self._driver_start_times:
            self._driver_start_times[driver_id] = t0
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
        self._driver_step_counts[driver_id] = self._driver_step_counts.get(driver_id, 0) + 1
        driver_step = self._driver_step_counts[driver_id]

        action_name = action.get("action", "?")
        params = action.get("params", {})
        reason = params.get("cargo_id", "") if action_name == "take_order" else ""
        qwen = self._planner._qwen_review_count
        try:
            status = self._api.get_driver_status(driver_id)
            sim_min = int(status.get("simulation_progress_minutes", 0) or 0)
            sim_day = sim_min // DAY_MINUTES
            sim_wall = minutes_to_wall_time(sim_min)
            # Detect driver completion: sim_min past month horizon
            from agent.geo import MONTH_HORIZON_MINUTES
            if sim_min >= MONTH_HORIZON_MINUTES and driver_id not in self._completed_drivers:
                self._completed_drivers.add(driver_id)
        except Exception:
            sim_min = 0
            sim_day = 0
            sim_wall = "?"

        driver_elapsed = int((time.monotonic() - self._driver_start_times.get(driver_id, time.monotonic())) * 1000)
        tag = "ERROR" if error else "PROGRESS"
        line = (
            f"[AGENT_{tag}] driver={driver_id} completed={len(self._completed_drivers)} "
            f"sim_day={sim_day} step={driver_step} sim={sim_wall} "
            f"action={action_name} reason={reason} qwen_reviews={qwen} "
            f"elapsed_ms={elapsed_ms} driver_elapsed_ms={driver_elapsed}\n"
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
