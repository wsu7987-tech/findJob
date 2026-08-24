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
    params: tuple[object, ...]
    where = ""
    if run_id:
        where = "WHERE run_id = ?"
        params = (run_id, limit)
    else:
        params = (limit,)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT id, run_id, level, action_type, message, detail_json, created_at
            FROM fj_action_logs
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_serialize_log(row) for row in rows]


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
    }
