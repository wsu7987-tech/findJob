from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import (
    get_config,
    get_database,
    get_web_draft_store,
    get_web_reparse_job_store,
    get_web_session_profile_store,
)
from backend.app.errors import AppError
from backend.app.schemas.web_drafts import (
    WebDraftCommitRequest,
    WebDraftCommitEnvelope,
    WebDraftCreateRequest,
    WebDraftDeleteResponse,
    WebDraftEnvelope,
    WebDraftParseResultResponse,
    WebDraftPreviewPageEnvelope,
    WebDraftReparseEnvelope,
    WebDraftReparseRequest,
    WebDraftResponse,
    WebDraftPreviewPageResponse,
    WebReparseJobEnvelope,
    WebReparseJobListEnvelope,
    WebReparseJobResponse,
)
from backend.app.services.web_capture.service import build_default_web_capture_service
from backend.app.services.web_draft_service import WebDraftService
from backend.app.services.web_draft_store import WebDraft, WebDraftParseResult, WebDraftStore
from backend.app.services.web_reparse_job_store import WebReparseJob, WebReparseJobStore
from backend.app.services.web_session_profiles import WebSessionProfileService, WebSessionProfileStore


router = APIRouter(prefix="/web/drafts", tags=["web-drafts"])


@router.get("/jobs", response_model=WebReparseJobListEnvelope)
def list_web_reparse_jobs(
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebReparseJobListEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    return WebReparseJobListEnvelope(
        jobs=[_serialize_job(item) for item in service.list_jobs()]
    )


@router.post("", response_model=WebDraftReparseEnvelope, status_code=status.HTTP_202_ACCEPTED)
def create_web_draft(
    payload: WebDraftCreateRequest,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftReparseEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    draft, job = service.start_create_draft(
        url=payload.url,
        title=payload.title,
        session_profile_id=payload.session_profile_id,
    )
    return WebDraftReparseEnvelope(draft=_serialize_draft(draft), job=_serialize_job(job))


@router.get("/{draft_id}", response_model=WebDraftEnvelope)
def get_web_draft(
    draft_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    draft = service.get_draft(draft_id)
    if draft is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Web draft not found.",
        )
    return WebDraftEnvelope(draft=_serialize_draft(draft))


@router.post(
    "/{draft_id}/reparse",
    response_model=WebDraftReparseEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
def reparse_web_draft(
    draft_id: str,
    payload: WebDraftReparseRequest,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftReparseEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    job = service.start_reparse_draft(
        draft_id,
        parser_name=payload.parser_name,
        session_profile_id=payload.session_profile_id,
    )
    return WebDraftReparseEnvelope(
        draft=_serialize_draft(service.get_draft(draft_id)),
        job=_serialize_job(job),
    )


@router.get("/{draft_id}/jobs/{job_id}", response_model=WebReparseJobEnvelope)
def get_web_reparse_job(
    draft_id: str,
    job_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebReparseJobEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    return WebReparseJobEnvelope(job=_serialize_job(service.get_job(draft_id, job_id)))


@router.post(
    "/{draft_id}/jobs/{job_id}/cancel",
    response_model=WebReparseJobEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_web_reparse_job(
    draft_id: str,
    job_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebReparseJobEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    return WebReparseJobEnvelope(job=_serialize_job(service.cancel_job(draft_id, job_id)))


@router.post("/{draft_id}/parse-results/{parse_result_id}/save", response_model=WebDraftEnvelope)
def save_web_draft_parse_result(
    draft_id: str,
    parse_result_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    return WebDraftEnvelope(draft=_serialize_draft(service.save_parse_result(draft_id, parse_result_id)))


@router.post(
    "/{draft_id}/commit",
    response_model=WebDraftCommitEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def commit_web_draft(
    draft_id: str,
    payload: WebDraftCommitRequest | None = None,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftCommitEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    return WebDraftCommitEnvelope(
        item=service.commit_draft(
            db=db,
            config=config,
            draft_id=draft_id,
            category=payload.category if payload else None,
            tags=payload.tags if payload else [],
            cleaned_text=payload.cleaned_text if payload else None,
            cleaning_level=payload.cleaning_level if payload else None,
        )
    )


@router.delete("/{draft_id}", response_model=WebDraftDeleteResponse)
def delete_web_draft(
    draft_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftDeleteResponse:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    return WebDraftDeleteResponse(deleted=service.delete_draft(draft_id))


@router.get(
    "/{draft_id}/parse-results/{parse_result_id}/pages/{page_number}",
    response_model=WebDraftPreviewPageEnvelope,
)
def get_web_draft_preview_page(
    draft_id: str,
    parse_result_id: str,
    page_number: int,
    config: AppConfig = Depends(get_config),
    draft_store: WebDraftStore = Depends(get_web_draft_store),
    job_store: WebReparseJobStore = Depends(get_web_reparse_job_store),
    session_store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebDraftPreviewPageEnvelope:
    service = WebDraftService(
        draft_store=draft_store,
        job_store=job_store,
        capture_service=_build_capture_service(config, session_store),
    )
    page = service.get_preview_page(
        draft_id=draft_id,
        parse_result_id=parse_result_id,
        page_number=page_number,
    )
    return WebDraftPreviewPageEnvelope(
        page=WebDraftPreviewPageResponse(
            page_number=page.page_number,
            content_type=page.content_type,
            content=page.content,
        )
    )


def _serialize_job(job: WebReparseJob) -> WebReparseJobResponse:
    return WebReparseJobResponse(
        id=job.id,
        draft_id=job.draft_id,
        parser_name=job.parser_name,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
        processed_pages=job.processed_pages,
        total_pages=job.total_pages,
        latest_available_page=job.latest_available_page,
        cancel_requested=job.cancel_requested,
        preview_result_id=job.preview_result_id,
    )


def _serialize_draft(draft: WebDraft | None) -> WebDraftResponse:
    if draft is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Web draft not found.",
        )
    return WebDraftResponse(
        id=draft.id,
        url=draft.url,
        title=draft.title,
        source_name=draft.source_name,
        session_profile_id=draft.session_profile_id,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        saved_parse_result_id=draft.saved_parse_result_id,
        latest_preview_result_id=draft.latest_preview_result_id,
        parse_results=[_serialize_parse_result(item) for item in draft.parse_results],
    )


def _serialize_parse_result(result: WebDraftParseResult) -> WebDraftParseResultResponse:
    return WebDraftParseResultResponse(
        id=result.id,
        parser_name=result.parser_name,
        status=result.status,
        raw_text=result.raw_text,
        markdown_text=result.markdown_text,
        preview_text=result.preview_text,
        section_count=result.section_count,
        char_count=result.char_count,
        quality_score=result.quality_score,
        warnings=list(result.warnings),
        auth_mode=result.auth_mode,
        created_at=result.created_at,
    )


def _build_capture_service(
    config: AppConfig,
    session_store: WebSessionProfileStore,
):
    session_service = WebSessionProfileService(
        store=session_store,
        app_data_dir=config.app_data_dir,
    )
    try:
        return build_default_web_capture_service(
            session_profile_loader=session_service.load_capture_profile
        )
    except TypeError:
        return build_default_web_capture_service()
