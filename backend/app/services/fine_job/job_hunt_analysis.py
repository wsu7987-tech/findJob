from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job import profile_store, profile_v3
from backend.app.services.fine_job.boss_capture_history import (
    get_capture_history_job,
    update_capture_job_delivery_evaluation,
)
from backend.app.services.fine_job.execution_reconciliation import (
    record_execution_evidence_with_connection,
)
from backend.app.services.fine_job.job_activity import (
    append_contact_origin_for_session_with_connection,
    append_job_activity_with_connection,
)
from backend.app.services.fine_job.filter_exclusions import assert_job_action_allowed, record_job_event
from backend.app.services.fine_job.profile_context import get_profile_context
from backend.app.services.fine_job.strategies import (
    get_filter_strategy,
    list_recommendation_strategies,
)
from backend.app.services.reasoning.codex_exec import run_codex_exec
from backend.app.utils import new_id, utc_now


ANALYSIS_VERSION = "job-hunt-refresh-analysis-v1"
PROMPT_VERSION = "job-hunt-refresh-codex-cli-v1"
MAX_PREPARE_MANIFEST_CHARACTERS = 1_000_000
MAX_ITEM_CONTEXT_CHARACTERS = 1_000_000
EXPLICIT_REJECTION_CONFIDENCE = 0.85
FOLLOWUP_DELAYS = {
    "greeted": timedelta(days=2),
    "communicating": timedelta(days=2),
    "resume_submitted": timedelta(days=2),
    "resume_viewed": timedelta(days=1),
    "under_review": timedelta(days=3),
}
FOLLOWUP_REASON_CODES = {
    "greeted": "high_match_no_reply",
    "communicating": "recruiter_owes_reply",
    "resume_submitted": "resume_sent_no_response",
    "resume_viewed": "resume_viewed_no_reply",
    "under_review": "under_review_stale",
}

ANALYSIS_WORKFLOWS = {
    "analyze_conversations",
    "generate_missing_suggestions",
    "generate_reply_drafts",
    "generate_followup_recommendations",
}
CONVERSATION_WORKFLOWS = {
    "analyze_conversations",
    "generate_reply_drafts",
    "generate_followup_recommendations",
}
ATTENTION_LABELS = {
    "needs_reply": "待回复",
    "needs_resume": "待发简历",
    "needs_followup": "建议跟进",
    "needs_rejection_reason": "建议询问",
    "needs_interview_confirm": "待确认面试",
    "needs_info": "待补充信息",
    "waiting": "等待 HR",
    "no_action": "无需处理",
    "unknown": "待判断",
}
ATTENTION_PRIORITY = {
    "needs_interview_confirm": 90,
    "needs_reply": 80,
    "needs_info": 75,
    "needs_resume": 70,
    "needs_rejection_reason": 55,
    "needs_followup": 45,
    "waiting": 20,
    "unknown": 10,
    "no_action": 0,
}
RECOMMENDED_ACTIONS = {
    "reply_recruiter",
    "send_resume",
    "follow_up",
    "ask_rejection_reason",
    "confirm_interview",
    "provide_information",
    "wait_for_recruiter",
    "no_further_action",
}


def analysis_requested(options: dict[str, Any]) -> bool:
    return any(bool(options.get(key)) for key in ANALYSIS_WORKFLOWS)


def prepare_run_analysis(db: Database, run_id: str) -> dict[str, Any]:
    run = _require_run(db, run_id)
    options = _load_json(run["workflow_options_json"], {})
    if not analysis_requested(options):
        return {"enabled": False, "run_id": run_id}
    _ensure_refresh_items_finished(db, run_id, run)

    existing = _load_context_snapshot(db, run_id)
    if existing is not None and _snapshot_has_manifest(existing):
        existing_summary = existing.get("summary") if isinstance(existing.get("summary"), dict) else {}
        existing_status = str(existing_summary.get("status") or existing.get("status") or "")
        if existing_status == "prepared":
            _set_run_step(db, run_id, "waiting_analysis_save")
        elif existing_status == "saved":
            _set_run_step(db, run_id, "waiting_completion")
        return existing

    scope = _load_scope(db, str(run["scope_id"]))
    _set_run_step(db, run_id, "prepare_analysis")
    manifest, prepare_summary = _build_unified_context(db, run_id, options, scope)
    serialized = _dump(manifest)
    manifest_characters = len(serialized)
    if manifest_characters > MAX_PREPARE_MANIFEST_CHARACTERS:
        blocker = {
            "enabled": True,
            "status": "blocked",
            "blocker": "analysis_manifest_too_large",
            "run_id": run_id,
            "manifest_characters": manifest_characters,
            "max_manifest_characters": MAX_PREPARE_MANIFEST_CHARACTERS,
            "size_breakdown": _size_breakdown(manifest),
            "summary": prepare_summary,
        }
        _store_context_snapshot(db, run_id, status="blocked", context=blocker, blocker_reason="analysis_manifest_too_large")
        _store_run_analysis_summary(db, run_id, {**prepare_summary, "status": "blocked", "blocker": "analysis_manifest_too_large"})
        return blocker

    needs_ai_save = bool(manifest["conversation_items"] or manifest["job_evaluation_items"])
    prepared_status = "prepared" if needs_ai_save else "saved"
    payload = {
        "enabled": True,
        "status": prepared_status,
        "run_id": run_id,
        "analysis_version": ANALYSIS_VERSION,
        "prompt_version": PROMPT_VERSION,
        "manifest_characters": manifest_characters,
        "max_manifest_characters": MAX_PREPARE_MANIFEST_CHARACTERS,
        "size_breakdown": _size_breakdown(manifest),
        "manifest": manifest,
        "summary": prepare_summary,
        "save_contract": _save_contract(),
    }
    _store_context_snapshot(db, run_id, status="prepared", context=payload, blocker_reason="")
    _store_run_analysis_summary(db, run_id, {**prepare_summary, "status": prepared_status})
    _set_run_step(db, run_id, "waiting_analysis_save" if needs_ai_save else "waiting_completion")
    return payload


def get_run_analysis_item_context(
    db: Database,
    run_id: str,
    *,
    item_type: str,
    item_id: str,
) -> dict[str, Any]:
    snapshot = _load_context_snapshot(db, run_id)
    if snapshot is None:
        raise AppError(409, "ANALYSIS_CONTEXT_MISSING", "请先调用 prepare_job_hunt_refresh_analysis。")
    if snapshot.get("status") == "blocked":
        raise AppError(409, "ANALYSIS_CONTEXT_BLOCKED", str(snapshot.get("blocker") or "分析上下文不可用。"))
    manifest = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else snapshot.get("context")
    if not isinstance(manifest, dict):
        raise AppError(409, "ANALYSIS_CONTEXT_INVALID", "分析任务清单不可用。")
    normalized_type = item_type.strip().lower()
    normalized_id = item_id.strip()
    if normalized_type in {"conversation", "chat_session", "session"}:
        manifest_item = _manifest_conversation_item(manifest, normalized_id)
        if manifest_item is None:
            raise AppError(404, "ANALYSIS_ITEM_NOT_IN_RUN", "该聊天会话不属于本次分析范围。")
        payload = _build_conversation_item_context(db, run_id, normalized_id, manifest_item)
    elif normalized_type in {"job_evaluation", "job"}:
        manifest_item = _manifest_job_item(manifest, normalized_id)
        if manifest_item is None:
            raise AppError(404, "ANALYSIS_ITEM_NOT_IN_RUN", "该岗位不属于本次投递建议分析范围。")
        payload = _build_job_evaluation_item_context(db, normalized_id, manifest_item)
    else:
        raise AppError(422, "ANALYSIS_ITEM_TYPE_INVALID", "分析 item 类型无效。")
    characters = len(_dump(payload))
    if characters > MAX_ITEM_CONTEXT_CHARACTERS:
        return {
            "status": "blocked",
            "blocker": "analysis_item_context_too_large",
            "run_id": run_id,
            "item_type": normalized_type,
            "item_id": normalized_id,
            "context_characters": characters,
            "max_context_characters": MAX_ITEM_CONTEXT_CHARACTERS,
            "size_breakdown": _size_breakdown(payload),
        }
    return {
        "status": "ready",
        "run_id": run_id,
        "item_type": normalized_type,
        "item_id": normalized_id,
        "context_characters": characters,
        "max_context_characters": MAX_ITEM_CONTEXT_CHARACTERS,
        "context": payload,
    }


def list_run_analysis_items(
    db: Database,
    run_id: str,
    *,
    item_type: str = "",
) -> dict[str, Any]:
    snapshot = _load_context_snapshot(db, run_id)
    if snapshot is None:
        raise AppError(409, "ANALYSIS_CONTEXT_MISSING", "请先调用 prepare_job_hunt_refresh_analysis。")
    if snapshot.get("status") == "blocked":
        raise AppError(409, "ANALYSIS_CONTEXT_BLOCKED", str(snapshot.get("blocker") or "分析上下文不可用。"))
    manifest = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else snapshot.get("context")
    if not isinstance(manifest, dict):
        raise AppError(409, "ANALYSIS_CONTEXT_INVALID", "分析任务清单不可用。")
    normalized_type = item_type.strip().lower()
    if normalized_type in {"job_evaluation", "job"}:
        items = _job_evaluation_analysis_items(db, run_id, manifest)
    elif normalized_type in {"conversation", "chat_session", "session"}:
        items = _conversation_analysis_items(db, run_id)
    elif not normalized_type:
        items = [
            *_conversation_analysis_items(db, run_id),
            *_job_evaluation_analysis_items(db, run_id, manifest),
        ]
    else:
        raise AppError(422, "ANALYSIS_ITEM_TYPE_INVALID", "分析 item 类型无效。")
    return {
        "run_id": run_id,
        "item_type": normalized_type or "all",
        "summary": _current_run_analysis_summary(db, run_id),
        "items": items,
    }


def save_run_analysis(
    db: Database,
    run_id: str,
    analysis_result: dict[str, Any],
    *,
    final_batch: bool = True,
) -> dict[str, Any]:
    snapshot = _load_context_snapshot(db, run_id)
    if snapshot is None:
        raise AppError(409, "ANALYSIS_CONTEXT_MISSING", "请先调用 prepare_job_hunt_refresh_analysis。")
    if snapshot.get("status") == "blocked":
        raise AppError(409, "ANALYSIS_CONTEXT_BLOCKED", str(snapshot.get("blocker") or "分析上下文不可用。"))
    options = _load_json(_require_run(db, run_id)["workflow_options_json"], {})
    context = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else snapshot.get("context")
    context = context if isinstance(context, dict) else {}
    allowed_session_ids = {
        str(item.get("session_id"))
        for item in context.get("conversation_items", [])
        if isinstance(item, dict) and item.get("session_id")
    }
    allowed_job_ids = {
        str(item.get("job_id"))
        for item in context.get("job_evaluation_items", [])
        if isinstance(item, dict) and item.get("job_id")
    }
    summary = _empty_summary(total=len(allowed_session_ids))
    summary["evaluation_jobs_total"] = len(allowed_job_ids)

    if any(bool(options.get(key)) for key in CONVERSATION_WORKFLOWS):
        for item in _result_list(analysis_result, "conversation_results"):
            session_id = str(item.get("session_id") or "").strip()
            if session_id not in allowed_session_ids:
                continue
            saved = _save_conversation_result(db, run_id, session_id, item, options)
            _merge_summary(summary, saved)

        if final_batch:
            _mark_missing_conversation_results_skipped(db, run_id, allowed_session_ids, summary)

    if bool(options.get("generate_missing_suggestions")):
        saved_job_ids: set[str] = set()
        job_outcomes: list[dict[str, Any]] = []
        for item in _result_list(analysis_result, "job_evaluation_results"):
            job_id = str(item.get("job_id") or "").strip()
            if job_id not in allowed_job_ids or job_id in saved_job_ids:
                continue
            saved_job_ids.add(job_id)
            saved = _save_job_evaluation_result(db, job_id, item, context)
            job_outcomes.append(_job_evaluation_outcome(job_id, saved, context))
            if saved["status"] == "saved":
                summary["generated_evaluation"] += 1
            else:
                summary["evaluation_jobs_skipped"] += 1
                reason = str(saved.get("reason") or "skipped_unknown")
                summary["evaluation_skip_reasons"][reason] = summary["evaluation_skip_reasons"].get(reason, 0) + 1
        missing_jobs = (
            {job_id for job_id in allowed_job_ids - saved_job_ids if not _job_already_evaluated(db, job_id)}
            if final_batch
            else set()
        )
        summary["evaluation_jobs_skipped"] += len(missing_jobs)
        if missing_jobs:
            summary["evaluation_skip_reasons"]["skipped_missing_ai_result"] = (
                summary["evaluation_skip_reasons"].get("skipped_missing_ai_result", 0) + len(missing_jobs)
            )
            for job_id in sorted(missing_jobs):
                job_outcomes.append(_job_evaluation_outcome(
                    job_id,
                    {"status": "skipped", "reason": "skipped_missing_ai_result"},
                    context,
                ))

    _refresh_conversation_summary_from_items(db, run_id, summary)
    old_summary = _current_run_analysis_summary(db, run_id)
    if bool(options.get("generate_missing_suggestions")):
        summary["job_evaluation_results"] = _merge_job_evaluation_outcomes(
            old_summary.get("job_evaluation_results"),
            job_outcomes,
        )
    summary["evaluation_jobs_total"] = max(
        int(summary.get("evaluation_jobs_total") or 0),
        int(old_summary.get("evaluation_jobs_total") or 0),
    )
    summary["generated_evaluation"] += int(old_summary.get("generated_evaluation") or 0)
    summary["evaluation_jobs_skipped"] += int(old_summary.get("evaluation_jobs_skipped") or 0)
    for reason, count in (old_summary.get("evaluation_skip_reasons") or {}).items():
        summary["evaluation_skip_reasons"][str(reason)] = summary["evaluation_skip_reasons"].get(str(reason), 0) + int(count or 0)
    summary["status"] = "saved" if final_batch else "saved_partial"
    _store_run_analysis_summary(db, run_id, summary)
    _set_run_step(db, run_id, "waiting_completion" if final_batch else "waiting_analysis_save")
    return summary


def ensure_analysis_ready_for_completion(db: Database, run_id: str) -> None:
    run = _require_run(db, run_id)
    options = _load_json(run["workflow_options_json"], {})
    if not analysis_requested(options):
        return
    snapshot = _load_context_snapshot(db, run_id)
    if snapshot is None:
        raise AppError(409, "ANALYSIS_NOT_PREPARED", "本次更新已启用分析，请先调用 prepare_job_hunt_refresh_analysis。")
    if snapshot.get("status") == "blocked":
        raise AppError(409, str(snapshot.get("blocker") or "ANALYSIS_CONTEXT_BLOCKED").upper(), "分析上下文过大或不可用，无法汇总。")
    with db.connect() as connection:
        pending = int(connection.execute(
            """
            SELECT COUNT(*) FROM fj_job_hunt_refresh_analysis_items
            WHERE run_id = ? AND status IN ('pending', 'running')
            """,
            (run_id,),
        ).fetchone()[0])
    if pending:
        raise AppError(409, "ANALYSIS_NOT_SAVED", "本次分析结果尚未保存，请先调用 save_job_hunt_refresh_analysis。")
    analysis = _current_run_analysis_summary(db, run_id)
    if analysis.get("status") != "saved":
        raise AppError(409, "ANALYSIS_NOT_SAVED", "本次分析结果尚未保存，请先调用 save_job_hunt_refresh_analysis。")


def latest_session_insight(db: Database, session_id: str) -> dict[str, Any] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM fj_conversation_insights
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["insight"] = _load_json(item.pop("insight_json"), {})
    return item


def latest_job_insight(db: Database, job_id: str) -> dict[str, Any] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM fj_conversation_insights
            WHERE job_id = ?
            ORDER BY updated_at DESC, created_at DESC LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["insight"] = _load_json(item.pop("insight_json"), {})
    return item


def analyze_single_session(
    db: Database,
    config: AppConfig,
    session_id: str,
) -> dict[str, Any]:
    """同一消息证据重复分析时覆盖单会话 Insight，并复用已存在的 Activity。"""
    # 正式 Activity/Pipeline 先独立提交，后续辅助任务处理失败也不会撤销已确认状态。
    with db.connect() as connection:
        session = _load_session(connection, session_id)
        job_id = _resolve_session_job_id(connection, session)
        messages = _load_messages(connection, session_id)
        conversation_messages = _real_conversation_messages(messages)
        deterministic = _anchor_facts(
            connection, session, job_id, messages, conversation_messages
        )

    task_sync: dict[str, Any]
    preparation_warning: str | None = None
    try:
        with db.connect() as connection:
            session = _load_session(connection, session_id)
            task_sync = _sync_tasks_from_facts(connection, session, job_id, deterministic)
    except Exception as exc:
        task_sync = {"auxiliary_error": type(exc).__name__}
        preparation_warning = (
            f"正式状态已保存，关联任务处理失败：{type(exc).__name__}"
        )

    with db.connect() as connection:
        session = _load_session(connection, session_id)
        messages = _load_messages(connection, session_id)
        context = _build_session_analysis_context(
            connection, db, session, job_id, messages, deterministic, task_sync
        )

    raw = _generate_single_analysis(config, context)
    message_ids = {str(message["id"]) for message in messages}
    insight = _normalize_codex_insight(
        raw,
        {"deterministic_facts": deterministic, "task_sync": task_sync},
        message_ids,
        {
            "analyze_conversations": True,
            "generate_reply_drafts": True,
            "generate_followup_recommendations": True,
        },
    )
    rejection_evidence_id: str | None = None
    reply_task_created = False
    with db.connect() as connection:
        if job_id:
            _created, rejection_evidence_id = _write_ai_activities(
                connection, job_id, session_id, insight, message_ids
            )
            _apply_followup_policy(connection, job_id, insight)
        insight_id = _save_insight(
            connection,
            run_id=None,
            session_id=session_id,
            job_id=job_id,
            insight=insight,
            model=_analysis_model_name(config),
            status="analyzed",
        )
        _save_attention_state(connection, None, session_id, job_id, insight_id, insight)
        reply_task_created = _create_analysis_reply_task(
            connection, session, insight_id, insight
        )
        if job_id:
            snapshot = connection.execute(
                "SELECT stage FROM fj_job_pipeline_snapshots WHERE job_id = ?", (job_id,)
            ).fetchone()
            if snapshot and snapshot["stage"] in {"offer", "rejected", "closed"}:
                _sync_legacy_terminal_status(
                    connection, job_id, str(snapshot["stage"]), rejection_evidence_id
                )
        terminal_fact = deterministic.get("rejected") or deterministic.get("job_closed")
        if not rejection_evidence_id and isinstance(terminal_fact, dict):
            rejection_evidence_id = str(terminal_fact.get("message_id") or "") or None

    auxiliary_warning = preparation_warning
    if job_id and rejection_evidence_id:
        try:
            with db.connect() as connection:
                _cancel_open_progress_tasks_for_rejection(
                    connection, job_id, session_id, rejection_evidence_id
                )
        except Exception as exc:
            auxiliary_warning = f"正式状态已保存，关联任务处理失败：{type(exc).__name__}"

    from backend.app.services.fine_job.job_progress import get_job_progress

    return {
        "insight": latest_session_insight(db, session_id),
        "progress": get_job_progress(db, job_id, session_id=session_id) if job_id else None,
        "reply_task_created": reply_task_created,
        "auxiliary_warning": auxiliary_warning,
    }


def _generate_single_analysis(config: AppConfig, context: dict[str, Any]) -> dict[str, Any]:
    if config.reasoning_executor == "llm" and (config.llm_provider or "").strip().lower() == "stub-llm":
        return _stub_single_analysis(context)
    prompt = (
        "你是 FineJob 求职沟通分析器。只依据当前会话判断求职进展。"
        "已招到合适候选人属于 rejected/position_filled；只有岗位取消、HC 关闭、停止招聘或职位关闭"
        "才属于 job_closed/headcount_closed。‘有消息再联系’等证据不足的表达不能判定为明确拒绝。"
        "progress_events 只输出有消息证据且置信度不低于 0.8 的正式事件。"
        "当 suggested_next_action 为 reply_recruiter、follow_up 或 ask_rejection_reason 时，"
        "reply_draft 必须生成一条待用户确认的中文消息；其余情况返回 null。"
        "招聘方只表达不合适或暂不考虑时，拒绝已成立，但具体原因仍需询问。"
        "草稿结合岗位匹配结论、已确认候选人资料和等待天数，保持简洁、真实且不重复提问。"
        "所有 evidence_message_ids 必须来自输入消息。只返回符合约定的 JSON。\n"
        f"上下文：{json.dumps(context, ensure_ascii=False, default=str)}"
    )
    schema = _single_analysis_json_schema()
    if config.reasoning_executor == "codex-cli":
        result = run_codex_exec(
            cli_path=config.codex_cli_path,
            prompt=prompt,
            output_schema=schema,
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
            timeout_seconds=config.codex_timeout_seconds,
        )
        return dict(result.output)
    if config.reasoning_executor != "llm" or not config.llm_model or not config.llm_api_key:
        raise AppError(400, "CONFIG_INVALID", "分析进展需要可用的 LLM 或 Codex 执行器。")
    try:
        response = httpx.post(
            f"{(config.llm_base_url or 'https://api.openai.com/v1').rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.llm_api_key}"},
            json={
                "model": config.llm_model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "严格输出求职进展分析 JSON。"},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=config.llm_timeout_seconds,
        )
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AppError(502, "CHAT_PROGRESS_ANALYSIS_FAILED", f"分析进展失败：{exc}") from exc


def _stub_single_analysis(context: dict[str, Any]) -> dict[str, Any]:
    messages = list(context.get("messages") or [])
    latest = messages[-1] if messages else {}
    evidence_id = str(latest.get("id") or "")
    waiting_on = "candidate" if latest.get("direction") == "inbound" else (
        "recruiter" if latest else "unknown"
    )
    rejection: dict[str, Any] = {
        "rejected": False, "rejection_type": "none", "outcome": "rejected",
        "reason_type": "unknown", "reason_source": "unknown", "reason_text": "",
        "confidence": 0, "evidence_message_ids": [],
    }
    progress_events: list[dict[str, Any]] = []
    for message in messages:
        for spec in _rule_activity_specs(message):
            event_type = str(spec["event_type"])
            if event_type in {"rejected", "job_closed"}:
                rejection = {
                    "rejected": True,
                    "rejection_type": "explicit",
                    "outcome": event_type,
                    "reason_type": spec.get("rejection_reason_category", "unknown"),
                    "reason_source": spec.get("rejection_reason_source", "unknown"),
                    "reason_text": spec.get("rejection_reason_summary", ""),
                    "confidence": 1,
                    "evidence_message_ids": [message["id"]],
                }
                waiting_on = "none"
            else:
                progress_events.append({
                    "event_type": event_type,
                    "confidence": 1,
                    "evidence_message_ids": [message["id"]],
                })
    pipeline = context.get("pipeline") if isinstance(context.get("pipeline"), dict) else {}
    rejection_needs_detail = bool(rejection.get("rejected")) and rejection.get("reason_type") in {
        "unknown", "fit"
    }
    if rejection_needs_detail or (
        pipeline.get("stage") == "rejected"
        and pipeline.get("rejection_reason_category") in {"unknown", "fit"}
    ):
        suggested_action = "ask_rejection_reason"
        reply_draft = "感谢您的回复。方便的话，想请教一下这次未能继续推进的主要原因，谢谢。"
    else:
        suggested_action = (
            "reply_recruiter" if waiting_on == "candidate" else
            "follow_up" if waiting_on == "recruiter" else
            "no_further_action"
        )
        reply_draft = (
            "您好，感谢您的消息。我已了解，会根据岗位要求及时补充相关信息。"
            if suggested_action == "reply_recruiter"
            else "您好，想礼貌跟进一下目前的评估进展。如需补充材料，请随时告诉我，谢谢。"
            if suggested_action == "follow_up"
            else None
        )
    return {"insight": {
        "conversation_summary": "已按当前本地聊天记录分析求职进展。",
        "current_conversation_state": "",
        "signals": [],
        "needs_candidate_reply": waiting_on == "candidate",
        "waiting_for_recruiter": waiting_on == "recruiter",
        "waiting_on": waiting_on,
        "progress_events": progress_events,
        "rejection_analysis": rejection,
        "suggested_next_action": suggested_action,
        "ai_followup_recommendation": {},
        "attention_status": "unknown",
        "reply_draft": reply_draft,
        "evidence_message_ids": [evidence_id] if evidence_id else [],
        "confidence": 1 if evidence_id else 0,
    }}


def _analysis_model_name(config: AppConfig) -> str:
    return str(config.codex_model if config.reasoning_executor == "codex-cli" else config.llm_model or "")


def _single_analysis_json_schema() -> dict[str, Any]:
    progress_event_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "event_type": {
                "type": "string",
                "enum": [
                    "resume_requested", "resume_submitted", "resume_accepted",
                    "resume_viewed", "under_review", "interview_invited",
                    "interview_scheduled", "offer_received",
                ],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_message_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["event_type", "confidence", "evidence_message_ids"],
    }
    rejection_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rejected": {"type": "boolean"},
            "outcome": {"type": "string", "enum": ["rejected", "job_closed"]},
            "rejection_type": {
                "type": "string",
                "enum": ["explicit", "soft", "none"],
            },
            "reason_type": {
                "type": "string",
                "enum": [
                    "experience", "education", "skills", "industry_background",
                    "salary", "location", "availability", "position_filled",
                    "headcount_closed", "fit", "other", "unknown",
                ],
            },
            "reason_text": {"type": "string"},
            "reason_source": {
                "type": "string",
                "enum": ["recruiter_explicit", "ai_inferred", "unknown"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_message_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "rejected", "outcome", "rejection_type", "reason_type", "reason_text",
            "reason_source", "confidence", "evidence_message_ids",
        ],
    }
    followup_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "attention_status": {
                "type": "string",
                "enum": list(ATTENTION_LABELS),
            },
            "recommended_action": {
                "type": "string",
                "enum": sorted(RECOMMENDED_ACTIONS),
            },
            "reason": {"type": "string"},
            "decision": {
                "type": "string",
                "enum": ["follow", "wait", "do_not_follow"],
            },
            "reason_code": {"type": "string"},
            "recommended_at": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
            },
            "evidence_message_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "attention_status", "recommended_action", "reason", "decision",
            "reason_code", "recommended_at", "evidence_message_ids",
        ],
    }
    insight_properties = {
        "conversation_summary": {"type": "string"},
        "current_conversation_state": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
        "needs_candidate_reply": {"type": "boolean"},
        "waiting_for_recruiter": {"type": "boolean"},
        "waiting_on": {
            "type": "string",
            "enum": ["candidate", "recruiter", "none", "unknown"],
        },
        "progress_events": {"type": "array", "items": progress_event_schema},
        "rejection_analysis": rejection_schema,
        "suggested_next_action": {
            "type": "string",
            "enum": sorted(RECOMMENDED_ACTIONS),
        },
        "ai_followup_recommendation": followup_schema,
        "attention_status": {"type": "string", "enum": list(ATTENTION_LABELS)},
        "reply_draft": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "evidence_message_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "insight": {
                "type": "object",
                "additionalProperties": False,
                "properties": insight_properties,
                "required": list(insight_properties),
            }
        },
        "required": ["insight"],
    }


def _build_conversation_item_context(
    db: Database,
    run_id: str,
    session_id: str,
    manifest_item: dict[str, Any],
) -> dict[str, Any]:
    with db.connect() as connection:
        session = _load_session(connection, session_id)
        job_id = str(manifest_item.get("job_id") or session["job_id"] or "") or None
        messages = _load_messages(connection, session_id)
        prepared = _load_prepared_item_result(connection, run_id, session_id)
        deterministic = prepared.get("deterministic_facts") if isinstance(prepared.get("deterministic_facts"), dict) else {}
        task_sync = prepared.get("task_sync") if isinstance(prepared.get("task_sync"), dict) else {}
        context = _build_session_analysis_context(
            connection,
            db,
            session,
            job_id,
            messages,
            deterministic,
            task_sync,
        )
    context["candidate_profile_context"] = _candidate_context(db, view="chat")
    context["manifest_item"] = manifest_item
    return context


def _build_job_evaluation_item_context(
    db: Database,
    job_id: str,
    manifest_item: dict[str, Any],
) -> dict[str, Any]:
    job = get_capture_history_job(db, job_id)
    return {
        "job_id": job_id,
        "job_detail_version": int(job.get("detail_version") or 1),
        "job": _job_public_payload(job),
        "recommendation_strategy": manifest_item.get("recommendation_strategy"),
        "filter_strategy": manifest_item.get("filter_strategy"),
        "candidate_profile_id": str(manifest_item.get("candidate_profile_id") or ""),
        "resume_version_id": str(manifest_item.get("resume_version_id") or ""),
        "context_revision_id": str(manifest_item.get("context_revision_id") or ""),
        "context_dependency_versions": manifest_item.get("context_dependency_versions") if isinstance(manifest_item.get("context_dependency_versions"), dict) else {},
        "candidate_profile_context": _candidate_context(db, view="evaluation"),
        "existing_evaluation": job.get("delivery_evaluation"),
        "expected_output": _job_evaluation_output_schema(),
        "manifest_item": manifest_item,
    }


def _build_unified_context(
    db: Database,
    run_id: str,
    options: dict[str, Any],
    scope: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_ids = [str(value) for value in scope.get("session_ids_in_scope") or [] if str(value)]
    summary = _empty_summary(total=len(session_ids))
    context: dict[str, Any] = {
        "run_id": run_id,
        "workflow_options": {key: bool(options.get(key)) for key in ANALYSIS_WORKFLOWS},
        "scope": {
            "id": str(scope.get("id") or ""),
            "selected_since_time": str(scope.get("selected_since_time") or ""),
            "scope_generated_at": str(scope.get("scope_generated_at") or ""),
            "counts": _load_json(scope.get("counts_json"), {}) if "counts_json" in scope else scope.get("counts", {}),
        },
        "rules": {
            "deterministic_before_ai": [
                "完整历史下按 FineJob 动作证据和首条真实消息判定 contact_origin；证据不足保持 unknown",
                "system message content exactly 附件状态更新 => resume_submitted",
                "inbound real chat message => recruiter_replied",
                "outbound real chat message => candidate_replied",
                "已经招到合适候选人/已招满 => rejected + position_filled",
                "仅岗位取消、HC 关闭、停止招聘、职位关闭 => job_closed + headcount_closed",
            ],
            "item_isolation": "统一上下文只用于一次 AI 调用；生成结果时必须按 session_id/job_id 独立，不得把不同聊天、岗位或 JD 的事实交叉使用。",
            "ai_boundary": "AI 结果只保存 insight/recommendation；只有代码规则校验后的高置信 explicit_rejection 可写 rejected Activity。",
            "reply_boundary": "回复草稿只保存展示，不发送，不创建发送动作。",
        },
        "context_reader": {
            "tool": "finejob.get_job_hunt_refresh_analysis_item_context",
            "usage": "按 conversation_items/job_evaluation_items 中的 context_arguments 读取单个 item 详情。",
        },
        "conversation_items": [],
        "job_evaluation_items": [],
        "skipped_items": [],
    }

    conversation_enabled = any(bool(options.get(key)) for key in CONVERSATION_WORKFLOWS)
    if conversation_enabled:
        for session_id in session_ids:
            result = _prepare_session_context(db, run_id, session_id)
            if result.get("status") == "prepared":
                context["conversation_items"].append(result["item"])
            else:
                context["skipped_items"].append(result)
                _merge_summary(summary, result)

    if bool(options.get("generate_missing_suggestions")):
        missing_entries = list(scope.get("jobs_missing_evaluation") or [])
        summary["evaluation_jobs_total"] = len(missing_entries)
        for entry in missing_entries:
            if isinstance(entry, dict) and not str(entry.get("job_id") or "").strip():
                skipped = {
                    "status": "skipped",
                    "item_type": "job_evaluation",
                    "session_id": str(entry.get("session_id") or ""),
                    "reason": "skipped_missing_job",
                }
                context["skipped_items"].append(skipped)
                summary["evaluation_jobs_skipped"] += 1
                summary["evaluation_skip_reasons"]["skipped_missing_job"] = (
                    summary["evaluation_skip_reasons"].get("skipped_missing_job", 0) + 1
                )
        job_ids = _scope_missing_evaluation_job_ids(scope)
        prepared_jobs: set[str] = set()
        for job_id in job_ids:
            if job_id in prepared_jobs:
                continue
            prepared_jobs.add(job_id)
            result = _prepare_job_evaluation_context(db, run_id, job_id)
            if result.get("status") == "prepared":
                context["job_evaluation_items"].append(result["item"])
            else:
                context["skipped_items"].append(result)
                summary["evaluation_jobs_skipped"] += 1
                reason = str(result.get("reason") or "skipped_unknown")
                summary["evaluation_skip_reasons"][reason] = summary["evaluation_skip_reasons"].get(reason, 0) + 1

    return context, summary


def _prepare_session_context(db: Database, run_id: str, session_id: str) -> dict[str, Any]:
    try:
        # 规则确认的 Activity/Pipeline 使用独立事务，辅助清理失败不影响正式状态。
        with db.connect() as connection:
            session = _load_session(connection, session_id)
            job_id = _resolve_session_job_id(connection, session)
            messages = _load_messages(connection, session_id)
            conversation_messages = _real_conversation_messages(messages)
            deterministic = _anchor_facts(connection, session, job_id, messages, conversation_messages)

        try:
            with db.connect() as connection:
                session = _load_session(connection, session_id)
                task_sync = _sync_tasks_from_facts(connection, session, job_id, deterministic)
        except Exception as exc:
            task_sync = {"auxiliary_error": type(exc).__name__}

        with db.connect() as connection:
            session = _load_session(connection, session_id)
            messages = _load_messages(connection, session_id)
            status = "prepared" if messages and job_id else "skipped"
            skipped_reasons: list[str] = []
            if not messages:
                skipped_reasons.append("skipped_missing_chat_messages")
            if not job_id:
                skipped_reasons.append("skipped_missing_job")
            if deterministic.get("greeting_anchor_skip_reason"):
                skipped_reasons.append(str(deterministic["greeting_anchor_skip_reason"]))
            item_result = {
                "status": status,
                "skipped_reasons": skipped_reasons,
                "deterministic_facts": deterministic,
                "task_sync": task_sync,
                "attention_status": "unknown",
                "generated_evaluation": False,
                "generated_reply_draft": False,
                "updated_pipeline": bool(deterministic.get("activities_created")),
                "reconciled_tasks": _task_sync_count(task_sync) > 0,
                "rejection_detected": bool(deterministic.get("rejected")),
            }
            _upsert_analysis_item(
                connection,
                run_id=run_id,
                session_id=session_id,
                job_id=job_id,
                status="pending" if status == "prepared" else "skipped",
                result=item_result,
                started_at=utc_now(),
                completed_at=utc_now() if status == "skipped" else None,
            )
        if status == "skipped":
            return {"status": "skipped", "session_id": session_id, "job_id": job_id, "skipped_reasons": skipped_reasons, **item_result}
        return {
            "status": "prepared",
            "item": _conversation_manifest_item(
                run_id=run_id,
                session=session,
                job_id=job_id,
                messages=messages,
                deterministic=deterministic,
                task_sync=task_sync,
            ),
        }
    except Exception as exc:
        result = _mark_analysis_failed(db, run_id, session_id, exc)
        return {"status": "failed", "session_id": session_id, **result}


def _prepare_job_evaluation_context(db: Database, run_id: str, job_id: str) -> dict[str, Any]:
    try:
        job = get_capture_history_job(db, job_id)
    except AppError as exc:
        return {"status": "skipped", "job_id": job_id, "reason": exc.error_category}
    if job.get("delivery_evaluation"):
        return {"status": "skipped", "job_id": job_id, "reason": "skipped_existing_evaluation"}
    if job.get("detail_status") != "completed":
        return {"status": "skipped", "job_id": job_id, "reason": "skipped_missing_jd"}
    detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
    if not any(_text(detail.get(key)) for key in ("jd", "description", "job_description")):
        return {"status": "skipped", "job_id": job_id, "reason": "skipped_missing_jd"}
    strategy = _default_recommendation_strategy(db)
    if strategy is None:
        return {"status": "skipped", "job_id": job_id, "reason": "skipped_missing_recommendation_strategy"}
    filter_strategy = _load_filter_strategy(db, strategy)
    profile_id = str(strategy.get("candidate_profile_id") or "").strip()
    resume_version_id = str(strategy.get("resume_version_id") or "").strip()
    context_revision = _current_evaluation_context_revision(db, profile_id, resume_version_id)
    if not profile_id or not resume_version_id or not context_revision:
        return {"status": "skipped", "job_id": job_id, "reason": "skipped_missing_profile"}
    return {
        "status": "prepared",
        "item": {
            "item_type": "job_evaluation",
            "job_id": job_id,
            "job_detail_version": int(job.get("detail_version") or 1),
            "title": str(job.get("title") or ""),
            "company_name": str(job.get("boss_name") or job.get("company_name") or ""),
            "recommendation_strategy": strategy,
            "filter_strategy": filter_strategy,
            "candidate_profile_id": profile_id,
            "resume_version_id": resume_version_id,
            "context_revision_id": str(context_revision["id"]),
            "context_dependency_versions": _load_json(context_revision["dependency_versions_json"], {}),
            "context_tool": "finejob.get_job_hunt_refresh_analysis_item_context",
            "context_arguments": {
                "run_id": run_id,
                "item_type": "job_evaluation",
                "item_id": job_id,
            },
            "expected_output": _job_evaluation_output_schema(),
        },
    }


def _conversation_manifest_item(
    *,
    run_id: str,
    session: sqlite3.Row,
    job_id: str | None,
    messages: list[dict[str, Any]],
    deterministic: dict[str, Any],
    task_sync: dict[str, Any],
) -> dict[str, Any]:
    conversation_messages = _real_conversation_messages(messages)
    return {
        "item_type": "conversation",
        "session_id": str(session["id"]),
        "job_id": job_id,
        "recruiter": str(session["peer_name"] or ""),
        "company": str(session["company_name"] or ""),
        "history_has_more": bool(session["history_has_more"]),
        "message_count": len(messages),
        "real_conversation_message_count": len(conversation_messages),
        "latest_message": _message_digest(messages[-1]) if messages else None,
        "deterministic_facts": deterministic,
        "task_sync": task_sync,
        "context_tool": "finejob.get_job_hunt_refresh_analysis_item_context",
        "context_arguments": {
            "run_id": run_id,
            "item_type": "conversation",
            "item_id": str(session["id"]),
        },
        "expected_output": _conversation_output_schema(),
    }


def _message_digest(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or ""),
        "direction": str(message.get("direction") or ""),
        "message_type": str(message.get("message_type") or ""),
        "sent_at": str(message.get("sent_at") or ""),
        "source": str(message.get("source") or ""),
        "content_preview": _text(message.get("content"))[:300],
    }


def _rule_activity_specs(message: dict[str, Any]) -> list[dict[str, Any]]:
    """把平台确定性文案转换为可追溯的正式求职事件。"""
    content = _text(message.get("content")).strip()
    compact = "".join(content.split())
    specs: list[dict[str, Any]] = []
    if message.get("message_type") == "system":
        if content == "附件状态更新" or ("附件简历" in compact and "已发送" in compact):
            specs.append({"event_type": "resume_submitted"})
        if "对方已同意" in compact and "简历已发送给对方" in compact:
            specs.append({"event_type": "resume_accepted"})
        if "对方已查看" in compact and "附件简历" in compact:
            specs.append({"event_type": "resume_viewed"})
        return specs
    if message.get("direction") != "inbound":
        return specs

    closed_phrases = ("HC关闭", "HC已关闭", "岗位取消", "职位取消", "停止招聘", "岗位关闭", "职位关闭")
    filled_phrases = ("已经招到合适候选人", "已经找到合适候选人", "已经找到人", "岗位已招满", "已经招满")
    review_phrases = (
        "发给业务部门看看", "发给用人部门看看", "给用人部门评估",
        "用人部门评估", "内部再评估", "业务部门评估",
    )
    if any(phrase in compact for phrase in closed_phrases):
        return [{
            "event_type": "job_closed",
            "rejection_reason_source": "recruiter_explicit",
            "rejection_reason_category": "headcount_closed",
            "rejection_reason_summary": content,
        }]
    if any(phrase in compact for phrase in filled_phrases):
        return [{
            "event_type": "rejected",
            "rejection_reason_source": "recruiter_explicit",
            "rejection_reason_category": "position_filled",
            "rejection_reason_summary": content,
        }]
    rejection_categories = (
        (("经验不匹配", "经验和岗位不太匹配", "工作经验不足"), "experience"),
        (("技能方向不符合", "技能不匹配"), "skills"),
        (("学历不符合", "学历不匹配"), "education"),
        (("薪资不匹配",), "salary"),
        (("到岗时间不合适",), "availability"),
        (("不合适", "暂时不考虑"), "fit"),
    )
    for phrases, category in rejection_categories:
        if any(phrase in compact for phrase in phrases):
            return [{
                "event_type": "rejected",
                "rejection_reason_source": "recruiter_explicit",
                "rejection_reason_category": category,
                "rejection_reason_summary": content,
            }]
    if any(phrase in compact for phrase in review_phrases):
        return [{"event_type": "under_review"}]
    return specs


def _anchor_facts(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    job_id: str | None,
    messages: list[dict[str, Any]],
    conversation_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "activities_created": 0,
        "greeting_anchor": None,
        "greeting_anchor_skip_reason": None,
        "resume_submitted": None,
        "resume_accepted": None,
        "resume_viewed": None,
        "under_review": None,
        "rejected": None,
        "job_closed": None,
        "first_recruiter_reply": None,
        "latest_candidate_reply": None,
    }
    if not job_id:
        return facts

    facts["activities_created"] += append_contact_origin_for_session_with_connection(
        connection, session, messages
    )

    if bool(session["history_has_more"]):
        facts["greeting_anchor_skip_reason"] = "skipped_missing_full_history_for_greeting_anchor"
    elif not conversation_messages:
        facts["greeting_anchor_skip_reason"] = "skipped_missing_real_chat_message_for_greeting_anchor"
    else:
        first_message = conversation_messages[0]
        # 已建立真实会话即可关闭旧打招呼任务，沟通来源由独立规则事件表达。
        facts["greeting_anchor"] = _message_fact(first_message, "greeting_sent")

    for message in conversation_messages:
        event_type = "recruiter_replied" if message["direction"] == "inbound" else "candidate_replied"
        _activity, inserted = append_job_activity_with_connection(
            connection,
            job_id=job_id,
            chat_session_id=str(session["id"]),
            event_type=event_type,
            occurred_at=str(message["sent_at"]),
            source="chat",
            source_ref_type="chat_message",
            source_ref_id=str(message["id"]),
            confidence=1.0,
            evidence_level="direct",
            payload={
                "direction": str(message["direction"]),
                "platform_message_id": str(message["platform_message_id"]),
            },
            dedupe_key=f"chat_message:{message['id']}:{event_type}",
        )
        facts["activities_created"] += int(inserted)
        if event_type == "recruiter_replied" and facts["first_recruiter_reply"] is None:
            facts["first_recruiter_reply"] = _message_fact(message, event_type)
        if event_type == "candidate_replied":
            facts["latest_candidate_reply"] = _message_fact(message, event_type)

    for message in messages:
        for spec in _rule_activity_specs(message):
            event_type = str(spec["event_type"])
            payload = {
                "derived_by": "rule",
                "analysis_version": ANALYSIS_VERSION,
                "evidence_message_id": str(message["id"]),
                "evidence_text": _text(message.get("content"))[:500],
                **{key: value for key, value in spec.items() if key != "event_type"},
            }
            _activity, inserted = append_job_activity_with_connection(
                connection,
                job_id=job_id,
                chat_session_id=str(session["id"]),
                event_type=event_type,
                occurred_at=str(message["sent_at"]),
                source="rule",
                source_ref_type="chat_message",
                source_ref_id=str(message["id"]),
                confidence=1.0,
                evidence_level="direct",
                payload=payload,
                dedupe_key=f"chat_message:{message['id']}:{event_type}:rule-v1",
            )
            facts["activities_created"] += int(inserted)
            if event_type in facts and facts[event_type] is None:
                facts[event_type] = _message_fact(message, event_type)
    return facts


def _sync_tasks_from_facts(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    job_id: str | None,
    facts: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "review_items_archived": 0,
        "automation_actions_cancelled": 0,
        "actions_reconciled": 0,
        "reply_tasks_staled": 0,
        "send_actions_cancelled": 0,
        "application_status_updated": False,
    }
    if not job_id:
        return result
    now = utc_now()

    greeting = facts.get("greeting_anchor")
    if isinstance(greeting, dict):
        cursor = connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'dismissed',
                resolution_note = '已检测到聊天第一条真实会话消息，打招呼事实已成立',
                updated_at = ?, resolved_at = ?
            WHERE job_id = ? AND action_type = 'start_conversation'
              AND status IN ('pending', 'rejected')
            """,
            (now, now, job_id),
        )
        result["review_items_archived"] += int(cursor.rowcount)
        for action in connection.execute(
            """
            SELECT id, status
            FROM fj_automation_actions
            WHERE job_id = ? AND action_type = 'BOSS_DEFAULT_GREETING'
            """,
            (job_id,),
        ).fetchall():
            if str(action["status"]) == "queued":
                connection.execute(
                    """
                    UPDATE fj_automation_actions
                    SET status = 'cancelled', execution_state = 'cancelled',
                        last_status_code = 'SUPERSEDED_BY_OBSERVED_FACT',
                        last_error = '已检测到聊天第一条真实会话消息，取消未开始的打招呼任务',
                        completed_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (now, now, action["id"]),
                )
                result["automation_actions_cancelled"] += 1
                continue
            evidence, _inserted, reconciliation = record_execution_evidence_with_connection(
                connection,
                action_ref_type="automation_action",
                action_ref_id=str(action["id"]),
                evidence_type="conversation_created",
                source="chat",
                source_ref_type="chat_message",
                source_ref_id=str(greeting["message_id"]),
                observed_at=str(greeting["occurred_at"]),
                confidence=1.0,
                evidence_level="direct",
                payload={
                    "confirmed": True,
                    "session_id": str(session["id"]),
                    "reason": "observed_chat_first_message",
                },
                dedupe_key=f"chat_message:{greeting['message_id']}:automation_action:{action['id']}:conversation_created",
            )
            result["actions_reconciled"] += int(reconciliation is not None or bool(evidence))

    resume = facts.get("resume_submitted")
    if isinstance(resume, dict):
        connection.execute(
            """
            INSERT INTO fj_job_applications (
              id, job_id, company_id, status, source, source_action_id,
              evidence_level, applied_at, note, created_at, updated_at
            )
            SELECT ?, j.id, j.company_id, 'communicating', 'mcp', ?, 'confirmed',
                   ?, '已检测到附件状态更新，简历投递事实已成立', ?, ?
            FROM fj_boss_jobs j WHERE j.id = ?
            ON CONFLICT(job_id) DO UPDATE SET
              company_id = excluded.company_id,
              status = CASE
                WHEN fj_job_applications.status = 'rejected' THEN fj_job_applications.status
                ELSE excluded.status
              END,
              source = excluded.source,
              source_action_id = excluded.source_action_id,
              evidence_level = excluded.evidence_level,
              applied_at = excluded.applied_at,
              note = excluded.note,
              updated_at = excluded.updated_at
            """,
            (new_id(), str(resume["message_id"]), str(resume["occurred_at"]), now, now, job_id),
        )
        result["application_status_updated"] = True

    candidate_reply = facts.get("latest_candidate_reply")
    if isinstance(candidate_reply, dict):
        candidate_time = str(candidate_reply["occurred_at"])
        cursor = connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET status = 'stale', cancelled_at = ?, updated_at = ?,
                decision_reason = '已检测到候选人后续回复，旧回复草稿失效'
            WHERE session_id = ?
              AND status IN ('pending_generation', 'generating', 'awaiting_review')
              AND created_at < ?
            """,
            (now, now, session["id"], candidate_time),
        )
        result["reply_tasks_staled"] += int(cursor.rowcount)
        cursor = connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'cancelled', outcome = NULL,
                status_code = 'SUPERSEDED_BY_OBSERVED_REPLY',
                error_message = '已检测到候选人后续回复，取消未发送动作',
                completed_at = ?, updated_at = ?
            WHERE session_id = ? AND status IN ('queued', 'leased')
              AND created_at < ?
            """,
            (now, now, session["id"], candidate_time),
        )
        result["send_actions_cancelled"] += int(cursor.rowcount)
    return result


def _save_conversation_result(
    db: Database,
    run_id: str,
    session_id: str,
    item: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    rejection_evidence_id: str | None = None
    with db.connect() as connection:
        session = _load_session(connection, session_id)
        job_id = _resolve_session_job_id(connection, session)
        prepared = _load_prepared_item_result(connection, run_id, session_id)
        messages = _load_messages(connection, session_id)
        message_ids = {str(message["id"]) for message in messages}
        insight = _normalize_codex_insight(item, prepared, message_ids, options)
        if job_id:
            ai_activity_count, rejection_evidence_id = _write_ai_activities(
                connection, job_id, session_id, insight, message_ids
            )
            _apply_followup_policy(connection, job_id, insight)
            snapshot = connection.execute(
                "SELECT stage FROM fj_job_pipeline_snapshots WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if snapshot and snapshot["stage"] in {"offer", "rejected", "closed"}:
                _sync_legacy_terminal_status(
                    connection,
                    job_id,
                    str(snapshot["stage"]),
                    rejection_evidence_id,
                )
            deterministic = prepared.get("deterministic_facts") or {}
            if not rejection_evidence_id:
                terminal_fact = deterministic.get("rejected") or deterministic.get("job_closed")
                if isinstance(terminal_fact, dict):
                    rejection_evidence_id = str(terminal_fact.get("message_id") or "") or None
        else:
            ai_activity_count = 0
        insight_id = _save_insight(
            connection,
            run_id=run_id,
            session_id=session_id,
            job_id=job_id,
            insight=insight,
            model=str(item.get("model") or "codex-cli"),
            status="analyzed",
        )
        _save_attention_state(connection, run_id, session_id, job_id, insight_id, insight)
        reply_task_created = _create_analysis_reply_task(
            connection, session, insight_id, insight
        ) if bool(options.get("generate_reply_drafts")) else False
        result = {
            "status": "analyzed",
            "skipped_reasons": prepared.get("skipped_reasons") or [],
            "deterministic_facts": prepared.get("deterministic_facts") or {},
            "task_sync": prepared.get("task_sync") or {},
            "generated_evaluation": False,
            "generated_reply_draft": bool(insight.get("reply_draft")),
            "reply_task_created": reply_task_created,
            "updated_pipeline": bool((prepared.get("deterministic_facts") or {}).get("activities_created") or ai_activity_count),
            "reconciled_tasks": _task_sync_count(prepared.get("task_sync") or {}) > 0,
            "rejection_detected": bool(
                (insight.get("rejection_analysis") or {}).get("rejected")
                or (prepared.get("deterministic_facts") or {}).get("rejected")
            ),
            "attention_status": str(insight.get("attention_status") or "unknown"),
            "insight_id": insight_id,
        }
        _upsert_analysis_item(
            connection,
            run_id=run_id,
            session_id=session_id,
            job_id=job_id,
            status="analyzed",
            result=result,
            started_at=None,
            completed_at=utc_now(),
        )
    if job_id and rejection_evidence_id:
        try:
            # 辅助任务清理与执行证据使用独立事务，失败时正式求职状态仍然保留。
            with db.connect() as connection:
                _cancel_open_progress_tasks_for_rejection(
                    connection, job_id, session_id, rejection_evidence_id
                )
        except Exception as exc:
            result["auxiliary_warning"] = (
                "正式拒绝状态已保存；关联任务清理或执行证据记录失败："
                f"{type(exc).__name__}"
            )
    return result


def _sync_legacy_terminal_status(
    connection: sqlite3.Connection,
    job_id: str,
    stage: str,
    evidence_message_id: str | None,
) -> None:
    """兼容仍读取旧投递表的调用方，正式状态始终由 Pipeline 决定。"""
    now = utc_now()
    connection.execute(
        """
        INSERT INTO fj_job_applications (
          id, job_id, company_id, status, source, source_action_id,
          evidence_level, applied_at, note, created_at, updated_at
        )
        SELECT ?, j.id, j.company_id, ?, 'mcp', ?, 'inferred', ?, ?, ?, ?
        FROM fj_boss_jobs j WHERE j.id = ?
        ON CONFLICT(job_id) DO UPDATE SET
          status = excluded.status,
          source = excluded.source,
          source_action_id = excluded.source_action_id,
          evidence_level = excluded.evidence_level,
          applied_at = excluded.applied_at,
          note = excluded.note,
          updated_at = excluded.updated_at
        """,
        (
            new_id(), stage, evidence_message_id, now,
            "由正式求职进展同步终态", now, now, job_id,
        ),
    )


def _create_analysis_reply_task(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    insight_id: str,
    insight: dict[str, Any],
) -> bool:
    draft = insight.get("reply_draft")
    if not isinstance(draft, dict) or not _text(draft.get("text")):
        return False
    action = str(insight.get("suggested_next_action") or "")
    if action not in {"reply_recruiter", "follow_up", "ask_rejection_reason"}:
        return False
    action_kind = {
        "follow_up": "followup",
        "ask_rejection_reason": "ask_rejection_reason",
    }.get(action, "reply")
    based_on_message_id = (
        session["latest_inbound_message_id"]
        if action_kind == "reply"
        else session["latest_message_id"]
    )
    if not based_on_message_id:
        return False
    active = connection.execute(
        """
        SELECT id, insight_id FROM fj_chat_reply_tasks
        WHERE session_id = ?
          AND status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed')
        ORDER BY updated_at DESC LIMIT 1
        """,
        (session["id"],),
    ).fetchone()
    if active is not None:
        return False
    now = utc_now()
    text = _text(draft.get("text"))[:5000]
    recommendation = insight.get("ai_followup_recommendation")
    reason = _text(recommendation.get("reason")) if isinstance(recommendation, dict) else ""
    connection.execute(
        """
        INSERT INTO fj_chat_reply_tasks (
          id, session_id, trigger_source, action_kind, insight_id, status,
          based_on_message_id, based_on_session_version, input_message_ids_json,
          decision, decision_reason, context_json, draft_text, final_text,
          generation_model, generated_at, created_at, updated_at
        ) VALUES (?, ?, 'manual', ?, ?, 'awaiting_review', ?, ?, ?,
                  'reply', ?, ?, ?, ?, 'codex-cli', ?, ?, ?)
        """,
        (
            new_id(), session["id"], action_kind, insight_id, based_on_message_id,
            session["session_version"], _dump(insight.get("evidence_message_ids") or []),
            reason[:500], _dump({"source": "conversation_analysis", "insight_id": insight_id}),
            text, text, now, now, now,
        ),
    )
    return True


def _normalize_codex_insight(
    item: dict[str, Any],
    prepared: dict[str, Any],
    message_ids: set[str],
    options: dict[str, Any],
) -> dict[str, Any]:
    raw_insight = item.get("insight") if isinstance(item.get("insight"), dict) else item
    recommendation = raw_insight.get("ai_followup_recommendation")
    if not isinstance(recommendation, dict):
        recommendation = raw_insight.get("recommendation") if isinstance(raw_insight.get("recommendation"), dict) else {}
    attention_status = _attention_status(str(raw_insight.get("attention_status") or recommendation.get("attention_status") or "unknown"))
    suggested_action = _recommended_action(str(raw_insight.get("suggested_next_action") or raw_insight.get("recommended_action") or "no_further_action"))
    evidence = _valid_message_ids(
        raw_insight.get("evidence_message_ids") or recommendation.get("evidence_message_ids"),
        message_ids,
    )
    waiting_on = str(raw_insight.get("waiting_on") or "")
    if waiting_on not in {"candidate", "recruiter", "none", "unknown"}:
        if bool(raw_insight.get("needs_candidate_reply")):
            waiting_on = "candidate"
        elif bool(raw_insight.get("waiting_for_recruiter")):
            waiting_on = "recruiter"
        else:
            waiting_on = "unknown"
    decision = str(recommendation.get("decision") or "")
    if decision not in {"follow", "wait", "do_not_follow"}:
        decision = "follow" if suggested_action == "follow_up" else (
            "do_not_follow" if suggested_action == "no_further_action" else "wait"
        )
    reply_draft = raw_insight.get("reply_draft") if bool(options.get("generate_reply_drafts")) else None
    if isinstance(reply_draft, dict):
        reply_text = _text(reply_draft.get("text"))
    else:
        reply_text = _text(reply_draft)
    normalized_reply = (
        {
            "status": "draft",
            "text": reply_text[:2000],
            "send_status": "not_sent",
            "source": "codex_cli_analysis",
        }
        if reply_text
        else None
    )
    return {
        "conversation_summary": _text(raw_insight.get("conversation_summary") or raw_insight.get("summary"))[:2000],
        "current_conversation_state": _text(raw_insight.get("current_conversation_state") or raw_insight.get("state"))[:500],
        "signals": _string_list(raw_insight.get("signals"))[:30],
        "needs_candidate_reply": bool(raw_insight.get("needs_candidate_reply")),
        "waiting_for_recruiter": bool(raw_insight.get("waiting_for_recruiter")),
        "waiting_on": waiting_on,
        "progress_events": _normalize_progress_events(
            raw_insight.get("progress_events"), message_ids
        ),
        "rejection_analysis": _normalize_rejection(raw_insight.get("rejection_analysis"), message_ids),
        "suggested_next_action": suggested_action,
        "ai_followup_recommendation": {
            "attention_status": attention_status,
            "recommended_action": suggested_action,
            "reason": _text(recommendation.get("reason") or raw_insight.get("reason"))[:1000],
            "decision": decision,
            "reason_code": _text(recommendation.get("reason_code"))[:100],
            "recommended_at": _text(recommendation.get("recommended_at"))[:80] or None,
            "evidence_message_ids": evidence,
        },
        "attention_status": attention_status,
        "reply_draft": normalized_reply,
        "evidence_message_ids": evidence,
        "confidence": _score(raw_insight.get("confidence"), 0.0),
        "deterministic_facts": prepared.get("deterministic_facts") or {},
        "task_sync": prepared.get("task_sync") or {},
        "analysis_version": ANALYSIS_VERSION,
        "prompt_version": PROMPT_VERSION,
        "source": "codex_cli",
    }


def _write_ai_activities(
    connection: sqlite3.Connection,
    job_id: str | None,
    session_id: str,
    insight: dict[str, Any],
    message_ids: set[str],
) -> tuple[int, str | None]:
    if not job_id or _has_manual_terminal_stage(connection, job_id):
        return 0, None
    created = 0
    evidence_ids = _valid_message_ids(insight.get("evidence_message_ids"), message_ids)

    for progress_event in insight.get("progress_events") or []:
        event_type = str(progress_event.get("event_type") or "")
        event_evidence = _valid_message_ids(
            progress_event.get("evidence_message_ids"), message_ids
        )
        if not event_evidence or _activity_exists_for_message(
            connection, job_id, event_type, event_evidence[0]
        ):
            continue
        occurred_at = _message_time(connection, event_evidence[0]) or utc_now()
        _activity, inserted = append_job_activity_with_connection(
            connection,
            job_id=job_id,
            chat_session_id=session_id,
            event_type=event_type,
            occurred_at=occurred_at,
            source="analysis",
            source_ref_type="chat_message",
            source_ref_id=event_evidence[0],
            confidence=_score(progress_event.get("confidence"), 0.0),
            evidence_level="strong_inferred",
            payload={
                "derived_by": "ai",
                "analysis_version": ANALYSIS_VERSION,
                "evidence_message_id": event_evidence[0],
                "evidence_text": _message_text(connection, event_evidence[0]),
            },
            dedupe_key=f"chat_message:{event_evidence[0]}:{ANALYSIS_VERSION}:{event_type}",
        )
        created += int(inserted)

    waiting_on = str(insight.get("waiting_on") or "unknown")
    if (
        waiting_on != "unknown"
        and evidence_ids
        and not _has_deterministic_waiting_event(
            connection, job_id, evidence_ids[-1]
        )
    ):
        occurred_at = _message_time(connection, evidence_ids[-1]) or utc_now()
        _activity, inserted = append_job_activity_with_connection(
            connection,
            job_id=job_id,
            chat_session_id=session_id,
            event_type="conversation_state_analyzed",
            occurred_at=occurred_at,
            source="analysis",
            source_ref_type="chat_message",
            source_ref_id=evidence_ids[-1],
            confidence=_score(insight.get("confidence"), 0.0),
            evidence_level="strong_inferred",
            payload={
                "waiting_on": waiting_on,
                "waiting_since_at": occurred_at,
                "derived_by": "ai",
                "analysis_version": ANALYSIS_VERSION,
                "evidence_message_id": evidence_ids[-1],
                "evidence_text": _message_text(connection, evidence_ids[-1]),
            },
            dedupe_key=(
                f"chat_session:{session_id}:message:{evidence_ids[-1]}:"
                f"{ANALYSIS_VERSION}:conversation_state"
            ),
        )
        created += int(inserted)

    rejection = insight.get("rejection_analysis") if isinstance(insight.get("rejection_analysis"), dict) else {}
    if not (
        rejection.get("rejection_type") == "explicit"
        and bool(rejection.get("rejected"))
        and _score(rejection.get("confidence"), 0.0) >= EXPLICIT_REJECTION_CONFIDENCE
    ):
        return created, None
    rejection_evidence = _valid_message_ids(rejection.get("evidence_message_ids"), message_ids)
    if not rejection_evidence:
        return created, None
    event_type = "job_closed" if rejection.get("outcome") == "job_closed" else "rejected"
    if _activity_exists_for_message(connection, job_id, event_type, rejection_evidence[0]):
        return created, rejection_evidence[0]
    occurred_at = _message_time(connection, rejection_evidence[0]) or utc_now()
    _activity, inserted = append_job_activity_with_connection(
        connection,
        job_id=job_id,
        chat_session_id=session_id,
        event_type=event_type,
        occurred_at=occurred_at,
        source="analysis",
        source_ref_type="chat_message",
        source_ref_id=rejection_evidence[0],
        confidence=_score(rejection.get("confidence"), 0.0),
        evidence_level="strong_inferred",
        payload={
            "rejection_analysis": rejection,
            "rejection_reason_source": rejection.get("reason_source", "unknown"),
            "rejection_reason_category": rejection.get("reason_type", "unknown"),
            "rejection_reason_summary": rejection.get("reason_text", ""),
            "derived_by": "ai",
            "analysis_version": ANALYSIS_VERSION,
            "evidence_message_id": rejection_evidence[0],
            "evidence_text": _message_text(connection, rejection_evidence[0]),
        },
        dedupe_key=f"chat_message:{rejection_evidence[0]}:{ANALYSIS_VERSION}:{event_type}",
    )
    created += int(inserted)
    return created, rejection_evidence[0] if inserted else None


def _activity_exists_for_message(
    connection: sqlite3.Connection,
    job_id: str,
    event_type: str,
    message_id: str,
) -> bool:
    return connection.execute(
        """
        SELECT 1 FROM fj_job_activity_events
        WHERE job_id = ? AND event_type = ?
          AND source_ref_type = 'chat_message' AND source_ref_id = ?
        LIMIT 1
        """,
        (job_id, event_type, message_id),
    ).fetchone() is not None


def _has_deterministic_waiting_event(
    connection: sqlite3.Connection,
    job_id: str,
    message_id: str,
) -> bool:
    return connection.execute(
        """
        SELECT 1 FROM fj_job_activity_events
        WHERE job_id = ? AND source_ref_type = 'chat_message' AND source_ref_id = ?
          AND event_type IN (
            'resume_requested', 'resume_submitted', 'resume_accepted', 'resume_viewed',
            'under_review', 'interview_invited', 'interview_scheduled',
            'offer_received', 'rejected', 'job_closed'
          )
        LIMIT 1
        """,
        (job_id, message_id),
    ).fetchone() is not None


def _apply_followup_policy(
    connection: sqlite3.Connection,
    job_id: str,
    insight: dict[str, Any],
) -> None:
    """规则决定是否行动，AI 文本只补充原因与草稿表达。"""
    snapshot = connection.execute(
        "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?", (job_id,)
    ).fetchone()
    if snapshot is None:
        return
    recommendation = insight.get("ai_followup_recommendation")
    if not isinstance(recommendation, dict):
        recommendation = {}
        insight["ai_followup_recommendation"] = recommendation
    stage = str(snapshot["stage"])
    waiting_on = str(snapshot["waiting_on"] or "unknown")
    insight["waiting_on"] = waiting_on

    if stage == "rejected":
        reason_source = str(snapshot["rejection_reason_source"] or "unknown")
        reason_category = str(snapshot["rejection_reason_category"] or "unknown")
        if reason_source == "unknown" or reason_category in {"unknown", "fit"}:
            insight["attention_status"] = "needs_rejection_reason"
            insight["suggested_next_action"] = "ask_rejection_reason"
            recommendation.update({
                "attention_status": "needs_rejection_reason",
                "recommended_action": "ask_rejection_reason",
                "decision": "follow",
                "reason_code": "rejected_no_reason",
                "recommended_at": utc_now(),
            })
            recommendation["reason"] = recommendation.get("reason") or "已确认拒绝，但招聘方尚未说明具体原因。"
        else:
            _set_no_follow(insight, recommendation, "rejection_reason_known")
        return
    if stage in {"closed", "offer"}:
        _set_no_follow(insight, recommendation, "job_closed" if stage == "closed" else "offer_received")
        return
    if waiting_on == "candidate":
        insight["attention_status"] = "needs_reply"
        insight["suggested_next_action"] = "reply_recruiter"
        recommendation.update({
            "attention_status": "needs_reply",
            "recommended_action": "reply_recruiter",
            "decision": "wait",
            "reason_code": "user_owes_reply",
            "recommended_at": None,
        })
        recommendation["reason"] = recommendation.get("reason") or "招聘方发来了需要处理的新消息。"
        return
    if waiting_on == "none":
        _set_no_follow(insight, recommendation, "no_action_required")
        return
    if waiting_on != "recruiter":
        insight["attention_status"] = "unknown"
        insight["suggested_next_action"] = "no_further_action"
        recommendation.update({
            "attention_status": "unknown",
            "recommended_action": "no_further_action",
            "decision": "wait",
            "reason_code": "",
            "recommended_at": None,
        })
        return

    delay = FOLLOWUP_DELAYS.get(stage, timedelta(days=2))
    waiting_since = _parse_datetime(str(snapshot["waiting_since_at"] or snapshot["stage_updated_at"]))
    recommended_at = waiting_since + delay if waiting_since else None
    due = bool(recommended_at and datetime.now(timezone.utc) >= recommended_at)
    if due:
        insight["attention_status"] = "needs_followup"
        insight["suggested_next_action"] = "follow_up"
        recommendation.update({
            "attention_status": "needs_followup",
            "recommended_action": "follow_up",
            "decision": "follow",
            "reason_code": FOLLOWUP_REASON_CODES.get(stage, "recruiter_owes_reply"),
            "recommended_at": recommended_at.isoformat().replace("+00:00", "Z"),
        })
        recommendation["reason"] = recommendation.get("reason") or "等待招聘方反馈已达到建议跟进时间。"
    else:
        insight["attention_status"] = "waiting"
        insight["suggested_next_action"] = "wait_for_recruiter"
        recommendation.update({
            "attention_status": "waiting",
            "recommended_action": "wait_for_recruiter",
            "decision": "wait",
            "reason_code": "recruiter_owes_reply",
            "recommended_at": recommended_at.isoformat().replace("+00:00", "Z") if recommended_at else None,
        })
        recommendation["reason"] = recommendation.get("reason") or "当前正在等待招聘方反馈。"


def _set_no_follow(
    insight: dict[str, Any],
    recommendation: dict[str, Any],
    reason_code: str,
) -> None:
    insight["attention_status"] = "no_action"
    insight["suggested_next_action"] = "no_further_action"
    recommendation.update({
        "attention_status": "no_action",
        "recommended_action": "no_further_action",
        "decision": "do_not_follow",
        "reason_code": reason_code,
        "recommended_at": None,
    })


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _save_job_evaluation_result(
    db: Database,
    job_id: str,
    item: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), dict) else item
    context_item = _context_job_item(context, job_id)
    if context_item is None:
        return {"status": "skipped", "reason": "skipped_missing_context_item"}
    if _job_already_evaluated(db, job_id):
        return {"status": "skipped", "reason": "skipped_existing_evaluation"}
    decision = str(evaluation.get("decision") or evaluation.get("conclusion") or "review")
    if decision not in {"recommend", "review", "reject"}:
        return {"status": "skipped", "reason": "skipped_invalid_decision"}
    normalized = {
        "evaluation_version": "2.0",
        "job_id": job_id,
        "decision": decision,
        "confidence": _score(evaluation.get("confidence"), 0.0),
        "summary": _text(evaluation.get("summary") or evaluation.get("suggestion"))[:2000],
        "reasons": _string_list(evaluation.get("reasons")),
        "risks": _string_list(evaluation.get("risks")),
        "missing_fields": _string_list(evaluation.get("missing_fields")),
        "missing_information": _string_list(evaluation.get("missing_information")),
        "hard_requirements": list(evaluation.get("hard_requirements") or []) if isinstance(evaluation.get("hard_requirements"), list) else [],
        "match_dimensions": dict(evaluation.get("match_dimensions") or {}) if isinstance(evaluation.get("match_dimensions"), dict) else {},
        "strengths": _string_list(evaluation.get("strengths") or evaluation.get("matches")),
        "gaps": list(evaluation.get("gaps") or []) if isinstance(evaluation.get("gaps"), list) else [],
        "resume_suggestions": list(evaluation.get("resume_suggestions") or []) if isinstance(evaluation.get("resume_suggestions"), list) else [],
        "greeting_draft": dict(evaluation.get("greeting_draft") or {"status": "not_generated", "text": "", "facts_used": []}),
        "source": "codex_cli_refresh",
        "analysis_version": ANALYSIS_VERSION,
    }
    job = get_capture_history_job(db, job_id)
    if int(job.get("detail_version") or 0) != int(context_item.get("job_detail_version") or 0):
        return {"status": "skipped", "reason": "skipped_job_context_changed"}
    recommendation_strategy = context_item.get("recommendation_strategy") if isinstance(context_item.get("recommendation_strategy"), dict) else {}
    filter_strategy = context_item.get("filter_strategy") if isinstance(context_item.get("filter_strategy"), dict) else {}
    candidate_profile_id = str(context_item.get("candidate_profile_id") or "")
    resume_version_id = str(context_item.get("resume_version_id") or "")
    context_revision_id = str(context_item.get("context_revision_id") or "")
    dependencies = context_item.get("context_dependency_versions") if isinstance(context_item.get("context_dependency_versions"), dict) else {}
    profile = profile_store.get_profile(db, candidate_profile_id)
    try:
        assert_job_action_allowed(db, job_id, strategy=filter_strategy, action="evaluation", allow_manual_override=True)
    except AppError as exc:
        return {
            "status": "skipped",
            "reason": exc.error_category,
            "message": exc.error_message,
        }
    evaluation_id = new_id()
    now = utc_now()
    versions = profile.get("versions") if isinstance(profile.get("versions"), dict) else {}
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_job_evaluations (
              id, job_id, evaluation_version, recommendation_strategy_id,
              filter_strategy_id, resume_id, source, decision, confidence,
              evaluation_json, created_at, candidate_profile_id,
              profile_context_version, resume_version_id, structure_version,
              context_revision_id, filter_strategy_version,
              recommendation_strategy_version, profile_facts_version,
              profile_questions_version, candidate_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                job_id,
                normalized["evaluation_version"],
                str(recommendation_strategy.get("id") or "") or None,
                str(filter_strategy.get("id") or "") or None,
                str(recommendation_strategy.get("resume_id") or "") or None,
                "llm",
                decision,
                normalized["confidence"],
                _dump(normalized),
                now,
                candidate_profile_id,
                int(versions.get("context_version") or 0),
                resume_version_id,
                context_revision_id,
                filter_strategy.get("strategy_version"),
                recommendation_strategy.get("strategy_version"),
                versions.get("facts_version"),
                versions.get("questions_version"),
                _dump(
                    {
                        "candidate_profile_id": candidate_profile_id,
                        "resume_version_id": resume_version_id,
                        "context_revision_id": context_revision_id,
                        "context_dependencies": dependencies,
                        "route": "job_hunt_refresh_non_routing",
                    }
                ),
            ),
        )
    update_capture_job_delivery_evaluation(db, job=job, evaluation=normalized)
    record_job_event(db, "evaluation", job_id, now)
    return {"status": "saved", "evaluation_id": evaluation_id}


def _build_session_analysis_context(
    connection: sqlite3.Connection,
    db: Database,
    session: sqlite3.Row,
    job_id: str | None,
    messages: list[dict[str, Any]],
    deterministic: dict[str, Any],
    task_sync: dict[str, Any],
) -> dict[str, Any]:
    job = _load_job_payload(connection, job_id)
    activities = _load_job_activities(connection, job_id)
    pipeline = connection.execute(
        "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?",
        (job_id or "",),
    ).fetchone() if job_id else None
    evaluation = connection.execute(
        """
        SELECT decision, confidence, evaluation_json, created_at
        FROM fj_job_evaluations WHERE job_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (job_id or "",),
    ).fetchone() if job_id else None
    pipeline_payload = dict(pipeline) if pipeline else None
    waiting_since = _parse_datetime(
        str(pipeline["waiting_since_at"] or pipeline["stage_updated_at"])
    ) if pipeline else None
    waiting_days = (
        max(0, int((datetime.now(timezone.utc) - waiting_since).total_seconds() // 86_400))
        if waiting_since else 0
    )
    return {
        "session_id": str(session["id"]),
        "job_id": job_id,
        "session": {
            "id": str(session["id"]),
            "recruiter": str(session["peer_name"] or ""),
            "company": str(session["company_name"] or ""),
            "latest_message_time": str(session["last_message_at"] or session["platform_latest_message_at"] or ""),
            "latest_message_direction": messages[-1]["direction"] if messages else None,
            "history_has_more": bool(session["history_has_more"]),
        },
        "messages": messages,
        "latest_message": messages[-1] if messages else None,
        "job": job,
        "activities": activities,
        "pipeline": pipeline_payload,
        "waiting_duration_days": waiting_days,
        "evaluation": {
            "decision": evaluation["decision"],
            "confidence": evaluation["confidence"],
            "created_at": evaluation["created_at"],
            "detail": _load_json(evaluation["evaluation_json"], {}),
        } if evaluation else None,
        "candidate_profile_context": _candidate_context(db, view="chat"),
        "deterministic_facts": deterministic,
        "task_sync": task_sync,
        "expected_output": _conversation_output_schema(),
    }


def _load_session(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise AppError(404, "CHAT_SESSION_NOT_FOUND", "聊天会话不存在。")
    return row


def _load_messages(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM fj_chat_messages
        WHERE session_id = ?
          AND NOT (source = 'assistant' AND platform_message_id LIKE 'assistant:%')
        ORDER BY sent_at ASC, rowid ASC
        """,
        (session_id,),
    ).fetchall()
    return [_message_payload(row) for row in rows]


def _real_conversation_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if message["message_type"] != "system"]


def _message_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "platform_message_id": str(row["platform_message_id"]),
        "direction": str(row["direction"]),
        "message_type": str(row["message_type"]),
        "content": str(row["content"] or ""),
        "sender_uid": str(row["sender_uid"] or ""),
        "client_mid": str(row["client_mid"] or ""),
        "sent_at": str(row["sent_at"]),
        "observed_at": str(row["observed_at"]),
        "source": str(row["source"]),
        "raw_type": _load_json(row["raw_meta_json"], {}).get("platform_type"),
    }


def _resolve_session_job_id(connection: sqlite3.Connection, session: sqlite3.Row) -> str | None:
    if session["job_id"]:
        return str(session["job_id"])
    encrypt_job_id = str(session["encrypt_job_id"] or "")
    if not encrypt_job_id:
        return None
    row = connection.execute(
        "SELECT id FROM fj_boss_jobs WHERE encrypt_job_id = ? ORDER BY last_collected_at DESC LIMIT 1",
        (encrypt_job_id,),
    ).fetchone()
    if row is None:
        return None
    connection.execute(
        "UPDATE fj_chat_sessions SET job_id = ?, updated_at = ? WHERE id = ?",
        (row["id"], utc_now(), session["id"]),
    )
    return str(row["id"])


def _load_job_payload(connection: sqlite3.Connection, job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    row = connection.execute("SELECT * FROM fj_boss_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["detail"] = _load_json(job.pop("detail_json"), {})
    job["payload"] = _load_json(job.pop("payload_json"), {})
    return job


def _load_job_activities(connection: sqlite3.Connection, job_id: str | None) -> list[dict[str, Any]]:
    if not job_id:
        return []
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT event_type, occurred_at, source, source_ref_type, source_ref_id,
                   confidence, evidence_level
            FROM fj_job_activity_events
            WHERE job_id = ?
            ORDER BY occurred_at DESC, created_at DESC LIMIT 30
            """,
            (job_id,),
        ).fetchall()
    ]


def _candidate_context(db: Database, *, view: str) -> dict[str, Any]:
    try:
        profile = profile_store.ensure_default_profile(db)
        return dict(get_profile_context(db, str(profile["id"]), view=view, persist_artifact=False))
    except AppError:
        return {}


def _default_recommendation_strategy(db: Database) -> dict[str, Any] | None:
    for strategy in list_recommendation_strategies(db):
        if not strategy.get("enabled"):
            continue
        item = dict(strategy)
        resume_version_id = str(item.get("resume_version_id") or "").strip()
        if resume_version_id:
            try:
                resume_version = profile_store.get_resume_version(db, resume_version_id)
            except AppError:
                continue
            item["candidate_profile_id"] = str(resume_version.get("profile_id") or item.get("candidate_profile_id") or "")
        return item
    return None


def _load_filter_strategy(db: Database, recommendation_strategy: dict[str, Any]) -> dict[str, Any] | None:
    filter_strategy_id = str(recommendation_strategy.get("filter_strategy_id") or "").strip()
    if not filter_strategy_id:
        return None
    try:
        return get_filter_strategy(db, filter_strategy_id)
    except AppError:
        return None


def _current_evaluation_context_revision(db: Database, profile_id: str, resume_version_id: str) -> sqlite3.Row | None:
    if not profile_id or not resume_version_id:
        return None
    try:
        resolution = profile_v3.resolve_task_context(db, profile_id, resume_version_id, "evaluation", "use_current")
    except AppError:
        return None
    context = resolution.get("context") if isinstance(resolution, dict) else {}
    current = context.get("current_revision") if isinstance(context, dict) else None
    if isinstance(current, dict) and current.get("id"):
        with db.connect() as connection:
            return connection.execute(
                """
                SELECT id, dependency_versions_json
                FROM fj_profile_context_revisions
                WHERE id = ?
                """,
                (str(current["id"]),),
            ).fetchone()
    with db.connect() as connection:
        return connection.execute(
            """
            SELECT r.id, r.dependency_versions_json
            FROM fj_profile_context_revisions r
            JOIN fj_profile_context_heads h ON h.id = r.head_id
            WHERE h.profile_id = ? AND h.resume_version_id = ? AND h.view_type = 'evaluation'
            ORDER BY r.created_at DESC LIMIT 1
            """,
            (profile_id, resume_version_id),
        ).fetchone()


def _scope_missing_evaluation_job_ids(scope: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for value in scope.get("jobs_missing_evaluation") or []:
        if isinstance(value, dict):
            job_id = str(value.get("id") or value.get("job_id") or "").strip()
        else:
            job_id = str(value).strip()
        if job_id and job_id not in result:
            result.append(job_id)
    return result


def _job_public_payload(job: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id", "job_id", "title", "boss_name", "company_name", "salary", "location",
        "job_link", "search_keyword", "filter_status", "filter_reasons",
        "detail", "detail_status", "detail_version",
    ]
    return {key: job.get(key) for key in keys if key in job}


def _job_already_evaluated(db: Database, job_id: str) -> bool:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM fj_job_evaluations WHERE job_id = ? LIMIT 1",
            (job_id,),
        ).fetchone()
    return row is not None


def _context_job_item(context: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    for item in context.get("job_evaluation_items", []):
        if isinstance(item, dict) and str(item.get("job_id") or "") == job_id:
            return item
    return None


def _job_evaluation_outcome(
    job_id: str,
    saved: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    context_item = _context_job_item(context, job_id) or {}
    outcome = {
        "item_type": "job_evaluation",
        "job_id": job_id,
        "status": str(saved.get("status") or "unknown"),
        "title": str(context_item.get("title") or ""),
        "company_name": str(context_item.get("company_name") or ""),
    }
    if saved.get("evaluation_id"):
        outcome["evaluation_id"] = str(saved["evaluation_id"])
    if saved.get("reason"):
        outcome["reason"] = str(saved["reason"])
    if saved.get("message"):
        outcome["message"] = str(saved["message"])
    return outcome


def _merge_job_evaluation_outcomes(
    old_value: object,
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    if isinstance(old_value, list):
        for item in old_value:
            if isinstance(item, dict) and item.get("job_id"):
                merged[str(item["job_id"])] = dict(item)
    for item in new_items:
        if item.get("job_id"):
            merged[str(item["job_id"])] = dict(item)
    return list(merged.values())


def _job_evaluation_analysis_items(
    db: Database,
    run_id: str,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    summary = _current_run_analysis_summary(db, run_id)
    stored_outcomes = {
        str(item.get("job_id")): dict(item)
        for item in summary.get("job_evaluation_results", [])
        if isinstance(item, dict) and item.get("job_id")
    }
    manifest_items = [
        item for item in manifest.get("job_evaluation_items", [])
        if isinstance(item, dict) and item.get("job_id")
    ]
    job_ids = [str(item["job_id"]) for item in manifest_items]
    evaluations = _latest_evaluations_by_job_ids(db, job_ids)
    analysis_status = str(summary.get("status") or "")
    results: list[dict[str, Any]] = []
    for manifest_item in manifest_items:
        job_id = str(manifest_item["job_id"])
        outcome = dict(stored_outcomes.get(job_id) or {})
        evaluation = evaluations.get(job_id)
        status = str(outcome.get("status") or "")
        if not status and evaluation:
            status = "saved"
        elif not status and analysis_status == "saved":
            status = "skipped"
            outcome["reason"] = "skipped_without_recorded_reason"
        elif not status:
            status = "pending"
        results.append({
            "item_type": "job_evaluation",
            "job_id": job_id,
            "status": status,
            "reason": str(outcome.get("reason") or ""),
            "message": str(outcome.get("message") or ""),
            "evaluation_id": str(outcome.get("evaluation_id") or evaluation.get("id") or "") if evaluation else str(outcome.get("evaluation_id") or ""),
            "decision": str(evaluation.get("decision") or "") if evaluation else "",
            "title": str(manifest_item.get("title") or outcome.get("title") or ""),
            "company_name": str(manifest_item.get("company_name") or outcome.get("company_name") or ""),
            "has_current_evaluation": bool(evaluation),
            "context_arguments": manifest_item.get("context_arguments") if isinstance(manifest_item.get("context_arguments"), dict) else {},
        })
    return results


def _conversation_analysis_items(db: Database, run_id: str) -> list[dict[str, Any]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT session_id, job_id, status, result_json, error_category, error_message,
                   started_at, completed_at, created_at, updated_at
            FROM fj_job_hunt_refresh_analysis_items
            WHERE run_id = ?
            ORDER BY created_at, session_id
            """,
            (run_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _load_json(row["result_json"], {})
        result.append({
            "item_type": "conversation",
            "session_id": str(row["session_id"] or ""),
            "job_id": str(row["job_id"] or ""),
            "status": str(row["status"] or ""),
            "attention_status": str(payload.get("attention_status") or ""),
            "skipped_reasons": payload.get("skipped_reasons") if isinstance(payload.get("skipped_reasons"), list) else [],
            "error_category": str(row["error_category"] or ""),
            "error_message": str(row["error_message"] or ""),
            "started_at": str(row["started_at"] or ""),
            "completed_at": str(row["completed_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        })
    return result


def _latest_evaluations_by_job_ids(db: Database, job_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    placeholders = ",".join("?" for _ in job_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT e.*
            FROM fj_job_evaluations e
            JOIN (
              SELECT job_id, MAX(created_at) AS created_at
              FROM fj_job_evaluations
              WHERE job_id IN ({placeholders})
              GROUP BY job_id
            ) latest ON latest.job_id = e.job_id AND latest.created_at = e.created_at
            """,
            tuple(job_ids),
        ).fetchall()
    return {str(row["job_id"]): dict(row) for row in rows}


def _manifest_conversation_item(manifest: dict[str, Any], session_id: str) -> dict[str, Any] | None:
    for item in manifest.get("conversation_items", []):
        if isinstance(item, dict) and str(item.get("session_id") or "") == session_id:
            return item
    return None


def _manifest_job_item(manifest: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    for item in manifest.get("job_evaluation_items", []):
        if isinstance(item, dict) and str(item.get("job_id") or "") == job_id:
            return item
    return None


def _has_manual_terminal_stage(connection: sqlite3.Connection, job_id: str) -> bool:
    rows = connection.execute(
        """
        SELECT payload_json
        FROM fj_job_activity_events
        WHERE job_id = ? AND event_type = 'manual_stage_changed'
        ORDER BY occurred_at DESC, created_at DESC LIMIT 10
        """,
        (job_id,),
    ).fetchall()
    for row in rows:
        payload = _load_json(row["payload_json"], {})
        if isinstance(payload, dict) and payload.get("stage") in {"rejected", "closed", "offer"}:
            return True
    return False


def _cancel_open_progress_tasks_for_rejection(
    connection: sqlite3.Connection,
    job_id: str,
    session_id: str,
    evidence_message_id: str,
) -> None:
    now = utc_now()
    connection.execute(
        """
        UPDATE fj_review_items
        SET status = 'dismissed',
            resolution_note = '已识别明确拒绝，关闭旧推进项',
            updated_at = ?, resolved_at = ?
        WHERE job_id = ? AND status IN ('pending', 'rejected')
        """,
        (now, now, job_id),
    )
    connection.execute(
        """
        UPDATE fj_chat_reply_tasks
        SET status = 'stale', cancelled_at = ?, updated_at = ?,
            decision_reason = '已识别明确拒绝，旧回复任务不再适用'
        WHERE session_id = ? AND status IN ('pending_generation', 'generating', 'awaiting_review')
        """,
        (now, now, session_id),
    )
    for action in connection.execute(
        """
        SELECT id, status
        FROM fj_automation_actions
        WHERE job_id = ? AND status IN ('queued', 'running', 'leased')
        """,
        (job_id,),
    ).fetchall():
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'cancelled', execution_state = 'cancelled',
                last_status_code = 'SUPERSEDED_BY_REJECTION',
                last_error = '已识别明确拒绝，取消未完成推进动作',
                completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, action["id"]),
        )
        record_execution_evidence_with_connection(
            connection,
            action_ref_type="automation_action",
            action_ref_id=str(action["id"]),
            evidence_type="rejection_observed",
            source="analysis",
            source_ref_type="chat_message",
            source_ref_id=evidence_message_id,
            observed_at=now,
            confidence=1.0,
            evidence_level="strong_inferred",
            payload={"reason": "explicit_rejection"},
            dedupe_key=f"chat_message:{evidence_message_id}:automation_action:{action['id']}:explicit_rejection",
        )


def _load_prepared_item_result(connection: sqlite3.Connection, run_id: str, session_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT result_json FROM fj_job_hunt_refresh_analysis_items WHERE run_id = ? AND session_id = ?",
        (run_id, session_id),
    ).fetchone()
    return _load_json(row["result_json"], {}) if row else {}


def _mark_missing_conversation_results_skipped(
    db: Database,
    run_id: str,
    allowed_session_ids: set[str],
    summary: dict[str, Any],
) -> None:
    if not allowed_session_ids:
        return
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT session_id, job_id, status, result_json
            FROM fj_job_hunt_refresh_analysis_items
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
        for row in rows:
            session_id = str(row["session_id"])
            if session_id not in allowed_session_ids or row["status"] != "pending":
                continue
            result = _load_json(row["result_json"], {})
            result["status"] = "skipped"
            reasons = list(result.get("skipped_reasons") or [])
            reasons.append("skipped_missing_ai_result")
            result["skipped_reasons"] = reasons
            _upsert_analysis_item(
                connection,
                run_id=run_id,
                session_id=session_id,
                job_id=str(row["job_id"]) if row["job_id"] else None,
                status="skipped",
                result=result,
                started_at=None,
                completed_at=utc_now(),
            )
            _merge_summary(summary, result)


def _upsert_analysis_item(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    session_id: str,
    job_id: str | None,
    status: str,
    result: dict[str, Any],
    started_at: str | None,
    completed_at: str | None,
) -> None:
    now = utc_now()
    existing = connection.execute(
        "SELECT id, started_at, created_at FROM fj_job_hunt_refresh_analysis_items WHERE run_id = ? AND session_id = ?",
        (run_id, session_id),
    ).fetchone()
    item_id = str(existing["id"]) if existing else new_id()
    created_at = str(existing["created_at"]) if existing else now
    effective_started_at = started_at or (str(existing["started_at"]) if existing and existing["started_at"] else None)
    connection.execute(
        """
        INSERT INTO fj_job_hunt_refresh_analysis_items (
          id, run_id, session_id, job_id, status, result_json,
          started_at, completed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, session_id) DO UPDATE SET
          job_id = excluded.job_id,
          status = excluded.status,
          result_json = excluded.result_json,
          error_category = NULL,
          error_message = NULL,
          started_at = COALESCE(fj_job_hunt_refresh_analysis_items.started_at, excluded.started_at),
          completed_at = excluded.completed_at,
          updated_at = excluded.updated_at
        """,
        (
            item_id,
            run_id,
            session_id,
            job_id,
            status,
            _dump(result),
            effective_started_at,
            completed_at,
            created_at,
            now,
        ),
    )


def _mark_analysis_failed(db: Database, run_id: str, session_id: str, exc: Exception) -> dict[str, Any]:
    category = exc.error_category if isinstance(exc, AppError) else type(exc).__name__
    message = exc.error_message if isinstance(exc, AppError) else str(exc)
    now = utc_now()
    result = {
        "status": "failed",
        "error_category": str(category)[:120],
        "error_message": str(message)[:500],
        "attention_status": "unknown",
    }
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id, created_at, started_at FROM fj_job_hunt_refresh_analysis_items WHERE run_id = ? AND session_id = ?",
            (run_id, session_id),
        ).fetchone()
        item_id = str(existing["id"]) if existing else new_id()
        created_at = str(existing["created_at"]) if existing else now
        started_at = str(existing["started_at"]) if existing and existing["started_at"] else now
        connection.execute(
            """
            INSERT INTO fj_job_hunt_refresh_analysis_items (
              id, run_id, session_id, status, result_json,
              error_category, error_message, started_at, completed_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, session_id) DO UPDATE SET
              status = 'failed',
              result_json = excluded.result_json,
              error_category = excluded.error_category,
              error_message = excluded.error_message,
              started_at = COALESCE(fj_job_hunt_refresh_analysis_items.started_at, excluded.started_at),
              completed_at = excluded.completed_at,
              updated_at = excluded.updated_at
            """,
            (
                item_id,
                run_id,
                session_id,
                _dump(result),
                result["error_category"],
                result["error_message"],
                started_at,
                now,
                created_at,
                now,
            ),
        )
    return result


def _save_insight(
    connection: sqlite3.Connection,
    *,
    run_id: str | None,
    session_id: str,
    job_id: str | None,
    insight: dict[str, Any],
    model: str,
    status: str,
) -> str:
    now = utc_now()
    if run_id is None:
        existing = connection.execute(
            """
            SELECT id, created_at FROM fj_conversation_insights
            WHERE run_id IS NULL AND session_id = ? AND analysis_version = ?
            """,
            (session_id, ANALYSIS_VERSION),
        ).fetchone()
    else:
        existing = connection.execute(
            """
            SELECT id, created_at FROM fj_conversation_insights
            WHERE run_id = ? AND session_id = ? AND analysis_version = ?
            """,
            (run_id, session_id, ANALYSIS_VERSION),
        ).fetchone()
    insight_id = str(existing["id"]) if existing else new_id()
    created_at = str(existing["created_at"]) if existing else now
    if existing:
        connection.execute(
            """
            UPDATE fj_conversation_insights
            SET job_id = ?, status = ?, insight_json = ?, model = ?,
                prompt_version = ?, updated_at = ?
            WHERE id = ?
            """,
            (job_id, status, _dump(insight), model, PROMPT_VERSION, now, insight_id),
        )
    else:
        connection.execute(
            """
            INSERT INTO fj_conversation_insights (
          id, run_id, session_id, job_id, status, insight_json, model,
          prompt_version, analysis_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                insight_id, run_id, session_id, job_id, status, _dump(insight),
                model, PROMPT_VERSION, ANALYSIS_VERSION, created_at, now,
            ),
        )
    return insight_id


def _save_attention_state(
    connection: sqlite3.Connection,
    run_id: str | None,
    session_id: str,
    job_id: str | None,
    insight_id: str,
    insight: dict[str, Any],
) -> None:
    status = _attention_status(str(insight.get("attention_status") or "unknown"))
    action = _recommended_action(str(insight.get("suggested_next_action") or "no_further_action"))
    recommendation = insight.get("ai_followup_recommendation")
    reason = _text(recommendation.get("reason")) if isinstance(recommendation, dict) else ""
    decision = _text(recommendation.get("decision")) if isinstance(recommendation, dict) else "wait"
    if decision not in {"follow", "wait", "do_not_follow"}:
        decision = "wait"
    reason_code = _text(recommendation.get("reason_code")) if isinstance(recommendation, dict) else ""
    recommended_at = _text(recommendation.get("recommended_at")) if isinstance(recommendation, dict) else ""
    evidence = _message_ids(insight.get("evidence_message_ids"))
    now = utc_now()
    existing = connection.execute(
        "SELECT created_at FROM fj_chat_attention_states WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    created_at = str(existing["created_at"]) if existing else now
    connection.execute(
        """
        INSERT INTO fj_chat_attention_states (
          session_id, job_id, run_id, insight_id, attention_status, display_label,
          recommended_action, reason, decision, reason_code, recommended_at,
          priority, evidence_message_ids_json,
          source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'analysis', ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
          job_id = excluded.job_id,
          run_id = excluded.run_id,
          insight_id = excluded.insight_id,
          attention_status = excluded.attention_status,
          display_label = excluded.display_label,
          recommended_action = excluded.recommended_action,
          reason = excluded.reason,
          decision = excluded.decision,
          reason_code = excluded.reason_code,
          recommended_at = excluded.recommended_at,
          priority = excluded.priority,
          evidence_message_ids_json = excluded.evidence_message_ids_json,
          source = excluded.source,
          updated_at = excluded.updated_at
        """,
        (
            session_id,
            job_id,
            run_id,
            insight_id,
            status,
            ATTENTION_LABELS[status],
            action,
            reason[:500],
            decision,
            reason_code[:100],
            recommended_at[:80] or None,
            ATTENTION_PRIORITY[status],
            _dump(evidence),
            created_at,
            now,
        ),
    )


def _store_context_snapshot(
    db: Database,
    run_id: str,
    *,
    status: str,
    context: dict[str, Any],
    blocker_reason: str,
) -> None:
    now = utc_now()
    existing = _context_created_at(db, run_id)
    created_at = existing or now
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_job_hunt_refresh_analysis_contexts (
              run_id, status, context_json, context_characters, max_context_characters,
              blocker_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              status = excluded.status,
              context_json = excluded.context_json,
              context_characters = excluded.context_characters,
              max_context_characters = excluded.max_context_characters,
              blocker_reason = excluded.blocker_reason,
              updated_at = excluded.updated_at
            """,
            (
                run_id,
                status,
                _dump(context),
                len(_dump(context)),
                MAX_PREPARE_MANIFEST_CHARACTERS,
                blocker_reason,
                created_at,
                now,
            ),
        )


def _load_context_snapshot(db: Database, run_id: str) -> dict[str, Any] | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT context_json FROM fj_job_hunt_refresh_analysis_contexts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return _load_json(row["context_json"], {})


def _snapshot_has_manifest(snapshot: dict[str, Any]) -> bool:
    manifest = snapshot.get("manifest") if isinstance(snapshot.get("manifest"), dict) else None
    return bool(manifest and isinstance(manifest.get("context_reader"), dict))


def _context_created_at(db: Database, run_id: str) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT created_at FROM fj_job_hunt_refresh_analysis_contexts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return str(row["created_at"]) if row else None


def _store_run_analysis_summary(db: Database, run_id: str, analysis: dict[str, Any]) -> None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT summary_json FROM fj_job_hunt_refresh_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        summary = _load_json(row["summary_json"], {}) if row else {}
        summary["analysis"] = analysis
        _merge_analysis_summary_fields(summary, analysis)
        connection.execute(
            "UPDATE fj_job_hunt_refresh_runs SET summary_json = ?, updated_at = ? WHERE id = ?",
            (_dump(summary), utc_now(), run_id),
        )


def _merge_analysis_summary_fields(summary: dict[str, Any], analysis: dict[str, Any]) -> None:
    summary.update(
        {
            "conversations_analyzed": int(analysis.get("analyzed") or 0),
            "conversations_skipped": int(analysis.get("skipped") or 0),
            "conversation_analysis_failed": int(analysis.get("failed") or 0),
            "activities_written": int(analysis.get("activities_created") or 0),
            "reply_drafts_generated": int(analysis.get("generated_reply_draft") or 0),
            "missing_suggestions_total": int(analysis.get("evaluation_jobs_total") or 0),
            "missing_suggestions_generated": int(analysis.get("generated_evaluation") or 0),
            "missing_suggestions_skipped": int(analysis.get("evaluation_jobs_skipped") or 0),
        }
    )


def _current_run_analysis_summary(db: Database, run_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT summary_json FROM fj_job_hunt_refresh_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    summary = _load_json(row["summary_json"], {}) if row else {}
    analysis = summary.get("analysis")
    return analysis if isinstance(analysis, dict) else {}


def _refresh_conversation_summary_from_items(db: Database, run_id: str, summary: dict[str, Any]) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT status, result_json
            FROM fj_job_hunt_refresh_analysis_items
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
    summary["analyzed"] = 0
    summary["skipped"] = 0
    summary["failed"] = 0
    summary["generated_reply_draft"] = 0
    summary["updated_pipeline"] = 0
    summary["activities_created"] = 0
    summary["reconciled_tasks"] = 0
    summary["rejection_detected"] = 0
    summary["attention_status"] = {}
    for row in rows:
        result = _load_json(row["result_json"], {})
        result["status"] = str(row["status"])
        _merge_summary(summary, result)


def _set_run_step(db: Database, run_id: str, step: str) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET status = 'running',
                current_step = ?,
                started_at = COALESCE(started_at, ?),
                completed_at = NULL,
                updated_at = ?
            WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled')
            """,
            (step, utc_now(), utc_now(), run_id),
        )


def _ensure_refresh_items_finished(db: Database, run_id: str, run: sqlite3.Row) -> None:
    with db.connect() as connection:
        unfinished = int(connection.execute(
            """
            SELECT COUNT(*) FROM fj_job_hunt_refresh_items
            WHERE run_id = ? AND status IN ('pending', 'running')
            """,
            (run_id,),
        ).fetchone()[0])
    if unfinished or run["chat_list_status"] in {"pending", "running"}:
        raise AppError(409, "REFRESH_RUN_NOT_READY_FOR_ANALYSIS", "请先完成本次聊天和岗位数据更新。")


def _load_scope(db: Database, scope_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_hunt_refresh_scopes WHERE id = ?",
            (scope_id,),
        ).fetchone()
    if row is None:
        raise AppError(404, "REFRESH_SCOPE_NOT_FOUND", "更新范围快照不存在。")
    result = dict(row)
    result["session_ids_in_scope"] = _load_json(result.pop("session_ids_in_scope_json"), [])
    result["session_ids_to_sync"] = _load_json(result.pop("session_ids_json"), [])
    result["jobs_missing_evaluation"] = _load_json(result.pop("jobs_missing_evaluation_json"), [])
    result["counts"] = _load_json(result.get("counts_json"), {})
    if not result["session_ids_in_scope"]:
        result["session_ids_in_scope"] = result["session_ids_to_sync"]
    return result


def _require_run(db: Database, run_id: str) -> sqlite3.Row:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_hunt_refresh_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise AppError(404, "REFRESH_RUN_NOT_FOUND", "求职数据更新任务不存在。")
    return row


def _message_time(connection: sqlite3.Connection, message_id: str) -> str | None:
    row = connection.execute("SELECT sent_at FROM fj_chat_messages WHERE id = ?", (message_id,)).fetchone()
    return str(row["sent_at"]) if row else None


def _message_text(connection: sqlite3.Connection, message_id: str) -> str:
    row = connection.execute(
        "SELECT content FROM fj_chat_messages WHERE id = ?", (message_id,)
    ).fetchone()
    return _text(row["content"] if row else "")[:500]


def _message_fact(message: dict[str, Any], fact_type: str) -> dict[str, Any]:
    return {
        "fact_type": fact_type,
        "message_id": str(message["id"]),
        "occurred_at": str(message["sent_at"]),
        "direction": str(message["direction"]),
    }


def _conversation_output_schema() -> dict[str, Any]:
    return {
        "session_id": "string",
        "job_id": "string|null",
        "insight": {
            "conversation_summary": "string",
            "current_conversation_state": "string",
            "signals": ["string"],
            "needs_candidate_reply": "boolean",
            "waiting_for_recruiter": "boolean",
            "waiting_on": "candidate|recruiter|none|unknown",
            "progress_events": [{
                "event_type": "resume_requested|resume_submitted|resume_accepted|resume_viewed|under_review|interview_invited|interview_scheduled|offer_received",
                "confidence": "0..1",
                "evidence_message_ids": ["message id from context"],
            }],
            "rejection_analysis": {
                "rejected": "boolean",
                "outcome": "rejected|job_closed",
                "rejection_type": "explicit|soft|none",
                "rejection_reason_source": "recruiter_explicit|ai_inferred|unknown",
                "rejection_reason_category": "experience|education|skills|industry_background|salary|location|availability|position_filled|headcount_closed|fit|other|unknown",
                "reason_text": "string",
                "confidence": "0..1",
                "evidence_message_ids": ["message id from context"],
            },
            "suggested_next_action": "enum",
            "ai_followup_recommendation": "object",
            "attention_status": "enum",
            "reply_draft": "string|null",
            "evidence_message_ids": ["message id from context"],
            "confidence": "0..1",
        },
    }


def _job_evaluation_output_schema() -> dict[str, Any]:
    return {
        "job_id": "string",
        "evaluation": {
            "decision": "recommend|review|reject",
            "confidence": "0..1",
            "summary": "string",
            "reasons": ["string"],
            "risks": ["string"],
            "missing_information": ["string"],
            "hard_requirements": ["object"],
            "match_dimensions": "object",
            "strengths": ["string"],
            "gaps": ["object|string"],
            "resume_suggestions": ["object|string"],
            "greeting_draft": "object",
        },
    }


def _save_contract() -> dict[str, Any]:
    return {
        "tool": "finejob.save_job_hunt_refresh_analysis",
        "context_reader": "finejob.get_job_hunt_refresh_analysis_item_context",
        "input": {
            "run_id": "same run_id",
            "final_batch": "boolean, default true; use false only for intermediate large-result batches",
            "analysis_result": {
                "conversation_results": [_conversation_output_schema()],
                "job_evaluation_results": [_job_evaluation_output_schema()],
            },
        },
        "rules": [
            "Use the prepared manifest and its context handles only.",
            "Do not call prepare again.",
            "Reading item context is allowed inside the same Codex CLI task and does not start another AI call.",
            "Do not split conversation, job evaluation, reply draft, and followup recommendation into separate AI calls.",
        ],
    }


def _empty_summary(*, total: int) -> dict[str, Any]:
    return {
        "enabled": True,
        "total": total,
        "analyzed": 0,
        "skipped": 0,
        "failed": 0,
        "generated_evaluation": 0,
        "evaluation_jobs_total": 0,
        "evaluation_jobs_skipped": 0,
        "evaluation_skip_reasons": {},
        "generated_reply_draft": 0,
        "updated_pipeline": 0,
        "activities_created": 0,
        "reconciled_tasks": 0,
        "rejection_detected": 0,
        "attention_status": {},
    }


def _merge_summary(summary: dict[str, Any], result: dict[str, Any]) -> None:
    status = str(result.get("status") or "")
    if status == "analyzed":
        summary["analyzed"] += 1
    elif status == "skipped":
        summary["skipped"] += 1
    elif status == "failed":
        summary["failed"] += 1
    if result.get("generated_evaluation"):
        summary["generated_evaluation"] += 1
    if result.get("generated_reply_draft"):
        summary["generated_reply_draft"] += 1
    if result.get("updated_pipeline"):
        summary["updated_pipeline"] += 1
    deterministic = result.get("deterministic_facts") if isinstance(result.get("deterministic_facts"), dict) else {}
    summary["activities_created"] += int(deterministic.get("activities_created") or 0)
    if result.get("reconciled_tasks"):
        summary["reconciled_tasks"] += 1
    if result.get("rejection_detected"):
        summary["rejection_detected"] += 1
    attention = _attention_status(str(result.get("attention_status") or "unknown"))
    summary["attention_status"][attention] = summary["attention_status"].get(attention, 0) + 1


def _task_sync_count(task_sync: dict[str, Any]) -> int:
    total = 0
    for value in task_sync.values():
        if isinstance(value, bool):
            total += int(value)
        else:
            try:
                total += int(value or 0)
            except (TypeError, ValueError):
                pass
    return total


def _normalize_rejection(value: object, message_ids: set[str]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    rejection_type = str(raw.get("rejection_type") or "none")
    if rejection_type not in {"explicit", "soft", "none"}:
        rejection_type = "none"
    outcome = str(raw.get("outcome") or raw.get("outcome_type") or "rejected")
    if outcome not in {"rejected", "job_closed"}:
        outcome = "rejected"
    reason_type = str(
        raw.get("rejection_reason_category") or raw.get("reason_type") or "unknown"
    )
    reason_type = {
        "experience_mismatch": "experience",
        "skill_mismatch": "skills",
        "education_mismatch": "education",
        "salary_mismatch": "salary",
        "location_mismatch": "location",
        "availability_mismatch": "availability",
        "background_mismatch": "industry_background",
    }.get(reason_type, reason_type)
    if reason_type not in {
        "experience",
        "skills",
        "education",
        "salary",
        "location",
        "availability",
        "industry_background",
        "position_filled",
        "headcount_closed",
        "fit",
        "other",
        "unknown",
    }:
        reason_type = "unknown"
    reason_source = str(
        raw.get("rejection_reason_source") or raw.get("reason_source") or "unknown"
    )
    reason_source = {
        "explicit": "recruiter_explicit",
        "inferred": "ai_inferred",
    }.get(reason_source, reason_source)
    if reason_source not in {"recruiter_explicit", "ai_inferred", "unknown"}:
        reason_source = "unknown"
    if reason_type == "position_filled":
        outcome = "rejected"
    if outcome == "job_closed" and reason_type != "headcount_closed":
        outcome = "rejected"
    return {
        "rejected": bool(raw.get("rejected")) and rejection_type != "none",
        "outcome": outcome,
        "rejection_type": rejection_type,
        "reason_type": reason_type,
        "reason_text": _text(raw.get("reason_text"))[:500],
        "reason_source": reason_source,
        "confidence": _score(raw.get("confidence"), 0.0),
        "evidence_message_ids": _valid_message_ids(raw.get("evidence_message_ids"), message_ids),
    }


def _normalize_progress_events(value: object, message_ids: set[str]) -> list[dict[str, Any]]:
    allowed = {
        "resume_requested", "resume_submitted", "resume_accepted", "resume_viewed",
        "under_review", "interview_invited", "interview_scheduled", "offer_received",
    }
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "")
        evidence = _valid_message_ids(item.get("evidence_message_ids"), message_ids)
        confidence = _score(item.get("confidence"), 0.0)
        if event_type not in allowed or not evidence or confidence < 0.8:
            continue
        normalized.append({
            "event_type": event_type,
            "confidence": confidence,
            "evidence_message_ids": evidence,
        })
    return normalized[:20]


def _attention_status(value: str) -> str:
    return value if value in ATTENTION_LABELS else "unknown"


def _recommended_action(value: str) -> str:
    return value if value in RECOMMENDED_ACTIONS else "no_further_action"


def _valid_message_ids(value: object, allowed: set[str]) -> list[str]:
    return [message_id for message_id in _message_ids(value) if message_id in allowed][:20]


def _message_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:20]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:30]


def _result_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _score(value: object, fallback: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = fallback
    return max(0.0, min(1.0, score))


def _text(value: object) -> str:
    return str(value or "").strip()


def _load_json(value: object, default: Any | None = None) -> Any:
    fallback = {} if default is None else default
    if value is None:
        return fallback
    try:
        loaded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback
    return loaded


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _size_breakdown(value: dict[str, Any]) -> dict[str, int]:
    return {str(key): len(_dump(item)) for key, item in value.items()}
