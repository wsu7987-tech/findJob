from __future__ import annotations

from pathlib import Path

from backend.app.services.fine_job.boss_capture_tasks import BossCaptureTaskManager
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    BossCaptureResult,
)


class ImmediateThread:
    def __init__(self, *, target, args, daemon):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class FakeScraper:
    def capture_jobs(self, request, *, progress_callback):
        jobs = [{"job_id": "job-1", "title": "Python 开发", "boss_name": "测试公司"}]
        progress_callback(
            {
                "stage": "list_completed",
                "jobs": jobs,
                "message": "列表完成",
            }
        )
        if request.include_details:
            progress_callback(
                {
                    "stage": "details_collecting",
                    "status": "completed",
                    "current": 1,
                    "total": 1,
                    "job_id": "job-1",
                    "title": "Python 开发",
                    "company": "测试公司",
                    "detail": {"job_id": "job-1", "jd": "岗位描述"},
                    "message": "详情完成",
                }
            )
        return BossCaptureResult(
            list_data={"keyword": "Python", "city": "上海", "jobs": jobs},
            details=[{"job_id": "job-1", "jd": "岗位描述"}] if request.include_details else None,
            jobs_path=Path("boss_jobs.json"),
            details_path=Path("boss_details.json") if request.include_details else None,
        )

    def capture_selected_details(
        self,
        *,
        list_data,
        job_ids,
        output_path,
        progress_callback,
    ):
        selected = [job for job in list_data["jobs"] if job["job_id"] in job_ids]
        for index, job in enumerate(selected, start=1):
            progress_callback(
                {
                    "stage": "details_collecting",
                    "status": "completed",
                    "current": index,
                    "total": len(selected),
                    "job_id": job["job_id"],
                    "title": job.get("title"),
                    "company": job.get("boss_name"),
                    "detail": {"job_id": job["job_id"], "jd": "重新采集的岗位描述"},
                    "message": "详情完成",
                }
            )
        return [
            {"job_id": job["job_id"], "jd": "重新采集的岗位描述"}
            for job in selected
        ]


def test_auto_detail_task_reports_completed_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_capture_tasks.Thread",
        ImmediateThread,
    )
    manager = BossCaptureTaskManager(scraper=FakeScraper())

    task = manager.start_capture(
        BossCaptureRequest(
            keyword="Python",
            city="上海",
            pages=1,
            include_details=True,
            output_dir=tmp_path,
        ),
        output_dir=tmp_path,
    )

    assert task["status"] == "completed"
    assert task["stage"] == "details_completed"
    assert task["details_completed"] == 1
    assert task["jobs"][0]["detail_status"] == "completed"
    assert task["jobs"][0]["detail"]["jd"] == "岗位描述"


def test_task_marks_job_seen_in_an_earlier_capture(monkeypatch, tmp_path: Path, test_db) -> None:
    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_capture_tasks.Thread",
        ImmediateThread,
    )
    manager = BossCaptureTaskManager(scraper=FakeScraper())
    request = BossCaptureRequest(
        keyword="Python",
        city="上海",
        pages=1,
        include_details=False,
        output_dir=tmp_path,
    )

    first = manager.start_capture(request, output_dir=tmp_path, db=test_db)
    second = manager.start_capture(request, output_dir=tmp_path, db=test_db)

    assert first["jobs"][0]["is_previously_collected"] is False
    assert first["duplicate_jobs_count"] == 0
    assert second["jobs"][0]["is_previously_collected"] is True
    assert second["jobs"][0]["collect_count"] == 2
    assert second["duplicate_jobs_count"] == 1


def test_force_recaptures_a_completed_job_detail(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_capture_tasks.Thread",
        ImmediateThread,
    )
    manager = BossCaptureTaskManager(scraper=FakeScraper())
    initial = manager.start_capture(
        BossCaptureRequest(
            keyword="Python",
            city="上海",
            pages=1,
            include_details=True,
            output_dir=tmp_path,
        ),
        output_dir=tmp_path,
    )

    refreshed = manager.start_details(initial["id"], ["job-1"], force=True)

    assert refreshed["status"] == "completed"
    assert refreshed["jobs"][0]["detail"]["jd"] == "重新采集的岗位描述"


def test_delivery_evaluation_is_written_to_job_detail_snapshot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_capture_tasks.Thread",
        ImmediateThread,
    )
    manager = BossCaptureTaskManager(scraper=FakeScraper())
    task = manager.start_capture(
        BossCaptureRequest(
            keyword="Python",
            city="上海",
            pages=1,
            include_details=True,
            output_dir=tmp_path,
        ),
        output_dir=tmp_path,
    )

    updated = manager.apply_delivery_evaluations(
        task["id"],
        [
            {
                "job_id": "job-1",
                "decision": "review",
                "confidence": 0.45,
                "reasons": ["信息不足"],
                "risks": ["缺少关键信息"],
                "missing_fields": ["完整 JD"],
                "source": "rules",
            }
        ],
    )

    job = updated["jobs"][0]
    assert job["delivery_evaluation"]["decision"] == "review"
    assert job["recommendation_reason"] == "信息不足"
    assert job["recommended"] is False


def test_history_detail_task_updates_detail_without_incrementing_count(
    monkeypatch,
    tmp_path: Path,
    test_db,
) -> None:
    from backend.app.services.fine_job.boss_capture_history import list_capture_history

    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_capture_tasks.Thread",
        ImmediateThread,
    )
    manager = BossCaptureTaskManager(scraper=FakeScraper())
    manager.start_capture(
        BossCaptureRequest(
            keyword="Python",
            city="上海",
            pages=1,
            include_details=False,
            output_dir=tmp_path,
        ),
        output_dir=tmp_path,
        db=test_db,
    )
    history_job = list_capture_history(test_db)["items"][0]

    task = manager.start_history_detail(history_job, output_dir=tmp_path, db=test_db)
    refreshed = list_capture_history(test_db)["items"][0]

    assert task["status"] == "completed"
    assert refreshed["detail"]["jd"] == "重新采集的岗位描述"
    assert refreshed["collect_count"] == 1
