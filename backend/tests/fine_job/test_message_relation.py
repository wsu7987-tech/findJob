from __future__ import annotations

import pytest

from backend.app.config import load_config
from backend.app.services.fine_job.message_relation import classify_pending_reply_delta


def _message(**overrides):
    return {
        "id": "message-b",
        "direction": "inbound",
        "content": "请问您什么时候方便沟通？",
        "semantic_type": "recruiter_text",
        "reply_required": True,
        "semantic_context": {},
        **overrides,
    }


def _classify(messages, provider):
    return classify_pending_reply_delta(
        load_config(),
        action_id="action-a",
        session_id="session-1",
        planned_reply="您好，我可以参加面试。",
        base_message_ids=["message-a"],
        messages=messages,
        conversation_revision=2,
        provider_call=provider,
    )


def test_non_reply_platform_event_skips_relation_provider() -> None:
    def provider(_payload):
        raise AssertionError("平台事件不应调用 relation provider")

    result = _classify([_message(reply_required=False, semantic_type="platform_ad")], provider)

    assert result["decision"] == "non_reply_platform_event"
    assert result["reason_code"] == "semantic_non_reply"


@pytest.mark.parametrize(
    ("decision", "reason_code"),
    [
        ("relevant_to_existing_reply", "answer_changes"),
        ("independent_reply_required", "separate_topic"),
    ],
)
def test_relation_provider_returns_allowed_decisions(decision: str, reason_code: str) -> None:
    result = _classify([_message()], lambda _payload: ({"decision": decision, "reason_code": reason_code}, "mock-provider"))

    assert result["decision"] == decision
    assert result["reason_code"] == reason_code
    assert result["provider_version"] == "mock-provider"


def test_incomplete_semantics_are_uncertain_without_provider() -> None:
    result = _classify([_message(semantic_type="")], lambda _payload: (_ for _ in ()).throw(AssertionError("不应调用 provider")))

    assert result["decision"] == "classification_uncertain"
    assert result["reason_code"] == "semantic_mixed_or_incomplete"


@pytest.mark.parametrize(
    "provider",
    [
        lambda _payload: (_ for _ in ()).throw(RuntimeError("offline provider failure")),
        lambda _payload: ({"decision": "unexpected", "reason_code": "bad"}, "mock-provider"),
    ],
)
def test_provider_failure_or_invalid_output_fails_closed(provider) -> None:
    result = _classify([_message()], provider)

    assert result["decision"] == "classification_uncertain"
    assert result["reason_code"] == "relation_provider_unavailable"
