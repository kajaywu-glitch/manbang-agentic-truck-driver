"""Qwen3.5-Flash integration for cargo ranking and strategic decisions.

The deterministic planner works without a model. When AGENT_ENABLE_QWEN35_FLASH
is set, the model assists in cargo ranking and action selection, falling back
to deterministic logic on any failure.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from simkit.ports import SimulationApiPort

QWEN_FLASH_MODEL = "qwen3.5-flash"
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
            "model": QWEN_FLASH_MODEL,
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

        # 限制候选数量，避免 prompt 过长
        top_cargos = cargos[:20]

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
            "model": QWEN_FLASH_MODEL,
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
            "model": QWEN_FLASH_MODEL,
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
