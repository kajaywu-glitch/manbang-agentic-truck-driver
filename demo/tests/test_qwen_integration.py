from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from agent.llm_helper import QwenFlashHelper
from agent.planner import CargoPlan, Candidate, DeterministicPlanner
from agent.preference_rules import PreferencePolicy
from agent.state_tracker import DriverMemory


class FakeApi:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def model_chat_completion(self, payload: dict) -> dict:
        self.payload = payload
        return {
            "choices": [{"message": {"content": '{"cargo_scores":{"C1":88}}'}}],
            "usage": {"total_tokens": 10},
        }


class QwenIntegrationTests(unittest.TestCase):
    def test_cargo_enrichment_uses_evaluated_plan_metrics(self) -> None:
        plan = CargoPlan(
            cargo_id="C1",
            cargo_name="test",
            price=1000.0,
            pickup_km=20.0,
            haul_km=180.0,
            pickup_minutes=20,
            wait_minutes=10,
            duration_minutes=240,
            finish_minutes=390,
            start_lat=23.0,
            start_lng=113.0,
            end_lat=24.0,
            end_lng=114.0,
            score=100.0,
        )

        enriched = DeterministicPlanner._cargo_for_qwen(
            {"distance_km": 999.0, "cargo": {"cargo_id": "C1", "price": 1000}},
            plan,
            now_minute=100,
        )

        self.assertEqual(enriched["distance_km"], 20.0)
        self.assertEqual(enriched["haul_distance_km"], 180.0)
        self.assertEqual(enriched["estimated_total_minutes"], 290)
        self.assertAlmostEqual(enriched["estimated_net_yuan"], 700.0)

    def test_rank_prompt_uses_enriched_metrics(self) -> None:
        api = FakeApi()
        helper = QwenFlashHelper(api)
        cargo = {
            "distance_km": 20.0,
            "haul_distance_km": 180.0,
            "estimated_net_yuan": 700.0,
            "estimated_total_minutes": 290,
            "estimated_net_per_hour": 144.83,
            "cargo": {
                "cargo_id": "C1",
                "cargo_name": "test",
                "price": 1000.0,
                "cost_time_minutes": 240,
                "end": {"city": "Guangzhou"},
            },
        }

        with patch.dict(os.environ, {"AGENT_ENABLE_QWEN35_FLASH": "1"}):
            scores = helper.rank_cargos("D001", {}, [cargo], {})

        self.assertEqual(scores, {"C1": 88.0})
        assert api.payload is not None
        prompt = json.loads(api.payload["messages"][1]["content"])
        summary = prompt["cargos"][0]
        self.assertEqual(summary["haul_km"], 180.0)
        self.assertEqual(summary["net_yuan"], 700.0)
        self.assertEqual(summary["net_per_hour"], 145.0)

    def test_review_budget_is_per_driver_and_one_call_per_step(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_ENABLE_QWEN35_FLASH": "1",
                "AGENT_QWEN_MAX_REVIEWS": "20",
                "AGENT_QWEN_MAX_REVIEWS_PER_DRIVER": "1",
            },
        ):
            planner = DeterministicPlanner(FakeApi())
            planner._step_counter = 10
            self.assertTrue(planner._qwen_review_available("D001", cooldown_steps=5))
            planner._record_qwen_review("D001", "rank_cargos")
            self.assertFalse(planner._qwen_review_available("D001", cooldown_steps=0))
            self.assertTrue(planner._qwen_review_available("D002", cooldown_steps=5))

    def test_candidate_gap_gate(self) -> None:
        close = [
            Candidate({"action": "wait", "params": {}}, 100.0, "a"),
            Candidate({"action": "wait", "params": {}}, 92.0, "b"),
        ]
        obvious = [
            Candidate({"action": "wait", "params": {}}, 100.0, "a"),
            Candidate({"action": "wait", "params": {}}, 20.0, "b"),
        ]

        self.assertAlmostEqual(DeterministicPlanner._candidate_gap_ratio(close), 0.08)
        self.assertAlmostEqual(DeterministicPlanner._candidate_gap_ratio(obvious), 0.8)

    def test_verification_context_uses_geo_wall_time_and_candidate_metrics(self) -> None:
        planner = DeterministicPlanner(FakeApi())
        chosen = Candidate(
            {"action": "take_order", "params": {"cargo_id": "C1"}},
            100.0,
            "best_cargo",
            {
                "estimated_minutes": 180,
                "end_lat": 24.0,
                "end_lng": 114.0,
            },
        )

        context = planner._build_verification_context(
            "rest",
            {},
            DriverMemory(),
            PreferencePolicy(daily_rest_minutes=360),
            chosen,
            100,
            23.0,
            113.0,
        )

        self.assertEqual(context["action_duration"], 180)
        self.assertEqual(context["end_lat"], 24.0)
        self.assertEqual(context["end_lng"], 114.0)
        self.assertEqual(context["finish_time"], "2026-03-01 04:40:00")

    def test_pending_rest_reserves_model_call_for_verification(self) -> None:
        self.assertTrue(
            DeterministicPlanner._verification_has_priority(
                PreferencePolicy(daily_rest_minutes=360),
                DriverMemory(),
                12 * 60,
            )
        )


if __name__ == "__main__":
    unittest.main()
