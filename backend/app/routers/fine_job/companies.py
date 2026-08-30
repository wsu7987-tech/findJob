from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.companies import (
    CompanyAliasDeleteResponse,
    CompanyAliasRequest,
    CompanyBlacklistRequest,
    CompanyCreateRequest,
    CompanyEnvelope,
    CompanyListEnvelope,
    CompanyUpdateRequest,
    JobApplicationRequest,
    JobApplicationResponse,
)
from backend.app.services.fine_job import companies
from backend.app.services.fine_job.job_applications import set_job_application


router = APIRouter(prefix="/fine-job/companies", tags=["fine-job-companies"])


@router.get("", response_model=CompanyListEnvelope)
def read_companies(
    query: str = "",
    company_type: str = Query(default="", pattern="^(|unknown|direct|outsourcing)$"),
    blacklist_status: str = Query(default="all", pattern="^(all|blacklisted|normal)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    db: Database = Depends(get_database),
) -> CompanyListEnvelope:
    return CompanyListEnvelope(
        **companies.list_companies(
            db,
            query=query,
            company_type=company_type,
            blacklist_status=blacklist_status,
            page=page,
            page_size=page_size,
        )
    )


@router.post("", response_model=CompanyEnvelope, status_code=status.HTTP_201_CREATED)
def add_company(
    payload: CompanyCreateRequest,
    db: Database = Depends(get_database),
) -> CompanyEnvelope:
    return CompanyEnvelope(
        company=companies.create_company(
            db,
            name=payload.name,
            company_type=payload.company_type,
            notes=payload.notes,
        )
    )


@router.get("/{company_id}", response_model=CompanyEnvelope)
def read_company(
    company_id: str,
    db: Database = Depends(get_database),
) -> CompanyEnvelope:
    return CompanyEnvelope(company=companies.get_company(db, company_id))


@router.patch("/{company_id}", response_model=CompanyEnvelope)
def edit_company(
    company_id: str,
    payload: CompanyUpdateRequest,
    db: Database = Depends(get_database),
) -> CompanyEnvelope:
    return CompanyEnvelope(
        company=companies.update_company(
            db,
            company_id,
            canonical_name=payload.canonical_name,
            company_type=payload.company_type,
            notes=payload.notes,
        )
    )


@router.put("/{company_id}/blacklist", response_model=CompanyEnvelope)
def edit_company_blacklist(
    company_id: str,
    payload: CompanyBlacklistRequest,
    db: Database = Depends(get_database),
) -> CompanyEnvelope:
    return CompanyEnvelope(
        company=companies.set_company_blacklist(
            db,
            company_id,
            blacklisted=payload.blacklisted,
            reason=payload.reason,
        )
    )


@router.post("/{company_id}/aliases", response_model=CompanyEnvelope)
def add_alias(
    company_id: str,
    payload: CompanyAliasRequest,
    db: Database = Depends(get_database),
) -> CompanyEnvelope:
    return CompanyEnvelope(
        company=companies.add_company_alias(db, company_id, payload.alias_name)
    )


@router.delete(
    "/{company_id}/aliases/{alias_id}",
    response_model=CompanyAliasDeleteResponse,
)
def remove_alias(
    company_id: str,
    alias_id: str,
    db: Database = Depends(get_database),
) -> CompanyAliasDeleteResponse:
    companies.delete_company_alias(db, company_id, alias_id)
    return CompanyAliasDeleteResponse(deleted=True, id=alias_id)


@router.put("/jobs/{job_id}/application", response_model=JobApplicationResponse)
def edit_job_application(
    job_id: str,
    payload: JobApplicationRequest,
    db: Database = Depends(get_database),
) -> JobApplicationResponse:
    return JobApplicationResponse(
        **set_job_application(
            db,
            job_id,
            applied=payload.applied,
            source="manual",
            applied_at=payload.applied_at,
            note=payload.note,
        )
    )

