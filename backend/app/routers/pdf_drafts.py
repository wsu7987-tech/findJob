from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import (
    get_config,
    get_database,
    get_pdf_draft_store,
    get_pdf_reparse_job_store,
)
from backend.app.errors import AppError
from backend.app.schemas.pdf_drafts import (
    PdfDraftCreateRequest,
    PdfDraftCommitRequest,
    PdfDraftDeleteResponse,
    PdfDraftEnvelope,
    PdfDraftParseResultResponse,
    PdfDraftPreviewPageEnvelope,
    PdfDraftReparseEnvelope,
    PdfDraftReparseRequest,
    PdfDraftResponse,
    PdfReparseJobResponse,
    PdfReparseJobEnvelope,
    PdfReparseJobListEnvelope,
    PoolCommitEnvelope,
)
from backend.app.services.pdf_draft_service import PdfDraftService
from backend.app.services.pdf_draft_store import PdfDraft, PdfDraftParseResult, PdfDraftStore
from backend.app.services.pdf_reparse_job_store import PdfReparseJob, PdfReparseJobStore
from backend.app.services.pdf_parse.service import build_default_pdf_parse_service


router = APIRouter(prefix="/pdf/drafts", tags=["pdf-drafts"])


@router.post("", response_model=PdfDraftReparseEnvelope, status_code=status.HTTP_202_ACCEPTED)
def create_pdf_draft(
    payload: PdfDraftCreateRequest,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfDraftReparseEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    draft, job = service.start_create_draft(file_path=payload.file_path, title=payload.title)
    return PdfDraftReparseEnvelope(draft=_serialize_draft(draft), job=_serialize_job(job))


@router.get(
    "/jobs",
    response_model=PdfReparseJobListEnvelope,
)
def list_pdf_reparse_jobs(
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfReparseJobListEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    return PdfReparseJobListEnvelope(jobs=[_serialize_job(item) for item in service.list_jobs()])


@router.get("/{draft_id}", response_model=PdfDraftEnvelope)
def get_pdf_draft(
    draft_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfDraftEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    draft = service.get_draft(draft_id)
    if draft is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="PDF draft not found.",
        )
    return PdfDraftEnvelope(draft=_serialize_draft(draft))


@router.post(
    "/{draft_id}/reparse",
    response_model=PdfDraftReparseEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
def reparse_pdf_draft(
    draft_id: str,
    payload: PdfDraftReparseRequest,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfDraftReparseEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    job = service.start_reparse_draft(draft_id, parser_name=payload.parser_name)
    draft = service.get_draft(draft_id)
    return PdfDraftReparseEnvelope(draft=_serialize_draft(draft), job=_serialize_job(job))


@router.get(
    "/{draft_id}/jobs/{job_id}",
    response_model=PdfReparseJobEnvelope,
)
def get_pdf_reparse_job(
    draft_id: str,
    job_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfReparseJobEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    return PdfReparseJobEnvelope(job=_serialize_job(service.get_job(draft_id, job_id)))


@router.post(
    "/{draft_id}/jobs/{job_id}/cancel",
    response_model=PdfReparseJobEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_pdf_reparse_job(
    draft_id: str,
    job_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfReparseJobEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    return PdfReparseJobEnvelope(job=_serialize_job(service.cancel_job(draft_id, job_id)))


@router.post(
    "/{draft_id}/parse-results/{parse_result_id}/save",
    response_model=PdfDraftEnvelope,
)
def save_pdf_draft_parse_result(
    draft_id: str,
    parse_result_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfDraftEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    draft = service.save_parse_result(draft_id, parse_result_id)
    return PdfDraftEnvelope(draft=_serialize_draft(draft))


@router.post(
    "/{draft_id}/commit",
    response_model=PoolCommitEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def commit_pdf_draft(
    draft_id: str,
    payload: PdfDraftCommitRequest | None = None,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PoolCommitEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    return PoolCommitEnvelope(
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


@router.delete("/{draft_id}", response_model=PdfDraftDeleteResponse)
def delete_pdf_draft(
    draft_id: str,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfDraftDeleteResponse:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    return PdfDraftDeleteResponse(deleted=service.delete_draft(draft_id))


@router.get(
    "/{draft_id}/parse-results/{parse_result_id}/pages/{page_number}",
    response_model=PdfDraftPreviewPageEnvelope,
)
def get_pdf_draft_preview_page(
    draft_id: str,
    parse_result_id: str,
    page_number: int,
    config: AppConfig = Depends(get_config),
    draft_store: PdfDraftStore = Depends(get_pdf_draft_store),
    job_store: PdfReparseJobStore = Depends(get_pdf_reparse_job_store),
) -> PdfDraftPreviewPageEnvelope:
    service = PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
    page = service.get_preview_page(
        draft_id=draft_id,
        parse_result_id=parse_result_id,
        page_number=page_number,
    )
    return PdfDraftPreviewPageEnvelope(
        page={
            "page_number": page.page_number,
            "content_type": page.content_type,
            "content": page.content,
        }
    )


def _serialize_draft(draft: PdfDraft | None) -> PdfDraftResponse:
    if draft is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="PDF draft not found.",
        )
    return PdfDraftResponse(
        id=draft.id,
        file_path=draft.file_path,
        title=draft.title,
        source_name=draft.source_name,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        saved_parse_result_id=draft.saved_parse_result_id,
        latest_preview_result_id=draft.latest_preview_result_id,
        parse_results=[_serialize_parse_result(item) for item in draft.parse_results],
    )


def _serialize_parse_result(result: PdfDraftParseResult) -> PdfDraftParseResultResponse:
    return PdfDraftParseResultResponse(
        id=result.id,
        parser_name=result.parser_name,
        status=result.status,
        raw_text=result.raw_text,
        markdown_text=result.markdown_text,
        preview_text=result.preview_text,
        page_count=result.page_count,
        char_count=result.char_count,
        quality_score=result.quality_score,
        is_ocr=result.is_ocr,
        warnings=list(result.warnings),
        fallback_from=result.fallback_from,
        fallback_reason=result.fallback_reason,
        created_at=result.created_at,
    )


def _serialize_job(job: PdfReparseJob) -> PdfReparseJobResponse:
    return PdfReparseJobResponse(
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
