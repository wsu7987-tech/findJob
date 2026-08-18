from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    BossScraperService,
)


def test_capture_jobs_calls_embedded_engine_and_resets_request_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}

    def fake_scrape_list(
        keyword,
        city,
        pages,
        filters,
        output_path,
        *,
        cdp_port,
        fmt,
        allow_dom_fallback,
    ):
        calls["list"] = {
            "keyword": keyword,
            "city": city,
            "pages": pages,
            "filters": filters,
            "output_path": output_path,
            "cdp_port": cdp_port,
            "fmt": fmt,
            "allow_dom_fallback": allow_dom_fallback,
            "request_counter": engine._request_counter,
        }
        return {"jobs": [{"job_id": "job-1"}]}

    def fake_scrape_details(list_data, max_details, output_path, *, cdp_port, fmt):
        calls["details"] = {
            "list_data": list_data,
            "max_details": max_details,
            "output_path": output_path,
            "cdp_port": cdp_port,
            "fmt": fmt,
        }
        return [{"job_id": "job-1", "jd": "Python"}]

    monkeypatch.setattr(engine, "scrape_list", fake_scrape_list)
    monkeypatch.setattr(engine, "scrape_details", fake_scrape_details)
    engine._request_counter = 499

    result = BossScraperService().capture_jobs(
        BossCaptureRequest(
            keyword=" AI Agent ",
            city=" 上海 ",
            pages=2,
            filters={"salary": "406"},
            max_details=1,
            output_dir=tmp_path,
        )
    )

    assert calls["list"] == {
        "keyword": "AI Agent",
        "city": "上海",
        "pages": 2,
        "filters": {"salary": "406"},
        "output_path": str(result.jobs_path),
        "cdp_port": 9222,
        "fmt": "json",
        "allow_dom_fallback": False,
        "request_counter": 0,
    }
    assert calls["details"] == {
        "list_data": {"jobs": [{"job_id": "job-1"}]},
        "max_details": 1,
        "output_path": str(result.details_path),
        "cdp_port": 9222,
        "fmt": "json",
    }
    assert result.details == [{"job_id": "job-1", "jd": "Python"}]


@pytest.mark.parametrize(
    ("capture_request", "message"),
    [
        (BossCaptureRequest(keyword="", city="上海"), "keyword"),
        (BossCaptureRequest(keyword="Python", city=""), "city"),
        (BossCaptureRequest(keyword="Python", city="上海", pages=0), "pages"),
    ],
)
def test_capture_jobs_validates_request(
    capture_request: BossCaptureRequest,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BossScraperService().capture_jobs(capture_request)


def test_capture_jobs_reuses_current_search_page(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_url = (
        "https://www.zhipin.com/web/geek/job?query=Go&city=101020100&page=1&jobType=1901"
    )
    service = BossScraperService()
    monkeypatch.setattr(
        service,
        "_find_interactive_target",
        lambda _port: {"targetId": "target-1", "url": current_url, "type": "page"},
    )
    captured: dict[str, object] = {}

    def fake_scrape_list(keyword, city, pages, filters, output_path, **kwargs):
        captured.update(
            keyword=keyword,
            city=city,
            pages=pages,
            filters=filters,
            output_path=output_path,
            kwargs=kwargs,
        )
        return {"keyword": keyword, "city": city, "jobs": []}

    monkeypatch.setattr(engine, "scrape_list", fake_scrape_list)

    result = service.capture_jobs(
        BossCaptureRequest(
            keyword="Python",
            city="上海",
            pages=1,
            include_details=False,
            output_dir=tmp_path,
            prefer_current_page=True,
        )
    )

    assert captured["keyword"] == "Go"
    assert captured["city"] == "101020100"
    assert captured["kwargs"] == {
        "cdp_port": 9222,
        "fmt": "json",
        "allow_dom_fallback": False,
        "target_id": "target-1",
        "start_url": current_url,
        "close_target": False,
    }
    assert result.source_url == current_url
    assert result.used_current_page is True


def test_capture_jobs_exposes_login_gate_as_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_scrape_list(*args, **kwargs):
        raise engine.LoginGateError("请先登录 BOSS")

    monkeypatch.setattr(engine, "scrape_list", fake_scrape_list)

    with pytest.raises(RuntimeError, match="请先登录 BOSS"):
        BossScraperService().capture_jobs(
            BossCaptureRequest(
                keyword="Python",
                city="上海",
                include_details=False,
                output_dir=tmp_path,
            )
        )
