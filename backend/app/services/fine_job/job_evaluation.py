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
        return {
            "job_id": job_id,
            "decision": "reject",
            "confidence": 1.0,
            "reasons": list(filter_result.get("reasons") or []),
            "risks": ["未通过岗位筛选策略"],
            "missing_fields": list(filter_result.get("missing_fields") or []),
            "source": "rules",
        }
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
    return {
        "job_id": job_id,
        "decision": decision,
        "confidence": round(confidence, 2),
        "reasons": reasons or ["符合已配置的结构化条件"],
        "risks": risks,
        "missing_fields": missing,
        "source": "rules",
    }


def _evaluate_delivery_by_llm(
    jobs: list[dict[str, object]],
    *,
    strategy: dict[str, object],
    resume_facts: list[dict[str, object]],
    extra_requirement: str,
    config: AppConfig,
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
        f"{fact.get('fact_key')}：{fact.get('fact_value')}"
        for fact in resume_facts
        if not fact.get("sensitive") and str(fact.get("fact_value") or "").strip()
    ]
    strategy_context = {
        key: value
        for key, value in strategy.items()
        if key not in {"created_at", "updated_at"}
    }
    for start in range(0, len(jobs), 5):
        batch = jobs[start:start + 5]
        citations = []
        for job in batch:
            detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
            citations.append(
                {
                    "citation_id": str(job.get("job_id") or ""),
                    "title": _text(job.get("title")),
                    "source_name": _text(job.get("boss_name") or job.get("company")),
                    "snippet": " | ".join(
                        _text(job.get(key))
                        for key in ("salary", "location", "experience", "degree", "company_industry")
                    ),
                    "context_snippet": (
                        " | ".join(
                            _text(job.get(key))
                            for key in ("skills", "job_labels", "company_scale", "boss_active_status")
                        )
                        + "\nJD："
                        + _text(detail.get("jd"))[:1500]
                    ),
                }
            )
        artifact = provider.answer(
            question=(
                "请逐个评估岗位是否值得投递。answer 字段必须是 JSON 数组字符串，每项包含 "
                "job_id、decision、confidence、reasons、risks、missing_fields；"
                "decision 只能是 recommend、review、reject。只引用 decision=recommend 的岗位。"
                f"\n投递策略：{strategy_context}"
                f"\n简历事实：{resume_context[:30]}"
                f"\n本次额外要求：{extra_requirement.strip() or '无'}"
                "\n回答中说明主要匹配依据和风险，不得虚构简历或岗位信息。"
            ),
            mode="answer",
            evidence_citations=citations,
            grounded_items=[],
        )
        structured = _parse_llm_evaluations(artifact.answer, batch)
        if structured:
            results.extend(structured)
            continue
        selected = set(artifact.citation_ids)
        for job in batch:
            job_id = str(job.get("job_id") or "")
            detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
            has_detail = bool(_text(detail.get("jd")))
            if job_id in selected:
                decision = "recommend"
            elif not has_detail:
                decision = (
                    "reject" if strategy.get("insufficient_info_action") == "reject" else "review"
                )
            else:
                decision = "review"
            results.append(
                {
                    "job_id": job_id,
                    "decision": decision,
                    "confidence": round(float(artifact.confidence), 2),
                    "reasons": [artifact.answer],
                    "risks": [] if decision == "recommend" else ["AI 未将该岗位列为明确推荐"],
                    "missing_fields": [] if has_detail else ["完整JD"],
                    "source": "llm",
                }
            )
    return results


def _parse_llm_evaluations(
    answer: str,
    batch: list[dict[str, object]],
) -> list[dict[str, object]]:
    """解析 AnswerProvider 的内层逐岗位 JSON；不合格时由调用方走兼容回退。"""
    text = answer.strip()
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    valid = {str(job.get("job_id") or ""): job for job in batch}
    normalized: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or "")
        decision = str(item.get("decision") or "")
        if job_id not in valid or decision not in {"recommend", "review", "reject"}:
            continue
        normalized.append(
            {
                "job_id": job_id,
                "decision": decision,
                "confidence": round(max(0.0, min(1.0, float(item.get("confidence") or 0))), 2),
                "reasons": _strings(item.get("reasons")),
                "risks": _strings(item.get("risks")),
                "missing_fields": _strings(item.get("missing_fields")),
                "source": "llm",
            }
        )
    return normalized if len(normalized) == len(valid) else []


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
