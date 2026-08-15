from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.resumes import (
    FineJobResumeFactListEnvelope,
    FineJobResumeEnvelope,
    FineJobResumeListEnvelope,
    ResumeFactsSaveRequest,
    ResumeCreateFromFileRequest,
)
from backend.app.services.fine_job.resumes import (
    create_resume_from_file,
    extract_resume_facts,
    get_resume,
    list_resume_facts,
    list_resumes,
    save_resume_facts,
)


router = APIRouter(prefix="/fine-job/resumes", tags=["fine-job-resumes"])


@router.get("", response_model=FineJobResumeListEnvelope)
def list_fine_job_resumes(db: Database = Depends(get_database)) -> FineJobResumeListEnvelope:
    return FineJobResumeListEnvelope(resumes=list_resumes(db))


@router.get("/{resume_id}", response_model=FineJobResumeEnvelope)
def get_fine_job_resume(
    resume_id: str,
    db: Database = Depends(get_database),
) -> FineJobResumeEnvelope:
    return FineJobResumeEnvelope(resume=get_resume(db, resume_id))


@router.post(
    "/from-file",
    response_model=FineJobResumeEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def create_fine_job_resume_from_file(
    payload: ResumeCreateFromFileRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> FineJobResumeEnvelope:
    return FineJobResumeEnvelope(
        resume=create_resume_from_file(
            db=db,
            config=config,
            file_path=payload.file_path,
            name=payload.name,
            parser_name=payload.parser_name,
        )
    )


@router.get("/{resume_id}/facts", response_model=FineJobResumeFactListEnvelope)
def list_fine_job_resume_facts(
    resume_id: str,
    db: Database = Depends(get_database),
) -> FineJobResumeFactListEnvelope:
    return FineJobResumeFactListEnvelope(facts=list_resume_facts(db, resume_id))


@router.post("/{resume_id}/facts/extract", response_model=FineJobResumeFactListEnvelope)
def extract_fine_job_resume_facts(
    resume_id: str,
    db: Database = Depends(get_database),
) -> FineJobResumeFactListEnvelope:
    return FineJobResumeFactListEnvelope(facts=extract_resume_facts(db, resume_id))


@router.put("/{resume_id}/facts", response_model=FineJobResumeFactListEnvelope)
def save_fine_job_resume_facts(
    resume_id: str,
    payload: ResumeFactsSaveRequest,
    db: Database = Depends(get_database),
) -> FineJobResumeFactListEnvelope:
    return FineJobResumeFactListEnvelope(
        facts=save_resume_facts(
            db,
            resume_id,
            [fact.model_dump() for fact in payload.facts],
        )
    )
