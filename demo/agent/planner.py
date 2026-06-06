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
    minutes_to_wall_time,
    wall_time_to_minutes,
)
from agent.llm_helper import QWEN_MODEL, QwenFlashHelper
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
    model_context: dict[str, Any] | None = None


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
        self._qwen_review_counts_by_driver: dict[str, int] = {}
        self._qwen_max_reviews = max(0, int(os.environ.get("AGENT_QWEN_MAX_REVIEWS", "25")))
        self._qwen_max_reviews_per_driver = max(
            0, int(os.environ.get("AGENT_QWEN_MAX_REVIEWS_PER_DRIVER", "5"))
        )
        self._qwen_last_call_step_by_driver: dict[str, int] = {}
        self._qwen_ranked_drivers: set[str] = set()
        self._qwen_rank_count = 0
        self._qwen_max_ranks = max(0, int(os.environ.get("AGENT_QWEN_MAX_RANKS", "10")))
        self._qwen_rank_gap_ratio = float(os.environ.get("AGENT_QWEN_RANK_MAX_GAP_RATIO", "0.25"))
        self._qwen_suggest_gap_ratio = float(os.environ.get("AGENT_QWEN_SUGGEST_MAX_GAP_RATIO", "0.25"))
        self._step_counter = 0

    def decide(self, driver_id: str) -> dict[str, Any]:
        self._step_counter += 1
        status = self._api.get_driver_status(driver_id)
        history = self._safe_history(driver_id)
        memory = build_memory(history)
        prefs_raw = list(status.get("preferences") or [])
        policy = parse_preferences(prefs_raw)

        # Qwen hints 只在没有未完成的 required_cargo 时应用，避免改变行为导致错过熟货
        if policy.required_cargo is None or memory.has_taken_cargo(policy.required_cargo.cargo_id):
            qwen_hints = self._qwen.preference_hints(list(status.get("preferences") or []))
            if qwen_hints:
                policy = apply_qwen_hints(policy, qwen_hints)
                self._logger.info("applied %s preference hints driver=%s keys=%s", QWEN_MODEL, driver_id, sorted(qwen_hints.keys()))

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

        # One model call per decision. Risk verification takes precedence over
        # optional action selection, and each driver gets an independent quota.
        if self._qwen_review_available(driver_id, cooldown_steps=5):
            scenario = self._detect_risk_scenario(policy, memory, chosen, now_minute)
            if scenario is not None:
                verify_ctx = self._build_verification_context(
                    scenario, status, memory, policy, chosen, now_minute, lat, lng,
                )
                verification = self._qwen.verify_constraints(driver_id, scenario, verify_ctx)
                self._record_qwen_review(driver_id, "verify_constraints")
                if verification is not None and not verification.get("safe", True):
                    self._logger.warning(
                        "Qwen verification: UNSAFE driver=%s scenario=%s risk=%s concern=%s",
                        driver_id, scenario, verification.get("risk"),
                        verification.get("concern"),
                    )
                    adjusted = self._apply_verification_feedback(
                        verification, candidates, chosen, scenario, policy, memory, now_minute,
                    )
                    if adjusted is not None:
                        chosen = adjusted
                        self._logger.info("Qwen verification: adjusted to %s", chosen.reason)
                else:
                    self._logger.debug(
                        "Qwen verification: safe driver=%s scenario=%s risk=%s",
                        driver_id, scenario, verification.get("risk") if verification else "n/a",
                    )
            elif len(candidates) > 1 and self._candidate_gap_ratio(candidates) <= self._qwen_suggest_gap_ratio:
                suggest_ctx = {
                    "now_minute": now_minute,
                    "rest_needed": needs_rest_today(policy, memory, now_minute),
                    "has_home_night": policy.home_night is not None,
                    "has_family": policy.family_task is not None,
                    "score_gap_ratio": round(self._candidate_gap_ratio(candidates), 4),
                }
                cand_dicts = [
                    {
                        "action": c.action.get("action", ""),
                        "params": c.action.get("params", {}),
                        "reason": c.reason,
                        **(c.model_context or {}),
                    }
                    for c in candidates
                ]
                model_idx = self._qwen.suggest_decision(driver_id, status, cand_dicts, suggest_ctx)
                self._record_qwen_review(driver_id, "suggest_decision")
                if model_idx is not None and 0 <= model_idx < len(candidates):
                    model_choice = candidates[model_idx]
                    if self._score_gap_ratio(chosen.score, model_choice.score) <= self._qwen_suggest_gap_ratio:
                        self._logger.info(
                            "Qwen suggest: model chose %s (score=%.1f) over %s (score=%.1f) for driver=%s",
                            model_choice.reason, model_choice.score,
                            chosen.reason, chosen.score, driver_id,
                        )
                        chosen = model_choice
                    else:
                        self._logger.info(
                            "Qwen suggest: rejected (score too low %.1f vs %.1f)",
                            model_choice.score, chosen.score,
                        )

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
        if family is not None:
            # 家事窗口内或即将进入窗口（提前 30 分钟），直接进入家事模式
            if family.start_minute - 30 <= now_minute < family.stay_until_minute:
                action = self._family_action(family, memory, now_minute, lat, lng)
                if action is not None:
                    return action
            # 家事窗口 6 小时内：强制前往接人点（不再接任何新单）
            if now_minute < family.start_minute and now_minute >= family.start_minute - 6 * 60:
                dist_to_pickup = haversine_km(lat, lng, family.pickup_lat, family.pickup_lng)
                if dist_to_pickup > family.radius_km:
                    return {"action": "reposition", "params": {"latitude": family.pickup_lat, "longitude": family.pickup_lng}}
            # 家事窗口 48 小时内：如果赶路时间紧张，提前前往
            if now_minute < family.start_minute and now_minute >= family.start_minute - 48 * 60:
                dist_to_pickup = haversine_km(lat, lng, family.pickup_lat, family.pickup_lng)
                travel_to_pickup = distance_to_minutes(dist_to_pickup)
                time_remaining = family.start_minute - now_minute
                # 如果赶路时间 + 接人等待超过剩余时间的 60%，立即前往
                if travel_to_pickup + family.pickup_wait_minutes > time_remaining * 0.6:
                    return {"action": "reposition", "params": {"latitude": family.pickup_lat, "longitude": family.pickup_lng}}

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

        # 连续休息前移：根据休息需求动态调整预触发窗口
        # 休息需求越长，预触发越早（避免长休息被 cargo 切碎）
        rest_remaining = needs_rest_today(policy, memory, now_minute)
        if rest_remaining > 0:
            rest_minutes = int(policy.daily_rest_minutes or 0)
            # Pre-trigger = max(4h, rest_minutes) + rest debt from yesterday.
            # If yesterday's longest rest was insufficient, increase urgency today
            # to prevent consecutive violations from accumulating.
            pre_trigger = max(240, rest_minutes)

            # Early-morning rest continuation: if the last action was a
            # substantial wait extending to or past midnight, keep resting
            if minute_of_day(now_minute) < pre_trigger and memory.records:
                last = memory.records[-1]
                if last.action_name == "wait" and last.action_exec_cost >= 60:
                    if abs(last.step_end - now_minute) <= 10:
                        return self._wait(max(60, min(policy.daily_rest_minutes, day_end(now_minute) - now_minute)))

            latest_start = self._latest_rest_start(policy, now_minute)
            mod = minute_of_day(now_minute)
            if mod >= latest_start - pre_trigger:
                duration = max(60, min(policy.daily_rest_minutes, day_end(now_minute) - now_minute))
                return self._wait(duration)

            # 硬截止：当天剩余时间不足以完成所需连续休息时，立即开始休息
            # 这是最后防线，防止 cargo 查询消耗时间导致错过休息窗口
            today_remain = max(0, day_end(now_minute) - now_minute)
            if today_remain <= rest_minutes + 30 and rest_remaining >= rest_minutes * 0.5:
                duration = max(60, min(policy.daily_rest_minutes, today_remain))
                return self._wait(duration)

            # 如果当前正在休息中（最近动作是 wait >= 60 分钟），不打断
            if memory.records:
                last = memory.records[-1]
                if last.action_name == "wait" and last.action_exec_cost >= 60:
                    return self._wait(max(60, min(policy.daily_rest_minutes, today_remain)))
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

        # 如果已到家且在 stay_until 之前，等待（这是硬约束，必须等到 stay_until）
        if at_home and now_minute < family.stay_until_minute:
            return self._wait(max(60, family.stay_until_minute - now_minute))

        # 如果已过 stay_until，家事完成
        if now_minute >= family.stay_until_minute:
            return None

        # home_deadline 紧迫性检查：接到人后如果 deadline 紧张，立即回家。
        # 未接配偶时仍必须先接人，否则会触发更高的固定罚分。
        if pickup_done and not at_home and family.home_deadline_minute > 0:
            dist_home = haversine_km(lat, lng, family.home_lat, family.home_lng)
            travel_home = distance_to_minutes(dist_home)
            time_to_deadline = family.home_deadline_minute - now_minute
            if time_to_deadline <= travel_home + 60:
                return {"action": "reposition", "params": {"latitude": family.home_lat, "longitude": family.home_lng}}

        # 永远先接配偶（跳过会导致 9000 固定罚分，远比迟到罚分严重）
        if not pickup_done:
            if not at_pickup:
                return {"action": "reposition", "params": {"latitude": family.pickup_lat, "longitude": family.pickup_lng}}
            return self._wait(family.pickup_wait_minutes)

        # pickup 完成 → 回家
        if not at_home:
            return {"action": "reposition", "params": {"latitude": family.home_lat, "longitude": family.home_lng}}

        # 到家后等待到 stay_until（硬约束，必须等到）
        if now_minute < family.stay_until_minute:
            return self._wait(max(60, family.stay_until_minute - now_minute))
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
        # 15:00 后：如果当前位置距家很远（行驶时间 > 剩余时间的 50%），主动回家
        if not at_home and mod >= 15 * 60 and time_to_deadline > 0:
            if travel > time_to_deadline * 0.5:
                if self._active_allowed(policy, now_minute, now_minute + travel):
                    return {"action": "reposition", "params": {"latitude": home.lat, "longitude": home.lng}}
        # 17:00 后：只要不在家且行驶时间 > 30 分钟，立即回家
        if not at_home and mod >= 17 * 60 and travel > 30:
            if self._active_allowed(policy, now_minute, now_minute + travel):
                return {"action": "reposition", "params": {"latitude": home.lat, "longitude": home.lng}}
        # 19:00 后：只要不在家，立即回家
        if not at_home and mod >= 19 * 60:
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

        ranked_plans = sorted(evaluated_plans, key=lambda pair: pair[1].score, reverse=True)
        rank_gap = (
            self._score_gap_ratio(ranked_plans[0][1].score, ranked_plans[1][1].score)
            if len(ranked_plans) >= 2 else 1.0
        )
        should_rank = (
            len(ranked_plans) >= 2
            and rank_gap <= self._qwen_rank_gap_ratio
            and not self._verification_has_priority(policy, memory, now_minute)
            and driver_id not in self._qwen_ranked_drivers
            and self._qwen_rank_count < self._qwen_max_ranks
            and self._qwen_review_available(driver_id, cooldown_steps=10)
        )
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
            top_ranked_plans = ranked_plans[:3]
            cargo_items = [
                self._cargo_for_qwen(item, plan, now_minute)
                for item, plan in top_ranked_plans
            ]
            model_scores = self._qwen.rank_cargos(driver_id, status, cargo_items, constraints)
            self._record_qwen_review(driver_id, "rank_cargos")
            self._qwen_ranked_drivers.add(driver_id)
            self._qwen_rank_count += 1
            if model_scores:
                alpha = 0.35
                blended_plans: list[tuple[CargoPlan, float]] = []
                for _, plan in top_ranked_plans:
                    model_s = model_scores.get(plan.cargo_id)
                    if model_s is not None:
                        blended = alpha * (model_s * 5.0) + (1 - alpha) * plan.score
                        blended_plans.append((plan, blended))
                baseline_blend = next(
                    (score for plan, score in blended_plans if plan.cargo_id == best.cargo_id),
                    None,
                )
                best_after_blend = max(blended_plans, key=lambda pair: pair[1]) if blended_plans else None
                if (
                    baseline_blend is not None
                    and best_after_blend is not None
                    and best_after_blend[1] > baseline_blend
                    and best_after_blend[0].cargo_id != best.cargo_id
                ):
                    self._logger.info(
                        "Qwen rank: model reranked cargo %s (%.0f) over %s (%.0f) for driver=%s",
                        best_after_blend[0].cargo_id, best_after_blend[1],
                        best.cargo_id, baseline_blend, driver_id,
                    )
                    best = best_after_blend[0]
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
        estimated_net = best.price - (best.pickup_km + best.haul_km) * DEFAULT_COST_PER_KM
        estimated_total_minutes = max(1, best.finish_minutes - now_minute)
        return Candidate(
            {"action": "take_order", "params": {"cargo_id": best.cargo_id}},
            best.score,
            "best_cargo",
            {
                "estimated_net": round(estimated_net, 2),
                "estimated_minutes": estimated_total_minutes,
                "pickup_km": round(best.pickup_km, 2),
                "haul_km": round(best.haul_km, 2),
                "end_lat": best.end_lat,
                "end_lng": best.end_lng,
            },
        )

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
        is_required_cargo = self._is_required_cargo(policy, memory, cargo_id)
        if home is not None:
            today_base = now_minute - minute_of_day(now_minute)
            today_deadline = today_base + home.deadline_minute_of_day
            # 如果已过23:00，不接新单
            if now_minute >= today_deadline:
                return None
            # 只有指定必接货源允许跨过当天 deadline：这是用可控 home-night 罚分换
            # 高额熟货损失的显式策略，普通订单必须当天 23:00 前回家。
            effective_deadline = today_deadline
            if finish > today_deadline:
                if not is_required_cargo:
                    return None
                tomorrow_base = today_base + DAY_MINUTES
                effective_deadline = tomorrow_base + home.deadline_minute_of_day
                if finish > effective_deadline:
                    return None
            # 从卸货点回家的时间
            dist_end_to_home = haversine_km(end_lat, end_lng, home.lat, home.lng)
            travel_end_to_home = distance_to_minutes(dist_end_to_home)
            arrive_home = finish + travel_end_to_home
            # 必须能在 deadline 前到家（含 60 分钟缓冲）
            if arrive_home > effective_deadline - 60:
                return None
            # 时间紧张度检查：当前距 deadline 不够回家 + 缓冲
            dist_to_home_now = haversine_km(current_lat, current_lng, home.lat, home.lng)
            travel_home_now = distance_to_minutes(dist_to_home_now)
            time_to_deadline = today_deadline - now_minute  # today's deadline for time-of-day checks
            if time_to_deadline < travel_home_now + 120:
                return None
            # 16:00 后：卸货点必须距家 60km 以内，且 finish 不晚于 deadline 前 2 小时
            mod = minute_of_day(now_minute)
            if mod >= 16 * 60:
                if dist_end_to_home > 60 or finish > today_deadline - 120:
                    return None
            # 18:00 后：卸货点必须距家 30km 以内，且 finish 不晚于 deadline 前 90 分钟
            if mod >= 18 * 60:
                if dist_end_to_home > 30 or finish > today_deadline - 90:
                    return None
            # 20:00 后：不接任何新单
            if mod >= 20 * 60:
                return None

        # 休息保障：如果司机今天还需要连续休息，且接单会打断休息，拒绝
        if policy.daily_rest_minutes > 0:
            rest_remaining = needs_rest_today(policy, memory, now_minute)
            if rest_remaining > 0:
                latest_rest_start = self._latest_rest_start(policy, now_minute)
                # 硬性截止：已过最晚休息开始时间，不再接单（紧急货物除外）
                if minute_of_day(now_minute) >= latest_rest_start:
                    if not is_required_cargo:
                        return None
                # 计算接单后到当天结束的可用时间（含回家时间）
                travel_home_after = 0
                if home is not None:
                    travel_home_after = distance_to_minutes(haversine_km(end_lat, end_lng, home.lat, home.lng))
                effective_finish = finish + travel_home_after
                remaining_today = day_end(now_minute) - effective_finish
                # 确保 cargo 完成后有足够时间完成完整连续休息块（含 90min 缓冲）
                if remaining_today < policy.daily_rest_minutes + 30:
                    return None
                # 如果接单完成时间太晚（在休息开始时间之后），拒绝
                if minute_of_day(effective_finish) >= latest_rest_start:
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
            # 通用保障：拒绝任何完成时间接近家事窗口的订单（不论何时开始）
            if now_minute < family.start_minute:
                dist_end_to_pickup = haversine_km(end_lat, end_lng, family.pickup_lat, family.pickup_lng)
                travel_end_to_pickup = distance_to_minutes(dist_end_to_pickup)
                # 如果完成 + 赶到接人点的时间超过家事开始前 2 小时，拒绝
                if finish + travel_end_to_pickup > family.start_minute - 120:
                    return None
            # 家事窗口前：确保能按时赶到接人点
            if now_minute < family.start_minute:
                # 从卸货点到接人点的赶路时间
                dist_end_to_pickup = haversine_km(end_lat, end_lng, family.pickup_lat, family.pickup_lng)
                travel_end_to_pickup = distance_to_minutes(dist_end_to_pickup)
                # 必须在窗口开始前有足够时间赶到接人点（含 60 分钟缓冲）
                if finish + travel_end_to_pickup > family.start_minute - 60:
                    return None
                # 家事窗口当天（从 0:00 开始）：从当前位置到接人点的赶路时间
                family_day_start = (family.start_minute // DAY_MINUTES) * DAY_MINUTES
                if now_minute >= family_day_start:
                    dist_to_pickup = haversine_km(current_lat, current_lng, family.pickup_lat, family.pickup_lng)
                    travel_to_pickup = distance_to_minutes(dist_to_pickup)
                    if now_minute + travel_to_pickup > family.start_minute - 30:
                        return None

        travel_cost = (pickup_km + haul_km) * DEFAULT_COST_PER_KM
        base_net = price - travel_cost
        total_minutes = max(1, finish - now_minute)
        net_per_hour = base_net / (total_minutes / 60.0)
        score = base_net + 0.5 * net_per_hour - pickup_km * 0.35 - wait_minutes * 0.08

        # Rest risk discount: when rest is needed, deprioritize cargos that
        # finish close to latest_rest_start (they risk fragmenting rest).
        if policy.daily_rest_minutes > 0 and needs_rest_today(policy, memory, now_minute) > 0:
            latest_rs = self._latest_rest_start(policy, now_minute)
            finish_mod = minute_of_day(finish)
            if finish_mod >= latest_rs - 120:
                # Cargo finishes within 2h of latest rest start — apply risk discount
                rest_risk = (finish_mod - (latest_rs - 60)) * 0.1
                score -= rest_risk

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
            # 休息紧迫度：使用安静窗口开始时间（如果有）来计算最晚休息开始
            latest_start = self._latest_rest_start(policy, now_minute)
            urgency = max(0, minute_of_day(now_minute) - latest_start + 60)
            rest_score = 600.0 + rest_remaining * 0.5 + urgency * 0.4
            # 更积极地触发休息：提前4小时，或没有好订单时提前3小时
            has_good_cargo = best_cargo is not None and best_cargo.score > 100
            if minute_of_day(now_minute) >= latest_start - 240 or (not has_good_cargo and rest_remaining > 60):
                duration = max(60, min(rest_remaining, day_end(now_minute) - now_minute))
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
            # home_night 限制：空驶目标必须在当天能返回 home 的范围内
            if policy.home_night is not None:
                mod = minute_of_day(now_minute)
                remaining_hours = max(0, (17 * 60 - mod) / 60.0) if mod < 17 * 60 else 0
                max_reposition_km = remaining_hours * 60 * 0.4  # 预留 60% 时间给回家
                if dist > max_reposition_km:
                    continue
                # 14:00 后不做远距离空驶
                if mod >= 14 * 60 and dist > 50:
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
    def _is_required_cargo(policy: PreferencePolicy, memory: DriverMemory, cargo_id: str) -> bool:
        rc = policy.required_cargo
        return rc is not None and not memory.has_taken_cargo(rc.cargo_id) and str(cargo_id) == str(rc.cargo_id)

    @staticmethod
    def _latest_rest_start(policy: PreferencePolicy, now_minute: int) -> int:
        """Compute the latest time-of-day (minute within day) to start resting.

        Late overnight quiet windows can split a wait across midnight in the
        scorer's per-day rest calculation, so start before those windows when
        possible.  Early-morning/lunch quiet windows should not force a negative
        or overly early cutoff.
        """
        rest_minutes = int(policy.daily_rest_minutes or 0)
        if rest_minutes <= 0:
            return DAY_MINUTES
        latest_start = max(0, DAY_MINUTES - rest_minutes)
        late_overnight_starts: list[int] = []
        for window in policy.quiet_windows:
            if window.end_minute <= window.start_minute and window.start_minute >= 18 * 60:
                candidate = window.start_minute - rest_minutes
                if candidate >= 0:
                    late_overnight_starts.append(candidate)
        if late_overnight_starts:
            latest_start = min(latest_start, min(late_overnight_starts))
        return max(0, latest_start)

    # ------------------------------------------------------------------
    # Qwen constraint detection (post-decision)
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_risk_scenario(
        policy: PreferencePolicy,
        memory: DriverMemory,
        chosen: Candidate,
        now_minute: int,
    ) -> str | None:
        """Return the risk scenario name if the decision involves a constraint."""
        reason = chosen.reason or ""
        mod = minute_of_day(now_minute)

        # Temporary family deadlines have the largest penalty and must not be
        # hidden behind the ordinary daily-rest scenario.
        if policy.family_task is not None and now_minute >= policy.family_task.start_minute - 48 * 60:
            return "family"

        # Home-night: only trigger in afternoon/evening when time pressure exists
        if policy.home_night is not None and mod >= 15 * 60:
            if chosen.action.get("action") == "take_order":
                return "home_night"

        # Rest: trigger when rest is still needed today
        rest_needed = needs_rest_today(policy, memory, now_minute)
        if rest_needed > 0 and policy.daily_rest_minutes > 0:
            return "rest"

        return None

    def _build_verification_context(
        self,
        scenario: str,
        status: dict[str, Any],
        memory: DriverMemory,
        policy: PreferencePolicy,
        chosen: Candidate,
        now_minute: int,
        lat: float,
        lng: float,
    ) -> dict[str, Any]:
        """Build scenario-specific context dict for Qwen verification."""
        ctx: dict[str, Any] = {
            "wall_time": minutes_to_wall_time(now_minute),
            "lat": lat,
            "lng": lng,
            "action_type": chosen.action.get("action", "?"),
            "action_duration": 0,
            "finish_time": "?",
            "end_lat": lat,
            "end_lng": lng,
        }

        # Estimate action duration
        if chosen.action.get("action") == "take_order":
            model_ctx = chosen.model_context or {}
            ctx["action_duration"] = int(model_ctx.get("estimated_minutes", 60) or 60)
            ctx["finish_time"] = minutes_to_wall_time(now_minute + ctx["action_duration"])
            ctx["end_lat"] = float(model_ctx.get("end_lat", lat))
            ctx["end_lng"] = float(model_ctx.get("end_lng", lng))
        elif chosen.action.get("action") == "wait":
            ctx["action_duration"] = chosen.action.get("params", {}).get("duration_minutes", 60)
            ctx["finish_time"] = minutes_to_wall_time(now_minute + ctx["action_duration"])
        elif chosen.action.get("action") == "reposition":
            rp = chosen.action.get("params", {})
            ctx["end_lat"] = float(rp.get("latitude", lat))
            ctx["end_lng"] = float(rp.get("longitude", lng))
            ctx["action_duration"] = distance_to_minutes(
                haversine_km(lat, lng, ctx["end_lat"], ctx["end_lng"])
            )
            ctx["finish_time"] = minutes_to_wall_time(now_minute + ctx["action_duration"])

        if scenario == "home_night" and policy.home_night is not None:
            hn = policy.home_night
            ctx["home_lat"] = hn.home_lat
            ctx["home_lng"] = hn.home_lng
            ctx["dist_home_km"] = haversine_km(lat, lng, hn.home_lat, hn.home_lng)
            ctx["travel_home_min"] = distance_to_minutes(ctx["dist_home_km"])
            ctx["end_to_home_km"] = haversine_km(ctx["end_lat"], ctx["end_lng"], hn.home_lat, hn.home_lng)
            ctx["end_to_home_min"] = distance_to_minutes(ctx["end_to_home_km"])
            deadline = now_minute - minute_of_day(now_minute) + hn.deadline_minute_of_day
            if deadline <= now_minute:
                deadline += DAY_MINUTES
            ctx["deadline_time"] = minutes_to_wall_time(deadline)
            ctx["time_to_deadline"] = deadline - now_minute

        elif scenario == "rest":
            rest_hours = int(policy.daily_rest_minutes or 0) / 60.0
            ctx["rest_hours"] = rest_hours
            ctx["rested_today"] = memory.longest_rest_today(now_minute)
            ctx["rest_remaining"] = max(0, int(policy.daily_rest_minutes or 0) - ctx["rested_today"])
            ctx["minutes_left_today"] = day_end(now_minute) - now_minute
            ctx["after_finish"] = day_end(now_minute) - (now_minute + ctx["action_duration"])

        elif scenario == "family" and policy.family_task is not None:
            ft = policy.family_task
            ctx["family_start"] = minutes_to_wall_time(ft.start_minute)
            ctx["family_deadline"] = minutes_to_wall_time(ft.home_deadline_minute) if ft.home_deadline_minute > 0 else "?"
            ctx["stay_until"] = minutes_to_wall_time(ft.stay_until_minute)
            ctx["end_to_pickup_km"] = haversine_km(ctx["end_lat"], ctx["end_lng"], ft.pickup_lat, ft.pickup_lng)
            ctx["end_to_pickup_min"] = distance_to_minutes(ctx["end_to_pickup_km"])
            ctx["time_to_start"] = ft.start_minute - now_minute
            ctx["time_to_deadline"] = (ft.home_deadline_minute - now_minute) if ft.home_deadline_minute > 0 else 9999

        return ctx

    @staticmethod
    def _apply_verification_feedback(
        verification: dict[str, Any],
        candidates: list[Candidate],
        chosen: Candidate,
        scenario: str,
        policy: PreferencePolicy,
        memory: DriverMemory,
        now_minute: int,
    ) -> Candidate | None:
        """When Qwen says unsafe, choose the safest fallback action."""
        risk = str(verification.get("risk", "low")).lower()

        if risk not in ("medium", "high"):
            return None  # Only act on medium/high risk

        # A generic market reposition is not necessarily a trip home. Keep the
        # deterministic action unless a future candidate is explicitly marked.
        if scenario == "home_night":
            for c in candidates:
                if c.action.get("params", {}).get("_qwen_safe_for") == "home_night":
                    return c
            return None

        if scenario == "rest":
            for c in candidates:
                if c.action.get("action") == "wait" and c.reason == "rest":
                    return c
            return None

        if scenario == "family":
            for c in candidates:
                if c.action.get("params", {}).get("_qwen_safe_for") == "family":
                    return c
            return None

        return None

    def _qwen_review_available(self, driver_id: str, *, cooldown_steps: int) -> bool:
        if not self._qwen.enabled or self._qwen_review_count >= self._qwen_max_reviews:
            return False
        driver_count = self._qwen_review_counts_by_driver.get(driver_id, 0)
        if driver_count >= self._qwen_max_reviews_per_driver:
            return False
        last_step = self._qwen_last_call_step_by_driver.get(driver_id, -999)
        return self._step_counter - last_step >= cooldown_steps

    @staticmethod
    def _verification_has_priority(
        policy: PreferencePolicy,
        memory: DriverMemory,
        now_minute: int,
    ) -> bool:
        if policy.family_task is not None and now_minute >= policy.family_task.start_minute - 48 * 60:
            return True
        if policy.home_night is not None and minute_of_day(now_minute) >= 15 * 60:
            return True
        return policy.daily_rest_minutes > 0 and needs_rest_today(policy, memory, now_minute) > 0

    def _record_qwen_review(self, driver_id: str, review_type: str) -> None:
        self._qwen_review_count += 1
        self._qwen_review_counts_by_driver[driver_id] = (
            self._qwen_review_counts_by_driver.get(driver_id, 0) + 1
        )
        self._qwen_last_call_step_by_driver[driver_id] = self._step_counter
        self._logger.info(
            "Qwen review recorded driver=%s type=%s driver_count=%s total_count=%s",
            driver_id,
            review_type,
            self._qwen_review_counts_by_driver[driver_id],
            self._qwen_review_count,
        )

    @staticmethod
    def _score_gap_ratio(best_score: float, other_score: float) -> float:
        return max(0.0, best_score - other_score) / max(1.0, abs(best_score))

    @classmethod
    def _candidate_gap_ratio(cls, candidates: list[Candidate]) -> float:
        if len(candidates) < 2:
            return 1.0
        ordered = sorted((candidate.score for candidate in candidates), reverse=True)
        return cls._score_gap_ratio(ordered[0], ordered[1])

    @staticmethod
    def _cargo_for_qwen(item: Any, plan: CargoPlan, now_minute: int) -> dict[str, Any]:
        enriched = dict(item) if isinstance(item, dict) else {}
        estimated_net = plan.price - (plan.pickup_km + plan.haul_km) * DEFAULT_COST_PER_KM
        total_minutes = max(1, plan.finish_minutes - now_minute)
        enriched.update(
            {
                "distance_km": plan.pickup_km,
                "haul_distance_km": plan.haul_km,
                "estimated_net_yuan": estimated_net,
                "estimated_total_minutes": total_minutes,
                "estimated_net_per_hour": estimated_net / (total_minutes / 60.0),
            }
        )
        return enriched

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
