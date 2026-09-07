from __future__ import annotations

from pathlib import Path

from backend.app.services.fine_job import boss_network_debug
from backend.app.services.fine_job.boss_network_trace import NetworkTraceCapture


def _status(**extra):
    return {
        "active": False,
        "event_count": 0,
        "request_count": 0,
        "output_path": None,
        "target_count": 0,
        "targets": [],
        **extra,
    }


def test_network_trace_api_uses_boss_scraper_service(
    configured_client,
    configured_app_paths: dict[str, Path],
    monkeypatch,
) -> None:
    from backend.app.routers.fine_job import boss_network_debug as router_module

    calls: dict[str, object] = {}
    monkeypatch.setattr(
        router_module.boss_scraper_service,
        "get_network_trace_status",
        lambda: _status(
            trace_id="trace-status",
            evidence_complete=False,
            gap_reasons=["droppedRequestMeta"],
        ),
    )

    def start_network_trace(*, output_dir, cdp_port=9222):
        calls["output_dir"] = output_dir
        calls["cdp_port"] = cdp_port
        return _status(active=True, trace_id="trace-start")

    monkeypatch.setattr(router_module.boss_scraper_service, "start_network_trace", start_network_trace)
    monkeypatch.setattr(
        router_module.boss_scraper_service,
        "mark_network_trace",
        lambda marker: _status(active=True, trace_id="trace-start", marker={"name": marker}),
    )
    monkeypatch.setattr(
        router_module.boss_scraper_service,
        "stop_network_trace",
        lambda: _status(trace_id="trace-start", output_path="trace.json"),
    )

    status = configured_client.get("/api/fine-job/boss-network-debug/status")
    started = configured_client.post("/api/fine-job/boss-network-debug/start")
    marked = configured_client.post(
        "/api/fine-job/boss-network-debug/mark",
        json={"marker": "before_send"},
    )
    stopped = configured_client.post("/api/fine-job/boss-network-debug/stop")

    assert status.json()["trace_id"] == "trace-status"
    assert status.json()["evidence_complete"] is False
    assert status.json()["gap_reasons"] == ["droppedRequestMeta"]
    assert started.json()["active"] is True
    assert calls["output_dir"] == configured_app_paths["output_root"] / "fine-job" / "cdp-network-debug"
    assert marked.json()["marker"] == {"name": "before_send"}
    assert stopped.json()["output_path"] == "trace.json"


class _RunningCDP:
    def event_buffer_diagnostics(self):
        return {
            "capacity": 4096,
            "buffered": 0,
            "latest_sequence": 2,
            "dropped_events": 0,
            "cursor_overflow_events": 0,
        }


def test_running_request_meta_eviction_reaches_service_and_api_schema(
    configured_client,
    configured_app_paths: dict[str, Path],
    monkeypatch,
) -> None:
    from backend.app.services.fine_job.boss_network_debug import boss_network_debug_manager
    from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service

    run = boss_network_debug.BossNetworkDebugRun(configured_app_paths["output_root"])
    run.MAX_REQUEST_META = 1
    run.cdp = _RunningCDP()
    run.sessions = {"session-1": "target-1"}
    run.trace = NetworkTraceCapture(run.sessions)
    for request_id in ("one", "two"):
        run._process_event({
            "sessionId": "session-1",
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": request_id,
                "request": {"url": "https://www.zhipin.com/wapi/test", "method": "GET"},
            },
        })
    monkeypatch.setattr(boss_network_debug_manager, "current", run)

    service_status = boss_scraper_service.get_network_trace_status()
    response = configured_client.get("/api/fine-job/boss-network-debug/status")

    assert run.dropped_request_meta == 1
    assert service_status["evidence_complete"] is False
    assert "droppedRequestMeta" in service_status["gap_reasons"]
    assert response.status_code == 200
    assert response.json()["evidence_complete"] is False
    assert "droppedRequestMeta" in response.json()["gap_reasons"]
