"""Rolling planner for the truck-driver cargo simulation.

Supports optional Qwen3.5-Flash model review for cargo ranking and
candidate selection. Falls back to deterministic logic on any failure.
"""

from __future__ import annotations

import logging
import os
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
    apply_qwen_hints,
    needs_rest_today,
    must_rest_today_proactive,
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
    """Rolling planner with optional Qwen3.5-Flash model review."""

    def __init__(self, api: SimulationApiPort) -> None:
        self._api = api
        self._logger = logging.getLogger("agent.planner")
        self._qwen = QwenFlashHelper(api)
        self._qwen_review_count = 0
        self._qwen_max_reviews = int(os.environ.get("AGENT_QWEN_MAX_REVIEWS", "100"))

    def decide(self, driver_id: str) -> dict[str, Any]:
        status = self._api.get_driver_status(driver_id)
        history = self._safe_history(driver_id)
        memory = build_memory(history)
        prefs_raw = list(status.get("preferences") or [])
        policy = parse_preferences(prefs_raw)

        qwen_hints = self._qwen.preference_hints(list(status.get("preferences") or []))
        if qwen_hints:
            policy = apply_qwen_hints(policy, qwen_hints)
            self._logger.info("applied %s preference hints driver=%s keys=%s", QWEN_FLASH_MODEL, driver_id, sorted(qwen_hints.keys()))

        urgent = self._urgent_action(status, memory, policy)
        if urgent is not None:
            self._logger.info("urgent decision driver=%s action=%s", driver_id, urgent)
            return urgent

        # 主动休息日前瞻：如果今天必须休息，直接 wait 到当天结束
        now_minute_check = int(status.get("simulation_progress_minutes", 0) or 0)
        rest_type = must_rest_today_proactive(policy, memory, now_minute_check)
        if rest_type is not None:
            wait_dur = max(30, day_end(now_minute_check) - now_minute_check)
            self._logger.info("proactive rest driver=%s type=%s wait=%d", driver_id, rest_type, wait_dur)
            return self._wait(wait_dur)

        lat = float(status["current_lat"])
        lng = float(status["current_lng"])
        cargo_resp = self._api.query_cargo(driver_id=driver_id, latitude=lat, longitude=lng)
        items = cargo_resp.get("items", [])
        if not isinstance(items, list):
            items = []

        # 记录市场观察
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("cargo"), dict):
                cargo = item["cargo"]
                try:
                    end = cargo.get("end", {})
                    price = float(cargo.get("price", 0) or 0)
                    start = cargo.get("start", {})
                    pickup_km = float(item.get("distance_km") or haversine_km(lat, lng, float(start.get("lat", 0)), float(start.get("lng", 0))))
                    haul_km = haversine_km(float(start.get("lat", 0)), float(start.get("lng", 0)), float(end.get("lat", 0)), float(end.get("lng", 0)))
                    est_net = price - (pickup_km + haul_km) * DEFAULT_COST_PER_KM
                    memory.record_market_observation(float(end.get("lat", 0)), float(end.get("lng", 0)), est_net, price)
                except (TypeError, ValueError):
                    pass

        # Querying cargo advances simulation time; refresh status before choosing action.
        status = self._api.get_driver_status(driver_id)
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        lat = float(status["current_lat"])
        lng = float(status["current_lng"])

        urgent_after_query = self._urgent_action(status, memory, policy)
        if urgent_after_query is not None:
            self._logger.info("post-query urgent decision driver=%s action=%s", driver_id, urgent_after_query)
            return urgent_after_query

        best_cargo = self._best_cargo_plan(driver_id, status, memory, policy, items)
        wait_candidate = self._wait_candidate(status, memory, policy, items, best_cargo)
        reposition_candidate = self._reposition_candidate(status, memory, policy, best_cargo)

        candidates = [c for c in (best_cargo, wait_candidate, reposition_candidate) if c is not None]
        if not candidates:
            return self._wait(60)

        # 确定性选择
        chosen = max(candidates, key=lambda c: c.score)

        # Qwen3.5-Flash 候选复审：当模型可用、还有复审额度、且候选分数接近时
        if self._qwen.enabled and self._qwen_review_count < self._qwen_max_reviews and len(candidates) > 1:
            scores = sorted([c.score for c in candidates], reverse=True)
            score_gap = scores[0] - scores[1] if len(scores) > 1 else 999
            has_high_risk = any(
                kw in (chosen.reason or "")
                for kw in ("required_cargo", "home_night", "family", "rest", "required_visit")
            )
            # 候选分数接近（<20%差距）且存在高风险偏好时，请求模型复审
            if score_gap < max(20, abs(scores[0]) * 0.2) and has_high_risk:
                context = {
                    "now_minute": now_minute,
                    "day": now_minute // DAY_MINUTES,
                    "rest_needed": needs_rest_today(policy, memory, now_minute),
                    "deadhead_km": memory.deadhead_km,
                    "high_risk": has_high_risk,
                }
                cand_dicts = [
                    {"action": c.action.get("action", ""), "params": c.action.get("params", {}),
                     "reason": c.reason, "score": c.score}
                    for c in candidates
                ]
                model_idx = self._qwen.suggest_decision(driver_id, status, cand_dicts, context)
                self._qwen_review_count += 1
                if model_idx is not None and 0 <= model_idx < len(candidates):
                    model_choice = candidates[model_idx]
                    # 安全检查：模型选择的候选分数不能太低（低于确定性选择的50%）
                    if model_choice.score >= chosen.score * 0.5:
                        self._logger.info(
                            "Qwen review: model chose %s (score=%.1f) over %s (score=%.1f) for driver=%s",
                            model_choice.reason, model_choice.score,
                            chosen.reason, chosen.score, driver_id,
                        )
                        chosen = model_choice
                    else:
                        self._logger.info(
                            "Qwen review: model choice rejected (score too low %.1f vs %.1f) for driver=%s",
                            model_choice.score, chosen.score, driver_id,
                        )
                else:
                    self._logger.debug("Qwen review: no valid choice for driver=%s, using deterministic", driver_id)

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
            # 更积极地安排必访点：月度前瞻
            days_remaining = (MONTH_HORIZON_MINUTES - now_minute) // DAY_MINUTES
            days_still_needed = visit.days_required - len(days)
            # 如果剩余天数紧张，更积极地安排
            urgent_visit = days_remaining <= days_still_needed + 3
            time_ok = minute_of_day(now_minute) < (14 * 60 if urgent_visit else 10 * 60)
            if time_ok:
                dist = haversine_km(lat, lng, visit.lat, visit.lng)
                if dist > visit.radius_km and dist <= 120 and self._active_allowed(policy, now_minute, now_minute + distance_to_minutes(dist)):
                    return {"action": "reposition", "params": {"latitude": visit.lat, "longitude": visit.lng}}

        # 连续休息前移：提前 3 小时检查，避免 query_cargo 切碎休息窗口
        rest_remaining = needs_rest_today(policy, memory, now_minute)
        if rest_remaining > 0:
            latest_start = DAY_MINUTES - policy.daily_rest_minutes
            mod = minute_of_day(now_minute)
            # 提前 3 小时开始休息，或没有好订单时提前 2 小时
            if mod >= latest_start - 180:
                duration = max(30, min(rest_remaining, day_end(now_minute) - now_minute))
                return self._wait(duration)
            # 如果当前正在休息中（最近动作是 wait >= 60 分钟），不打断
            if memory.records:
                last = memory.records[-1]
                if last.action_name == "wait" and last.action_exec_cost >= 60:
                    return self._wait(max(30, min(rest_remaining, day_end(now_minute) - now_minute)))
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

        # 如果已到家且在 stay_until 之前，等待
        if at_home and now_minute < family.stay_until_minute:
            return self._wait(max(1, family.stay_until_minute - now_minute))

        # 如果已过 stay_until，家事完成
        if now_minute >= family.stay_until_minute:
            return None

        # home_deadline 紧迫性检查：如果距 deadline 不足，立即回家
        if not at_home and family.home_deadline_minute > 0:
            dist_home = haversine_km(lat, lng, family.home_lat, family.home_lng)
            travel_home = distance_to_minutes(dist_home)
            time_to_deadline = family.home_deadline_minute - now_minute
            # 如果回家路上时间 + 30分钟缓冲 >= 距 deadline 时间，立即回家
            if time_to_deadline <= travel_home + 30 and not pickup_done:
                # 先去接人再回家已来不及，直接回家避免最大罚分
                return {"action": "reposition", "params": {"latitude": family.home_lat, "longitude": family.home_lng}}

        # 永远先接配偶（跳过会导致 9000 固定罚分，远比迟到罚分严重）
        if not pickup_done:
            if not at_pickup:
                return {"action": "reposition", "params": {"latitude": family.pickup_lat, "longitude": family.pickup_lng}}
            return self._wait(family.pickup_wait_minutes)

        # pickup 完成 → 回家
        if not at_home:
            return {"action": "reposition", "params": {"latitude": family.home_lat, "longitude": family.home_lng}}

        # 到家后等待到 stay_until
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
            # 在安静窗口内（23:00-08:00）
            if at_home:
                # 在家：等到安静窗口结束
                end = (now_minute - mod) + home.quiet_end_minute
                if mod >= home.quiet_start_minute:
                    end += DAY_MINUTES
                return self._wait(max(1, end - now_minute))
            # 不在家：等到安静窗口结束（不能 reposition，因为是安静窗口）
            end = (now_minute - mod) + home.quiet_end_minute
            if mod >= home.quiet_start_minute:
                end += DAY_MINUTES
            return self._wait(max(1, end - now_minute))
        # 不在安静窗口内
        dist = haversine_km(lat, lng, home.lat, home.lng)
        travel = distance_to_minutes(dist)
        deadline = (now_minute - mod) + home.deadline_minute_of_day
        time_to_deadline = deadline - now_minute
        # 动态缓冲：距离越远、时间越紧，越早出发
        buffer = max(60, travel // 3)
        latest_depart = deadline - travel - buffer
        if latest_depart < now_minute:
            latest_depart = now_minute  # 已经晚了，立即出发
        if not at_home and now_minute >= latest_depart:
            # 需要回家了
            if self._active_allowed(policy, now_minute, now_minute + travel):
                return {"action": "reposition", "params": {"latitude": home.lat, "longitude": home.lng}}
        # 18:00 后且不在家，主动回家（不等 latest_depart）
        if not at_home and mod >= 18 * 60 and time_to_deadline < travel + 180:
            if self._active_allowed(policy, now_minute, now_minute + travel):
                return {"action": "reposition", "params": {"latitude": home.lat, "longitude": home.lng}}
        return None

    def _best_cargo_plan(
        self,
        driver_id: str,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
        items: list[Any],
    ) -> Candidate | None:
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        current_lat = float(status["current_lat"])
        current_lng = float(status["current_lng"])
        truck_length = str(status.get("truck_length") or "").strip()

        # 指定熟货优先：搜索 items 中匹配 required_cargo.cargo_id 的货源。
        rc = policy.required_cargo
        if rc is not None and not memory.has_taken_cargo(rc.cargo_id):
            for item in items:
                cargo = item.get("cargo") if isinstance(item, dict) else None
                if isinstance(cargo, dict) and str(cargo.get("cargo_id", "")).strip() == rc.cargo_id:
                    plan = self._evaluate_cargo(item, now_minute, current_lat, current_lng, truck_length, memory, policy)
                    if plan is not None:
                        self._logger.info("required cargo %s found in items, taking unconditionally", rc.cargo_id)
                        return Candidate({"action": "take_order", "params": {"cargo_id": rc.cargo_id}}, 99999.0, "required_cargo")
                    else:
                        # TODO: 这里仍会强制接单，后续需区分硬约束失败和普通降权失败。
                        self._logger.info("required cargo %s found but evaluation failed, forcing take", rc.cargo_id)
                        return Candidate({"action": "take_order", "params": {"cargo_id": rc.cargo_id}}, 99999.0, "required_cargo_forced")

        best: CargoPlan | None = None
        evaluated_plans: list[tuple[Any, CargoPlan]] = []
        for item in items:
            plan = self._evaluate_cargo(item, now_minute, current_lat, current_lng, truck_length, memory, policy)
            if plan is None:
                continue
            evaluated_plans.append((item, plan))
            if best is None or plan.score > best.score:
                best = plan

        # Qwen3.5-Flash 货源评分融合 — 只在高风险或候选不确定时触发
        should_rank = False
        if self._qwen.enabled and evaluated_plans and self._qwen_review_count < self._qwen_max_reviews:
            # 高风险场景：home-night、家事、休息、必访点、熟货
            has_risk = (
                policy.home_night is not None
                or policy.family_task is not None
                or policy.daily_rest_minutes > 0
                or policy.required_visits
                or policy.required_cargo is not None
            )
            # 候选不确定：前两名分数接近
            if len(evaluated_plans) >= 2:
                scores_sorted = sorted([p.score for _, p in evaluated_plans], reverse=True)
                gap = scores_sorted[0] - scores_sorted[1]
                uncertain = gap < max(30, abs(scores_sorted[0]) * 0.25)
            else:
                uncertain = False
            should_rank = has_risk and uncertain
        if should_rank:
            constraints = {
                "forbidden_cargo": list(policy.forbidden_cargo_names),
                "soft_avoid_cargo": list(policy.soft_avoid_cargo_names),
                "max_haul_km": policy.max_haul_km,
                "max_pickup_km": policy.max_pickup_km,
                "daily_rest_minutes": policy.daily_rest_minutes,
                "deadhead_budget_remaining": (
                    max(0, policy.max_month_deadhead_km - memory.deadhead_km)
                    if policy.max_month_deadhead_km is not None else None
                ),
            }
            cargo_items = [item for item, _ in evaluated_plans]
            model_scores = self._qwen.rank_cargos(driver_id, status, cargo_items, constraints)
            self._qwen_review_count += 1  # 无论成功失败都计数，避免无限重试
            if model_scores:
                # 融合：alpha * model_score + (1-alpha) * det_score
                alpha = 0.35
                best_after_blend = None
                for item, plan in evaluated_plans:
                    model_s = model_scores.get(plan.cargo_id)
                    if model_s is not None:
                        # model_s 是 0-100，det_score 通常是 -500 到 500+
                        # 将 model_s 映射到 det_score 的量级
                        blended = alpha * (model_s * 5.0) + (1 - alpha) * plan.score
                        if best_after_blend is None or blended > best_after_blend[1]:
                            best_after_blend = (plan, blended)
                if best_after_blend is not None and best_after_blend[0].cargo_id != best.cargo_id:
                    self._logger.info(
                        "Qwen rank: model reranked cargo %s (%.0f) over %s (%.0f) for driver=%s",
                        best_after_blend[0].cargo_id, best_after_blend[1],
                        best.cargo_id, best.score, driver_id,
                    )
                    best = best_after_blend[0]
                    # 更新 score 为融合后的值
                    best = CargoPlan(
                        cargo_id=best.cargo_id, cargo_name=best.cargo_name,
                        price=best.price, pickup_km=best.pickup_km, haul_km=best.haul_km,
                        pickup_minutes=best.pickup_minutes, wait_minutes=best.wait_minutes,
                        duration_minutes=best.duration_minutes, finish_minutes=best.finish_minutes,
                        start_lat=best.start_lat, start_lng=best.start_lng,
                        end_lat=best.end_lat, end_lng=best.end_lng,
                        score=best_after_blend[1],
                    )

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

        # home_night 保障：接单+送货后必须能在当天23:00前到家
        score_penalty = 0.0
        home = policy.home_night
        if home is not None:
            today_base = now_minute - minute_of_day(now_minute)
            today_deadline = today_base + home.deadline_minute_of_day
            # 如果已过23:00，不接新单
            if now_minute >= today_deadline:
                return None
            # 从卸货点回家的时间
            dist_end_to_home = haversine_km(end_lat, end_lng, home.lat, home.lng)
            travel_end_to_home = distance_to_minutes(dist_end_to_home)
            arrive_home = finish + travel_end_to_home
            # 送货完成时间不能超过今天23:00前90分钟
            if finish > today_deadline - 90:
                return None
            # 必须能在今天23:00前到家
            if arrive_home > today_deadline:
                return None
            # 时间紧张度检查
            dist_to_home_now = haversine_km(current_lat, current_lng, home.lat, home.lng)
            travel_home_now = distance_to_minutes(dist_to_home_now)
            time_to_deadline = today_deadline - now_minute
            if time_to_deadline < travel_home_now + 90:
                return None
            # 20:00 后：只接极短单
            if minute_of_day(now_minute) >= 20 * 60:
                if finish + travel_end_to_home > today_deadline - 30:
                    return None

        # 休息保障：如果司机今天还需要连续休息，且接单会打断休息，拒绝
        if policy.daily_rest_minutes > 0:
            rest_remaining = needs_rest_today(policy, memory, now_minute)
            if rest_remaining > 0:
                remaining_today = day_end(now_minute) - finish
                if remaining_today < rest_remaining:
                    # 接单会侵占休息时间，严重降权
                    return None
                # 如果司机当前正在休息（最近一个动作是 wait 且已持续 >= 60 分钟），不打断
                if memory.records:
                    last = memory.records[-1]
                    if last.action_name == "wait" and last.action_exec_cost >= 60:
                        return None

        # 家事窗口保障：不接会延伸到家事窗口的单，也不在家事窗口内接单
        family = policy.family_task
        if family is not None:
            if family.start_minute <= now_minute < family.stay_until_minute:
                return None
            # 拒绝完成时间接近家事窗口开始的订单（预留60分钟缓冲）
            if finish > family.start_minute - 60:
                return None

        travel_cost = (pickup_km + haul_km) * DEFAULT_COST_PER_KM
        base_net = price - travel_cost
        total_minutes = max(1, finish - now_minute)
        net_per_hour = base_net / (total_minutes / 60.0)
        score = base_net + 0.25 * net_per_hour - pickup_km * 0.35 - wait_minutes * 0.08

        # Risk-Gated MPC: penalty_risk 估算 — 接单后是否还能满足硬约束
        penalty_risk = self._estimate_penalty_risk(
            finish, end_lat, end_lng, now_minute, policy, memory
        )
        if penalty_risk >= 500:
            return None  # 高罚分风险直接拒绝
        score -= penalty_risk

        # 目的地机会价值：根据 market_heat 给加分
        dest_bonus = 0.0
        best_areas = memory.best_market_areas(top_k=5)
        for area_lat, area_lng, area_net in best_areas:
            dest_dist = haversine_km(end_lat, end_lng, area_lat, area_lng)
            if dest_dist < 100:
                dest_bonus += area_net * 0.1 * max(0, 1 - dest_dist / 100)
        score += dest_bonus

        # 月底风险：月底接长单降权
        days_left = (MONTH_HORIZON_MINUTES - finish) / DAY_MINUTES
        if days_left < 3 and duration > 600:
            score -= (3 - days_left) * 100

        if cargo_name in policy.soft_avoid_cargo_names:
            score -= 450.0
        if pickup_km > 80:
            score -= (pickup_km - 80) * 2.0
        if finish // DAY_MINUTES != now_minute // DAY_MINUTES:
            score -= 50.0
        if base_net <= 0:
            score -= 200.0
        score -= score_penalty
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

    def _estimate_penalty_risk(
        self,
        finish_minute: int,
        end_lat: float,
        end_lng: float,
        now_minute: int,
        policy: PreferencePolicy,
        memory: DriverMemory,
    ) -> float:
        """Risk-Gated MPC: 估算接单后的罚分风险。

        检查接单完成后是否还能满足 home-night、家事、休息等硬约束。
        返回估算的罚分风险值（>= 500 表示应直接拒绝）。
        """
        risk = 0.0

        # 1. Home-night 风险：完单后能否在当天 23:00 前到家
        home = policy.home_night
        if home is not None:
            today_base = now_minute - minute_of_day(now_minute)
            today_deadline = today_base + home.deadline_minute_of_day
            if finish_minute < today_deadline:
                dist_end_to_home = haversine_km(end_lat, end_lng, home.lat, home.lng)
                travel_end_to_home = distance_to_minutes(dist_end_to_home)
                arrive_home = finish_minute + travel_end_to_home
                if arrive_home > today_deadline:
                    risk += 300  # 高风险：赶不回家
                elif arrive_home > today_deadline - 60:
                    risk += 150  # 中风险：非常紧张
            # 如果完单跨天，检查明天是否能按时回家
            elif finish_minute // DAY_MINUTES > now_minute // DAY_MINUTES:
                risk += 50

        # 2. 家事窗口风险：接单是否会侵占家事窗口
        family = policy.family_task
        if family is not None:
            if finish_minute > family.start_minute - 30:
                risk += 400  # 接单完成时间接近家事开始
            if now_minute < family.start_minute and finish_minute > family.start_minute:
                risk += 500  # 接单会跨越家事窗口开始

        # 3. 休息风险：接单后今天是否还有足够连续休息时间
        if policy.daily_rest_minutes > 0:
            remaining_today = day_end(now_minute) - finish_minute
            if remaining_today < policy.daily_rest_minutes:
                risk += 200  # 休息时间不足

        # 4. 必访点风险：接单后是否影响必访点安排
        for visit in policy.required_visits:
            days = memory.visit_days(visit.lat, visit.lng, visit.radius_km)
            if len(days) >= visit.days_required:
                continue
            days_remaining = (MONTH_HORIZON_MINUTES - finish_minute) // DAY_MINUTES
            days_still_needed = visit.days_required - len(days)
            if days_remaining <= days_still_needed + 1:
                # 时间紧张，接远单可能耽误必访
                dist_visit = haversine_km(end_lat, end_lng, visit.lat, visit.lng)
                if dist_visit > 80:
                    risk += 100

        return risk

    def _wait_candidate(
        self,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
        items: list[Any],
        best_cargo: Candidate | None = None,
    ) -> Candidate | None:
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        rest_remaining = needs_rest_today(policy, memory, now_minute)
        if rest_remaining > 0:
            # 休息紧迫度：剩余时间越少，分数越高
            latest_start = DAY_MINUTES - policy.daily_rest_minutes
            urgency = max(0, minute_of_day(now_minute) - latest_start + 60)
            rest_score = 500.0 + rest_remaining * 0.5 + urgency * 0.3
            # 更积极地触发休息：提前3小时，或没有好订单时
            has_good_cargo = best_cargo is not None and best_cargo.score > 100
            if minute_of_day(now_minute) >= latest_start - 180 or (not has_good_cargo and rest_remaining > 60):
                duration = max(30, min(rest_remaining, day_end(now_minute) - now_minute))
                return Candidate(self._wait(duration), rest_score, "rest")
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
        now_minute = int(status.get("simulation_progress_minutes", 0) or 0)
        lat = float(status["current_lat"])
        lng = float(status["current_lng"])

        # 如果有高质量订单，不做空驶
        if best_cargo is not None and best_cargo.score > 100:
            return None

        # 限制空驶频率：至少间隔 4 小时
        if now_minute - memory.last_reposition_minute < 4 * 60:
            return None

        # 检查安静窗口
        quiet_end = quiet_window_end_if_inside(policy.quiet_windows, now_minute)
        if quiet_end is not None:
            return None

        # 查找最佳市场区域
        best_areas = memory.best_market_areas(top_k=3)
        if not best_areas:
            return None

        for area_lat, area_lng, area_net in best_areas:
            dist = haversine_km(lat, lng, area_lat, area_lng)
            if dist < 20:
                continue  # 已经在附近
            move_cost = dist * DEFAULT_COST_PER_KM
            move_minutes = distance_to_minutes(dist)
            # 月度空驶限额检查
            if policy.max_month_deadhead_km is not None and memory.deadhead_km + dist > policy.max_month_deadhead_km:
                continue
            # 空驶必须有足够回报：预期净收益 > 空驶成本 * 1.5
            if area_net * 1.5 < move_cost:
                continue
            # 检查时间是否允许
            if not self._active_allowed(policy, now_minute, now_minute + move_minutes):
                continue
            # 检查是否越界
            if not policy.point_allowed(area_lat, area_lng):
                continue
            score = area_net - move_cost - move_minutes * 0.1
            return Candidate(
                {"action": "reposition", "params": {"latitude": area_lat, "longitude": area_lng}},
                score,
                f"reposition_to_market(net={area_net:.0f},dist={dist:.0f})",
            )
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
