from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from backend.app.schemas.web_drafts import WebDraftParserName
from backend.app.utils import new_id, utc_now


@dataclass(slots=True)
class WebReparseJob:
    id: str
    draft_id: str
    parser_name: WebDraftParserName
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    processed_pages: int = 0
    total_pages: int = 0
    latest_available_page: int = 0
    cancel_requested: bool = False
    preview_result_id: str | None = None


class WebReparseJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, WebReparseJob] = {}
        self._lock = RLock()

    def create_job(self, *, draft_id: str, parser_name: WebDraftParserName) -> WebReparseJob:
        job = WebReparseJob(
            id=new_id(),
            draft_id=draft_id,
            parser_name=parser_name,
            status="queued",
            created_at=utc_now(),
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> WebReparseJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, *, draft_id: str | None = None, active_only: bool = False) -> list[WebReparseJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        if draft_id is not None:
            jobs = [job for job in jobs if job.draft_id == draft_id]
        if active_only:
            jobs = [job for job in jobs if job.status in {"queued", "running"}]
        jobs.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return jobs

    def mark_running(
        self,
        job_id: str,
        *,
        total_pages: int,
        preview_result_id: str,
    ) -> WebReparseJob:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            if job.started_at is None:
                job.started_at = utc_now()
            job.total_pages = total_pages
            job.preview_result_id = preview_result_id
            job.finished_at = None
            job.error_message = None
            job.cancel_requested = False
            return job

    def update_progress(
        self,
        job_id: str,
        *,
        processed_pages: int,
        latest_available_page: int,
    ) -> WebReparseJob:
        with self._lock:
            job = self._jobs[job_id]
            job.processed_pages = processed_pages
            job.latest_available_page = latest_available_page
            return job

    def mark_completed(
        self,
        job_id: str,
        *,
        preview_result_id: str | None = None,
    ) -> WebReparseJob:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.finished_at = utc_now()
            job.error_message = None
            if preview_result_id is not None:
                job.preview_result_id = preview_result_id
            return job

    def mark_failed(self, job_id: str, *, error_message: str) -> WebReparseJob:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.finished_at = utc_now()
            job.error_message = error_message
            return job

    def mark_cancelled(self, job_id: str) -> WebReparseJob:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.error_message = None
            job.cancel_requested = True
            return job

    def request_cancel(self, job_id: str) -> WebReparseJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = utc_now()
            return job

    def delete_jobs_for_draft(self, draft_id: str) -> None:
        with self._lock:
            self._jobs = {
                job_id: job
                for job_id, job in self._jobs.items()
                if job.draft_id != draft_id
            }
