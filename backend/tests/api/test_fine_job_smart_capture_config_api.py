from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.schemas.fine_job.smart_captures import SmartCaptureCreateRequest
from backend.app.services.fine_job import smart_captures


def _config_payload(*, delivery_target_enabled: bool) -> dict[str, object]:
    return {
        "filter_strategy_id": "strategy-1",
        "allowed_search_keywords": ["Python", "MCP"],
        "allowed_cities": ["上海", "广州"],
        "filters": {"experience": "104,105", "degree": "203"},
        "candidate_target_count": 12,
        "pages": 2,
        "include_details": True,
        "prefer_current_page": False,
        "delivery_target_enabled": delivery_target_enabled,
        "recommendation_strategy_id": "recommendation-1" if delivery_target_enabled else None,
        "recommend_target": 4 if delivery_target_enabled else None,
        "review_target": 2 if delivery_target_enabled else None,
        "target_mode": "all",
        "analyze_all_candidates": True,
        "stop_after_current_batch": True,
        "analysis_batch_size": 3,
        "execution_policy_after_analysis_batch": "wait_for_user",
        "execution_policy_codex_handoff": "manual",
        "analysis_guidance": "关注平台工程和自动化经验",
        "codex_model": "gpt-5.6-luna" if delivery_target_enabled else None,
        "codex_reasoning_effort": "high" if delivery_target_enabled else None,
        "context_soft_budget_characters": 16000,
        "min_depth": 2,
        "scroll_batch_size": 4,
        "max_depth": 18,
        "low_yield_streak_limit": 5,
    }


def test_off_config_does_not_require_delivery_fields() -> None:
    request = SmartCaptureCreateRequest(**_config_payload(delivery_target_enabled=False))

    assert request.delivery_target_enabled is False
    assert request.codex_model is None
    assert request.recommend_target is None


def test_on_config_requires_codex_strategy_and_target() -> None:
    payload = _config_payload(delivery_target_enabled=True)
    payload["codex_model"] = None
    payload["recommendation_strategy_id"] = None
    payload["recommend_target"] = None

    with pytest.raises(ValidationError) as error:
        SmartCaptureCreateRequest(**payload)

    message = str(error.value)
    assert "建议投递策略" in message
    assert "Codex 模型" in message
    assert "Recommend 目标" in message


def test_shared_execution_config_is_identical_for_off_and_on_shape(test_db) -> None:
    off_payload = _config_payload(delivery_target_enabled=False)
    on_payload = _config_payload(delivery_target_enabled=True)

    off_config = smart_captures._build_execution_config(off_payload)
    on_config = smart_captures._build_execution_config(on_payload)

    assert set(off_config) == set(on_config)
    assert off_config["search"] == on_config["search"]
    assert off_config["jd_detail_policy"] == on_config["jd_detail_policy"]
    assert off_config["stop_policy"] == on_config["stop_policy"]
    assert off_config["delivery_target"]["enabled"] is False
    assert on_config["delivery_target"]["enabled"] is True


def test_snapshot_round_trip_preserves_complete_execution_config(test_db) -> None:
    payload = _config_payload(delivery_target_enabled=True)
    execution_config = smart_captures._build_execution_config(payload)
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config=payload,
        target_count=12,
        execution_config=execution_config,
    )

    snapshot = smart_captures.get_smart_capture(test_db, str(capture["smart_capture_id"]))

    assert snapshot["execution_config"] == execution_config
    assert snapshot["execution_config"]["search"]["filters"] == payload["filters"]
    assert snapshot["execution_config"]["delivery_target"]["recommend_target"] == 4
    assert snapshot["execution_config"]["analysis"]["codex_model"] == "gpt-5.6-luna"
    assert snapshot["execution_config"]["stop_policy"]["max_depth"] == 18
