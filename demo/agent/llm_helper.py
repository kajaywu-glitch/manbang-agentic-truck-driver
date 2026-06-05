"""Qwen model integration for constraint verification and strategic decisions.

The deterministic planner works without a model. When AGENT_ENABLE_QWEN35_FLASH
is set, Qwen assists in preference parsing and constraint verification,
falling back to deterministic logic on any failure.

Model selection: defaults to 'qwen-plus' (non-reasoning, good quality/cost).
Override with AGENT_QWEN_MODEL env var (e.g. 'qwen-turbo', 'qwen3.5-flash').
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from simkit.ports import SimulationApiPort

_DEFAULT_MODEL = "qwen-plus"
QWEN_MODEL = os.environ.get("AGENT_QWEN_MODEL", "").strip() or _DEFAULT_MODEL
ENABLE_ENV = "AGENT_ENABLE_QWEN35_FLASH"


class QwenFlashHelper:
    """Cached wrapper around the official model_chat_completion API."""

    def __init__(self, api: SimulationApiPort) -> None:
        self._api = api
        self._logger = logging.getLogger("agent.qwen_flash")
        self._cache: dict[str, dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return os.environ.get(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

    # ------------------------------------------------------------------
    # 偏好解析辅助
    # ------------------------------------------------------------------
    def preference_hints(self, preferences: list[Any]) -> dict[str, Any]:
        """Return optional structured hints from Qwen3.5-Flash."""
        if not self.enabled or not preferences:
            return {}
        cache_key = json.dumps(preferences, ensure_ascii=False, sort_keys=True, default=str)
        if cache_key in self._cache:
            return self._cache[cache_key]
        prompt = {
            "task": "将卡车司机偏好文本结构化为约束提示，必须只输出JSON对象。",
            "allowed_keys": [
                "forbidden_cargo_names",
                "soft_avoid_cargo_names",
                "daily_rest_hours",
                "quiet_windows",
                "max_pickup_km",
                "max_haul_km",
                "notes",
            ],
            "preferences": preferences,
        }
        payload = {
            "model": QWEN_MODEL,
            "temperature": 0,
            "max_tokens": 192,
            "messages": [
                {
                    "role": "system",
                    "content": "你是货运仿真偏好解析器。只输出紧凑JSON对象，不输出解释。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            resp = self._api.model_chat_completion(payload)
            content = self._extract_content(resp)
            if content is None:
                return {}
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return {}
            self._cache[cache_key] = parsed
            return parsed
        except Exception as exc:
            self._logger.warning("preference_hints unavailable: %s", exc)
            self._cache[cache_key] = {}
            return {}

    # ------------------------------------------------------------------
    # 货源排序：模型对候选货源打分
    # ------------------------------------------------------------------
    def rank_cargos(
        self,
        driver_id: str,
        driver_status: dict[str, Any],
        cargos: list[dict[str, Any]],
        constraints: dict[str, Any],
    ) -> dict[str, float]:
        """Ask the model to score each cargo. Returns {cargo_id: score}.

        Scores should be 0-100 where higher is better. Returns empty dict
        on failure (caller falls back to deterministic scoring).
        """
        if not self.enabled or not cargos:
            return {}

        # 限制候选数量，避免 prompt 过长和模型长时间推理。
        top_cargos = cargos[:5]

        cargo_summaries = []
        for c in top_cargos:
            cargo = c.get("cargo", {})
            start = cargo.get("start", {})
            end = cargo.get("end", {})
            cargo_summaries.append({
                "cargo_id": str(cargo.get("cargo_id", "")),
                "name": str(cargo.get("cargo_name", "")),
                "category": str(cargo.get("cargo_category", "")),
                "price": float(cargo.get("price", 0) or 0),
                "pickup_lat": float(start.get("lat", 0) or 0),
                "pickup_lng": float(start.get("lng", 0) or 0),
                "dest_lat": float(end.get("lat", 0) or 0),
                "dest_lng": float(end.get("lng", 0) or 0),
                "distance_km": float(c.get("distance_km", 0) or 0),
                "haul_km": float(c.get("haul_distance_km", 0) or 0),
                "cost_time_minutes": int(cargo.get("cost_time_minutes", 0) or 0),
            })

        prompt_data = {
            "task": "对以下候选货源按盈利潜力打分(0-100)。考虑：运价、距离成本、时间效率、目的地机会。",
            "driver": {
                "id": driver_id,
                "lat": float(driver_status.get("current_lat", 0)),
                "lng": float(driver_status.get("current_lng", 0)),
                "cost_per_km": 1.5,
            },
            "constraints": constraints,
            "cargos": cargo_summaries,
            "output_format": {"cargo_scores": {"cargo_id": "score_0_to_100"}},
        }

        payload = {
            "model": QWEN_MODEL,
            "temperature": 0,
            "max_tokens": 192,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是货运调度AI。对每个候选货源给出0-100的盈利潜力评分。"
                        "只输出JSON: {\"cargo_scores\": {\"id\": score, ...}}"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            resp = self._api.model_chat_completion(payload)
            content = self._extract_content(resp)
            if content is None:
                return {}
            parsed = json.loads(content)
            scores = parsed.get("cargo_scores", parsed.get("scores", parsed))
            if not isinstance(scores, dict):
                return {}
            result = {}
            for k, v in scores.items():
                try:
                    result[str(k)] = max(0.0, min(100.0, float(v)))
                except (TypeError, ValueError):
                    continue
            self._logger.info("model ranked %d cargos for driver=%s", len(result), driver_id)
            return result
        except Exception as exc:
            self._logger.warning("rank_cargos unavailable: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # 决策建议：模型选择最佳动作
    # ------------------------------------------------------------------
    def suggest_decision(
        self,
        driver_id: str,
        driver_status: dict[str, Any],
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> int | None:
        """Ask the model to pick the best candidate. Returns index or None.

        candidates is a list of {action, params, reason, score}.
        Returns the index into candidates, or None on failure.
        """
        if not self.enabled or not candidates:
            return None

        cand_summaries = []
        for i, c in enumerate(candidates):
            cand_summaries.append({
                "index": i,
                "action": c.get("action", ""),
                "params": c.get("params", {}),
                "reason": c.get("reason", ""),
                "deterministic_score": c.get("score", 0),
            })

        prompt_data = {
            "task": "选择最佳动作。考虑收益、偏好约束、时间效率。",
            "driver": {
                "id": driver_id,
                "lat": float(driver_status.get("current_lat", 0)),
                "lng": float(driver_status.get("current_lng", 0)),
            },
            "context": context,
            "candidates": cand_summaries,
            "output_format": {"chosen_index": "int"},
        }

        payload = {
            "model": QWEN_MODEL,
            "temperature": 0,
            "max_tokens": 96,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是货运调度AI。从候选动作中选最优的一个。"
                        "只输出JSON: {\"chosen_index\": N}"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            resp = self._api.model_chat_completion(payload)
            content = self._extract_content(resp)
            if content is None:
                return None
            parsed = json.loads(content)
            idx = parsed.get("chosen_index", parsed.get("index", parsed.get("choice")))
            if idx is None:
                return None
            idx = int(idx)
            if 0 <= idx < len(candidates):
                self._logger.info("model chose candidate %d for driver=%s", idx, driver_id)
                return idx
            return None
        except Exception as exc:
            self._logger.warning("suggest_decision unavailable: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 约束验证：模型检查确定性选择是否违反硬约束
    # ------------------------------------------------------------------
    def verify_constraints(
        self,
        driver_id: str,
        scenario: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Ask Qwen to verify that a candidate action respects hard constraints.

        scenario is one of: 'home_night', 'rest', 'family'.
        context provides computed values (distances, times, deadlines).
        Returns {safe: bool, risk: 'low'|'medium'|'high', concern: str,
                 suggestion: str} or None on failure.
        """
        if not self.enabled:
            return None

        prompt_data = self._build_verification_prompt(scenario, context)
        if prompt_data is None:
            return None

        payload = {
            "model": QWEN_MODEL,
            "temperature": 0,
            "max_tokens": 384,
            "messages": [
                {"role": "system", "content": prompt_data["system"]},
                {"role": "user", "content": json.dumps(prompt_data["user"], ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            resp = self._api.model_chat_completion(payload)
            content = self._extract_content(resp)
            if content is None:
                return None
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return None
            safe = parsed.get("safe")
            risk = str(parsed.get("risk", "low")).lower()
            if risk not in ("low", "medium", "high"):
                risk = "low"
            result = {
                "safe": bool(safe) if isinstance(safe, bool) else True,
                "risk": risk,
                "concern": str(parsed.get("concern", "")),
                "suggestion": str(parsed.get("suggestion", "")),
            }
            self._logger.info(
                "verify_constraints driver=%s scenario=%s safe=%s risk=%s",
                driver_id, scenario, result["safe"], result["risk"],
            )
            return result
        except Exception as exc:
            self._logger.warning("verify_constraints unavailable: %s", exc)
            return None

    @staticmethod
    def _build_verification_prompt(
        scenario: str,
        ctx: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return {system, user} prompt dicts for a constraint scenario."""
        if scenario == "home_night":
            return QwenFlashHelper._build_home_night_prompt(ctx)
        if scenario == "rest":
            return QwenFlashHelper._build_rest_prompt(ctx)
        if scenario == "family":
            return QwenFlashHelper._build_family_prompt(ctx)
        return None

    @staticmethod
    def _build_home_night_prompt(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "system": (
                "你是货运安全审核员。司机必须在每天 23:00 前到家。"
                "根据给定的时间、距离和截止时间判断操作是否安全。"
                "只输出JSON: {\"safe\": bool, \"risk\": \"low\"|\"medium\"|\"high\", "
                "\"concern\": \"风险描述\", \"suggestion\": \"建议\"}"
            ),
            "user": {
                "task": "判断此操作是否会违反 23:00 前到家约束",
                "wall_time": ctx.get("wall_time", "?"),
                "current_lat": ctx.get("lat", 0),
                "current_lng": ctx.get("lng", 0),
                "home_lat": ctx.get("home_lat", 0),
                "home_lng": ctx.get("home_lng", 0),
                "distance_to_home_km": ctx.get("dist_home_km", 0),
                "travel_to_home_minutes": ctx.get("travel_home_min", 0),
                "action_type": ctx.get("action_type", "?"),
                "action_duration_minutes": ctx.get("action_duration", 0),
                "finish_wall_time": ctx.get("finish_time", "?"),
                "finish_lat": ctx.get("end_lat", 0),
                "finish_lng": ctx.get("end_lng", 0),
                "finish_to_home_km": ctx.get("end_to_home_km", 0),
                "finish_to_home_minutes": ctx.get("end_to_home_min", 0),
                "deadline": "23:00",
                "time_to_deadline_minutes": ctx.get("time_to_deadline", 0),
                "instruction": (
                    "如果行动结束时间 + 回家路程 >= 23:00，safe=false 且 risk=high。"
                    "如果行动结束时间 + 回家路程在 22:30-23:00 之间，risk=medium。"
                    "其他情况 safe=true 且 risk=low。"
                ),
            },
        }

    @staticmethod
    def _build_rest_prompt(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "system": (
                "你是货运休息合规审核员。司机每天必须连续休息指定小时数。"
                "根据已休息时间和剩余时间判断接单是否会破坏连续休息。"
                "只输出JSON: {\"safe\": bool, \"risk\": \"low\"|\"medium\"|\"high\", "
                "\"concern\": \"风险描述\", \"suggestion\": \"建议\"}"
            ),
            "user": {
                "task": "判断接单是否会违反连续休息约束",
                "wall_time": ctx.get("wall_time", "?"),
                "required_rest_hours": ctx.get("rest_hours", 0),
                "rested_today_minutes": ctx.get("rested_today", 0),
                "rest_remaining_minutes": ctx.get("rest_remaining", 0),
                "minutes_left_today": ctx.get("minutes_left_today", 0),
                "action_type": ctx.get("action_type", "?"),
                "action_duration_minutes": ctx.get("action_duration", 0),
                "finish_wall_time": ctx.get("finish_time", "?"),
                "minutes_after_finish": ctx.get("after_finish", 0),
                "instruction": (
                    "如果行动结束后当天剩余分钟数 < 还需休息分钟数，safe=false 且 risk=high。"
                    "如果当天剩余分钟数在还需休息分钟数的 1.0-1.2 倍之间，risk=medium。"
                    "其他情况 safe=true 且 risk=low。"
                ),
            },
        }

    @staticmethod
    def _build_family_prompt(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "system": (
                "你是货运家庭事务审核员。司机有家中急事需要处理。"
                "根据家事时间窗口和截止时间判断操作是否会冲突。"
                "只输出JSON: {\"safe\": bool, \"risk\": \"low\"|\"medium\"|\"high\", "
                "\"concern\": \"风险描述\", \"suggestion\": \"建议\"}"
            ),
            "user": {
                "task": "判断接单是否会与家事安排冲突",
                "wall_time": ctx.get("wall_time", "?"),
                "family_start_time": ctx.get("family_start", "?"),
                "family_deadline_time": ctx.get("family_deadline", "?"),
                "stay_until_time": ctx.get("stay_until", "?"),
                "action_type": ctx.get("action_type", "?"),
                "action_duration_minutes": ctx.get("action_duration", 0),
                "finish_wall_time": ctx.get("finish_time", "?"),
                "finish_to_pickup_km": ctx.get("end_to_pickup_km", 0),
                "finish_to_pickup_minutes": ctx.get("end_to_pickup_min", 0),
                "time_to_family_start": ctx.get("time_to_start", 0),
                "time_to_deadline": ctx.get("time_to_deadline", 0),
                "instruction": (
                    "如果行动结束时间 + 前往接人点路程 >= 家事开始时间 - 60 分钟，safe=false 且 risk=high。"
                    "如果行动在家庭截止时间（home_deadline）之后才结束，risk=high。"
                    "如果当前已在家事窗口内（开始时间 ~ 结束时间），safe=false 且 risk=high。"
                    "其他情况 safe=true 且 risk=low。"
                ),
            },
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_content(resp: dict[str, Any]) -> str | None:
        choices = resp.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        return content.strip()
