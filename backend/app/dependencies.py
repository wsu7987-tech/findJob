from __future__ import annotations

from fastapi import Request

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.services.pdf_draft_store import PdfDraftStore
from backend.app.services.pdf_reparse_job_store import PdfReparseJobStore
from backend.app.services.web_draft_store import WebDraftStore
from backend.app.services.web_reparse_job_store import WebReparseJobStore
from backend.app.services.web_session_profiles import WebSessionProfileStore


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_pdf_draft_store(request: Request) -> PdfDraftStore:
    return request.app.state.pdf_draft_store


def get_pdf_reparse_job_store(request: Request) -> PdfReparseJobStore:
    return request.app.state.pdf_reparse_job_store


def get_web_draft_store(request: Request) -> WebDraftStore:
    return request.app.state.web_draft_store


def get_web_reparse_job_store(request: Request) -> WebReparseJobStore:
    return request.app.state.web_reparse_job_store


def get_web_session_profile_store(request: Request) -> WebSessionProfileStore:
    return request.app.state.web_session_profile_store
