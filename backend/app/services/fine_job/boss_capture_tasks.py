from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import RLock, Thread

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
    update_capture_job_filter_result,
    update_capture_batch,
    update_capture_job_detail,
    update_capture_job_delivery_evaluation,
)
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    BossScraperService,
    boss_scraper_service,
)
from backend.app.services.fine_job.filter_exclusions import (
    apply_filter_exclusions,
    assert_job_action_allowed,
)
from backend.app.services.fine_job.job_evaluation import evaluate_filter_strategy
from backend.app.services.fine_job.strategies import get_filter_strategy
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
            "continuation_available": False,
            "has_more": True,
            "last_added_jobs": 0,
            "total_pages_loaded": 0,
            "stop_requested": False,
            "_request": request,
            "_output_dir": output_dir,
            "_list_data": None,
            "_db": db,
            "_history_recorded": False,
            "_filter_strategy_id": request.filter_strategy_id,
            "_capture_target_id": None,
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

    def continue_capture(self, task_id: str, *, pages: int) -> dict[str, object]:
        """在原搜索页面继续下滑，不创建新的采集任务。"""
        with self._lock:
            task = self._require_task(task_id)
            if task["status"] in {"queued", "running"}:
                raise AppError(409, "TASK_RUNNING", "当前采集任务仍在运行。")
            if not isinstance(task.get("_list_data"), dict):
                raise AppError(409, "CAPTURE_NOT_READY", "首次岗位列表尚未采集完成。")
            if not task.get("jobs_path"):
                raise AppError(409, "CAPTURE_NOT_READY", "首次采集结果文件尚未准备完成。")
            if not task.get("_capture_target_id"):
                raise AppError(409, "CAPTURE_PAGE_NOT_REUSABLE", "首次采集没有保留搜索页面，无法继续下滑。")
            if task.get("has_more") is False:
                raise AppError(409, "CAPTURE_REACHED_END", "当前搜索结果已经没有更多岗位。")
            if pages < 1 or pages > 10:
                raise AppError(422, "VALIDATION_FAILED", "继续下滑采集页数必须在 1 到 10 之间。")
            request = task.get("_request")
            if not isinstance(request, BossCaptureRequest):
                raise AppError(409, "CAPTURE_NOT_READY", "采集任务缺少原搜索条件。")
            task["_continue_request"] = replace(
                request,
                pages=pages,
                include_details=False,
                max_details=None,
                prefer_current_page=True,
            )
            task.update(
                status="queued",
                stage="list_continue_queued",
                message=f"准备在原搜索页面继续下滑采集 {pages} 页。",
                pages=pages,
                progress_current=0,
                progress_total=pages,
                estimated_seconds_min=max(8, pages * 12),
                estimated_seconds_max=max(20, pages * 22),
                finished_at=None,
                error_message=None,
                stop_requested=False,
                last_added_jobs=0,
                updated_at=utc_now(),
            )
            self._sync_capture_batch(task, status="running")
        Thread(target=self._run_continue_capture, args=(task_id,), daemon=True).start()
        return self.get_task(task_id)

    def stop_capture(self, task_id: str) -> dict[str, object]:
        """请求停止列表采集，保留浏览器和已经采集的岗位。"""
        with self._lock:
            task = self._require_task(task_id)
            list_stage = str(task["stage"]) == "queued" or str(task["stage"]).startswith("list")
            if task["status"] not in {"queued", "running"} or not list_stage:
                raise AppError(409, "CAPTURE_NOT_RUNNING", "当前没有正在执行的列表采集。")
            task["stop_requested"] = True
            task["message"] = "正在停止采集；已获得的岗位会继续保留。"
            task["updated_at"] = utc_now()
        return self.get_task(task_id)

    def start_details(
        self,
        task_id: str,
        job_ids: list[str],
        *,
        force: bool = False,
        manual_override: bool = False,
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
            db = task.get("_db")
            if isinstance(db, Database):
                for job_id in job_ids:
                    job = existing.get(job_id)
                    if not job:
                        continue
                    strategy_id = str(job.get("filter_strategy_id") or "")
                    strategy = get_filter_strategy(db, strategy_id) if strategy_id else None
                    history_id = str(job.get("history_record_id") or "")
                    if history_id:
                        assert_job_action_allowed(
                            db,
                            history_id,
                            strategy=strategy,
                            action="detail",
                            allow_manual_override=manual_override,
                        )
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

    def apply_filter_results(
        self,
        task_id: str,
        results: list[dict[str, object]],
    ) -> dict[str, object]:
        by_id = {str(result.get("job_id") or ""): result for result in results}
        with self._lock:
            task = self._require_task(task_id)
            for job in task["jobs"]:
                result = by_id.get(str(job.get("job_id") or ""))
                if not result:
                    continue
                job["filter_status"] = result.get("status")
                job["strategy_filter_status"] = result.get(
                    "strategy_filter_status", result.get("status")
                )
                job["final_filter_status"] = result.get(
                    "final_filter_status", result.get("status")
                )
                job["filter_reasons"] = list(result.get("reasons") or [])
                job["filter_missing_fields"] = list(result.get("missing_fields") or [])
                job["filter_strategy_id"] = result.get("strategy_id")
                for field in (
                    "company_id",
                    "company_type",
                    "is_outsourcing_company",
                    "is_blacklisted",
                    "application_status",
                    "applied_at",
                    "cooldown_excluded",
                    "cooldown_reasons",
                ):
                    if field in result:
                        job[field] = result[field]
                db = task.get("_db")
                if isinstance(db, Database):
                    update_capture_job_filter_result(db, job=job, result=result)
                strategy_status = str(
                    result.get("strategy_filter_status") or result.get("status") or ""
                )
                final_status = str(result.get("status") or "")
                detail_completed = job.get("detail_status") == "completed"
                suggestion_completed = bool(job.get("delivery_evaluation"))
                if final_status in {"pass", "review"}:
                    if job.get("is_previously_collected") and detail_completed and suggestion_completed:
                        job["processing_state"] = "duplicate"
                    elif job.get("is_previously_collected") and strategy_status in {"pass", "review"}:
                        job["processing_state"] = "reprocessable"
                    else:
                        job["processing_state"] = "new"
                else:
                    job["processing_state"] = "excluded"
            task["updated_at"] = utc_now()
        return self.get_task(task_id)

    def apply_delivery_evaluations(
        self,
        task_id: str,
        evaluations: list[dict[str, object]],
    ) -> dict[str, object]:
        by_id = {
            str(evaluation.get("job_id") or ""): evaluation
            for evaluation in evaluations
        }
        with self._lock:
            task = self._require_task(task_id)
            for job in task["jobs"]:
                evaluation = by_id.get(str(job.get("job_id") or ""))
                if not evaluation:
                    continue
                job["delivery_evaluation"] = evaluation
                job["recommended"] = evaluation.get("decision") == "recommend"
                job["recommendation_source"] = evaluation.get("source")
                job["recommendation_reason"] = "；".join(
                    str(value) for value in evaluation.get("reasons") or []
                )
                db = task.get("_db")
                if isinstance(db, Database):
                    update_capture_job_delivery_evaluation(
                        db,
                        job=job,
                        evaluation=evaluation,
                    )
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
                should_stop=lambda: self._capture_stop_requested(task_id),
            )
            with self._lock:
                task = self._require_task(task_id)
                task["_list_data"] = result.list_data
                task["jobs_path"] = str(result.jobs_path)
                task["details_path"] = str(result.details_path) if result.details_path else None
                task["source_url"] = result.source_url
                task["used_current_page"] = result.used_current_page
                task["_capture_target_id"] = result.capture_target_id
                if not task["jobs"]:
                    self._set_list_jobs(task, result.list_data.get("jobs") or [])
                    self._persist_list_jobs(task)
                stopped = bool(result.list_data.get("stopped") or task.get("stop_requested"))
                has_more = bool(result.list_data.get("has_more", True))
                continuation_available = bool(result.capture_target_id and has_more)
                task.update(
                    status="completed",
                    stage=(
                        "list_stopped"
                        if stopped
                        else "details_completed" if request.include_details else "list_completed"
                    ),
                    message=(
                        f"已停止采集，保留 {len(task['jobs'])} 个岗位。"
                        if stopped
                        else (
                            f"采集完成：{len(task['jobs'])} 个岗位，"
                            f"{task['details_completed']} 个详情。"
                        )
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
                    has_more=has_more,
                    continuation_available=continuation_available,
                    last_added_jobs=int(
                        result.list_data.get("new_jobs_count") or len(task["jobs"])
                    ),
                    total_pages_loaded=int(result.list_data.get("pages_loaded") or request.pages),
                    stop_requested=False,
                    updated_at=utc_now(),
                    finished_at=utc_now(),
                )
                self._sync_capture_batch(task, status="completed", finished=True)
        except Exception as exc:  # noqa: BLE001 - 后台任务边界
            self._mark_failed(task_id, exc)

    def _run_continue_capture(self, task_id: str) -> None:
        with self._lock:
            task = self._require_task(task_id)
            request = task["_continue_request"]
            # 详情门禁会收窄内部列表；续采去重必须使用任务中保存的完整岗位集合。
            list_data = {
                **task["_list_data"],
                "jobs": [dict(job) for job in task["jobs"]],
            }
            jobs_path = Path(str(task["jobs_path"]))
            target_id = str(task["_capture_target_id"])
            task.update(
                status="running",
                stage="list_continuing",
                message=f"正在原搜索页面继续下滑采集 {request.pages} 页。",
                updated_at=utc_now(),
            )
        try:
            result = self._scraper.capture_more_jobs(
                request,
                list_data=list_data,
                jobs_path=jobs_path,
                expected_target_id=target_id,
                progress_callback=lambda event: self._handle_progress(task_id, event),
                should_stop=lambda: self._capture_stop_requested(task_id),
            )
            with self._lock:
                task = self._require_task(task_id)
                task["_list_data"] = result.list_data
                task["source_url"] = result.source_url
                task["used_current_page"] = True
                stopped = bool(result.list_data.get("stopped") or task.get("stop_requested"))
                added = int(result.list_data.get("new_jobs_count") or 0)
                loaded = int(result.list_data.get("pages_loaded") or 0)
                has_more = bool(result.list_data.get("has_more", True))
                task.update(
                    status="completed",
                    stage="list_stopped" if stopped else "list_completed",
                    message=(
                        f"已停止继续采集，本次新增 {added} 个，累计 {len(task['jobs'])} 个岗位。"
                        if stopped
                        else f"继续下滑采集完成，本次新增 {added} 个，累计 {len(task['jobs'])} 个岗位。"
                    ),
                    progress_current=loaded,
                    progress_total=int(request.pages),
                    jobs_collected=len(task["jobs"]),
                    estimated_seconds_min=0,
                    estimated_seconds_max=0,
                    current_job=None,
                    continuation_available=has_more,
                    has_more=has_more,
                    last_added_jobs=added,
                    total_pages_loaded=int(task.get("total_pages_loaded") or 0) + loaded,
                    stop_requested=False,
                    updated_at=utc_now(),
                    finished_at=utc_now(),
                )
                self._sync_capture_batch(task, status="completed", finished=True)
        except Exception as exc:  # noqa: BLE001 - 后台任务边界
            self._mark_failed(task_id, exc)

    def _run_selected_details(self, task_id: str, job_ids: list[str]) -> None:
        with self._lock:
            task = self._require_task(task_id)
            # 详情采集使用任务内保存的完整岗位集合，自动门禁只影响自动采集候选。
            list_data = {
                **(task.get("_list_data") or {}),
                "jobs": [dict(job) for job in task["jobs"]],
            }
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
        except Exception as exc:  # noqa: BLE001 - 后台任务边界
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
                self._apply_capture_gate(task, jobs)
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
                    detail = event.get("detail")
                    job["detail"] = detail
                    if isinstance(detail, dict) and detail.get("boss_active_status"):
                        # 列表接口常不返回活跃状态，详情完成后同步到表格正式字段。
                        job["boss_active_status"] = str(detail["boss_active_status"])
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
        if not isinstance(db, Database):
            return
        jobs = list(task["jobs"])
        pending_indices = [
            index for index, job in enumerate(jobs)
            if not job.get("history_record_id")
        ]
        if not pending_indices:
            return
        # 续采只落库本次新增岗位，既保持同一采集任务，也避免重复累计采集次数。
        persisted = record_capture_jobs(
            db,
            capture_id=str(task["id"]),
            search_keyword=str(task.get("keyword") or ""),
            jobs=[jobs[index] for index in pending_indices],
        )
        for index, job in zip(pending_indices, persisted, strict=True):
            jobs[index] = job
        task["jobs"] = jobs
        task["duplicate_jobs_count"] = sum(
            1 for job in task["jobs"] if job.get("is_previously_collected")
        )
        task["_history_recorded"] = True

    def _apply_capture_gate(
        self,
        task: dict[str, object],
        raw_jobs: list[dict[str, object]],
    ) -> None:
        """列表采集完成后一次性应用冷却清单，再把允许岗位交给详情采集。"""
        strategy_id = str(task.get("_filter_strategy_id") or "")
        db = task.get("_db")
        if not isinstance(db, Database):
            return
        if not strategy_id:
            blocked_ids: set[str] = set()
            for job in task["jobs"]:
                if not job.get("is_blacklisted"):
                    continue
                job["filter_status"] = "exclude"
                job["filter_reasons"] = ["公司黑名单"]
                job["cooldown_excluded"] = True
                job["cooldown_reasons"] = ["公司黑名单"]
                blocked_ids.add(str(job.get("job_id") or ""))
            raw_jobs[:] = [
                job for job in raw_jobs
                if str(job.get("job_id") or "") not in blocked_ids
            ]
            return
        strategy = get_filter_strategy(db, strategy_id)
        results = evaluate_filter_strategy(task["jobs"], strategy)
        _enriched, results = apply_filter_exclusions(db, strategy, task["jobs"], results)
        by_id = {str(result.get("job_id") or ""): result for result in results}
        for job in task["jobs"]:
            result = by_id.get(str(job.get("job_id") or ""))
            if not result:
                continue
            job["filter_status"] = result.get("status")
            job["filter_reasons"] = list(result.get("reasons") or [])
            job["filter_missing_fields"] = list(result.get("missing_fields") or [])
            job["filter_strategy_id"] = strategy_id
            for field in (
                "company_id", "company_type", "is_outsourcing_company",
                "is_blacklisted", "application_status", "applied_at",
                "cooldown_excluded", "cooldown_reasons",
            ):
                if field in result:
                    job[field] = result[field]
            update_capture_job_filter_result(db, job=job, result=result)
        allowed_ids = {
            str(result.get("job_id") or "")
            for result in results
            if result.get("status") in {"pass", "review"}
        }
        raw_jobs[:] = [
            job for job in raw_jobs if str(job.get("job_id") or "") in allowed_ids
        ]

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
                    **previous,
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
                    "filter_status": previous.get("filter_status"),
                    "strategy_filter_status": previous.get("strategy_filter_status"),
                    "final_filter_status": previous.get("final_filter_status"),
                    "filter_reasons": previous.get("filter_reasons", []),
                    "filter_missing_fields": previous.get("filter_missing_fields", []),
                    "filter_strategy_id": previous.get("filter_strategy_id"),
                    "delivery_evaluation": previous.get("delivery_evaluation"),
                    "processing_state": previous.get("processing_state"),
                    "list_collected_at": previous.get("list_collected_at") or utc_now(),
                    "detail_collected_at": previous.get("detail_collected_at"),
                }
            )
        task["jobs"] = normalized

    def _capture_stop_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.get("stop_requested"))

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
