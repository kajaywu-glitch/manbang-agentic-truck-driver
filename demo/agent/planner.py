"""Deterministic rolling planner for the truck-driver cargo simulation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.geo import (
    DAY_MINUTES,
    MONTH_HORIZON_MINUTES,
    day_end,
    distance_to_minutes,
    haversine_km,
    interval_overlap,
    minute_of_day,
    wall_time_to_minutes,
)
from agent.llm_helper import QWEN_FLASH_MODEL, QwenFlashHelper
from agent.preference_rules import (
    FamilyTask,
    HomeNightRule,
    PreferencePolicy,
    RequiredCargo,
    needs_rest_today,
    parse_preferences,
    quiet_window_end_if_inside,
    should_preserve_no_order_day,
    should_preserve_off_day,
)
from agent.state_tracker import DriverMemory, build_memory
from simkit.ports import SimulationApiPort

DEFAULT_SPEED_KM_PER_HOUR = 60.0
DEFAULT_COST_PER_KM = 1.5


@dataclass(frozen=True)
class Candidate:
    action: dict[str, Any]
    score: float
    reason: str


@dataclass(frozen=True)
class CargoPlan:
    cargo_id: str
    cargo_name: str
    price: float
    pickup_km: float
    haul_km: float
    pickup_minutes: int
    wait_minutes: int
    duration_minutes: int
    finish_minutes: int
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    score: float


class DeterministicPlanner:
    """A model-free planner that only uses the official SimulationApiPort."""

    def __init__(self, api: SimulationApiPort) -> None:
        self._api = api
        self._logger = logging.getLogger("agent.planner")
        self._qwen = QwenFlashHelper(api)

    def decide(self, driver_id: str) -> dict[str, Any]:
        status = self._api.get_driver_status(driver_id)
        history = self._safe_history(driver_id)
        memory = build_memory(history)
        policy = parse_preferences(list(status.get("preferences") or []))
        qwen_hints = self._qwen.preference_hints(list(status.get("preferences") or []))
        if qwen_hints:
            self._logger.info("loaded %s preference hints driver=%s keys=%s", QWEN_FLASH_MODEL, driver_id, sorted(qwen_hints.keys()))

        urgent = self._urgent_action(status, memory, policy)
        if urgent is not None:
            self._logger.info("urgent decision driver=%s action=%s", driver_id, urgent)
            return urgent

        lat = float(status["current_lat"])
        lng = float(status["current_lng"])
        cargo_resp = self._api.query_cargo(driver_id=driver_id, latitude=lat, longitude=lng)
        items = cargo_resp.get("items", [])
        if not isinstance(items, list):
            items = []

        # Querying cargo advances simulation time; refresh status before choosing action.
        status = self._api.get_driver_status(driver_id)
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        lat = float(status["current_lat"])
        lng = float(status["current_lng"])

        urgent_after_query = self._urgent_action(status, memory, policy)
        if urgent_after_query is not None:
            self._logger.info("post-query urgent decision driver=%s action=%s", driver_id, urgent_after_query)
            return urgent_after_query

        best_cargo = self._best_cargo_plan(status, memory, policy, items)
        wait_candidate = self._wait_candidate(status, memory, policy, items)
        reposition_candidate = self._reposition_candidate(status, memory, policy, best_cargo)

        candidates = [c for c in (best_cargo, wait_candidate, reposition_candidate) if c is not None]
        if not candidates:
            return self._wait(60)
        chosen = max(candidates, key=lambda c: c.score)
        self._logger.info(
            "decision driver=%s now=%s loc=(%.5f,%.5f) action=%s reason=%s score=%.2f items=%s",
            driver_id,
            now_minute,
            lat,
            lng,
            chosen.action,
            chosen.reason,
            chosen.score,
            len(items),
        )
        return chosen.action

    def _safe_history(self, driver_id: str) -> dict[str, Any]:
        try:
            return self._api.query_decision_history(driver_id, -1)
        except Exception as exc:  # noqa: BLE001 - keep the simulation alive.
            self._logger.warning("query_decision_history failed driver=%s err=%s", driver_id, exc)
            return {"records": []}

    def _urgent_action(
        self,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
    ) -> dict[str, Any] | None:
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        lat = float(status["current_lat"])
        lng = float(status["current_lng"])

        family = policy.family_task
        if family is not None and family.start_minute <= now_minute < family.stay_until_minute:
            action = self._family_action(family, memory, now_minute, lat, lng)
            if action is not None:
                return action

        required_cargo = policy.required_cargo
        if required_cargo is not None and not memory.has_taken_cargo(required_cargo.cargo_id):
            action = self._required_cargo_positioning(required_cargo, now_minute, lat, lng, policy)
            if action is not None:
                return action

        home = policy.home_night
        if home is not None:
            action = self._home_night_action(home, now_minute, lat, lng, policy)
            if action is not None:
                return action

        quiet_end = quiet_window_end_if_inside(policy.quiet_windows, now_minute)
        if quiet_end is not None:
            return self._wait(max(1, quiet_end - now_minute))

        if should_preserve_off_day(policy, memory, now_minute):
            return self._wait_until_next_day(now_minute)
        if should_preserve_no_order_day(policy, memory, now_minute):
            return self._wait_until_next_day(now_minute)

        for visit in policy.required_visits:
            days = memory.visit_days(visit.lat, visit.lng, visit.radius_km)
            if len(days) >= visit.days_required or (now_minute // DAY_MINUTES) in days:
                continue
            if minute_of_day(now_minute) < 10 * 60:
                dist = haversine_km(lat, lng, visit.lat, visit.lng)
                if dist > visit.radius_km and dist <= 80 and self._active_allowed(policy, now_minute, now_minute + distance_to_minutes(dist)):
                    return {"action": "reposition", "params": {"latitude": visit.lat, "longitude": visit.lng}}

        rest_remaining = needs_rest_today(policy, memory, now_minute)
        if rest_remaining > 0:
            latest_start = DAY_MINUTES - policy.daily_rest_minutes
            if minute_of_day(now_minute) >= latest_start:
                return self._wait(max(30, min(policy.daily_rest_minutes, day_end(now_minute) - now_minute)))
        return None

    def _family_action(
        self,
        family: FamilyTask,
        memory: DriverMemory,
        now_minute: int,
        lat: float,
        lng: float,
    ) -> dict[str, Any] | None:
        pickup_done = memory.has_waited_at(
            family.pickup_lat,
            family.pickup_lng,
            family.radius_km,
            family.start_minute,
            family.pickup_wait_minutes,
        )
        at_pickup = haversine_km(lat, lng, family.pickup_lat, family.pickup_lng) <= family.radius_km
        at_home = haversine_km(lat, lng, family.home_lat, family.home_lng) <= family.radius_km
        if not pickup_done:
            if not at_pickup:
                return {"action": "reposition", "params": {"latitude": family.pickup_lat, "longitude": family.pickup_lng}}
            return self._wait(family.pickup_wait_minutes)
        if not at_home:
            return {"action": "reposition", "params": {"latitude": family.home_lat, "longitude": family.home_lng}}
        if now_minute < family.stay_until_minute:
            return self._wait(max(1, family.stay_until_minute - now_minute))
        return None

    def _required_cargo_positioning(
        self,
        rule: RequiredCargo,
        now_minute: int,
        lat: float,
        lng: float,
        policy: PreferencePolicy,
    ) -> dict[str, Any] | None:
        if rule.target_lat is None or rule.target_lng is None:
            return None
        if rule.end_minute is not None and now_minute > rule.end_minute:
            return None
        start = rule.start_minute if rule.start_minute is not None else now_minute
        if now_minute < start - 8 * 60:
            return None
        dist = haversine_km(lat, lng, rule.target_lat, rule.target_lng)
        move_minutes = distance_to_minutes(dist)
        if dist <= 3.0:
            if now_minute < start:
                return self._wait(min(max(1, start - now_minute), 120))
            return None
        if self._active_allowed(policy, now_minute, now_minute + move_minutes):
            return {"action": "reposition", "params": {"latitude": rule.target_lat, "longitude": rule.target_lng}}
        return None

    def _home_night_action(
        self,
        home: HomeNightRule,
        now_minute: int,
        lat: float,
        lng: float,
        policy: PreferencePolicy,
    ) -> dict[str, Any] | None:
        mod = minute_of_day(now_minute)
        at_home = haversine_km(lat, lng, home.lat, home.lng) <= home.radius_km
        if mod >= home.quiet_start_minute or mod < home.quiet_end_minute:
            if at_home:
                end = (now_minute - mod) + home.quiet_end_minute
                if mod >= home.quiet_start_minute:
                    end += DAY_MINUTES
                return self._wait(max(1, end - now_minute))
            return self._wait(max(30, min(120, day_end(now_minute) - now_minute)))
        dist = haversine_km(lat, lng, home.lat, home.lng)
        travel = distance_to_minutes(dist)
        deadline = (now_minute - mod) + home.deadline_minute_of_day
        if not at_home and now_minute + travel <= deadline and mod >= 20 * 60:
            if self._active_allowed(policy, now_minute, now_minute + travel):
                return {"action": "reposition", "params": {"latitude": home.lat, "longitude": home.lng}}
        return None

    def _best_cargo_plan(
        self,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
        items: list[Any],
    ) -> Candidate | None:
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        current_lat = float(status["current_lat"])
        current_lng = float(status["current_lng"])
        truck_length = str(status.get("truck_length") or "").strip()
        best: CargoPlan | None = None
        for item in items:
            plan = self._evaluate_cargo(item, now_minute, current_lat, current_lng, truck_length, memory, policy)
            if plan is None:
                continue
            if best is None or plan.score > best.score:
                best = plan
        if best is None:
            return None
        if best.score < 15.0:
            return None
        return Candidate({"action": "take_order", "params": {"cargo_id": best.cargo_id}}, best.score, "best_cargo")

    def _evaluate_cargo(
        self,
        item: Any,
        now_minute: int,
        current_lat: float,
        current_lng: float,
        truck_length: str,
        memory: DriverMemory,
        policy: PreferencePolicy,
    ) -> CargoPlan | None:
        if not isinstance(item, dict) or not isinstance(item.get("cargo"), dict):
            return None
        cargo = item["cargo"]
        cargo_id = str(cargo.get("cargo_id", "")).strip()
        if not cargo_id:
            return None
        truck_options = cargo.get("truck_length")
        if truck_length and isinstance(truck_options, list) and truck_length not in {str(v) for v in truck_options}:
            return None
        cargo_name = str(cargo.get("cargo_name") or "").strip()
        if cargo_name in policy.forbidden_cargo_names:
            return None
        start = cargo.get("start") if isinstance(cargo.get("start"), dict) else {}
        end = cargo.get("end") if isinstance(cargo.get("end"), dict) else {}
        try:
            start_lat = float(start["lat"])
            start_lng = float(start["lng"])
            end_lat = float(end["lat"])
            end_lng = float(end["lng"])
            duration = int(cargo.get("cost_time_minutes", 0) or 0)
            price = float(cargo.get("price", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        if duration <= 0 or price <= 0:
            return None
        remove_minute = wall_time_to_minutes(str(cargo.get("remove_time", "")))
        if remove_minute is not None and now_minute > remove_minute:
            return None
        if not (policy.point_allowed(current_lat, current_lng) and policy.point_allowed(start_lat, start_lng) and policy.point_allowed(end_lat, end_lng)):
            return None
        pickup_km = float(item.get("distance_km") or haversine_km(current_lat, current_lng, start_lat, start_lng))
        haul_km = haversine_km(start_lat, start_lng, end_lat, end_lng)
        if policy.max_pickup_km is not None and pickup_km > policy.max_pickup_km:
            return None
        if policy.max_haul_km is not None and haul_km > policy.max_haul_km:
            return None
        if policy.max_month_deadhead_km is not None and memory.deadhead_km + pickup_km > policy.max_month_deadhead_km:
            return None
        if policy.daily_max_orders is not None and memory.accepted_orders_today(now_minute) >= policy.daily_max_orders:
            return None
        if policy.first_order_latest_minute is not None and memory.accepted_orders_today(now_minute) == 0:
            if minute_of_day(now_minute) >= policy.first_order_latest_minute:
                return None

        pickup_minutes = distance_to_minutes(pickup_km) if pickup_km > 1e-6 else 0
        arrival = now_minute + pickup_minutes
        load_start, load_end = _load_window_minutes(cargo)
        if load_end is not None and arrival > load_end:
            return None
        ready = max(arrival, load_start) if load_start is not None else arrival
        wait_minutes = max(0, ready - arrival)
        finish = ready + duration
        if finish > MONTH_HORIZON_MINUTES:
            return None
        if not self._active_allowed(policy, now_minute, finish):
            return None

        travel_cost = (pickup_km + haul_km) * DEFAULT_COST_PER_KM
        base_net = price - travel_cost
        total_minutes = max(1, finish - now_minute)
        net_per_hour = base_net / (total_minutes / 60.0)
        score = base_net + 0.15 * net_per_hour - pickup_km * 0.35 - wait_minutes * 0.08
        if cargo_name in policy.soft_avoid_cargo_names:
            score -= 450.0
        if pickup_km > 80:
            score -= (pickup_km - 80) * 2.0
        if finish // DAY_MINUTES != now_minute // DAY_MINUTES:
            score -= 50.0
        if base_net <= 0:
            score -= 200.0
        return CargoPlan(
            cargo_id=cargo_id,
            cargo_name=cargo_name,
            price=price,
            pickup_km=pickup_km,
            haul_km=haul_km,
            pickup_minutes=pickup_minutes,
            wait_minutes=wait_minutes,
            duration_minutes=duration,
            finish_minutes=finish,
            start_lat=start_lat,
            start_lng=start_lng,
            end_lat=end_lat,
            end_lng=end_lng,
            score=score,
        )

    def _wait_candidate(
        self,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
        items: list[Any],
    ) -> Candidate | None:
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        rest_remaining = needs_rest_today(policy, memory, now_minute)
        if rest_remaining > 0 and (minute_of_day(now_minute) >= DAY_MINUTES - policy.daily_rest_minutes - 60 or not items):
            duration = max(30, min(policy.daily_rest_minutes, day_end(now_minute) - now_minute))
            return Candidate(self._wait(duration), 80.0 + rest_remaining * 0.05, "rest")
        if not items:
            return Candidate(self._wait(60), 10.0, "no_cargo")
        if minute_of_day(now_minute) >= 22 * 60:
            return Candidate(self._wait(min(120, day_end(now_minute) - now_minute)), 15.0, "late_day")
        return Candidate(self._wait(30), -20.0, "low_priority_wait")

    def _reposition_candidate(
        self,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
        best_cargo: Candidate | None,
    ) -> Candidate | None:
        # V1 keeps speculative repositioning conservative; urgent repositioning is
        # handled in _urgent_action. Avoid moving without an explicit rule target.
        _ = status, memory, policy, best_cargo
        return None

    def _active_allowed(self, policy: PreferencePolicy, start_minute: int, end_minute: int) -> bool:
        if end_minute <= start_minute:
            return True
        return not policy.active_interval_blocked(start_minute, end_minute)

    @staticmethod
    def _wait(duration_minutes: int) -> dict[str, Any]:
        return {"action": "wait", "params": {"duration_minutes": max(1, int(duration_minutes))}}

    @staticmethod
    def _wait_until_next_day(now_minute: int) -> dict[str, Any]:
        return DeterministicPlanner._wait(max(1, day_end(now_minute) - now_minute))


def _load_window_minutes(cargo: dict[str, Any]) -> tuple[int | None, int | None]:
    raw = cargo.get("load_time")
    if not isinstance(raw, list) or len(raw) != 2:
        return None, None
    start = wall_time_to_minutes(str(raw[0]))
    end = wall_time_to_minutes(str(raw[1]))
    if start is None or end is None or end < start:
        return None, None
    return start, end
