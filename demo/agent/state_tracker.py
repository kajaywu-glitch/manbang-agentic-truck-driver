"""Rebuild per-driver state from the official in-memory decision history API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.geo import DAY_MINUTES, haversine_km, iter_day_segments, merged_longest_span


@dataclass(frozen=True)
class StepContext:
    action_name: str
    params: dict[str, Any]
    result: dict[str, Any]
    step_start: int
    action_start: int
    action_end: int
    step_end: int
    action_exec_cost: int
    before_lat: float
    before_lng: float
    after_lat: float
    after_lng: float


@dataclass
class DriverMemory:
    records: list[StepContext] = field(default_factory=list)
    accepted_orders_by_day: dict[int, int] = field(default_factory=dict)
    active_minutes_by_day: dict[int, int] = field(default_factory=dict)
    wait_intervals_by_day: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    deadhead_km: float = 0.0

    def accepted_orders_today(self, now_minute: int) -> int:
        return self.accepted_orders_by_day.get(now_minute // DAY_MINUTES, 0)

    def active_minutes_today(self, now_minute: int) -> int:
        return self.active_minutes_by_day.get(now_minute // DAY_MINUTES, 0)

    def longest_rest_today(self, now_minute: int) -> int:
        return self.longest_rest_for_day(now_minute // DAY_MINUTES)

    def longest_rest_for_day(self, day: int) -> int:
        return merged_longest_span(self.wait_intervals_by_day.get(day, []))

    def completed_no_order_days(self, now_minute: int) -> int:
        current_day = now_minute // DAY_MINUTES
        return sum(1 for day in range(current_day) if self.accepted_orders_by_day.get(day, 0) == 0)

    def completed_off_days(self, now_minute: int) -> int:
        current_day = now_minute // DAY_MINUTES
        return sum(1 for day in range(current_day) if self.active_minutes_by_day.get(day, 0) == 0)

    def has_taken_cargo(self, cargo_id: str) -> bool:
        target = str(cargo_id)
        for rec in self.records:
            if rec.action_name != "take_order" or not rec.result.get("accepted"):
                continue
            if str(rec.params.get("cargo_id", "")) == target:
                return True
        return False

    def has_waited_at(self, lat: float, lng: float, radius_km: float, since_minute: int, duration_minutes: int) -> bool:
        running = 0
        for rec in self.records:
            if rec.step_end < since_minute:
                continue
            if rec.action_name == "wait" and haversine_km(rec.after_lat, rec.after_lng, lat, lng) <= radius_km:
                running += rec.action_exec_cost
                if running >= duration_minutes:
                    return True
            else:
                running = 0
        return False

    def visit_days(self, lat: float, lng: float, radius_km: float) -> set[int]:
        days: set[int] = set()
        for rec in self.records:
            if haversine_km(rec.after_lat, rec.after_lng, lat, lng) <= radius_km:
                days.add(rec.step_end // DAY_MINUTES)
        return days


def build_memory(history_resp: dict[str, Any] | None) -> DriverMemory:
    rows = []
    if isinstance(history_resp, dict):
        raw = history_resp.get("records")
        if isinstance(raw, list):
            rows = raw

    memory = DriverMemory()
    prev_end = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            query_scan = int(row.get("query_scan_cost_minutes", 0) or 0)
            action_exec = int(row.get("action_exec_cost_minutes", 0) or 0)
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            step_end = int(result.get("simulation_progress_minutes", prev_end + query_scan + action_exec))
            action_obj = row.get("action") if isinstance(row.get("action"), dict) else {}
            params = action_obj.get("params") if isinstance(action_obj.get("params"), dict) else {}
            pos_before = row.get("position_before") if isinstance(row.get("position_before"), dict) else {}
            pos_after = row.get("position_after") if isinstance(row.get("position_after"), dict) else {}
            rec = StepContext(
                action_name=str(action_obj.get("action", "")).strip().lower(),
                params=dict(params),
                result=dict(result),
                step_start=prev_end,
                action_start=prev_end + query_scan,
                action_end=prev_end + query_scan + action_exec,
                step_end=step_end,
                action_exec_cost=action_exec,
                before_lat=float(pos_before.get("lat", 0.0) or 0.0),
                before_lng=float(pos_before.get("lng", 0.0) or 0.0),
                after_lat=float(pos_after.get("lat", 0.0) or 0.0),
                after_lng=float(pos_after.get("lng", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            prev_end = int(row.get("result", {}).get("simulation_progress_minutes", prev_end) or prev_end)
            continue

        memory.records.append(rec)
        if rec.action_name in {"take_order", "reposition"}:
            for day, seg_start, seg_end in iter_day_segments(rec.action_start, rec.action_end):
                memory.active_minutes_by_day[day] = memory.active_minutes_by_day.get(day, 0) + (seg_end - seg_start)
        if rec.action_name == "wait" and rec.action_exec_cost > 0:
            for day, seg_start, seg_end in iter_day_segments(rec.action_start, rec.action_end):
                memory.wait_intervals_by_day.setdefault(day, []).append((seg_start, seg_end))
        if rec.action_name == "take_order" and rec.result.get("accepted"):
            day = rec.action_start // DAY_MINUTES
            memory.accepted_orders_by_day[day] = memory.accepted_orders_by_day.get(day, 0) + 1
            memory.deadhead_km += float(rec.result.get("pickup_deadhead_km", 0.0) or 0.0)
        elif rec.action_name == "reposition":
            memory.deadhead_km += float(rec.result.get("distance_km", 0.0) or 0.0)

        prev_end = step_end
    return memory
