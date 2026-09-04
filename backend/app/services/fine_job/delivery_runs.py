from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    boss_scraper_service,
)
from backend.app.services.fine_job.delivery_strategies import get_delivery_strategy
from backend.app.services.fine_job.job_intents import get_job_intent
from backend.app.services.fine_job.platform_sessions import (
    DEFAULT_PLATFORM,
    get_platform_session,
)
from backend.app.utils import new_id, utc_now


@dataclass(slots=True)
class BossCdpJob:
    keyword: str
    city: str
    job_url: str
    job_title: str
    company_name: str
    salary_text: str
    location_text: str
    experience_text: str
    education_text: str
    hr_active_text: str
    jd_text: str


def create_delivery_run(
    db: Database,
    *,
    config: AppConfig,
    mode: str = "dry_run",
    real_collect: bool = True,
) -> dict[str, object]:
    if mode != "dry_run":
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Live delivery is not enabled yet.",
        )

    intent = get_job_intent(db)
    strategy = get_delivery_strategy(db)
    if not intent or not intent.get("ready"):
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Job intent is required before starting delivery.",
        )
    if not strategy or not strategy.get("ready"):
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Delivery strategy is required before starting delivery.",
        )

    run_id = new_id()
    now = utc_now()
    keywords = _build_search_keywords(intent)
    cities = [str(city) for city in intent.get("cities", [])]
    min_match_score = float(strategy.get("min_match_score") or 0.72)

    _create_run_record(
        db,
        run_id=run_id,
        mode=mode,
        status="running",
        stage="boss_collecting" if real_collect else "dry_run_candidates_generating",
        intent=intent,
        strategy=strategy,
        created_at=now,
    )
    _log(
        db,
        run_id=run_id,
        level="info",
        action_type="run_created",
        message="已创建 dry-run 投递任务，不会发送打招呼消息。",
        detail={"mode": mode, "real_collect": real_collect},
    )
    _log(
        db,
        run_id=run_id,
        level="info",
        action_type="search_plan",
        message=f"计划搜索 {len(cities)} 个城市、{len(keywords)} 个关键词。",
        detail={"cities": cities, "keywords": keywords},
    )

    if not real_collect:
        candidates = _build_dry_run_candidates(
            run_id=run_id,
            cities=cities,
            keywords=keywords,
            min_match_score=min_match_score,
            created_at=now,
        )
        _save_candidates(db, candidates)
        _log(
            db,
            run_id=run_id,
            level="info",
            action_type="candidates_generated",
            message=f"已生成 {len(candidates)} 条 dry-run 候选岗位占位数据。",
            detail={"candidate_count": len(candidates)},
        )
        _finish_run(
            db,
            run_id=run_id,
            status="completed",
            stage="dry_run_candidates_generated",
            searched_count=len(candidates),
            skipped_count=sum(1 for item in candidates if item["decision"] == "skipped"),
            error_count=0,
        )
        _log(
            db,
            run_id=run_id,
            level="warning",
            action_type="dry_run_guard",
            message="当前使用占位模式：只生成候选结构和日志，不搜索 BOSS，不发送打招呼。",
            detail={"candidate_count": len(candidates)},
        )
        return get_delivery_run(db, run_id)

    session = get_platform_session(db, DEFAULT_PLATFORM)
    browser_status = boss_scraper_service.get_browser_status()
    if not session or not session.get("ready") or not browser_status.running:
        _pause_run(
            db,
            run_id=run_id,
            stage="waiting_for_login",
            message="BOSS 登录状态未就绪，请先在平台登录页完成登录检测。",
        )
        return get_delivery_run(db, run_id)

    candidates: list[dict[str, object]] = []
    seen_urls: set[str] = set()
    try:
        for city in cities:
            for keyword in keywords:
                _log(
                    db,
                    run_id=run_id,
                    level="info",
                    action_type="boss_search_started",
                    message=f"开始采集 BOSS：{city} / {keyword}。",
                    detail={"city": city, "keyword": keyword},
                )
                # 旧 Puppeteer 采集模块保留在 boss_puppeteer_collector.py，
                # 正式投递准备链路从这里开始统一调用内置 CDP 服务。
                capture_result = boss_scraper_service.capture_jobs(
                    BossCaptureRequest(
                        keyword=keyword,
                        city=city,
                        pages=1,
                        include_details=True,
                        max_details=3,
                        output_dir=config.output_root / "fine-job" / "boss-capture",
                    )
                )
                jobs = _map_cdp_jobs(
                    capture_result.list_data,
                    capture_result.details,
                    keyword=keyword,
                    city=city,
                    limit=3,
                )
                for job in jobs:
                    if job.job_url in seen_urls:
                        continue
                    seen_urls.add(job.job_url)
                    candidates.append(
                        _build_candidate_from_job(
                            run_id=run_id,
                            job=job,
                            intent=intent,
                            strategy=strategy,
                            min_match_score=min_match_score,
                        )
                    )
                _log(
                    db,
                    run_id=run_id,
                    level="info",
                    action_type="boss_search_finished",
                    message=f"完成采集 BOSS：{city} / {keyword}，获得 {len(jobs)} 条。",
                    detail={"city": city, "keyword": keyword, "count": len(jobs)},
                )
    except AppError as exc:
        if candidates:
            _save_candidates(db, candidates)
        _pause_run(
            db,
            run_id=run_id,
            stage="boss_collection_paused",
            message=exc.error_message,
            searched_count=len(candidates),
            skipped_count=sum(1 for item in candidates if item["decision"] == "skipped"),
        )
        return get_delivery_run(db, run_id)
    except RuntimeError as exc:
        if candidates:
            _save_candidates(db, candidates)
        _pause_run(
            db,
            run_id=run_id,
            stage="boss_collection_paused",
            message=str(exc),
            searched_count=len(candidates),
            skipped_count=sum(1 for item in candidates if item["decision"] == "skipped"),
        )
        return get_delivery_run(db, run_id)
    except Exception as exc:  # pragma: no cover - 防御性保护
        if candidates:
            _save_candidates(db, candidates)
        _finish_run(
            db,
            run_id=run_id,
            status="failed",
            stage="boss_collection_failed",
            searched_count=len(candidates),
            skipped_count=sum(1 for item in candidates if item["decision"] == "skipped"),
            error_count=1,
            error_message=str(exc),
        )
        _log(
            db,
            run_id=run_id,
            level="error",
            action_type="boss_collection_failed",
            message=str(exc),
            detail={},
        )
        return get_delivery_run(db, run_id)

    _save_candidates(db, candidates)
    _finish_run(
        db,
        run_id=run_id,
        status="completed",
        stage="boss_candidates_collected",
        searched_count=len(candidates),
        skipped_count=sum(1 for item in candidates if item["decision"] == "skipped"),
        error_count=0,
    )
    _log(
        db,
        run_id=run_id,
        level="warning",
        action_type="dry_run_guard",
        message="真实采集已完成，但不会发送打招呼、投简历或回复 HR。",
        detail={"candidate_count": len(candidates)},
    )
    return get_delivery_run(db, run_id)


def list_delivery_runs(db: Database, *, limit: int = 20) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, mode, status, stage, searched_count, skipped_count,
                   greeted_count, error_count, started_at, updated_at,
                   finished_at, error_message
            FROM fj_delivery_runs
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_serialize_run(row) for row in rows]


def get_delivery_run(db: Database, run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, mode, status, stage, searched_count, skipped_count,
                   greeted_count, error_count, started_at, updated_at,
                   finished_at, error_message
            FROM fj_delivery_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="FineJob delivery run not found.",
        )
    return _serialize_run(row)


def list_delivery_candidates(db: Database, run_id: str) -> list[dict[str, object]]:
    get_delivery_run(db, run_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, run_id, platform, keyword, city, job_url, job_title, company_name,
                   salary_text, location_text, experience_text, education_text,
                   hr_active_text, jd_text, match_score, decision, reason,
                   created_at, updated_at
            FROM fj_delivery_candidates
            WHERE run_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_action_logs(
    db: Database,
    *,
    run_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    return query_action_logs(db, run_id=run_id, page_size=limit)["logs"]


def query_action_logs(
    db: Database,
    *,
    run_id: str | None = None,
    query: str = "",
    level: str | None = None,
    action_type: str | None = None,
    category: str | None = None,
    outcome: str | None = None,
    source: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, object]:
    # 旧日志与主工作流日志共用一张表，在查询层统一补充业务分类和执行结果。
    category_sql = """
      CASE
        WHEN l.action_type LIKE 'review_%' THEN 'review'
        WHEN l.action_type = 'executor_control' OR l.action_type LIKE 'boss_%'
          OR l.action_type LIKE 'action_%' THEN 'execution'
        WHEN l.action_type LIKE 'run_%' OR l.action_type LIKE 'search_%'
          OR l.action_type LIKE 'candidates_%' OR l.action_type LIKE 'dry_run_%'
          OR l.action_type LIKE 'waiting_%' THEN 'capture'
        WHEN l.action_type LIKE '%chat%' THEN 'chat'
        ELSE 'system'
      END
    """
    outcome_sql = """
      CASE
        WHEN l.level = 'error' THEN 'failed'
        WHEN l.action_type LIKE '%succeeded%' OR l.action_type LIKE '%accepted%'
          OR l.action_type LIKE '%opened%' OR l.action_type LIKE '%approved%'
          OR l.action_type LIKE '%restored%' THEN 'succeeded'
        WHEN l.level = 'warning' THEN 'warning'
        ELSE 'info'
      END
    """
    conditions: list[str] = []
    values: list[object] = []
    if run_id:
        conditions.append("l.run_id = ?")
        values.append(run_id)
    search = query.strip()
    if search:
        wildcard = f"%{search}%"
        conditions.append(
            "(l.message LIKE ? OR l.action_type LIKE ? OR l.detail_json LIKE ? "
            "OR j.title LIKE ? OR j.company_name LIKE ?)"
        )
        values.extend([wildcard, wildcard, wildcard, wildcard, wildcard])
    if level == "issue":
        conditions.append("l.level IN ('warning', 'error')")
    elif level:
        conditions.append("l.level = ?")
        values.append(level)
    if action_type:
        conditions.append("l.action_type = ?")
        values.append(action_type)
    if category:
        conditions.append(f"({category_sql}) = ?")
        values.append(category)
    if outcome:
        conditions.append(f"({outcome_sql}) = ?")
        values.append(outcome)
    if source == "legacy_run":
        conditions.append("l.run_id IS NOT NULL")
    elif source == "main_workflow":
        conditions.append("l.run_id IS NULL")
    if created_from:
        conditions.append("l.created_at >= ?")
        values.append(created_from)
    if created_to:
        conditions.append("l.created_at <= ?")
        values.append(created_to)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    joins = """
      LEFT JOIN fj_automation_actions a
        ON a.id = json_extract(l.detail_json, '$.action_id')
      LEFT JOIN fj_boss_jobs j
        ON j.id = COALESCE(json_extract(l.detail_json, '$.job_id'), a.job_id)
    """
    offset = (page - 1) * page_size
    with db.connect() as connection:
        total = int(connection.execute(
            f"SELECT COUNT(DISTINCT l.id) FROM fj_action_logs l {joins} {where}",
            values,
        ).fetchone()[0])
        rows = connection.execute(
            f"""
            SELECT l.id, l.run_id, l.level, l.action_type, l.message,
                   l.detail_json, l.created_at,
                   CASE WHEN l.run_id IS NULL THEN 'main_workflow' ELSE 'legacy_run' END AS source,
                   {category_sql} AS category,
                   {outcome_sql} AS outcome,
                   j.id AS job_id, j.title AS job_title, j.company_name
            FROM fj_action_logs l
            {joins}
            {where}
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT ? OFFSET ?
            """,
            [*values, page_size, offset],
        ).fetchall()
        action_types = [
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT action_type FROM fj_action_logs ORDER BY action_type"
            ).fetchall()
        ]
    return {
        "logs": [_serialize_log(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "action_types": action_types,
    }


def cleanup_action_logs(db: Database, *, before: str, source: str = "all") -> int:
    conditions = ["created_at < ?"]
    values: list[object] = [before]
    if source == "legacy_run":
        conditions.append("run_id IS NOT NULL")
    elif source == "main_workflow":
        conditions.append("run_id IS NULL")
    with db.connect() as connection:
        cursor = connection.execute(
            f"DELETE FROM fj_action_logs WHERE {' AND '.join(conditions)}",
            values,
        )
    return max(0, int(cursor.rowcount))


def delete_delivery_run(db: Database, run_id: str) -> dict[str, object]:
    run = get_delivery_run(db, run_id)
    if run["status"] in {"pending", "running"}:
        raise AppError(
            status_code=409,
            error_category="RUN_ACTIVE",
            error_message="运行中的旧任务需要先结束后再删除。",
        )
    with db.connect() as connection:
        candidates_deleted = int(connection.execute(
            "SELECT COUNT(*) FROM fj_delivery_candidates WHERE run_id = ?", (run_id,)
        ).fetchone()[0])
        logs_deleted = int(connection.execute(
            "SELECT COUNT(*) FROM fj_action_logs WHERE run_id = ?", (run_id,)
        ).fetchone()[0])
        # 旧任务删除时同步清理候选岗位和绑定日志，避免留下失去归属的数据。
        connection.execute("DELETE FROM fj_action_logs WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM fj_delivery_candidates WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM fj_delivery_runs WHERE id = ?", (run_id,))
    return {
        "deleted": True,
        "id": run_id,
        "candidates_deleted": candidates_deleted,
        "logs_deleted": logs_deleted,
    }


def get_operations_dashboard(db: Database) -> dict[str, object]:
    from backend.app.services.fine_job.boss_executor import executor_status

    # 看板只读取当前岗位、评估、确认和动作表，旧 dry-run 单独作为历史数据展示。
    with db.connect() as connection:
        review_counts = _group_counts(connection, "fj_review_items", "status")
        action_counts = _group_counts(connection, "fj_automation_actions", "status")
        execution_counts = _group_counts(connection, "fj_automation_actions", "execution_state")
        capture_counts = _group_counts(connection, "fj_boss_capture_batches", "status")
        metrics = {
            "jobs": int(connection.execute("SELECT COUNT(*) FROM fj_boss_jobs").fetchone()[0]),
            "detailed_jobs": int(connection.execute(
                "SELECT COUNT(*) FROM fj_boss_jobs WHERE detail_status = 'completed'"
            ).fetchone()[0]),
            "evaluated_jobs": int(connection.execute(
                "SELECT COUNT(DISTINCT job_id) FROM fj_job_evaluations"
            ).fetchone()[0]),
            "pending_reviews": review_counts.get("pending", 0),
            "queued_actions": action_counts.get("queued", 0),
            "active_actions": sum(
                execution_counts.get(state, 0)
                for state in ("running",)
            ),
            "successful_actions": action_counts.get("succeeded", 0),
            "issue_actions": sum(action_counts.get(state, 0) for state in ("failed", "blocked", "unknown")),
        }
    runtime = executor_status(db)
    queue = runtime.get("queue") if isinstance(runtime.get("queue"), dict) else {"actions": [], "total": 0}
    warnings = query_action_logs(db, level="warning", page_size=8)["logs"]
    errors = query_action_logs(db, level="error", page_size=8)["logs"]
    recent_issues = sorted(
        [*warnings, *errors], key=lambda item: str(item["created_at"]), reverse=True
    )[:8]
    return {
        "generated_at": utc_now(),
        "metrics": metrics,
        "review_counts": review_counts,
        "action_counts": action_counts,
        "execution_counts": execution_counts,
        "capture_counts": capture_counts,
        "executor": runtime.get("executor"),
        "current_task": runtime.get("current_task"),
        "queue": queue,
        "recent_issues": recent_issues,
        "legacy_runs": list_delivery_runs(db, limit=20),
    }


def _group_counts(connection, table: str, column: str) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT {column}, COUNT(*) AS total FROM {table} GROUP BY {column}"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _create_run_record(
    db: Database,
    *,
    run_id: str,
    mode: str,
    status: str,
    stage: str,
    intent: dict[str, object],
    strategy: dict[str, object],
    created_at: str,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_delivery_runs (
              id, mode, status, stage, intent_snapshot_json, strategy_snapshot_json,
              searched_count, skipped_count, greeted_count, error_count,
              started_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)
            """,
            (
                run_id,
                mode,
                status,
                stage,
                json.dumps(intent, ensure_ascii=False),
                json.dumps(strategy, ensure_ascii=False),
                created_at,
                created_at,
            ),
        )


def _finish_run(
    db: Database,
    *,
    run_id: str,
    status: str,
    stage: str,
    searched_count: int,
    skipped_count: int,
    error_count: int,
    error_message: str | None = None,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_delivery_runs
            SET status = ?, stage = ?, searched_count = ?, skipped_count = ?,
                greeted_count = 0, error_count = ?, updated_at = ?,
                finished_at = ?, error_message = ?
            WHERE id = ?
            """,
            (
                status,
                stage,
                searched_count,
                skipped_count,
                error_count,
                now,
                now,
                error_message,
                run_id,
            ),
        )


def _pause_run(
    db: Database,
    *,
    run_id: str,
    stage: str,
    message: str,
    searched_count: int = 0,
    skipped_count: int = 0,
) -> None:
    _finish_run(
        db,
        run_id=run_id,
        status="paused",
        stage=stage,
        searched_count=searched_count,
        skipped_count=skipped_count,
        error_count=1,
        error_message=message,
    )
    _log(
        db,
        run_id=run_id,
        level="warning",
        action_type=stage,
        message=message,
        detail={"searched_count": searched_count, "skipped_count": skipped_count},
    )


def _save_candidates(db: Database, candidates: list[dict[str, object]]) -> None:
    if not candidates:
        return
    with db.connect() as connection:
        for candidate in candidates:
            connection.execute(
                """
                INSERT INTO fj_delivery_candidates (
                  id, run_id, platform, keyword, city, job_url, job_title, company_name,
                  salary_text, location_text, experience_text, education_text,
                  hr_active_text, jd_text, match_score, decision, reason,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate["id"],
                    candidate["run_id"],
                    candidate["platform"],
                    candidate["keyword"],
                    candidate["city"],
                    candidate["job_url"],
                    candidate["job_title"],
                    candidate["company_name"],
                    candidate["salary_text"],
                    candidate["location_text"],
                    candidate["experience_text"],
                    candidate["education_text"],
                    candidate["hr_active_text"],
                    candidate["jd_text"],
                    candidate["match_score"],
                    candidate["decision"],
                    candidate["reason"],
                    candidate["created_at"],
                    candidate["updated_at"],
                ),
            )


def _build_search_keywords(intent: dict[str, object]) -> list[str]:
    values = [
        *[str(item) for item in intent.get("keywords", [])],
        *[str(item) for item in intent.get("expanded_keywords", [])],
    ]
    seen = set()
    result = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _map_cdp_jobs(
    list_data: dict[str, object],
    details: list[dict[str, object]] | None,
    *,
    keyword: str,
    city: str,
    limit: int,
) -> list[BossCdpJob]:
    """把内置 CDP 输出转换为现有候选岗位评估所需的稳定结构。"""
    details_by_id = {
        str(detail.get("job_id") or ""): detail
        for detail in details or []
        if detail.get("job_id")
    }
    jobs: list[BossCdpJob] = []
    for raw in list_data.get("jobs") or []:
        if not isinstance(raw, dict):
            continue
        detail = details_by_id.get(str(raw.get("job_id") or ""), {})
        jobs.append(
            BossCdpJob(
                keyword=keyword,
                city=city,
                job_url=str(raw.get("job_link") or ""),
                job_title=str(raw.get("title") or ""),
                company_name=str(raw.get("boss_name") or ""),
                salary_text=str(raw.get("salary") or ""),
                location_text=str(raw.get("location") or ""),
                experience_text=str(raw.get("experience") or raw.get("tags") or ""),
                education_text=str(raw.get("degree") or ""),
                hr_active_text=str(
                    detail.get("boss_active_status")
                    or raw.get("boss_active_status")
                    or ""
                ),
                jd_text=str(detail.get("jd") or ""),
            )
        )
        if len(jobs) >= limit:
            break
    return jobs


def _build_candidate_from_job(
    *,
    run_id: str,
    job: BossCdpJob,
    intent: dict[str, object],
    strategy: dict[str, object],
    min_match_score: float,
) -> dict[str, object]:
    now = utc_now()
    payload = asdict(job)
    decision, reason, score = _evaluate_job(
        job=job,
        intent=intent,
        strategy=strategy,
        min_match_score=min_match_score,
    )
    return {
        "id": new_id(),
        "run_id": run_id,
        "platform": "boss",
        "keyword": job.keyword,
        "city": job.city,
        "job_url": payload["job_url"],
        "job_title": payload["job_title"],
        "company_name": payload["company_name"],
        "salary_text": payload["salary_text"],
        "location_text": payload["location_text"],
        "experience_text": payload["experience_text"],
        "education_text": payload["education_text"],
        "hr_active_text": payload["hr_active_text"],
        "jd_text": payload["jd_text"],
        "match_score": score,
        "decision": decision,
        "reason": reason,
        "created_at": now,
        "updated_at": now,
    }


def _evaluate_job(
    *,
    job: BossCdpJob,
    intent: dict[str, object],
    strategy: dict[str, object],
    min_match_score: float,
) -> tuple[str, str, float]:
    text = f"{job.job_title}\n{job.company_name}\n{job.jd_text}".lower()
    excluded = [str(item).strip().lower() for item in intent.get("excluded_keywords", []) if str(item).strip()]
    hit_excluded = [item for item in excluded if item in text]
    if hit_excluded:
        return "skipped", f"命中排除词：{', '.join(hit_excluded)}", 0.2

    keywords = _build_search_keywords(intent)
    hit_count = sum(1 for keyword in keywords if keyword.lower() in text)
    score = min(0.95, 0.68 + 0.08 * hit_count)
    if score < min_match_score:
        return "needs_review", f"初步匹配分 {score:.2f} 低于阈值 {min_match_score:.2f}。", score

    if bool(strategy.get("auto_greeting_enabled")):
        return "would_greet", "满足基础规则；当前 dry-run 只记录为可打招呼，不会真实发送。", score
    return "needs_review", "满足基础规则；策略未开启自动打招呼，进入待确认。", score


def _build_dry_run_candidates(
    *,
    run_id: str,
    cities: list[str],
    keywords: list[str],
    min_match_score: float,
    created_at: str,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for city in cities:
        for keyword in keywords:
            candidates.append(
                {
                    "id": new_id(),
                    "run_id": run_id,
                    "platform": "boss",
                    "keyword": keyword,
                    "city": city,
                    "job_url": "",
                    "job_title": f"[dry-run] {keyword}",
                    "company_name": "待真实采集",
                    "salary_text": "",
                    "location_text": city,
                    "experience_text": "",
                    "education_text": "",
                    "hr_active_text": "",
                    "jd_text": "dry-run 占位：后续会替换为 BOSS 搜索结果和 JD 文本。",
                    "match_score": min_match_score,
                    "decision": "needs_review",
                    "reason": "dry-run 占位模式，不执行真实页面动作。",
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
    return candidates


def _log(
    db: Database,
    *,
    run_id: str,
    level: str,
    action_type: str,
    message: str,
    detail: dict[str, object],
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_action_logs (
              id, run_id, level, action_type, message, detail_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                run_id,
                level,
                action_type,
                message,
                json.dumps(detail, ensure_ascii=False),
                utc_now(),
            ),
        )


def _serialize_run(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "mode": row["mode"],
        "status": row["status"],
        "stage": row["stage"],
        "searched_count": row["searched_count"],
        "skipped_count": row["skipped_count"],
        "greeted_count": row["greeted_count"],
        "error_count": row["error_count"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
        "error_message": row["error_message"],
    }


def _serialize_log(row) -> dict[str, object]:
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except json.JSONDecodeError:
        detail = {}
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "level": row["level"],
        "action_type": row["action_type"],
        "message": row["message"],
        "detail": detail if isinstance(detail, dict) else {},
        "created_at": row["created_at"],
        "source": row["source"] if "source" in row.keys() else ("legacy_run" if row["run_id"] else "main_workflow"),
        "category": row["category"] if "category" in row.keys() else "system",
        "outcome": row["outcome"] if "outcome" in row.keys() else row["level"],
        "job_id": row["job_id"] if "job_id" in row.keys() else None,
        "job_title": row["job_title"] if "job_title" in row.keys() else None,
        "company_name": row["company_name"] if "company_name" in row.keys() else None,
    }
