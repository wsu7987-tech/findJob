from __future__ import annotations

import json
import re

from backend.app.config import AppConfig
from backend.app.errors import AppError
from backend.app.services.ai import create_answer_provider


def evaluate_filter_strategy(
    jobs: list[dict[str, object]],
    strategy: dict[str, object],
) -> list[dict[str, object]]:
    return [_evaluate_filter_job(job, strategy) for job in jobs]


def evaluate_delivery_jobs(
    jobs: list[dict[str, object]],
    *,
    filter_strategy: dict[str, object] | None,
    recommendation_strategy: dict[str, object],
    resume_facts: list[dict[str, object]],
    extra_requirement: str,
    config: AppConfig,
    candidate_context: str = "",
) -> list[dict[str, object]]:
    method = str(recommendation_strategy.get("evaluation_method") or "hybrid")
    rule_results = {
        str(result["job_id"]): result
        for result in (
            evaluate_filter_strategy(jobs, filter_strategy) if filter_strategy else []
        )
    }
    local_results = [
        _evaluate_delivery_rules(
            job,
            strategy=recommendation_strategy,
            filter_result=rule_results.get(str(job.get("job_id") or "")),
            resume_facts=resume_facts,
        )
        for job in jobs
    ]
    if method == "rules":
        return local_results

    eligible_jobs = [
        job
        for job, result in zip(jobs, local_results, strict=True)
        if result["decision"] != "reject"
    ]
    llm_results = _evaluate_delivery_by_llm(
        eligible_jobs,
        strategy=recommendation_strategy,
        resume_facts=resume_facts,
        extra_requirement=extra_requirement,
        config=config,
        candidate_context=candidate_context,
    )
    llm_by_id = {str(result["job_id"]): result for result in llm_results}
    merged = []
    for local in local_results:
        job_id = str(local["job_id"])
        if local["decision"] == "reject" and method == "hybrid":
            merged.append(local)
            continue
        merged.append(llm_by_id.get(job_id, local))
    return merged


def _evaluate_filter_job(
    job: dict[str, object],
    strategy: dict[str, object],
) -> dict[str, object]:
    job_id = str(job.get("job_id") or "")
    reasons: list[str] = []
    failures: list[str] = []
    missing: list[str] = []
    title = _text(job.get("title"))
    company = _text(job.get("boss_name") or job.get("company"))
    location = _text(job.get("location"))
    detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
    skill_text = " | ".join(
        _text(value)
        for value in (
            job.get("skills"), job.get("tags"), job.get("job_labels"), detail.get("jd")
        )
        if _text(value)
    )

    _contains_any(title, strategy.get("title_include_any"), "岗位名称", reasons, failures)
    _contains_all(title, strategy.get("title_include_all"), "岗位名称", reasons, failures)
    _exclude_terms(title, strategy.get("title_exclude"), "岗位名称", failures)
    _contains_any(company, strategy.get("company_include"), "公司", reasons, failures)
    _exclude_terms(company, strategy.get("company_exclude"), "公司", failures)
    _exact_allowed(job, "company_scale", strategy, "company_scales", "公司规模", reasons, failures, missing)
    _exact_allowed(job, "company_industry", strategy, "company_industries", "公司行业", reasons, failures, missing)
    _exact_allowed(job, "company_stage", strategy, "company_stages", "融资阶段", reasons, failures, missing)
    _exact_allowed(job, "degree", strategy, "degrees", "学历", reasons, failures, missing)
    _exact_allowed(job, "experience", strategy, "experiences", "经验", reasons, failures, missing)

    cities = _strings(strategy.get("cities"))
    if cities:
        if location and any(city.lower() in location.lower() for city in cities):
            reasons.append("地点符合")
        elif not location:
            missing.append("地点")
        else:
            failures.append("地点不在限定城市")

    job_types = _strings(strategy.get("job_types"))
    if job_types:
        detected = _detect_job_type(job)
        if detected in job_types:
            reasons.append(f"工作性质符合：{_job_type_label(detected)}")
        else:
            failures.append(f"工作性质为{_job_type_label(detected)}")

    _evaluate_salary(job, strategy, reasons, failures, missing)
    _contains_any(skill_text, strategy.get("skill_include_any"), "技能", reasons, failures)
    _contains_all(skill_text, strategy.get("skill_include_all"), "技能", reasons, failures)
    _exclude_terms(skill_text, strategy.get("skill_exclude"), "技能/JD", failures)

    active_allowed = _strings(strategy.get("boss_active_statuses"))
    if active_allowed:
        active = _text(job.get("boss_active_status") or detail.get("boss_active_status"))
        if not active:
            missing.append("招聘者活跃状态")
        elif any(value.lower() in active.lower() for value in active_allowed):
            reasons.append(f"招聘者状态符合：{active}")
        else:
            failures.append(f"招聘者状态不符合：{active}")

    policy = str(strategy.get("unknown_value_policy") or "review")
    if failures:
        status = "reject"
    elif missing and policy == "exclude":
        status = "reject"
        failures.append(f"缺少必要字段：{'、'.join(missing)}")
    elif missing and policy == "review":
        status = "review"
    else:
        status = "pass"
    if not reasons and status == "pass":
        reasons.append("未配置会排除此岗位的条件")
    return {
        "job_id": job_id,
        "status": status,
        "reasons": [*reasons, *failures],
        "missing_fields": missing,
        "strategy_id": strategy.get("id"),
    }


def _evaluate_delivery_rules(
    job: dict[str, object],
    *,
    strategy: dict[str, object],
    filter_result: dict[str, object] | None,
    resume_facts: list[dict[str, object]],
) -> dict[str, object]:
    job_id = str(job.get("job_id") or "")
    detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
    jd = _text(detail.get("jd"))
    combined = " | ".join(
        _text(value)
        for value in (job.get("title"), job.get("skills"), job.get("job_labels"), jd)
        if _text(value)
    )
    reasons: list[str] = []
    risks: list[str] = []
    missing: list[str] = []
    if filter_result and filter_result.get("status") == "reject":
        reasons = list(filter_result.get("reasons") or [])
        missing_fields = list(filter_result.get("missing_fields") or [])
        return _build_v2_result(
            job=job,
            decision="reject",
            confidence=1.0,
            summary="岗位未通过已配置的硬性筛选条件。",
            reasons=reasons,
            risks=["未通过岗位筛选策略"],
            missing_fields=missing_fields,
            source="rules",
            hard_requirements=[
                {
                    "name": "岗位筛选策略",
                    "status": "fail",
                    "jd_evidence": "；".join(str(value) for value in reasons),
                    "resume_evidence": "",
                }
            ],
        )
    if not jd:
        missing.append("完整JD")
    required = _strings(strategy.get("required_skills"))
    missing_required = [value for value in required if value.lower() not in combined.lower()]
    if missing_required:
        risks.append(f"未发现必备技能：{'、'.join(missing_required)}")
    elif required:
        reasons.append("必备技能均有匹配")
    excluded_hits = [
        value for value in _strings(strategy.get("excluded_terms"))
        if value.lower() in combined.lower()
    ]
    if excluded_hits:
        risks.append(f"命中排除项：{'、'.join(excluded_hits)}")
    preferred_hits = [
        value for value in _strings(strategy.get("preferred_skills"))
        if value.lower() in combined.lower()
    ]
    if preferred_hits:
        reasons.append(f"匹配加分技能：{'、'.join(preferred_hits[:5])}")
    industry = _text(job.get("company_industry"))
    if industry and industry in _strings(strategy.get("preferred_industries")):
        reasons.append(f"行业偏好匹配：{industry}")

    confidence = min(0.95, 0.62 + len(preferred_hits) * 0.05 + (0.1 if jd else 0))
    if risks:
        decision = "reject"
        confidence = max(confidence, 0.85)
    elif missing:
        decision = "reject" if strategy.get("insufficient_info_action") == "reject" else "review"
        confidence = 0.45
    elif confidence >= float(strategy.get("minimum_confidence") or 0.7):
        decision = "recommend"
    else:
        decision = "review"
    normalized_reasons = reasons or ["符合已配置的结构化条件"]
    hard_requirements = []
    for value in required:
        hard_requirements.append(
            {
                "name": value,
                "status": "fail" if value in missing_required else "pass",
                "jd_evidence": f"建议投递策略要求技能：{value}",
                "resume_evidence": _find_resume_evidence(resume_facts, value),
            }
        )
    gaps = [
        {"item": value, "severity": "high", "can_fix_by_resume": False}
        for value in missing_required
    ]
    return _build_v2_result(
        job=job,
        decision=decision,
        confidence=round(confidence, 2),
        summary=_rule_summary(decision, normalized_reasons, risks, missing),
        reasons=normalized_reasons,
        risks=risks,
        missing_fields=missing,
        source="rules",
        hard_requirements=hard_requirements,
        match_dimensions={
            "core_skills": round(max(0.0, 1.0 - len(missing_required) * 0.35), 2),
            "job_relevance": round(confidence, 2),
        },
        strengths=normalized_reasons,
        gaps=gaps,
        greeting_draft=_safe_greeting_draft(job, decision=decision),
    )


def _evaluate_delivery_by_llm(
    jobs: list[dict[str, object]],
    *,
    strategy: dict[str, object],
    resume_facts: list[dict[str, object]],
    extra_requirement: str,
    config: AppConfig,
    candidate_context: str,
) -> list[dict[str, object]]:
    if not jobs:
        return []
    if config.reasoning_executor == "llm" and (config.llm_provider or "stub-llm") == "stub-llm":
        raise AppError(
            status_code=409,
            error_category="AI_NOT_CONFIGURED",
            error_message="当前未配置真实 AI 执行器，请先在 FineJob 配置中完成设置。",
        )
    provider = create_answer_provider(config)
    results: list[dict[str, object]] = []
    resume_context = [
        {
            "fact_type": fact.get("fact_type"),
            "fact_key": fact.get("fact_key"),
            "fact_value": fact.get("fact_value"),
            "user_confirmed": bool(fact.get("user_confirmed")),
        }
        for fact in resume_facts
        if not fact.get("sensitive") and str(fact.get("fact_value") or "").strip()
    ]
    strategy_context = {
        key: value
        for key, value in strategy.items()
        if key not in {"created_at", "updated_at"}
    }
    # V2 固定一岗一调用，避免多岗位的 JD 和简历证据相互污染。
    for job in jobs:
        detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
        job_id = str(job.get("job_id") or "")
        citations = [
            {
                "citation_id": job_id,
                "title": _text(job.get("title")),
                "source_name": _text(job.get("boss_name") or job.get("company")),
                "snippet": " | ".join(
                    _text(job.get(key))
                    for key in (
                        "salary",
                        "location",
                        "experience",
                        "degree",
                        "company_industry",
                        "company_scale",
                    )
                ),
                "context_snippet": (
                    "技能与标签："
                    + " | ".join(
                        _text(job.get(key))
                        for key in ("skills", "job_labels", "boss_active_status")
                    )
                    + "\n完整 JD："
                    + _text(detail.get("jd"))[:6000]
                ),
            }
        ]
        artifact = provider.answer(
            question=(
                "你是 FineJob 岗位投递评估器。只评估当前一个岗位。"
                "answer 字段必须是一个严格 JSON 对象字符串，禁止 Markdown 代码块和额外说明。"
                "JSON 必须包含：job_id、decision、confidence、summary、reasons、risks、"
                "missing_information、hard_requirements、match_dimensions、strengths、gaps、"
                "resume_suggestions、greeting_draft。"
                "decision 只能是 recommend、review、reject；不确定或证据缺失时必须使用 review。"
                "hard_requirements 每项包含 name、status(pass/fail/unknown)、jd_evidence、resume_evidence。"
                "match_dimensions 是 0 到 1 的数字对象，至少考虑 job_direction、core_skills、"
                "experience、project_relevance、industry_relevance、salary_location。"
                "gaps 每项包含 item、severity(high/medium/low)、can_fix_by_resume。"
                "resume_suggestions 每项包含 section、suggestion、basis；不得建议伪造经历。"
                "greeting_draft 包含 status(ready/not_generated)、text、facts_used。"
                "recommend 和 review 生成简洁中文招呼语；reject 必须返回 not_generated 和空文本。"
                "招呼语只能使用简历事实中的真实内容；没有简历证据时使用不带能力断言的通用表达。"
                "不要生成向招聘者追问的问题，也不要输出此类字段。"
                f"\n岗位 ID：{job_id}"
                f"\n投递策略：{json.dumps(strategy_context, ensure_ascii=False)}"
                f"\n简历事实：{json.dumps(resume_context, ensure_ascii=False)}"
                f"\n岗位评估上下文：{candidate_context.strip() or '无'}"
                f"\n本次额外要求：{extra_requirement.strip() or '无'}"
                "\n所有判断必须能从岗位证据或简历事实中找到依据。"
            ),
            mode="answer",
            evidence_citations=citations,
            grounded_items=[],
        )
        structured = _parse_llm_evaluation_v2(
            artifact.answer,
            job=job,
            resume_facts=resume_facts,
        )
        if structured:
            results.append(structured)
            continue
        has_detail = bool(_text(detail.get("jd")))
        missing = [] if has_detail else ["完整JD"]
        results.append(
            _build_v2_result(
                job=job,
                decision="review",
                confidence=min(0.5, round(float(artifact.confidence), 2)),
                summary="AI 未返回符合 V2 协议的结构化结果，已转入人工确认。",
                reasons=[artifact.answer] if artifact.answer.strip() else ["AI 结构化输出无效"],
                risks=["AI 结构化输出校验失败"],
                missing_fields=missing,
                source="llm",
                greeting_draft=_safe_greeting_draft(job, decision="review"),
            )
        )
    return results


def _parse_llm_evaluation_v2(
    answer: str,
    *,
    job: dict[str, object],
    resume_facts: list[dict[str, object]],
) -> dict[str, object] | None:
    """解析并收紧单岗位 V2 JSON；不合格时由调用方转人工确认。"""
    text = answer.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    job_id = str(job.get("job_id") or "")
    decision = str(parsed.get("decision") or "")
    if str(parsed.get("job_id") or "") != job_id or decision not in {
        "recommend",
        "review",
        "reject",
    }:
        return None

    hard_requirements = _normalize_hard_requirements(parsed.get("hard_requirements"))
    match_dimensions = _normalize_dimensions(parsed.get("match_dimensions"))
    gaps = _normalize_gaps(parsed.get("gaps"))
    resume_suggestions = _normalize_resume_suggestions(parsed.get("resume_suggestions"))
    greeting_draft, greeting_risks = _normalize_greeting_draft(
        parsed.get("greeting_draft"),
        job=job,
        decision=decision,
        resume_facts=resume_facts,
    )
    missing = _strings(parsed.get("missing_information"))
    risks = [*_strings(parsed.get("risks")), *greeting_risks]
    reasons = _strings(parsed.get("reasons"))
    strengths = _strings(parsed.get("strengths"))
    summary = _text(parsed.get("summary"))
    if not summary:
        return None
    return _build_v2_result(
        job=job,
        decision=decision,
        confidence=round(_clamp_score(parsed.get("confidence")), 2),
        summary=summary,
        reasons=reasons or strengths or [summary],
        risks=risks,
        missing_fields=missing,
        source="llm",
        hard_requirements=hard_requirements,
        match_dimensions=match_dimensions,
        strengths=strengths,
        gaps=gaps,
        resume_suggestions=resume_suggestions,
        greeting_draft=greeting_draft,
    )


def _build_v2_result(
    *,
    job: dict[str, object],
    decision: str,
    confidence: float,
    summary: str,
    reasons: list[str],
    risks: list[str],
    missing_fields: list[str],
    source: str,
    hard_requirements: list[dict[str, object]] | None = None,
    match_dimensions: dict[str, float] | None = None,
    strengths: list[str] | None = None,
    gaps: list[dict[str, object]] | None = None,
    resume_suggestions: list[dict[str, object]] | None = None,
    greeting_draft: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "evaluation_version": "2.0",
        "job_id": str(job.get("job_id") or ""),
        "decision": decision,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "summary": summary,
        # 保留 V1 字段，确保旧页面和历史数据读取逻辑继续工作。
        "reasons": reasons,
        "risks": risks,
        "missing_fields": missing_fields,
        "missing_information": missing_fields,
        "hard_requirements": hard_requirements or [],
        "match_dimensions": match_dimensions or {},
        "strengths": strengths or reasons,
        "gaps": gaps or [],
        "resume_suggestions": resume_suggestions or [],
        "greeting_draft": greeting_draft or _safe_greeting_draft(job, decision=decision),
        "source": source,
    }


def _safe_greeting_draft(
    job: dict[str, object],
    *,
    decision: str,
) -> dict[str, object]:
    if decision == "reject":
        return {"status": "not_generated", "text": "", "facts_used": []}
    title = _text(job.get("title")) or "该岗位"
    return {
        "status": "ready",
        "text": f"您好，我对贵司的{title}很感兴趣，希望有机会进一步沟通，谢谢。",
        "facts_used": [],
    }


def _normalize_greeting_draft(
    value: object,
    *,
    job: dict[str, object],
    decision: str,
    resume_facts: list[dict[str, object]],
) -> tuple[dict[str, object], list[str]]:
    if decision == "reject":
        return _safe_greeting_draft(job, decision=decision), []
    if not isinstance(value, dict):
        return _safe_greeting_draft(job, decision=decision), ["AI 招呼语结构无效，已使用安全模板"]
    text = _text(value.get("text"))
    facts_used = _strings(value.get("facts_used"))
    resume_text = "\n".join(
        _text(fact.get("fact_value"))
        for fact in resume_facts
        if not fact.get("sensitive") and _text(fact.get("fact_value"))
    ).lower()
    unsupported = [
        fact
        for fact in facts_used
        if not resume_text
        or not any(
            part and part in resume_text
            for part in re.split(r"[，,、；;：:\s]+", fact.lower())
            if len(part) >= 2
        )
    ]
    if not text or unsupported:
        risk = (
            f"招呼语引用了未验证的简历事实：{'、'.join(unsupported)}"
            if unsupported
            else "AI 未生成有效招呼语"
        )
        return _safe_greeting_draft(job, decision=decision), [f"{risk}，已使用安全模板"]
    return {"status": "ready", "text": text[:300], "facts_used": facts_used}, []


def _normalize_hard_requirements(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        status = _text(item.get("status"))
        if not name or status not in {"pass", "fail", "unknown"}:
            continue
        normalized.append(
            {
                "name": name,
                "status": status,
                "jd_evidence": _text(item.get("jd_evidence")),
                "resume_evidence": _text(item.get("resume_evidence")),
            }
        )
    return normalized


def _normalize_dimensions(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): round(_clamp_score(score), 2)
        for key, score in value.items()
        if str(key).strip()
    }


def _normalize_gaps(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict) or not _text(item.get("item")):
            continue
        severity = _text(item.get("severity"))
        normalized.append(
            {
                "item": _text(item.get("item")),
                "severity": severity if severity in {"high", "medium", "low"} else "medium",
                "can_fix_by_resume": bool(item.get("can_fix_by_resume")),
            }
        )
    return normalized


def _normalize_resume_suggestions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict) or not _text(item.get("suggestion")):
            continue
        normalized.append(
            {
                "section": _text(item.get("section")) or "简历",
                "suggestion": _text(item.get("suggestion")),
                "basis": _text(item.get("basis")),
            }
        )
    return normalized


def _find_resume_evidence(resume_facts: list[dict[str, object]], keyword: str) -> str:
    for fact in resume_facts:
        value = _text(fact.get("fact_value"))
        if not fact.get("sensitive") and keyword.lower() in value.lower():
            return value[:240]
    return ""


def _rule_summary(
    decision: str,
    reasons: list[str],
    risks: list[str],
    missing: list[str],
) -> str:
    label = {"recommend": "建议投递", "review": "需要人工确认", "reject": "不建议投递"}.get(
        decision, "需要人工确认"
    )
    evidence = risks or missing or reasons
    return f"{label}：{'；'.join(evidence[:3])}"


def _clamp_score(value: object) -> float:
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _evaluate_salary(job, strategy, reasons, failures, missing) -> None:
    salary = _text(job.get("salary"))
    monthly_min = strategy.get("monthly_salary_min")
    monthly_max = strategy.get("monthly_salary_max_at_least")
    daily_min = strategy.get("daily_salary_min")
    if not any(value is not None for value in (monthly_min, monthly_max, daily_min)):
        return
    match = re.search(r"(\d+)\s*-\s*(\d+)\s*[Kk]", salary)
    daily = re.search(r"(\d+)\s*-\s*(\d+)\s*元/天", salary)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if monthly_min is not None and low < int(monthly_min):
            failures.append(f"月薪下限 {low}K 低于要求")
        elif monthly_max is not None and high < int(monthly_max):
            failures.append(f"月薪上限 {high}K 低于要求")
        else:
            reasons.append("月薪范围符合")
    elif daily:
        low = int(daily.group(1))
        if daily_min is not None and low < int(daily_min):
            failures.append(f"日薪下限 {low} 元低于要求")
        elif daily_min is not None:
            reasons.append("日薪范围符合")
        elif monthly_min is not None or monthly_max is not None:
            failures.append("岗位为日薪，不能满足月薪条件")
    else:
        missing.append("薪资")


def _exact_allowed(job, job_key, strategy, strategy_key, label, reasons, failures, missing):
    allowed = _strings(strategy.get(strategy_key))
    if not allowed:
        return
    value = _text(job.get(job_key))
    if not value:
        missing.append(label)
    elif value in allowed:
        reasons.append(f"{label}符合：{value}")
    else:
        failures.append(f"{label}不符合：{value}")


def _contains_any(text, values, label, reasons, failures):
    terms = _strings(values)
    if not terms:
        return
    hits = [term for term in terms if term.lower() in text.lower()]
    if hits:
        reasons.append(f"{label}命中：{'、'.join(hits[:5])}")
    else:
        failures.append(f"{label}未命中任一要求词")


def _contains_all(text, values, label, reasons, failures):
    terms = _strings(values)
    if not terms:
        return
    missing = [term for term in terms if term.lower() not in text.lower()]
    if missing:
        failures.append(f"{label}缺少：{'、'.join(missing[:5])}")
    else:
        reasons.append(f"{label}满足全部要求词")


def _exclude_terms(text, values, label, failures):
    hits = [term for term in _strings(values) if term.lower() in text.lower()]
    if hits:
        failures.append(f"{label}命中排除词：{'、'.join(hits[:5])}")


def _detect_job_type(job: dict[str, object]) -> str:
    text = " | ".join(_text(job.get(key)) for key in ("title", "job_labels", "salary"))
    if "实习" in text or re.search(r"\d+个月|\d+天/周", text):
        return "internship"
    if "兼职" in text:
        return "part_time"
    return "full_time"


def _job_type_label(value: str) -> str:
    return {"full_time": "正职", "internship": "实习", "part_time": "兼职"}.get(value, value)


def _strings(value: object) -> list[str]:
    return [str(item).strip() for item in value] if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value or "").strip()
