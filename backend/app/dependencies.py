from __future__ import annotations

from fastapi import Request

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.services.pdf_draft_store import PdfDraftStore
from backend.app.services.pdf_reparse_job_store import PdfReparseJobStore


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_pdf_draft_store(request: Request) -> PdfDraftStore:
    return request.app.state.pdf_draft_store


def get_pdf_reparse_job_store(request: Request) -> PdfReparseJobStore:
    return request.app.state.pdf_reparse_job_store


