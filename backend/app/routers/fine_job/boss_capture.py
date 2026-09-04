from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.boss_capture import (
    BossBrowserStatusResponse,
    BossCapturePayload,
    BossCaptureHistoryResponse,
    BossCaptureTaskResponse,
    BossContinueCaptureRequest,
    BossCityListResponse,
    BossDetailCaptureRequest,
    BossHistoryDetailCaptureRequest,
    BossDeliveryEvaluationRequest,
    BossDeliveryEvaluationResponse,
    BossHistoryDeliveryEvaluationResponse,
    BossDetailSuggestionRequest,
    BossDetailSuggestionResponse,
    BossFilterApplicationRequest,
    BossFilterApplicationResponse,
    BossSearchPageRequest,
    BossSearchPageResponse,
)
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_capture_history import (
    HistorySortField,
    HistorySortOrder,
    get_capture_history_job,
    list_capture_history,
    update_capture_job_delivery_evaluation,
)
from backend.app.services.fine_job.boss_detail_suggestions import (
    suggest_by_ai,
    suggest_by_strategy,
)
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    boss_scraper_service,
)
from backend.app.services.fine_job.delivery_strategies import get_delivery_strategy
from backend.app.services.fine_job.job_intents import get_job_intent
from backend.app.services.fine_job.job_evaluation import (
    evaluate_delivery_jobs,
    evaluate_filter_strategy,
)
from backend.app.services.fine_job.resumes import list_resume_facts
from backend.app.services.fine_job import boss_executor, profile_store, profile_v3
from backend.app.services.fine_job.strategies import (
    get_filter_strategy,
    get_recommendation_strategy,
)
from backend.app.services.fine_job.workflow import record_evaluation_and_route
from backend.app.services.fine_job.filter_exclusions import (
    apply_filter_exclusions,
    assert_job_action_allowed,
)


router = APIRouter(prefix="/fine-job/boss-capture", tags=["fine-job-boss-capture"])


def _candidate_evaluation_context(
    db: Database,
    legacy_resume_id: str,
    recommendation_strategy: dict[str, object],
    stale_action: str | None,
) -> tuple[
    list[dict[str, object]],
    dict[str, object] | None,
    str | None,
    dict[str, object] | None,
]:
    resume_version_id = str(recommendation_strategy.get("resume_version_id") or "")
    if resume_version_id:
        resume_version = profile_store.get_resume_version(db, resume_version_id)
        profile_id = str(resume_version["profile_id"])
        profile = profile_store.get_profile(db, profile_id)
        resolution = profile_v3.resolve_task_context(
            db, profile_id, resume_version_id, "evaluation", stale_action
        )
        if resolution["status"] == "confirmation_required":
            raise AppError(
                status_code=409,
                error_category="CONTEXT_STALE_CONFIRMATION_REQUIRED",
                error_message="岗位评估上下文已过期，请选择重新生成、继续使用当前版本或取消。",
            )
        if resolution["status"] == "cancelled":
            raise AppError(
                status_code=409,
                error_category="CONTEXT_TASK_CANCELLED",
                error_message="已取消本次岗位评估。",
            )
        context = dict(resolution["context"] or {})
        return (
            profile_store.evaluation_facts(db, profile_id, resume_version_id),
            profile,
            resume_version_id,
            context,
        )
    profile = profile_store.ensure_default_profile(db)
    facts = profile_store.evaluation_facts(db, str(profile["id"]))
    if facts:
        return facts, profile, None, None
    legacy_facts = list_resume_facts(db, legacy_resume_id) if legacy_resume_id else []
    return legacy_facts, None, None, None


@router.get("/status", response_model=BossBrowserStatusResponse)
def get_boss_browser_status() -> BossBrowserStatusResponse:
    return BossBrowserStatusResponse(**asdict(boss_scraper_service.get_browser_status()))


@router.get("/cities", response_model=BossCityListResponse)
def list_boss_cities() -> BossCityListResponse:
    return BossCityListResponse(cities=boss_scraper_service.list_cities())


@router.post("/browser/start", response_model=BossBrowserStatusResponse)
def start_boss_browser() -> BossBrowserStatusResponse:
    code = boss_scraper_service.start_browser(wait_login=False)
    if code != 0:
        raise AppError(
            status_code=502,
            error_category="FETCH_FAILED",
            error_message="FineJob 专用 Chrome 启动失败，请检查 Chrome 安装或 CDP 端口。",
        )
    boss_scraper_service.open_login_page()
    return BossBrowserStatusResponse(**asdict(boss_scraper_service.get_browser_status()))


@router.post("/browser/stop", response_model=BossBrowserStatusResponse)
def stop_boss_browser() -> BossBrowserStatusResponse:
    boss_scraper_service.stop_browser()
    return BossBrowserStatusResponse(**asdict(boss_scraper_service.get_browser_status()))


@router.post("/locate", response_model=BossSearchPageResponse)
def locate_boss_search_page(payload: BossSearchPageRequest) -> BossSearchPageResponse:
    try:
        url = boss_scraper_service.locate_search_page(
            keyword=payload.keyword,
            city=payload.city,
            filters=payload.filters,
        )
    except ValueError as exc:
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise AppError(
            status_code=409,
            error_category="FETCH_FAILED",
            error_message=str(exc),
        ) from exc
    return BossSearchPageResponse(
        url=url,
        status=BossBrowserStatusResponse(**asdict(boss_scraper_service.get_browser_status())),
    )


@router.get("/history/{history_job_id}")
def get_boss_capture_history_job(
    history_job_id: str,
    db: Database = Depends(get_database),
) -> dict[str, object]:
    return get_capture_history_job(db, history_job_id)


@router.post(
    "/capture",
    response_model=BossCaptureTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_boss_capture(
    payload: BossCapturePayload,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BossCaptureTaskResponse:
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(
            status_code=409,
            error_category="BROWSER_NOT_RUNNING",
            error_message="FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。",
        )
    task = boss_capture_task_manager.start_capture(
        BossCaptureRequest(
            keyword=payload.keyword,
            city=payload.city,
            pages=payload.pages,
            filters=payload.filters,
            include_details=payload.include_details,
            max_details=None,
            output_dir=config.output_root / "fine-job" / "boss-capture",
            prefer_current_page=payload.prefer_current_page,
            filter_strategy_id=payload.filter_strategy_id,
        ),
        output_dir=config.output_root / "fine-job" / "boss-capture",
        db=db,
    )
    return BossCaptureTaskResponse(**task)


@router.get("/history", response_model=BossCaptureHistoryResponse)
def get_boss_capture_history(
    query: str = "",
    search_keyword: str = "",
    city: str = "",
    company_scale: str = "",
    company_industry: str = "",
    company_stage: str = "",
    detail_status: str = "",
    repeat_status: str = Query(default="all", pattern="^(all|first_seen|repeated)$"),
    collected_from: str = "",
    collected_to: str = "",
    sort_by: HistorySortField = "last_collected_at",
    sort_order: HistorySortOrder = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    db: Database = Depends(get_database),
) -> BossCaptureHistoryResponse:
    return BossCaptureHistoryResponse(
        **list_capture_history(
            db,
            query=query,
            search_keyword=search_keyword,
            city=city,
            company_scale=company_scale,
            company_industry=company_industry,
            company_stage=company_stage,
            detail_status=detail_status,
            repeat_status=repeat_status,
            collected_from=collected_from,
            collected_to=collected_to,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/history/{history_job_id}/details",
    response_model=BossCaptureTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def capture_history_job_details(
    history_job_id: str,
    payload: BossHistoryDetailCaptureRequest | None = None,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BossCaptureTaskResponse:
    job = get_capture_history_job(db, history_job_id)
    filter_strategy_id = str(job.get("filter_strategy_id") or "")
    filter_strategy = get_filter_strategy(db, filter_strategy_id) if filter_strategy_id else None
    manual_override = bool(payload and payload.manual_override)
    assert_job_action_allowed(
        db,
        history_job_id,
        strategy=filter_strategy,
        action="detail",
        allow_manual_override=manual_override,
    )
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(
            status_code=409,
            error_category="BROWSER_NOT_RUNNING",
            error_message="FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。",
        )
    start_kwargs = {
        "output_dir": config.output_root / "fine-job" / "boss-capture",
        "db": db,
    }
    return BossCaptureTaskResponse(
        **boss_capture_task_manager.start_history_detail(job, **start_kwargs)
    )


@router.get("/tasks/{task_id}", response_model=BossCaptureTaskResponse)
def get_boss_capture_task(task_id: str) -> BossCaptureTaskResponse:
    return BossCaptureTaskResponse(**boss_capture_task_manager.get_task(task_id))


@router.post(
    "/tasks/{task_id}/continue",
    response_model=BossCaptureTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def continue_boss_capture(
    task_id: str,
    payload: BossContinueCaptureRequest,
) -> BossCaptureTaskResponse:
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(
            status_code=409,
            error_category="BROWSER_NOT_RUNNING",
            error_message="FineJob 专用 Chrome 未启动，原搜索页面无法继续下滑。",
        )
    return BossCaptureTaskResponse(
        **boss_capture_task_manager.continue_capture(task_id, pages=payload.pages)
    )


@router.post(
    "/tasks/{task_id}/stop",
    response_model=BossCaptureTaskResponse,
)
def stop_boss_capture(task_id: str) -> BossCaptureTaskResponse:
    return BossCaptureTaskResponse(**boss_capture_task_manager.stop_capture(task_id))


@router.post(
    "/tasks/{task_id}/details",
    response_model=BossCaptureTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def capture_selected_boss_details(
    task_id: str,
    payload: BossDetailCaptureRequest,
) -> BossCaptureTaskResponse:
    start_kwargs = {"force": payload.force}
    if payload.manual_override:
        start_kwargs["manual_override"] = True
    return BossCaptureTaskResponse(
        **boss_capture_task_manager.start_details(task_id, payload.job_ids, **start_kwargs)
    )


@router.post(
    "/tasks/{task_id}/filters",
    response_model=BossFilterApplicationResponse,
)
def apply_boss_filter_strategy(
    task_id: str,
    payload: BossFilterApplicationRequest,
    db: Database = Depends(get_database),
) -> BossFilterApplicationResponse:
    task = boss_capture_task_manager.get_task(task_id)
    strategy = get_filter_strategy(db, payload.strategy_id)
    results = evaluate_filter_strategy(task["jobs"], strategy)
    _enriched_jobs, results = apply_filter_exclusions(db, strategy, task["jobs"], results)
    updated = boss_capture_task_manager.apply_filter_results(task_id, results)
    processing_states = {
        str(job.get("job_id") or ""): job.get("processing_state")
        for job in updated.get("jobs") or []
    }
    selected_ids = [
        str(result["job_id"])
        for result in results
        if result["status"] in {"pass", "review"}
        and processing_states.get(str(result["job_id"])) != "duplicate"
    ]
    return BossFilterApplicationResponse(
        selected_job_ids=selected_ids,
        results=results,
        task=BossCaptureTaskResponse(**updated),
    )


@router.post(
    "/tasks/{task_id}/suggestions",
    response_model=BossDetailSuggestionResponse,
)
def suggest_boss_details(
    task_id: str,
    payload: BossDetailSuggestionRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> BossDetailSuggestionResponse:
    task = boss_capture_task_manager.get_task(task_id)
    jobs = [job for job in task["jobs"] if not job.get("is_blacklisted")]
    recommendation_strategy = (
        get_recommendation_strategy(db, payload.recommendation_strategy_id)
        if payload.mode == "ai" and payload.recommendation_strategy_id
        else None
    )
    filter_strategy_id = payload.filter_strategy_id or (
        recommendation_strategy.get("filter_strategy_id")
        if recommendation_strategy is not None
        else None
    )
    if filter_strategy_id:
        filter_strategy = get_filter_strategy(db, str(filter_strategy_id))
        filter_results = evaluate_filter_strategy(jobs, filter_strategy)
        jobs, filter_results = apply_filter_exclusions(
            db, filter_strategy, jobs, filter_results
        )
        updated_task = boss_capture_task_manager.apply_filter_results(task_id, filter_results)
        processing_states = {
            str(job.get("job_id") or ""): job.get("processing_state")
            for job in updated_task.get("jobs") or []
        }
        # 自动推荐任务只评估本轮筛选产生的候选岗位，手动详情入口不受该选择影响。
        candidate_ids = {
            str(item.get("job_id") or "")
            for item in filter_results
            if item.get("status") in {"pass", "review"}
            and processing_states.get(str(item.get("job_id") or "")) != "duplicate"
        }
        jobs = [
            job
            for job in jobs
            if str(job.get("job_id") or job.get("id") or "") in candidate_ids
        ]
    else:
        filter_strategy = None
    if recommendation_strategy is not None:
        resume_id = str(recommendation_strategy.get("resume_id") or "")
        facts, _candidate_profile, _resume_version_id, evaluation_context = (
            _candidate_evaluation_context(
                db,
                resume_id,
                recommendation_strategy,
                stale_action=payload.context_stale_action,
            )
        )
        evaluations = evaluate_delivery_jobs(
            jobs,
            filter_strategy=filter_strategy,
            recommendation_strategy=recommendation_strategy,
            resume_facts=facts,
            extra_requirement=payload.extra_requirement or payload.command,
            config=config,
            candidate_context=_context_content(evaluation_context),
        )
        selected_ids = [
            str(item["job_id"])
            for item in evaluations
            if item["decision"] in {"recommend", "review"}
        ]
        updated = boss_capture_task_manager.apply_delivery_evaluations(
            task_id, evaluations
        )
        return BossDetailSuggestionResponse(
            selected_job_ids=selected_ids,
            task=BossCaptureTaskResponse(**updated),
        )
    intent = get_job_intent(db)
    strategy = get_delivery_strategy(db)
    if payload.mode == "ai":
        recommendations = suggest_by_ai(
            jobs,
            intent=intent,
            strategy=strategy,
            command=payload.command,
            config=config,
        )
    else:
        recommendations = suggest_by_strategy(jobs, intent=intent)
    updated = boss_capture_task_manager.apply_recommendations(
        task_id,
        recommendations,
        source=payload.mode,
    )
    return BossDetailSuggestionResponse(
        selected_job_ids=list(recommendations),
        task=BossCaptureTaskResponse(**updated),
    )


@router.post(
    "/tasks/{task_id}/delivery-evaluations",
    response_model=BossDeliveryEvaluationResponse,
)
async def evaluate_boss_deliveries(
    task_id: str,
    payload: BossDeliveryEvaluationRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> BossDeliveryEvaluationResponse:
    task = boss_capture_task_manager.get_task(task_id)
    recommendation_strategy = get_recommendation_strategy(
        db, payload.recommendation_strategy_id
    )
    filter_strategy_id = payload.filter_strategy_id or recommendation_strategy.get(
        "filter_strategy_id"
    )
    filter_strategy = (
        get_filter_strategy(db, str(filter_strategy_id)) if filter_strategy_id else None
    )
    # 投递建议只处理前端明确选中的已完成详情岗位；其他岗位保持原状态。
    requested_job_ids = set(payload.job_ids) if payload.job_ids is not None else None
    manual_override = payload.manual_override or requested_job_ids is not None
    candidate_job_ids: set[str] | None = None
    if requested_job_ids is None and filter_strategy is not None:
        filter_results = evaluate_filter_strategy(task["jobs"], filter_strategy)
        _enriched_jobs, filter_results = apply_filter_exclusions(
            db, filter_strategy, task["jobs"], filter_results
        )
        task = boss_capture_task_manager.apply_filter_results(task_id, filter_results)
        # 未显式选岗时沿用关联筛选策略确定本轮自动评估候选。
        processing_states = {
            str(job.get("job_id") or ""): job.get("processing_state")
            for job in task.get("jobs") or []
        }
        candidate_job_ids = {
            str(item.get("job_id") or "")
            for item in filter_results
            if item.get("status") in {"pass", "review"}
            and processing_states.get(str(item.get("job_id") or "")) != "duplicate"
        }
    completed_jobs = [
        job
        for job in task["jobs"]
        if job.get("detail_status") == "completed"
        and (manual_override or not job.get("is_blacklisted"))
        and (requested_job_ids is None or str(job.get("job_id") or "") in requested_job_ids)
        and (candidate_job_ids is None or str(job.get("job_id") or "") in candidate_job_ids)
    ]
    resume_id = str(recommendation_strategy.get("resume_id") or "")
    facts, candidate_profile, resume_version_id, evaluation_context = (
        _candidate_evaluation_context(
            db,
            resume_id,
            recommendation_strategy,
            stale_action=payload.context_stale_action,
        )
    )
    evaluations = evaluate_delivery_jobs(
        completed_jobs,
        filter_strategy=filter_strategy,
        recommendation_strategy=recommendation_strategy,
        resume_facts=facts,
        extra_requirement=payload.extra_requirement,
        config=config,
        candidate_context=_context_content(evaluation_context),
    )
    updated = boss_capture_task_manager.apply_delivery_evaluations(
        task_id, evaluations
    )
    delivery_strategy = get_delivery_strategy(db)
    completed_by_id = {
        str(job.get("job_id") or ""): job for job in completed_jobs
    }
    queue_changed = False
    for evaluation in evaluations:
        job = completed_by_id.get(str(evaluation.get("job_id") or ""))
        if job:
            route_result = record_evaluation_and_route(
                db,
                job=job,
                evaluation=evaluation,
                recommendation_strategy=recommendation_strategy,
                filter_strategy=filter_strategy,
                resume_id=resume_id or None,
                delivery_strategy=delivery_strategy,
                candidate_profile=candidate_profile,
                resume_version_id=resume_version_id,
                context_revision_id=_context_revision_id(evaluation_context),
                context_dependency_versions=_context_dependencies(evaluation_context),
            )
            queue_changed = queue_changed or (
                route_result is not None and route_result.get("action") is not None
            )
    if queue_changed:
        await boss_executor.notify_queue_changed(db)
    return BossDeliveryEvaluationResponse(
        evaluations=evaluations,
        task=BossCaptureTaskResponse(**updated),
    )


@router.post(
    "/history/{history_job_id}/delivery-evaluations",
    response_model=BossHistoryDeliveryEvaluationResponse,
)
async def evaluate_history_job_delivery(
    history_job_id: str,
    payload: BossDeliveryEvaluationRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> BossHistoryDeliveryEvaluationResponse:
    job = get_capture_history_job(db, history_job_id)
    if job.get("detail_status") != "completed":
        raise AppError(
            status_code=409,
            error_category="CAPTURE_NOT_READY",
            error_message="请先完成历史岗位详情采集。",
        )
    recommendation_strategy = get_recommendation_strategy(
        db, payload.recommendation_strategy_id
    )
    filter_strategy_id = payload.filter_strategy_id or recommendation_strategy.get(
        "filter_strategy_id"
    )
    filter_strategy = (
        get_filter_strategy(db, str(filter_strategy_id)) if filter_strategy_id else None
    )
    assert_job_action_allowed(
        db,
        history_job_id,
        strategy=filter_strategy,
        action="evaluation",
        allow_manual_override=payload.manual_override,
    )
    resume_id = str(recommendation_strategy.get("resume_id") or "")
    facts, candidate_profile, resume_version_id, evaluation_context = (
        _candidate_evaluation_context(
            db,
            resume_id,
            recommendation_strategy,
            stale_action=payload.context_stale_action,
        )
    )
    evaluation = evaluate_delivery_jobs(
        [job],
        filter_strategy=filter_strategy,
        recommendation_strategy=recommendation_strategy,
        resume_facts=facts,
        extra_requirement=payload.extra_requirement,
        config=config,
        candidate_context=_context_content(evaluation_context),
    )[0]
    update_capture_job_delivery_evaluation(db, job=job, evaluation=evaluation)
    route_result = record_evaluation_and_route(
        db,
        job=job,
        evaluation=evaluation,
        recommendation_strategy=recommendation_strategy,
        filter_strategy=filter_strategy,
        resume_id=resume_id or None,
        delivery_strategy=get_delivery_strategy(db),
        candidate_profile=candidate_profile,
        resume_version_id=resume_version_id,
        context_revision_id=_context_revision_id(evaluation_context),
        context_dependency_versions=_context_dependencies(evaluation_context),
    )
    if route_result is not None and route_result.get("action") is not None:
        await boss_executor.notify_queue_changed(db)
    return BossHistoryDeliveryEvaluationResponse(
        evaluation=evaluation,
        job=get_capture_history_job(db, history_job_id),
    )


def _context_content(context: dict[str, object] | None) -> str:
    current = (context or {}).get("current_revision")
    return str(current.get("content") or "") if isinstance(current, dict) else ""


def _context_revision_id(context: dict[str, object] | None) -> str | None:
    current = (context or {}).get("current_revision")
    return str(current.get("id")) if isinstance(current, dict) and current.get("id") else None


def _context_dependencies(context: dict[str, object] | None) -> dict[str, object] | None:
    dependencies = (context or {}).get("dependency_versions")
    return dict(dependencies) if isinstance(dependencies, dict) else None
