"""Optional Qwen3.5-Flash integration points.

The deterministic planner must run without a real model key. This helper keeps
the Qwen3.5-Flash hook explicit and opt-in so复赛切换成本低, while local
debugging remains stable.
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
    """Small cached wrapper around the official model_chat_completion API."""

    def __init__(self, api: SimulationApiPort) -> None:
        self._api = api
        self._logger = logging.getLogger("agent.qwen_flash")
        self._cache: dict[str, dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return os.environ.get(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

    def preference_hints(self, preferences: list[Any]) -> dict[str, Any]:
        """Return optional structured hints from Qwen3.5-Flash.

        The planner does not require these hints; failures return an empty dict.
        This preserves a clear base-model interface without making model calls a
        hard dependency for every local run.
        """
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
            choices = resp.get("choices")
            if not isinstance(choices, list) or not choices:
                return {}
            content = choices[0].get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                return {}
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return {}
            self._cache[cache_key] = parsed
            return parsed
        except Exception as exc:  # noqa: BLE001 - Qwen hints are optional.
            self._logger.warning("Qwen3.5-Flash preference hints unavailable: %s", exc)
            self._cache[cache_key] = {}
            return {}
