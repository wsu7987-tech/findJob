from __future__ import annotations

import re
from dataclasses import dataclass

from backend.app.config import AppConfig
from backend.app.errors import AppError


SENSITIVE_OPERATION_KEYS = (
    "send_greeting",
    "send_chat_reply",
    "send_contact_info",
    "send_commitment_reply",
    "send_interview_decision",
    "start_greeting_batch",
    "resume_external_executor",
    "submit_application",
    "change_automation_policy",
)


@dataclass(slots=True)
class ContentClassification:
    categories: list[str]
    classification_version: int = 1
    classification_unknown: bool = False


def classify_outbound_content(
    final_text: str,
    *,
    base_operation: str,
) -> ContentClassification:
    """根据最终文本生成授权类别，Codex 传入的类别不会参与判断。"""
    text = final_text.strip()
    if base_operation not in SENSITIVE_OPERATION_KEYS:
        raise AppError(
            status_code=422,
            error_category="SENSITIVE_OPERATION_UNKNOWN",
            error_message="敏感操作标识未登记。",
        )
    categories = [base_operation]
    if base_operation in {"send_chat_reply", "send_greeting"}:
        if re.search(r"(?:1[3-9]\d{9}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|微信|手机号|电话)", text):
            categories.append("send_contact_info")
        if re.search(r"(?:薪资|工资|月薪|年薪|到岗|入职|最低|底薪|承诺)", text):
            categories.append("send_commitment_reply")
        if re.search(r"(?:面试|改期|接受|拒绝|时间安排|线下|线上)", text):
            categories.append("send_interview_decision")
    return ContentClassification(categories=list(dict.fromkeys(categories)))


def resolve_codex_authorization(
    config: AppConfig,
    *,
    classification: ContentClassification,
) -> dict[str, object]:
    permissions = config.codex_sensitive_operation_permissions or {}
    pre_authorized = bool(
        config.codex_sensitive_auto_authorization_enabled
        and not classification.classification_unknown
        and classification.categories
        and all(permissions.get(category, False) for category in classification.categories)
    )
    return {
        "sensitive_operation": True,
        "authorization_mode": (
            "pre_authorized" if pre_authorized else "manual_confirmation"
        ),
        "authorization_source": "settings",
        "requires_confirmation": not pre_authorized,
        "content_categories": classification.categories,
        "classification_version": classification.classification_version,
        "classification_unknown": classification.classification_unknown,
    }
