from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job import (
    pipeline_repository,
    smart_capture_engine,
    smart_captures,
    workflow_runs,
)
from backend.app.services.fine_job.boss_capture_history import get_capture_history_job
from backend.app.utils import new_id, utc_now


TERMINAL_STATUSES = {"completed", "stopped", "failed"}


def get_context_snapshot(
    db: Database, smart_capture_id: str, channel: str = "deep_job_search"
) -> dict[str, object]:
    """按 Smart Capture owner 读取 current context。"""
    capture = smart_captures.get_smart_capture(db, smart_capture_id)

    repository = pipeline_repository.PipelineRepository.for_smart_capture(
        db, smart_capture_id
    )
    row = repository.get_context_snapshot(channel)
    if row is None:
        _create_context_snapshot(db, capture, channel)
        row = repository.get_context_snapshot(channel)
    if row is None:
        raise AppError(404, "CONTEXT_SNAPSHOT_NOT_FOUND", "本轮上下文快照不存在。")
    return workflow_runs._serialize_snapshot(row)


def create_manual_analysis_batch(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    *,
    job_ids: list[str],
    analysis_batch_size: int,
    recommendation_strategy_id: str | None = None,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
) -> dict[str, object]:
    """以 Smart Capture 为 owner 创建自动或手工 Analysis 批次。"""
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    workflow_run_id = str(capture.get("workflow_run_id") or "")

    if str(capture.get("status")) in {"stopped", "failed"}:
        raise AppError(409, "SMART_CAPTURE_ANALYSIS_NOT_ALLOWED", "已停止或失败的 Smart Capture 不能创建分析批次。")

    normalized_ids = list(dict.fromkeys(str(job_id).strip() for job_id in job_ids if str(job_id).strip()))
    if not normalized_ids:
        raise AppError(422, "VALIDATION_FAILED", "至少选择一个岗位进入分析。")
    allowed_ids = _candidate_job_ids(db, smart_capture_id)
    invalid_ids = [job_id for job_id in normalized_ids if job_id not in allowed_ids]
    if invalid_ids:
        raise AppError(422, "CANDIDATE_NOT_IN_POOL", "只能选择当前 Smart Capture 候选池中的岗位。")

    ready_ids: list[str] = []
    for job_id in normalized_ids:
        job = get_capture_history_job(db, job_id)
        if str(job.get("detail_status") or "") != "completed":
            raise AppError(409, "CAPTURE_NOT_READY", f"岗位 {job_id} 的详情尚未完成，不能进入 Codex 分析。")
        if not _analysis_exists_for_smart_capture(db, smart_capture_id, job_id):
            ready_ids.append(job_id)
    if not ready_ids:
        raise AppError(409, "ANALYSIS_ALREADY_CREATED", "所选岗位都已经进入分析任务。")
    ready_ids = ready_ids[: max(1, int(analysis_batch_size))]
    analysis_batch_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for job_id in ready_ids:
            task_id = new_id()
            try:
                connection.execute(
                    """
                    INSERT INTO fj_workflow_candidate_reservations (
                      id, workflow_run_id, smart_capture_id, job_id, owner_type, owner_id, status, created_at
                    ) VALUES (?, ?, ?, ?, 'formal_analysis', ?, 'reserved', ?)
                    """,
                    (new_id(), workflow_run_id or None, smart_capture_id, job_id, task_id, now),
                )
                connection.execute(
                    """
                    INSERT INTO fj_workflow_tasks (
                      id, workflow_run_id, smart_capture_id, task_type, status, payload_json, result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'deep_job_search_analysis', 'pending', ?, '{}', ?, ?)
                    """,
                    (
                        task_id,
                        workflow_run_id or None,
                        smart_capture_id,
                        _dump(
                            {
                                "job_id": job_id,
                                "analysis_batch_id": analysis_batch_id,
                                "manual_batch": True,
                                "recommendation_strategy_id": recommendation_strategy_id or "",
                                "codex_model": codex_model or "",
                                "codex_reasoning_effort": codex_reasoning_effort or "",
                            }
                        ),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AppError(409, "CANDIDATE_RESERVATION_CONFLICT", "岗位已被其他活动 Smart Capture 占用。") from exc

    get_context_snapshot(db, smart_capture_id, "candidate_analysis")
    return _domain_snapshot(db, smart_capture_id, analysis_batch_id)


def list_analysis_items(
    db: Database, smart_capture_id: str, analysis_batch_id: str | None = None
) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    workflow_run_id = str(capture.get("workflow_run_id") or "")

    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_workflow_tasks
            WHERE smart_capture_id = ? AND task_type = 'deep_job_search_analysis'
            ORDER BY created_at, id
            """,
            (smart_capture_id,),
        ).fetchall()
        discoveries = connection.execute(
            """
            SELECT d.*, j.title, j.company_name, j.salary, j.location, j.detail_status, j.payload_json
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.smart_capture_id = ?
            ORDER BY d.discovered_at, d.job_id
            """,
            (smart_capture_id,),
        ).fetchall()
        feedback_rows = connection.execute(
            "SELECT * FROM fj_workflow_evaluation_feedback WHERE smart_capture_id = ? ORDER BY created_at DESC, id DESC",
            (smart_capture_id,),
        ).fetchall()
    discoveries_by_job = {str(row["job_id"]): row for row in discoveries}
    feedback_by_task: dict[str, list[dict[str, object]]] = {}
    for row in feedback_rows:
        feedback_by_task.setdefault(str(row["workflow_task_id"]), []).append(
            workflow_runs._serialize_feedback(row)
        )
    if analysis_batch_id:
        rows = [
            row
            for row in rows
            if _analysis_batch_id(row["payload_json"], smart_capture_id) == analysis_batch_id
        ]
    items = [
        workflow_runs._serialize_analysis_item(
            row,
            discoveries_by_job.get(str(workflow_runs._load(row["payload_json"], {}).get("job_id") or "")),
            feedback_by_task.get(str(row["id"]), []),
        )
        for row in rows
    ]
    return {
        "smart_capture_id": smart_capture_id,
        "workflow_run_id": workflow_run_id or None,
        "analysis_batch_id": analysis_batch_id or _latest_analysis_batch_id(rows, smart_capture_id),
        "analysis_handoff": _handoff_summary(db, smart_capture_id, rows),
        "items": items,
    }


def get_analysis_item_context(
    db: Database, smart_capture_id: str, workflow_task_id: str
) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    workflow_run_id = str(capture.get("workflow_run_id") or "")

    item = _get_analysis_item(db, smart_capture_id, workflow_task_id)
    payload = workflow_runs._load(item["payload_json"], {})
    job_id = str(payload.get("job_id") or "")
    job = get_capture_history_job(db, job_id)
    if str(job.get("detail_status") or "") != "completed":
        raise AppError(409, "CAPTURE_NOT_READY", "当前 Item 缺少完整 JD，不能进入 Codex 分析。")
    shared = get_context_snapshot(db, smart_capture_id, "candidate_analysis")
    execution_config = capture.get("execution_config") if isinstance(capture.get("execution_config"), dict) else {}
    sections = [
        workflow_runs._section(
            "analysis_item", "analysis_item", {"workflow_task_id": workflow_task_id, "job_id": job_id},
            "workflow_task", None, True, "",
        ),
        workflow_runs._section(
            "job_material", "analysis_item", workflow_runs._compact_job_for_analysis(job),
            "boss_job", int(job.get("detail_version") or 0), True, "只包含当前岗位的 JD 与必要岗位事实。",
        ),
        workflow_runs._section(
            "smart_capture_config", "shared_base", {"smart_capture_id": smart_capture_id, "execution_config": execution_config},
            "smart_capture", int(capture.get("state_version") or 1), True, "使用当前 Smart Capture 的执行配置。",
        ),
        workflow_runs._section(
            "analysis_guidance", "shared_base", execution_config.get("analysis", {}).get("guidance", "") if isinstance(execution_config.get("analysis"), dict) else "",
            "smart_capture", int(capture.get("state_version") or 1), True, "当前批次的分析指导。",
        ),
        workflow_runs._section(
            "shared_base_reference", "shared_base", {"context_snapshot_id": shared["context_snapshot_id"]},
            "smart_capture_context_snapshot", None, True, "复用当前 Smart Capture 上下文快照。",
        ),
    ]
    sections.extend(
        workflow_runs._section(section_id, "excluded", None, source, None, False, reason)
        for section_id, source, reason in (
            ("complete_resume", "resume", "单 Item 默认不注入完整简历。"),
            ("unrelated_qa", "profile_qa", "岗位评估默认不注入无关 QA。"),
            ("unrelated_chat", "chat", "岗位评估默认不注入无关聊天。"),
        )
    )
    snapshot = _write_context_snapshot(
        db,
        capture,
        f"analysis_item:{workflow_task_id}",
        sections,
        int(shared["soft_budget_characters"]),
    )
    if snapshot["status"] == "blocked":
        raise AppError(409, "CONTEXT_SNAPSHOT_BLOCKED", str(snapshot["blocker_reason"]))
    return {
        "smart_capture_id": smart_capture_id,
        "workflow_run_id": workflow_run_id or None,
        "workflow_task_id": workflow_task_id,
        "job_id": job_id,
        "shared_context_snapshot_id": shared["context_snapshot_id"],
        "item_context_snapshot": snapshot,
        "expected_output": {
            "decision": "recommend | review | reject",
            "confidence": "0 到 1",
            "summary": "简要结论",
            "hard_requirements": ["硬条件判断"],
            "match_dimensions": {"维度": "判断"},
            "strengths": ["匹配点"],
            "gaps": ["差距"],
            "risks": ["风险"],
            "missing_information": ["缺失信息"],
            "reasons": ["依据"],
            "jd_evidence": ["JD 证据"],
            "candidate_evidence": ["候选人证据"],
        },
    }


def save_analysis_item(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    workflow_task_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    item = _get_analysis_item(db, smart_capture_id, workflow_task_id)
    if str(item["status"]) == "succeeded":
        return _domain_snapshot(
            db,
            smart_capture_id,
            _analysis_batch_id(item["payload_json"], smart_capture_id),
            workflow_task_id=workflow_task_id,
        )
    batch_id = _analysis_batch_id(item["payload_json"], smart_capture_id)
    _require_handoff_started(db, smart_capture_id, batch_id)
    context = get_analysis_item_context(db, smart_capture_id, workflow_task_id)
    decision = str(payload.get("decision") or "review")
    if decision not in {"recommend", "review", "reject"}:
        raise AppError(422, "VALIDATION_FAILED", "分析结论无效。")
    result = {
        "job_id": str(context["job_id"]),
        "decision": decision,
        "evaluation_id": str(payload.get("evaluation_id") or ""),
        "confidence": float(payload.get("confidence") or 0),
        "summary": str(payload.get("summary") or ""),
        "reasons": list(payload.get("reasons") or []),
        "risks": list(payload.get("risks") or []),
        "strengths": list(payload.get("strengths") or []),
        "gaps": list(payload.get("gaps") or []),
        "hard_requirements": list(payload.get("hard_requirements") or []),
        "match_dimensions": dict(payload.get("match_dimensions") or {}),
        "missing_information": list(payload.get("missing_information") or []),
        "jd_evidence": list(payload.get("jd_evidence") or []),
        "candidate_evidence": list(payload.get("candidate_evidence") or []),
    }
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'succeeded', result_json = ?, completed_at = ?, updated_at = ? WHERE id = ? AND smart_capture_id = ?",
            (_dump(result), now, now, workflow_task_id, smart_capture_id),
        )
        connection.execute(
            """
            UPDATE fj_workflow_candidate_reservations
            SET status = 'released', released_at = ?, terminal_at = ?
            WHERE smart_capture_id = ? AND owner_type = 'formal_analysis' AND owner_id = ?
              AND job_id = ? AND status = 'reserved'
            """,
            (now, now, smart_capture_id, workflow_task_id, str(context["job_id"])),
        )
    smart_capture_engine.analysis_item_saved(db, smart_capture_id, batch_id)
    return _domain_snapshot(db, smart_capture_id, batch_id, workflow_task_id=workflow_task_id)


def save_feedback(
    db: Database, smart_capture_id: str, workflow_task_id: str, payload: dict[str, object]
) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    _get_analysis_item(db, smart_capture_id, workflow_task_id)
    feedback_id = new_id()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_evaluation_feedback (
              id, workflow_run_id, smart_capture_id, workflow_task_id, evaluation_id,
              sentiment, reason, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                workflow_run_id or None,
                smart_capture_id,
                workflow_task_id,
                str(payload.get("evaluation_id") or "") or None,
                str(payload.get("sentiment") or "expected"),
                str(payload.get("reason") or "") or None,
                str(payload.get("note") or "").strip(),
                utc_now(),
            ),
        )
    return {"feedback_id": feedback_id, "smart_capture_id": smart_capture_id, "workflow_task_id": workflow_task_id}


def update_guidance(db: Database, smart_capture_id: str, guidance: str) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    execution_config = dict(capture.get("execution_config") or {})
    analysis = dict(execution_config.get("analysis") or {})
    normalized_guidance = guidance.strip()
    if str(analysis.get("guidance") or "") == normalized_guidance:
        return capture
    analysis["guidance"] = normalized_guidance
    analysis["guidance_version"] = int(analysis.get("guidance_version") or 0) + 1
    execution_config["analysis"] = analysis
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_smart_captures SET execution_config_json = ?, state_version = state_version + 1, updated_at = ? WHERE id = ?",
            (_dump(execution_config), utc_now(), smart_capture_id),
        )
    return smart_captures.get_smart_capture(db, smart_capture_id)


def attach_codex_session(
    db: Database,
    smart_capture_id: str,
    codex_session_ref: str,
    codex_runtime_id: str | None,
    analysis_batch_id: str | None = None,
) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM fj_codex_sessions WHERE id = ?", (codex_session_ref,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO fj_codex_sessions (
                  id, smart_capture_id, workflow_run_id, analysis_batch_id, status,
                  started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (codex_session_ref, smart_capture_id, workflow_run_id or None, analysis_batch_id, now, now, now),
            )
        else:
            connection.execute(
                "UPDATE fj_codex_sessions SET smart_capture_id = ?, workflow_run_id = ?, analysis_batch_id = ?, status = 'running', updated_at = ? WHERE id = ?",
                (smart_capture_id, workflow_run_id or None, analysis_batch_id, now, codex_session_ref),
            )
    return smart_captures.get_smart_capture(db, smart_capture_id)


def claim_handoff(
    db: Database,
    smart_capture_id: str,
    *,
    codex_session_ref: str,
    codex_runtime_id: str | None,
    handoff_kind: str,
    retry_handoff_attempt_id: str | None = None,
) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    rows = _analysis_rows(db, smart_capture_id)
    active_batch_id = _active_batch_id(rows)
    if not active_batch_id:
        raise AppError(409, "SMART_CAPTURE_ANALYSIS_BATCH_NOT_READY", "当前没有可交接的 pending 分析批次。")
    now = utc_now()
    attempt_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, active_batch_id),
        ).fetchone()
        if existing is not None and str(existing["attempt_status"] or "") in {"claimed", "prompt_written", "started"}:
            can_retry = (
                str(existing["attempt_status"] or "") == "prompt_written"
                and str(existing["handoff_attempt_id"] or "") == str(retry_handoff_attempt_id or "")
                and workflow_runs._start_ack_timed_out(existing)
            )
            if not can_retry:
                raise AppError(409, "SMART_CAPTURE_ANALYSIS_BATCH_IN_PROGRESS", "当前分析批次已有有效 Codex 交接。")
        if existing is None:
            connection.execute(
                """
                INSERT INTO fj_workflow_analysis_handoffs (
                  workflow_run_id, smart_capture_id, analysis_batch_id, status, handoff_attempt_id,
                  attempt_status, codex_session_ref, codex_runtime_id, claimed_at
                ) VALUES (?, ?, ?, 'claimed', ?, 'claimed', ?, ?, ?)
                """,
                (workflow_run_id or None, smart_capture_id, active_batch_id, attempt_id, codex_session_ref, codex_runtime_id or "", now),
            )
        else:
            connection.execute(
                """
                UPDATE fj_workflow_analysis_handoffs
                SET status = 'claimed', handoff_attempt_id = ?, attempt_status = 'claimed',
                    codex_session_ref = ?, codex_runtime_id = ?, claimed_at = ?, submitted_at = NULL,
                    prompt_written_at = NULL, started_at = NULL, released_at = NULL, completed_at = NULL,
                    recovered_at = ?, recovery_reason = ?
                WHERE smart_capture_id = ? AND analysis_batch_id = ?
                """,
                (attempt_id, codex_session_ref, codex_runtime_id or "", now, now if retry_handoff_attempt_id else None,
                 "retry" if retry_handoff_attempt_id else "", smart_capture_id, active_batch_id),
            )
    return _domain_snapshot(db, smart_capture_id, active_batch_id, handoff=True)


def prompt_written(
    db: Database, smart_capture_id: str, analysis_batch_id: str, handoff_attempt_id: str, codex_session_ref: str
) -> dict[str, object]:
    _update_handoff(
        db, smart_capture_id, analysis_batch_id, handoff_attempt_id, codex_session_ref,
        expected={"claimed", "prompt_written"}, updates={"status": "submitted", "attempt_status": "prompt_written", "submitted_at": utc_now(), "prompt_written_at": utc_now()},
        idempotent_attempt_statuses={"prompt_written", "started"},
    )
    return _domain_snapshot(db, smart_capture_id, analysis_batch_id, handoff=True)


def ack_started(
    db: Database, config: AppConfig, smart_capture_id: str, analysis_batch_id: str, handoff_attempt_id: str
) -> dict[str, object]:
    _update_handoff(
        db, smart_capture_id, analysis_batch_id, handoff_attempt_id, None,
        expected={"prompt_written", "started"}, updates={"status": "submitted", "attempt_status": "started", "started_at": utc_now()},
        check_session=False, idempotent_attempt_statuses={"started"},
    )
    output_root = Path(getattr(config, "output_root", Path.cwd()))
    smart_capture_engine.start_prefetch_after_handoff(
        db,
        smart_capture_id,
        analysis_batch_id,
        output_root / "fine-job" / "boss-capture",
    )
    return _domain_snapshot(db, smart_capture_id, analysis_batch_id, handoff=True)


def release_handoff(
    db: Database, smart_capture_id: str, analysis_batch_id: str, handoff_attempt_id: str,
    codex_session_ref: str, release_reason: str | None = None,
) -> dict[str, object]:
    handoff = _update_handoff(
        db, smart_capture_id, analysis_batch_id, handoff_attempt_id, codex_session_ref,
        expected={"claimed", "prompt_written"},
        updates={"status": "released", "attempt_status": "released", "released_at": utc_now(), "recovery_reason": release_reason or "transport_failure"},
        allow_full_retry=release_reason == "full_retry",
    )
    return _domain_snapshot(db, smart_capture_id, analysis_batch_id, handoff=True, handoff_row=handoff)


def retry_handoff(
    db: Database, smart_capture_id: str, analysis_batch_id: str, handoff_attempt_id: str,
    codex_session_ref: str, codex_runtime_id: str | None,
) -> dict[str, object]:
    return claim_handoff(
        db,
        smart_capture_id,
        codex_session_ref=codex_session_ref,
        codex_runtime_id=codex_runtime_id,
        handoff_kind="next",
        retry_handoff_attempt_id=handoff_attempt_id,
    )


def _create_context_snapshot(db: Database, capture: dict[str, object], channel: str) -> None:
    execution_config = capture.get("execution_config") if isinstance(capture.get("execution_config"), dict) else {}
    candidate_ids = sorted(_candidate_job_ids(db, str(capture["smart_capture_id"])))
    sections = [
        workflow_runs._section(
            "smart_capture", "shared_base",
            {"smart_capture_id": capture["smart_capture_id"], "source": capture["source"], "status": capture["status"]},
            "smart_capture", int(capture.get("state_version") or 1), True, "",
        ),
        workflow_runs._section(
            "execution_config", "task_channel", execution_config, "smart_capture", int(capture.get("state_version") or 1), True, "",
        ),
        workflow_runs._section(
            "candidate_pool", "task_channel", {"job_ids": candidate_ids, "count": len(candidate_ids)}, "smart_capture_pipeline", None, True, "",
        ),
    ]
    _write_context_snapshot(
        db,
        capture,
        channel,
        sections,
        int(execution_config.get("context_budget") or 12000),
    )


def _write_context_snapshot(
    db: Database, capture: dict[str, object], channel: str, sections: list[dict[str, object]], soft_budget: int
) -> dict[str, object]:
    workflow_run_id = str(capture.get("workflow_run_id") or "") or None
    smart_capture_id = str(capture["smart_capture_id"])
    characters = sum(int(section.get("character_count") or 0) for section in sections if section.get("included"))
    status = "blocked" if characters > soft_budget else "ready"
    blocker = "上下文超过本轮软预算，请裁剪后继续。" if status == "blocked" else ""
    now = utc_now()
    snapshot_id = new_id()
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM fj_workflow_context_snapshots WHERE smart_capture_id = ? AND channel = ? ORDER BY created_at DESC LIMIT 1",
            (smart_capture_id, channel),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO fj_workflow_context_snapshots (
                  id, workflow_run_id, smart_capture_id, channel, snapshot_json, context_characters,
                  estimated_tokens, soft_budget_characters, hard_budget_characters, status, blocker_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, workflow_run_id, smart_capture_id, channel, _dump({"sections": sections}), characters,
                 workflow_runs._estimate_tokens(characters), soft_budget, 1_000_000, status, blocker, now),
            )
        else:
            connection.execute(
                """
                UPDATE fj_workflow_context_snapshots
                SET workflow_run_id = ?, snapshot_json = ?, context_characters = ?, estimated_tokens = ?,
                    soft_budget_characters = ?, status = ?, blocker_reason = ?, created_at = ?
                WHERE id = ?
                """,
                (workflow_run_id, _dump({"sections": sections}), characters, workflow_runs._estimate_tokens(characters),
                 soft_budget, status, blocker, now, str(existing["id"])),
            )
    return {
        "context_snapshot_id": str(existing["id"]) if existing is not None else snapshot_id,
        "channel": channel,
        "sections": sections,
        "context_characters": characters,
        "estimated_tokens": workflow_runs._estimate_tokens(characters),
        "soft_budget_characters": soft_budget,
        "hard_budget_characters": 1_000_000,
        "status": status,
        "blocker_reason": blocker,
        "generated_at": now,
    }


def _candidate_job_ids(db: Database, smart_capture_id: str) -> set[str]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT job_id FROM fj_workflow_job_discoveries WHERE smart_capture_id = ?",
            (smart_capture_id,),
        ).fetchall()
    return {str(row["job_id"]) for row in rows}


def _analysis_rows(db: Database, smart_capture_id: str) -> list[Any]:
    with db.connect() as connection:
        return connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_analysis' ORDER BY created_at, id",
            (smart_capture_id,),
        ).fetchall()


def _get_analysis_item(db: Database, smart_capture_id: str, workflow_task_id: str) -> Any:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ? AND smart_capture_id = ? AND task_type = 'deep_job_search_analysis'",
            (workflow_task_id, smart_capture_id),
        ).fetchone()
    if row is None:
        raise AppError(404, "SMART_CAPTURE_ANALYSIS_ITEM_NOT_FOUND", "Smart Capture 分析 Item 不存在。")
    return row


def _analysis_exists_for_smart_capture(db: Database, smart_capture_id: str, job_id: str) -> bool:
    with db.connect() as connection:
        return connection.execute(
            "SELECT 1 FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_analysis' AND json_extract(payload_json, '$.job_id') = ? LIMIT 1",
            (smart_capture_id, job_id),
        ).fetchone() is not None


def _analysis_batch_id(payload_json: object, smart_capture_id: str) -> str:
    payload = workflow_runs._load(payload_json, {})
    return str(payload.get("analysis_batch_id") or f"legacy:{smart_capture_id}")


def _active_batch_id(rows: list[Any]) -> str:
    for row in rows:
        if str(row["status"]) in {"pending", "running"}:
            return _analysis_batch_id(row["payload_json"], str(row["smart_capture_id"]))
    return _analysis_batch_id(rows[-1]["payload_json"], str(rows[-1]["smart_capture_id"])) if rows else ""


def _latest_analysis_batch_id(rows: list[Any], smart_capture_id: str) -> str:
    return _active_batch_id(rows) or f"legacy:{smart_capture_id}"


def _handoff_summary(db: Database, smart_capture_id: str, rows: list[Any]) -> dict[str, object]:
    batch_id = _active_batch_id(rows)
    if not batch_id:
        return {"analysis_batch_id": "", "pending_item_count": 0, "attempt_status": "none"}
    with db.connect() as connection:
        handoff = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, batch_id),
        ).fetchone()
    batch_rows = [row for row in rows if _analysis_batch_id(row["payload_json"], smart_capture_id) == batch_id]
    return {
        "analysis_batch_id": batch_id,
        "pending_item_count": sum(str(row["status"]) == "pending" for row in batch_rows),
        "running_item_count": sum(str(row["status"]) == "running" for row in batch_rows),
        "succeeded_item_count": sum(str(row["status"]) == "succeeded" for row in batch_rows),
        "handoff_status": str(handoff["status"]) if handoff is not None else "none",
        "handoff_attempt_id": str(handoff["handoff_attempt_id"]) if handoff is not None else None,
        "attempt_status": str(handoff["attempt_status"]) if handoff is not None else "none",
        "codex_session_ref": str(handoff["codex_session_ref"]) if handoff is not None else None,
        "codex_runtime_id": str(handoff["codex_runtime_id"]) if handoff is not None else None,
    }


def _domain_snapshot(
    db: Database, smart_capture_id: str, analysis_batch_id: str, *,
    workflow_task_id: str | None = None, handoff: bool = False, handoff_row: Any | None = None,
) -> dict[str, object]:
    capture = smart_captures.get_smart_capture(db, smart_capture_id)
    items = list_analysis_items(db, smart_capture_id, analysis_batch_id).get("items", [])
    result: dict[str, object] = {
        "smart_capture_id": smart_capture_id,
        "workflow_run_id": capture.get("workflow_run_id"),
        "status": capture.get("status"),
        "analysis_batch_id": analysis_batch_id,
        "items": items,
        "smart_capture": capture,
    }
    if workflow_task_id:
        result["workflow_task_id"] = workflow_task_id
    if handoff:
        summary = _handoff_summary(db, smart_capture_id, _analysis_rows(db, smart_capture_id))
        if handoff_row is not None:
            summary["attempt_status"] = str(handoff_row["attempt_status"])
        result["handoff"] = summary
    return result


def _update_handoff(
    db: Database, smart_capture_id: str, analysis_batch_id: str, handoff_attempt_id: str,
    codex_session_ref: str | None, *, expected: set[str], updates: dict[str, object],
    check_session: bool = True, allow_full_retry: bool = False,
    idempotent_attempt_statuses: set[str] | None = None,
) -> Any:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        handoff = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, analysis_batch_id),
        ).fetchone()
        if handoff is None or str(handoff["handoff_attempt_id"] or "") != handoff_attempt_id:
            raise AppError(409, "SMART_CAPTURE_ANALYSIS_HANDOFF_STALE", "当前 Codex 交接尝试已失效。")
        if str(handoff["attempt_status"] or "") not in expected:
            raise AppError(409, "SMART_CAPTURE_ANALYSIS_HANDOFF_STALE", "当前 Codex 交接状态不允许此操作。")
        if check_session and codex_session_ref is not None and str(handoff["codex_session_ref"] or "") != codex_session_ref:
            raise AppError(409, "SMART_CAPTURE_ANALYSIS_HANDOFF_STALE", "当前 Codex 会话不是有效交接会话。")
        if str(handoff["attempt_status"] or "") in (idempotent_attempt_statuses or set()):
            return handoff
        if allow_full_retry and str(handoff["attempt_status"] or "") != "prompt_written":
            raise AppError(409, "SMART_CAPTURE_ANALYSIS_HANDOFF_ALREADY_SUBMITTED", "当前交接不能按完整重试释放。")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        connection.execute(
            f"UPDATE fj_workflow_analysis_handoffs SET {assignments} WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (*updates.values(), smart_capture_id, analysis_batch_id),
        )
        return connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, analysis_batch_id),
        ).fetchone()


def _require_handoff_started(db: Database, smart_capture_id: str, analysis_batch_id: str) -> None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT attempt_status FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, analysis_batch_id),
        ).fetchone()
    if row is None or str(row["attempt_status"] or "") != "started":
        raise AppError(409, "SMART_CAPTURE_ANALYSIS_NOT_STARTED", "当前分析批次尚未收到 Codex 开始 ACK。")


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
