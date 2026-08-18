from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock, Thread

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
    update_capture_batch,
    update_capture_job_detail,
)
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    BossScraperService,
    boss_scraper_service,
)
from backend.app.utils import new_id, utc_now


DETAIL_SECONDS_MIN = 25
DETAIL_SECONDS_MAX = 55
ESTIMATED_JOBS_PER_PAGE = 30


class BossCaptureTaskManager:
    """管理长时间 CDP 采集任务，并向桌面端提供可轮询的进度快照。"""

    def __init__(self, *, scraper: BossScraperService | None = None) -> None:
        self._scraper = scraper or boss_scraper_service
        self._tasks: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    def start_capture(
        self,
        request: BossCaptureRequest,
        *,
        output_dir: Path,
        db: Database | None = None,
    ) -> dict[str, object]:
        task_id = new_id()
        now = utc_now()
        expected_jobs = request.pages * ESTIMATED_JOBS_PER_PAGE
        list_seconds = 8 + max(0, request.pages - 1) * 25
        detail_min = expected_jobs * DETAIL_SECONDS_MIN if request.include_details else 0
        detail_max = expected_jobs * DETAIL_SECONDS_MAX if request.include_details else 0
        task: dict[str, object] = {
            "id": task_id,
            "status": "queued",
            "stage": "queued",
            "message": "采集任务已创建，正在等待执行。",
            "keyword": request.keyword,
            "city": request.city,
            "pages": request.pages,
            "auto_details": request.include_details,
            "used_current_page": False,
            "source_url": None,
            "progress_current": 0,
            "progress_total": request.pages,
            "jobs_collected": 0,
            "details_completed": 0,
            "details_failed": 0,
            "duplicate_jobs_count": 0,
            "current_job": None,
            "estimated_seconds_min": list_seconds + detail_min,
            "estimated_seconds_max": list_seconds + detail_max,
            "jobs": [],
            "jobs_path": None,
            "details_path": None,
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "error_message": None,
            "_request": request,
            "_output_dir": output_dir,
            "_list_data": None,
            "_db": db,
            "_history_recorded": False,
        }
        if db is not None:
            create_capture_batch(
                db,
                capture_id=task_id,
                keyword=request.keyword,
                city=request.city,
                pages=request.pages,
                auto_details=request.include_details,
                created_at=now,
            )
        with self._lock:
            self._tasks[task_id] = task
        Thread(target=self._run_capture, args=(task_id,), daemon=True).start()
        return self.get_task(task_id)

    def start_details(
        self,
        task_id: str,
        job_ids: list[str],
        *,
        force: bool = False,
    ) -> dict[str, object]:
        with self._lock:
            task = self._require_task(task_id)
            if task["status"] in {"queued", "running"}:
                raise AppError(
                    status_code=409,
                    error_category="TASK_RUNNING",
                    error_message="当前采集任务仍在运行，请等待完成后再选择详情。",
                )
            list_data = task.get("_list_data")
            if not isinstance(list_data, dict):
                raise AppError(
                    status_code=409,
                    error_category="CAPTURE_NOT_READY",
                    error_message="岗位列表尚未完成，不能启动详情采集。",
                )
            existing = {str(job["job_id"]): job for job in task["jobs"]}
            selected_ids = [
                job_id
                for job_id in dict.fromkeys(job_ids)
                if job_id in existing
                and (force or existing[job_id].get("detail_status") != "completed")
            ]
            if not selected_ids:
                raise AppError(
                    status_code=400,
                    error_category="VALIDATION_FAILED",
                    error_message="请至少选择一个尚未完成详情采集的岗位。",
                )
            for job_id in selected_ids:
                existing[job_id]["detail_status"] = "queued"
                existing[job_id]["detail_error"] = None
            task.update(
                status="queued",
                stage="details_queued",
                message=f"已选择 {len(selected_ids)} 个岗位，等待采集详情。",
                progress_current=0,
                progress_total=len(selected_ids),
                details_completed=sum(
                    1 for job in task["jobs"] if job.get("detail_status") == "completed"
                ),
                details_failed=sum(
                    1 for job in task["jobs"] if job.get("detail_status") == "failed"
                ),
                current_job=None,
                estimated_seconds_min=len(selected_ids) * DETAIL_SECONDS_MIN,
                estimated_seconds_max=len(selected_ids) * DETAIL_SECONDS_MAX,
                finished_at=None,
                error_message=None,
                updated_at=utc_now(),
            )
            self._sync_capture_batch(task, status="running")
        Thread(target=self._run_selected_details, args=(task_id, selected_ids), daemon=True).start()
        return self.get_task(task_id)

    def start_history_detail(
        self,
        job: dict[str, object],
        *,
        output_dir: Path,
        db: Database,
    ) -> dict[str, object]:
        """为历史岗位创建独立详情任务，不新增采集批次或岗位采集次数。"""
        task_id = new_id()
        now = utc_now()
        history_record_id = str(job.get("id") or "")
        source_job_id = str(job.get("job_id") or "").strip()
        task_job_id = (
            source_job_id
            or str(job.get("encrypt_job_id") or "").strip()
            or history_record_id
        )
        task_job = {
            **job,
            "job_id": task_job_id,
            "history_record_id": history_record_id,
            "detail_status": "queued",
            "detail_error": None,
        }
        task: dict[str, object] = {
            "id": task_id,
            "status": "queued",
            "stage": "details_queued",
            "message": "历史岗位详情任务已创建，正在等待执行。",
            "keyword": str(job.get("title") or ""),
            "city": str(job.get("location") or ""),
            "pages": 1,
            "auto_details": False,
            "used_current_page": False,
            "source_url": str(job.get("job_link") or "") or None,
            "progress_current": 0,
            "progress_total": 1,
            "jobs_collected": 1,
            "details_completed": 0,
            "details_failed": 0,
            "duplicate_jobs_count": 1,
            "current_job": None,
            "estimated_seconds_min": DETAIL_SECONDS_MIN,
            "estimated_seconds_max": DETAIL_SECONDS_MAX,
            "jobs": [task_job],
            "jobs_path": None,
            "details_path": None,
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "error_message": None,
            "_request": None,
            "_output_dir": output_dir,
            "_list_data": {
                "keyword": str(job.get("title") or ""),
                "city": str(job.get("location") or ""),
                "jobs": [task_job],
            },
            "_db": db,
            "_history_recorded": True,
        }
        with self._lock:
            self._tasks[task_id] = task
        Thread(
            target=self._run_selected_details,
            args=(task_id, [task_job_id]),
            daemon=True,
        ).start()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, object]:
        with self._lock:
            task = deepcopy(self._require_task(task_id))
        return {key: value for key, value in task.items() if not key.startswith("_")}

    def apply_recommendations(
        self,
        task_id: str,
        recommendations: dict[str, str],
        *,
        source: str,
    ) -> dict[str, object]:
        with self._lock:
            task = self._require_task(task_id)
            for job in task["jobs"]:
                job_id = str(job.get("job_id") or "")
                reason = recommendations.get(job_id)
                job["recommended"] = bool(reason)
                job["recommendation_source"] = source if reason else None
                job["recommendation_reason"] = reason
            task["updated_at"] = utc_now()
        return self.get_task(task_id)

    def _run_capture(self, task_id: str) -> None:
        with self._lock:
            task = self._require_task(task_id)
            request = task["_request"]
            output_dir = task["_output_dir"]
            task.update(
                status="running",
                stage="list_collecting",
                message="正在准备浏览器并采集岗位列表。",
                updated_at=utc_now(),
            )
            self._sync_capture_batch(task, status="running")
        try:
            result = self._scraper.capture_jobs(
                request,
                progress_callback=lambda event: self._handle_progress(task_id, event),
            )
            with self._lock:
                task = self._require_task(task_id)
                task["_list_data"] = result.list_data
                task["jobs_path"] = str(result.jobs_path)
                task["details_path"] = str(result.details_path) if result.details_path else None
                task["source_url"] = result.source_url
                task["used_current_page"] = result.used_current_page
                if not task["jobs"]:
                    self._set_list_jobs(task, result.list_data.get("jobs") or [])
                    self._persist_list_jobs(task)
                task.update(
                    status="completed",
                    stage="details_completed" if request.include_details else "list_completed",
                    message=(
                        f"采集完成：{len(task['jobs'])} 个岗位，"
                        f"{task['details_completed']} 个详情。"
                    ),
                    progress_current=(
                        int(task["details_completed"]) + int(task["details_failed"])
                        if request.include_details
                        else len(task["jobs"])
                    ),
                    progress_total=len(task["jobs"]),
                    estimated_seconds_min=0,
                    estimated_seconds_max=0,
                    current_job=None,
                    updated_at=utc_now(),
                    finished_at=utc_now(),
                )
                self._sync_capture_batch(task, status="completed", finished=True)
        except Exception as exc:  # noqa: BLE001 - background task boundary
            self._mark_failed(task_id, exc)

    def _run_selected_details(self, task_id: str, job_ids: list[str]) -> None:
        with self._lock:
            task = self._require_task(task_id)
            list_data = task["_list_data"]
            output_dir = Path(task["_output_dir"])
            output_path = output_dir / f"boss_details_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
            task.update(
                status="running",
                stage="details_collecting",
                message=f"开始采集选中的 {len(job_ids)} 个岗位详情。",
                updated_at=utc_now(),
            )
        try:
            self._scraper.capture_selected_details(
                list_data=list_data,
                job_ids=job_ids,
                output_path=output_path,
                progress_callback=lambda event: self._handle_progress(task_id, event),
            )
            with self._lock:
                task = self._require_task(task_id)
                selected_jobs = [
                    job for job in task["jobs"] if str(job.get("job_id") or "") in job_ids
                ]
                selected_completed = sum(
                    1 for job in selected_jobs if job.get("detail_status") == "completed"
                )
                selected_failed = sum(
                    1 for job in selected_jobs if job.get("detail_status") == "failed"
                )
                task.update(
                    status="completed",
                    stage="details_completed",
                    message=(
                        f"所选详情采集完成：成功 {selected_completed}，"
                        f"失败 {selected_failed}。"
                    ),
                    details_path=str(output_path),
                    progress_current=len(job_ids),
                    progress_total=len(job_ids),
                    estimated_seconds_min=0,
                    estimated_seconds_max=0,
                    current_job=None,
                    updated_at=utc_now(),
                    finished_at=utc_now(),
                )
                self._sync_capture_batch(task, status="completed", finished=True)
        except Exception as exc:  # noqa: BLE001 - background task boundary
            self._mark_failed(task_id, exc)

    def _handle_progress(self, task_id: str, event: dict[str, object]) -> None:
        with self._lock:
            task = self._require_task(task_id)
            stage = str(event.get("stage") or task["stage"])
            task["stage"] = stage
            task["message"] = str(event.get("message") or task["message"])
            task["updated_at"] = utc_now()
            if stage == "list_collecting":
                task["progress_current"] = int(event.get("current") or 0)
                task["progress_total"] = int(event.get("total") or task["pages"])
                task["jobs_collected"] = int(event.get("jobs_collected") or 0)
                return
            if stage == "list_completed":
                jobs = event.get("jobs") or []
                self._set_list_jobs(task, jobs)
                task["_list_data"] = {
                    "keyword": task["keyword"],
                    "city": task["city"],
                    "jobs": jobs,
                }
                task["jobs_path"] = event.get("jobs_path") or task.get("jobs_path")
                task["details_path"] = event.get("details_path") or task.get("details_path")
                task["jobs_collected"] = len(task["jobs"])
                self._persist_list_jobs(task)
                task["progress_current"] = 0 if task["auto_details"] else len(task["jobs"])
                task["progress_total"] = len(task["jobs"])
                if task["auto_details"]:
                    task["estimated_seconds_min"] = len(task["jobs"]) * DETAIL_SECONDS_MIN
                    task["estimated_seconds_max"] = len(task["jobs"]) * DETAIL_SECONDS_MAX
                else:
                    task["estimated_seconds_min"] = 0
                    task["estimated_seconds_max"] = 0
                return
            if stage != "details_collecting":
                return

            current = int(event.get("current") or 0)
            total = int(event.get("total") or 0)
            task["progress_current"] = current
            task["progress_total"] = total
            task["current_job"] = {
                "job_id": str(event.get("job_id") or ""),
                "title": str(event.get("title") or ""),
                "company": str(event.get("company") or ""),
            }
            job = self._find_job(task, str(event.get("job_id") or ""))
            status = str(event.get("status") or "collecting")
            if job:
                job["detail_status"] = status
                if status == "completed":
                    job["detail"] = event.get("detail")
                    job["detail_error"] = None
                    job["detail_collected_at"] = utc_now()
                elif status == "failed":
                    job["detail_error"] = str(event.get("error") or "详情采集失败")
                db = task.get("_db")
                if isinstance(db, Database) and status in {"completed", "failed"}:
                    update_capture_job_detail(
                        db,
                        job=job,
                        detail=event.get("detail"),
                        status=status,
                        error=str(event.get("error") or "") or None,
                        collected_at=job.get("detail_collected_at"),
                    )
            task["details_completed"] = sum(
                1 for item in task["jobs"] if item.get("detail_status") == "completed"
            )
            task["details_failed"] = sum(
                1 for item in task["jobs"] if item.get("detail_status") == "failed"
            )
            remaining = max(0, total - current)
            task["estimated_seconds_min"] = remaining * DETAIL_SECONDS_MIN
            task["estimated_seconds_max"] = remaining * DETAIL_SECONDS_MAX

    def _persist_list_jobs(self, task: dict[str, object]) -> None:
        db = task.get("_db")
        if not isinstance(db, Database) or task.get("_history_recorded"):
            return
        task["jobs"] = record_capture_jobs(
            db,
            capture_id=str(task["id"]),
            jobs=list(task["jobs"]),
        )
        task["duplicate_jobs_count"] = sum(
            1 for job in task["jobs"] if job.get("is_previously_collected")
        )
        task["_history_recorded"] = True

    @staticmethod
    def _sync_capture_batch(
        task: dict[str, object],
        *,
        status: str,
        finished: bool = False,
    ) -> None:
        db = task.get("_db")
        if not isinstance(db, Database):
            return
        update_capture_batch(
            db,
            capture_id=str(task["id"]),
            status=status,
            source_url=str(task.get("source_url") or "") or None,
            jobs_collected=int(task.get("jobs_collected") or 0),
            details_completed=int(task.get("details_completed") or 0),
            details_failed=int(task.get("details_failed") or 0),
            finished_at=str(task.get("finished_at") or "") or (utc_now() if finished else None),
        )

    @staticmethod
    def _set_list_jobs(task: dict[str, object], jobs: list[dict[str, object]]) -> None:
        existing = {str(job.get("job_id") or ""): job for job in task.get("jobs") or []}
        normalized = []
        for raw in jobs:
            job_id = str(raw.get("job_id") or "")
            previous = existing.get(job_id, {})
            normalized.append(
                {
                    **raw,
                    "detail_status": previous.get(
                        "detail_status",
                        "queued" if task.get("auto_details") else "not_collected",
                    ),
                    "detail": previous.get("detail"),
                    "detail_error": previous.get("detail_error"),
                    "recommended": previous.get("recommended", False),
                    "recommendation_source": previous.get("recommendation_source"),
                    "recommendation_reason": previous.get("recommendation_reason"),
                    "list_collected_at": previous.get("list_collected_at") or utc_now(),
                    "detail_collected_at": previous.get("detail_collected_at"),
                }
            )
        task["jobs"] = normalized

    @staticmethod
    def _find_job(task: dict[str, object], job_id: str) -> dict[str, object] | None:
        return next(
            (job for job in task["jobs"] if str(job.get("job_id") or "") == job_id),
            None,
        )

    def _mark_failed(self, task_id: str, exc: Exception) -> None:
        with self._lock:
            task = self._require_task(task_id)
            current_job = task.get("current_job") or {}
            job = self._find_job(task, str(current_job.get("job_id") or ""))
            if job and job.get("detail_status") == "collecting":
                job["detail_status"] = "failed"
                job["detail_error"] = str(exc)
                task["details_failed"] = sum(
                    1 for item in task["jobs"] if item.get("detail_status") == "failed"
                )
            task.update(
                status="failed",
                stage="failed",
                message=str(exc),
                error_message=str(exc),
                current_job=None,
                updated_at=utc_now(),
                finished_at=utc_now(),
            )
            self._sync_capture_batch(task, status="failed", finished=True)

    def _require_task(self, task_id: str) -> dict[str, object]:
        task = self._tasks.get(task_id)
        if task is None:
            raise AppError(
                status_code=404,
                error_category="NOT_FOUND",
                error_message="BOSS 采集任务不存在。",
            )
        return task


boss_capture_task_manager = BossCaptureTaskManager()
