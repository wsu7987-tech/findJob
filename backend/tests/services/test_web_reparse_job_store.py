from __future__ import annotations

from collections.abc import Iterator

import pytest


def _time_sequence(*values: str) -> Iterator[str]:
    yield from values


def test_web_reparse_job_store_marks_progress() -> None:
    from backend.app.services.web_reparse_job_store import WebReparseJobStore

    store = WebReparseJobStore()
    job = store.create_job(draft_id="draft-1", parser_name="playwright_dom")
    store.mark_running(job.id, total_pages=3, preview_result_id="parse-1")
    store.update_progress(job.id, processed_pages=2, latest_available_page=2)

    latest = store.get_job(job.id)
    assert latest is not None
    assert latest.status == "running"
    assert latest.total_pages == 3
    assert latest.processed_pages == 2


def test_web_reparse_job_store_lists_jobs_by_filter_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.services.web_reparse_job_store as web_reparse_job_store

    times = iter(
        _time_sequence(
            "2026-04-20T00:00:01Z",
            "2026-04-20T00:00:02Z",
            "2026-04-20T00:00:03Z",
            "2026-04-20T00:00:04Z",
            "2026-04-20T00:00:05Z",
            "2026-04-20T00:00:06Z",
            "2026-04-20T00:00:07Z",
        )
    )
    monkeypatch.setattr(web_reparse_job_store, "utc_now", lambda: next(times))

    store = web_reparse_job_store.WebReparseJobStore()
    job_a = store.create_job(draft_id="draft-1", parser_name="playwright_dom")
    job_b = store.create_job(draft_id="draft-2", parser_name="playwright_dom")
    job_c = store.create_job(draft_id="draft-1", parser_name="playwright_dom")
    store.mark_running(job_b.id, total_pages=2, preview_result_id="preview-2")

    assert store.list_jobs()[0].id == job_c.id
    assert [job.id for job in store.list_jobs(draft_id="draft-1")] == [job_c.id, job_a.id]
    assert [job.id for job in store.list_jobs(active_only=True)] == [job_c.id, job_b.id, job_a.id]
    assert store.get_job(job_a.id) is not None
    assert store.get_job(job_a.id).started_at is None
    assert store.get_job(job_a.id).finished_at is None
    assert store.get_job(job_a.id).error_message is None
    assert store.get_job(job_a.id).cancel_requested is False


def test_web_reparse_job_store_terminal_methods_clear_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.services.web_reparse_job_store as web_reparse_job_store

    times = iter(
        _time_sequence(
            "2026-04-20T00:00:01Z",
            "2026-04-20T00:00:02Z",
            "2026-04-20T00:00:03Z",
            "2026-04-20T00:00:04Z",
            "2026-04-20T00:00:05Z",
            "2026-04-20T00:00:06Z",
            "2026-04-20T00:00:07Z",
        )
    )
    monkeypatch.setattr(web_reparse_job_store, "utc_now", lambda: next(times))

    store = web_reparse_job_store.WebReparseJobStore()
    job = store.create_job(draft_id="draft-1", parser_name="playwright_dom")

    running = store.mark_running(job.id, total_pages=3, preview_result_id="preview-1")
    running.cancel_requested = True
    running.finished_at = "2026-04-20T00:00:10Z"
    running.error_message = "stale"

    cleared = store.mark_running(job.id, total_pages=4, preview_result_id="preview-2")
    assert cleared.status == "running"
    assert cleared.started_at == "2026-04-20T00:00:02Z"
    assert cleared.finished_at is None
    assert cleared.error_message is None
    assert cleared.cancel_requested is False

    cancelled = store.request_cancel(job.id)
    assert cancelled is not None
    assert cancelled.cancel_requested is True

    completed = store.mark_completed(job.id, preview_result_id="preview-3")
    assert completed.status == "completed"
    assert completed.finished_at == "2026-04-20T00:00:03Z"
    assert completed.preview_result_id == "preview-3"
    assert completed.cancel_requested is True

    failed = store.mark_failed(job.id, error_message="boom")
    assert failed.status == "failed"
    assert failed.finished_at == "2026-04-20T00:00:04Z"
    assert failed.error_message == "boom"

    other = store.create_job(draft_id="draft-2", parser_name="playwright_dom")
    store.delete_jobs_for_draft("draft-1")

    assert store.get_job(job.id) is None
    assert store.get_job(other.id) is not None


def test_web_reparse_job_store_request_cancel_cancels_queued_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services.web_reparse_job_store as web_reparse_job_store

    monkeypatch.setattr(web_reparse_job_store, "utc_now", lambda: "2026-04-20T00:00:00Z")

    store = web_reparse_job_store.WebReparseJobStore()
    job = store.create_job(draft_id="draft-1", parser_name="playwright_dom")

    cancelled = store.request_cancel(job.id)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at == "2026-04-20T00:00:00Z"
    assert cancelled.cancel_requested is True


def test_web_reparse_job_store_delete_jobs_for_draft_keeps_other_drafts_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services.web_reparse_job_store as web_reparse_job_store

    times = iter(
        _time_sequence(
            "2026-04-20T00:00:01Z",
            "2026-04-20T00:00:02Z",
            "2026-04-20T00:00:03Z",
            "2026-04-20T00:00:04Z",
        )
    )
    monkeypatch.setattr(web_reparse_job_store, "utc_now", lambda: next(times))

    store = web_reparse_job_store.WebReparseJobStore()
    draft_one_job_a = store.create_job(draft_id="draft-1", parser_name="playwright_dom")
    draft_one_job_b = store.create_job(draft_id="draft-1", parser_name="playwright_dom")
    draft_two_job_a = store.create_job(draft_id="draft-2", parser_name="playwright_dom")
    draft_two_job_b = store.create_job(draft_id="draft-2", parser_name="playwright_dom")

    store.delete_jobs_for_draft("draft-1")

    remaining_ids = [job.id for job in store.list_jobs()]
    assert draft_one_job_a.id not in remaining_ids
    assert draft_one_job_b.id not in remaining_ids
    assert draft_two_job_a.id in remaining_ids
    assert draft_two_job_b.id in remaining_ids
