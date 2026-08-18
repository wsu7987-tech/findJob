from __future__ import annotations

import re

from backend.app.config import AppConfig
from backend.app.errors import AppError
from backend.app.services.ai import create_answer_provider


def suggest_by_strategy(
    jobs: list[dict[str, object]],
    *,
    intent: dict[str, object] | None,
) -> dict[str, str]:
    intent = intent or {}
    excluded = [
        str(value).strip().lower()
        for value in intent.get("excluded_keywords", [])
        if str(value).strip()
    ]
    keywords = [
        str(value).strip()
        for value in [
            intent.get("target_title") or "",
            *intent.get("keywords", []),
            *intent.get("expanded_keywords", []),
        ]
        if str(value).strip()
    ]
    salary_min = int(intent.get("salary_min") or 0)
    recommendations: dict[str, str] = {}
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        text = " ".join(
            str(job.get(key) or "")
            for key in ("title", "boss_name", "tags", "skills", "job_labels", "location")
        ).lower()
        hits = [value for value in excluded if value in text]
        if not job_id or hits:
            continue
        reasons = []
        keyword_hits = [value for value in keywords if value.lower() in text]
        if keyword_hits:
            reasons.append(f"匹配求职关键词：{', '.join(keyword_hits[:3])}")
        active = str(job.get("boss_active_status") or "").strip()
        if active:
            reasons.append(f"招聘者状态：{active}")
        salary = _salary_lower_bound(str(job.get("salary") or ""))
        if salary_min and salary >= salary_min:
            reasons.append(f"薪资下限约 {salary}K，达到期望下限")
        if reasons:
            recommendations[job_id] = "；".join(reasons)
    return recommendations


def suggest_by_ai(
    jobs: list[dict[str, object]],
    *,
    intent: dict[str, object] | None,
    strategy: dict[str, object] | None,
    command: str,
    config: AppConfig,
) -> dict[str, str]:
    if config.reasoning_executor == "llm" and (config.llm_provider or "stub-llm") == "stub-llm":
        raise AppError(
            status_code=409,
            error_category="AI_NOT_CONFIGURED",
            error_message="当前未配置真实 AI 执行器，请先在 FineJob 配置中完成设置。",
        )
    provider = create_answer_provider(config)
    recommendations: dict[str, str] = {}
    instruction = command.strip() or "结合求职意向和投递策略，选择值得继续采集完整 JD 的岗位。"
    context = f"求职意向：{intent or {}}\n投递策略：{strategy or {}}\n用户要求：{instruction}"
    # AnswerProvider 当前每次最多读取 5 条证据，因此分批处理，避免后面的岗位被静默忽略。
    for start in range(0, len(jobs), 5):
        batch = jobs[start:start + 5]
        citations = [
            {
                "citation_id": str(job.get("job_id") or ""),
                "title": str(job.get("title") or ""),
                "source_name": str(job.get("boss_name") or "BOSS"),
                "snippet": " | ".join(
                    str(job.get(key) or "")
                    for key in ("salary", "location", "experience", "degree", "boss_active_status")
                ),
                "context_snippet": " | ".join(
                    str(job.get(key) or "")
                    for key in ("tags", "skills", "job_labels", "company_industry")
                ),
            }
            for job in batch
            if job.get("job_id")
        ]
        artifact = provider.answer(
            question=(
                f"{context}\n只把建议采集详情的岗位放入 citation_ids；不建议的岗位不要引用。"
            ),
            mode="answer",
            evidence_citations=citations,
            grounded_items=[],
        )
        valid_ids = {str(job.get("job_id") or "") for job in batch}
        for job_id in artifact.citation_ids:
            if job_id in valid_ids:
                recommendations[job_id] = artifact.answer
    return recommendations


def _salary_lower_bound(value: str) -> int:
    match = re.search(r"(\d+)(?:\s*-\s*\d+)?\s*[Kk]", value)
    return int(match.group(1)) if match else 0
