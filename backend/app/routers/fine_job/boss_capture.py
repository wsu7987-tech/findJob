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
    BossCityListResponse,
    BossDetailCaptureRequest,
    BossDeliveryEvaluationRequest,
    BossDeliveryEvaluationResponse,
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
from backend.app.services.fine_job.strategies import (
    get_filter_strategy,
    get_recommendation_strategy,
)


router = APIRouter(prefix="/fine-job/boss-capture", tags=["fine-job-boss-capture"])


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
        ),
        output_dir=config.output_root / "fine-job" / "boss-capture",
        db=db,
    )
    return BossCaptureTaskResponse(**task)


@router.get("/history", response_model=BossCaptureHistoryResponse)
def get_boss_capture_history(
    query: str = "",
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
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BossCaptureTaskResponse:
    job = get_capture_history_job(db, history_job_id)
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(
            status_code=409,
            error_category="BROWSER_NOT_RUNNING",
            error_message="FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。",
        )
    return BossCaptureTaskResponse(
        **boss_capture_task_manager.start_history_detail(
            job,
            output_dir=config.output_root / "fine-job" / "boss-capture",
            db=db,
        )
    )


@router.get("/tasks/{task_id}", response_model=BossCaptureTaskResponse)
def get_boss_capture_task(task_id: str) -> BossCaptureTaskResponse:
    return BossCaptureTaskResponse(**boss_capture_task_manager.get_task(task_id))


@router.post(
    "/tasks/{task_id}/details",
    response_model=BossCaptureTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def capture_selected_boss_details(
    task_id: str,
    payload: BossDetailCaptureRequest,
) -> BossCaptureTaskResponse:
    return BossCaptureTaskResponse(
        **boss_capture_task_manager.start_details(
            task_id,
            payload.job_ids,
            force=payload.force,
        )
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
    selected_ids = [
        str(result["job_id"])
        for result in results
        if result["status"] in {"pass", "review"}
    ]
    updated = boss_capture_task_manager.apply_filter_results(task_id, results)
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
    jobs = task["jobs"]
    if payload.filter_strategy_id:
        filter_strategy = get_filter_strategy(db, payload.filter_strategy_id)
        filter_results = evaluate_filter_strategy(jobs, filter_strategy)
        boss_capture_task_manager.apply_filter_results(task_id, filter_results)
    else:
        filter_strategy = None
    if payload.mode == "ai" and payload.recommendation_strategy_id:
        recommendation_strategy = get_recommendation_strategy(
            db, payload.recommendation_strategy_id
        )
        resume_id = str(recommendation_strategy.get("resume_id") or "")
        facts = list_resume_facts(db, resume_id) if resume_id else []
        evaluations = evaluate_delivery_jobs(
            jobs,
            filter_strategy=filter_strategy,
            recommendation_strategy=recommendation_strategy,
            resume_facts=facts,
            extra_requirement=payload.extra_requirement or payload.command,
            config=config,
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
def evaluate_boss_deliveries(
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
    resume_id = str(recommendation_strategy.get("resume_id") or "")
    facts = list_resume_facts(db, resume_id) if resume_id else []
    evaluations = evaluate_delivery_jobs(
        task["jobs"],
        filter_strategy=filter_strategy,
        recommendation_strategy=recommendation_strategy,
        resume_facts=facts,
        extra_requirement=payload.extra_requirement,
        config=config,
    )
    updated = boss_capture_task_manager.apply_delivery_evaluations(
        task_id, evaluations
    )
    return BossDeliveryEvaluationResponse(
        evaluations=evaluations,
        task=BossCaptureTaskResponse(**updated),
    )
