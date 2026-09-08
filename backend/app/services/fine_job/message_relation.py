"""待发送回复与新增聊天消息的关系判定。"""
from __future__ import annotations

import json
from typing import Any, Callable

from backend.app.config import AppConfig
from backend.app.services.ai import _post_json
from backend.app.services.reasoning.codex_exec import run_codex_exec


_DECISIONS = {
    "relevant_to_existing_reply",
    "independent_reply_required",
    "classification_uncertain",
}
CLASSIFIER_VERSION = "finejob-message-relation-v1"


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": sorted(_DECISIONS)},
            "reason_code": {"type": "string"},
        },
        "required": ["decision", "reason_code"],
    }


def classify_pending_reply_delta(
    config: AppConfig,
    *,
    action_id: str,
    session_id: str,
    planned_reply: str,
    base_message_ids: list[str],
    messages: list[dict[str, Any]],
    conversation_revision: int,
    provider_call: Callable[[dict[str, Any]], tuple[dict[str, Any], str]] | None = None,
) -> dict[str, Any]:
    """只返回受限关系枚举，调用失败一律保守阻断。"""
    message_ids = [str(item.get("id") or "") for item in messages if str(item.get("id") or "")]
    if not messages or all(not bool(item.get("reply_required")) for item in messages):
        return _result("non_reply_platform_event", "semantic_non_reply", "stage1-semantic", action_id, session_id, base_message_ids, message_ids, conversation_revision)
    if any(
        str(item.get("direction")) != "inbound"
        or str(item.get("semantic_type")) != "recruiter_text"
        or not bool(item.get("reply_required"))
        for item in messages
    ):
        return _result("classification_uncertain", "semantic_mixed_or_incomplete", "stage1-semantic", action_id, session_id, base_message_ids, message_ids, conversation_revision)
    payload = {
        "action_id": action_id,
        "session_id": session_id,
        "planned_reply": planned_reply,
        "base_message_ids": base_message_ids,
        "new_messages": [
            {"id": item.get("id"), "content": item.get("content"), "semantic_context": item.get("semantic_context")}
            for item in messages
        ],
        "conversation_revision": conversation_revision,
    }
    try:
        result, provider_version = provider_call(payload) if provider_call else _provider_classify(config, payload)
        decision = str(result.get("decision") or "")
        reason_code = str(result.get("reason_code") or "")[:120]
        if decision not in _DECISIONS or not reason_code:
            raise ValueError("invalid_relation_response")
        return _result(decision, reason_code, provider_version, action_id, session_id, base_message_ids, message_ids, conversation_revision)
    except Exception:
        return _result("classification_uncertain", "relation_provider_unavailable", "provider-error", action_id, session_id, base_message_ids, message_ids, conversation_revision)


def _provider_classify(config: AppConfig, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    prompt = (
        "只判断新增招聘方消息与已计划回复的关系，不生成任何回复文本。"
        "只输出 JSON：decision 只能是 relevant_to_existing_reply、independent_reply_required、classification_uncertain；"
        "reason_code 为简短稳定代码。\n输入：" + json.dumps(payload, ensure_ascii=False)
    )
    executor = (config.reasoning_executor or "llm").strip().lower()
    if executor == "codex-cli":
        result = run_codex_exec(
            cli_path=config.codex_cli_path,
            prompt=prompt,
            output_schema=_schema(),
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
            timeout_seconds=config.codex_timeout_seconds,
        )
        return dict(result.output), str(result.model or config.codex_model or "codex-cli")
    if executor != "llm" or not config.llm_provider or not config.llm_model:
        raise ValueError("relation_provider_not_configured")
    response = _post_json(
        url=f"{(config.llm_base_url or 'https://api.openai.com/v1').rstrip('/')}/chat/completions",
        api_key=config.llm_api_key,
        timeout_seconds=config.llm_timeout_seconds,
        payload={"model": config.llm_model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
    )
    raw = str(response["choices"][0]["message"]["content"]).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return dict(json.loads(raw)), str(config.llm_model)


def _result(decision: str, reason_code: str, provider_version: str, action_id: str, session_id: str, base_message_ids: list[str], message_ids: list[str], conversation_revision: int) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason_code": reason_code,
        "classifier_version": CLASSIFIER_VERSION,
        "provider_version": provider_version,
        "action_id": action_id,
        "session_id": session_id,
        "base_message_ids": base_message_ids,
        "message_ids": message_ids,
        "conversation_revision": conversation_revision,
    }
