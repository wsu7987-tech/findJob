from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from backend.app.services.fine_job.boss_network_trace import (
    NetworkTraceCapture,
    trace_evidence_status,
)
from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_headers(headers: Any) -> Any:
    if not isinstance(headers, dict):
        return headers
    sensitive_names = {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-token",
    }
    return {
        key: "[已隐藏]" if str(key).lower() in sensitive_names else value
        for key, value in headers.items()
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_headers(item) if str(key).lower() == "headers" else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _is_boss_target(target: dict[str, Any]) -> bool:
    parsed = urlparse(str(target.get("url") or ""))
    return target.get("type") == "page" and parsed.hostname in {"www.zhipin.com", "zhipin.com"}


class BossNetworkDebugRun:
    """复用现有 CDP 生命周期记录 HTTP、WebSocket 与协议 Trace。"""

    MAX_REQUEST_META = 4096
    STOP_DRAIN_MAX_CYCLES = 16
    STOP_DRAIN_STABLE_PASSES = 2

    def __init__(self, output_dir: Path, *, cdp_port: int = engine.DEFAULT_CDP_PORT) -> None:
        self.output_dir = output_dir
        self.cdp_port = cdp_port
        self.output_path: Path | None = None
        self.cdp: Any = None
        self.targets: list[dict[str, Any]] = []
        self.sessions: dict[str, str] = {}
        self.completed_requests: list[dict[str, Any]] = []
        self.request_meta: dict[tuple[str, str], dict[str, Any]] = {}
        self.completed_request_ids: set[tuple[str, str]] = set()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error_message: str | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.trace: NetworkTraceCapture | None = None
        self.event_cursor = 0
        self.cdp_diagnostics: dict[str, Any] = {}
        self.dropped_request_meta = 0
        self.evidence_gap_reasons: list[str] = []

    @property
    def active(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        self.cdp = engine.CDPSession(self.cdp_port)
        self.event_cursor = self.cdp.create_event_cursor()
        target_response = self.cdp.send("Target.getTargets")
        all_targets = target_response.get("result", {}).get("targetInfos", [])
        self.targets = [target for target in all_targets if _is_boss_target(target)]
        if not self.targets:
            self.cdp.close()
            self.cdp = None
            raise RuntimeError("没有找到 BOSS 页面，请先在专用 Chrome 中打开 BOSS 页面。")

        for target in self.targets:
            target_id = str(target.get("targetId") or "")
            try:
                session_id = engine.attach_page_session(self.cdp, target_id)
                self.cdp.send("Network.enable", {}, session_id)
                self.sessions[session_id] = target_id
            except Exception:
                continue

        if not self.sessions:
            self.cdp.close()
            self.cdp = None
            raise RuntimeError("无法连接到 BOSS 页面，请重新打开专用 Chrome 后再试。")

        self.trace = NetworkTraceCapture(self.sessions)
        self.started_at = _now()
        self.thread = threading.Thread(target=self._listen, name="boss-network-debug", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.active:
            self.error_message = self.error_message or "监听线程未能在规定时间内结束。"
            self._add_evidence_gap("tail_drain_unconfirmed")

    def snapshot(self) -> dict[str, Any]:
        records = self.trace.records if self.trace is not None else []
        request_count = sum(1 for record in records if record.get("transport") == "http")
        frame_count = sum(
            1
            for record in records
            if record.get("transport") == "websocket" and record.get("event") == "frame"
        )
        diagnostics = self._current_cdp_diagnostics()
        trace_evidence = trace_evidence_status(self.trace.export(
            cdp_diagnostics=diagnostics,
            gap_reasons=self.evidence_gap_reasons,
        )) if self.trace is not None else {
            "evidenceComplete": False,
            "gapReasons": ["trace_unavailable"],
        }
        for reason in self.evidence_gap_reasons:
            if reason not in trace_evidence["gapReasons"]:
                trace_evidence["gapReasons"].append(reason)
        trace_evidence["evidenceComplete"] = not trace_evidence["gapReasons"]
        return {
            "active": self.active,
            "trace_id": self.trace.trace_id if self.trace is not None else None,
            "event_count": len(records),
            "request_count": request_count,
            "frame_count": frame_count,
            "marker_count": len(self.trace.markers) if self.trace is not None else 0,
            "dropped_event_count": int(diagnostics.get("dropped_events") or 0)
            + (self.trace.dropped_records if self.trace is not None else 0),
            "output_path": str(self.output_path) if self.output_path else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "target_count": len(self.sessions),
            "targets": [
                {
                    "target_id": str(target.get("targetId") or ""),
                    "url": self._safe_target_url(target.get("url")),
                    "title": self._safe_target_title(target.get("title")),
                }
                for target in self.targets
            ],
            "error_message": self.error_message,
            "evidence_complete": trace_evidence["evidenceComplete"],
            "gap_reasons": trace_evidence["gapReasons"],
        }

    def _current_cdp_diagnostics(self) -> dict[str, Any]:
        """汇总运行中与停止后的 CDP 和请求元数据诊断。"""
        diagnostics = (
            self.cdp.event_buffer_diagnostics()
            if self.cdp is not None
            else self.cdp_diagnostics
        )
        return {
            **diagnostics,
            "requestMetaCapacity": self.MAX_REQUEST_META,
            "requestMetaBuffered": len(self.request_meta),
            "droppedRequestMeta": self.dropped_request_meta,
        }

    @staticmethod
    def _safe_target_url(url: Any) -> str:
        return str(url or "")

    @staticmethod
    def _safe_target_title(title: Any) -> str:
        return str(title or "")

    def mark(self, name: str) -> dict[str, Any]:
        if self.trace is None or not self.active:
            raise RuntimeError("网络 Trace 当前未运行。")
        return self.trace.mark(name)

    def _listen(self) -> None:
        try:
            while not self.stop_event.is_set():
                # 同一连接仅由 CDPSession 的读取入口接收并分流消息。
                self.cdp.drain_events(0.5)
                self._process_buffered_events()
        except Exception as exc:
            self.error_message = str(exc)
        finally:
            self._finish()

    def _process_buffered_events(self) -> bool:
        previous_cursor = self.event_cursor
        buffered, self.event_cursor = self.cdp.events_since(
            self.event_cursor,
            methods={
                "Network.requestWillBeSent",
                "Network.responseReceived",
                "Network.loadingFinished",
                "Network.webSocketCreated",
                "Network.webSocketWillSendHandshakeRequest",
                "Network.webSocketHandshakeResponseReceived",
                "Network.webSocketFrameSent",
                "Network.webSocketFrameReceived",
                "Network.webSocketFrameError",
                "Network.webSocketClosed",
            },
        )
        for event in buffered:
            self._process_event(event)
        return self.event_cursor != previous_cursor

    def _add_evidence_gap(self, reason: str) -> None:
        if reason not in self.evidence_gap_reasons:
            self.evidence_gap_reasons.append(reason)

    def _drain_stop_tail(self) -> bool:
        """在关闭 Network 前处理命令读取期间到达的所有尾部事件。"""
        if self.cdp is None or not all(
            hasattr(self.cdp, name) for name in ("drain_events", "events_since", "event_buffer_diagnostics")
        ):
            self._add_evidence_gap("tail_drain_unconfirmed")
            return False
        stable_passes = 0
        for _ in range(self.STOP_DRAIN_MAX_CYCLES):
            sequence_before = int(self.cdp.event_buffer_diagnostics().get("latest_sequence") or 0)
            self.cdp.drain_events(0.05)
            self._process_buffered_events()
            # getResponseBody 的 send() 可能在上次消费中继续缓冲 CDP event。
            self.cdp.drain_events(0.05)
            self._process_buffered_events()
            latest = int(self.cdp.event_buffer_diagnostics().get("latest_sequence") or 0)
            no_new_sequence = latest == sequence_before
            if no_new_sequence and self.event_cursor >= latest:
                stable_passes += 1
                if stable_passes >= self.STOP_DRAIN_STABLE_PASSES:
                    return True
            else:
                stable_passes = 0
        self._add_evidence_gap("tail_drain_unconfirmed")
        return False

    def _process_event(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("sessionId") or "")
        if session_id not in self.sessions:
            return
        method = str(event.get("method") or "")
        params = event.get("params") or {}
        request_id = str(params.get("requestId") or "")
        if method == "Network.requestWillBeSent" and request_id:
            request = params.get("request") or {}
            key = (session_id, request_id)
            if key not in self.request_meta and len(self.request_meta) >= self.MAX_REQUEST_META:
                self.request_meta.pop(next(iter(self.request_meta)))
                self.dropped_request_meta += 1
            self.request_meta[(session_id, request_id)] = {
                "resource_type": params.get("type"),
            }
        elif method == "Network.responseReceived" and request_id:
            response = params.get("response") or {}
            key = (session_id, request_id)
            if key not in self.request_meta and len(self.request_meta) >= self.MAX_REQUEST_META:
                self.request_meta.pop(next(iter(self.request_meta)))
                self.dropped_request_meta += 1
            meta = self.request_meta.setdefault(key, {})
            meta.update({
                "resource_type": params.get("type") or meta.get("resource_type"),
                "mime_type": response.get("mimeType"),
            })
        response_body = None
        response_body_base64_encoded = False
        if method == "Network.loadingFinished" and request_id:
            response_body, response_body_base64_encoded, _note = self._read_response_body(
                session_id,
                request_id,
            )
        if self.trace is not None:
            self.trace.process_event(
                event,
                response_body=response_body,
                response_body_base64_encoded=response_body_base64_encoded,
            )
        if method == "Network.loadingFinished" and request_id:
            self.request_meta.pop((session_id, request_id), None)

    def _read_response_body(self, session_id: str, request_id: str) -> tuple[Any, bool, str | None]:
        meta = self.request_meta.get((session_id, request_id), {})
        resource_type = str(meta.get("resource_type") or "").lower()
        mime_type = str(meta.get("mime_type") or "").lower()
        readable = resource_type in {"xhr", "fetch", "eventsource", "websocket"}
        readable = readable or mime_type.startswith(("application/json", "application/graphql", "text/"))
        if not readable:
            return None, False, "非文本资源"
        try:
            result = self.cdp.send(
                "Network.getResponseBody",
                {"requestId": request_id},
                session_id,
                timeout=5,
            )
            body_result = result.get("result", {})
            body = body_result.get("body")
            if body_result.get("base64Encoded"):
                return body, True, None
            if not isinstance(body, str):
                return None, False, "响应正文格式不可读取"
            return body, False, None
        except Exception as exc:
            return None, False, str(exc)

    def _finish(self) -> None:
        self._drain_stop_tail()
        cdp_diagnostics = self._current_cdp_diagnostics()
        self.cdp_diagnostics = cdp_diagnostics
        if self.cdp is not None:
            for session_id in self.sessions:
                try:
                    self.cdp.send("Network.disable", {}, session_id, timeout=3)
                except Exception:
                    pass
            try:
                self.cdp.close()
            except Exception:
                pass
            self.cdp = None
        self.finished_at = _now()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"网络监听-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        self.output_path = self.output_dir / filename
        payload = self.trace.export(
            cdp_diagnostics=cdp_diagnostics,
            gap_reasons=self.evidence_gap_reasons,
        ) if self.trace is not None else {}
        payload.update({
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "targets": self.snapshot()["targets"],
        })
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class BossNetworkDebugManager:
    def __init__(self) -> None:
        self.current: BossNetworkDebugRun | None = None

    def start(
        self,
        output_dir: Path,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> dict[str, Any]:
        if self.current is not None and self.current.active:
            raise RuntimeError("网络监听已经在运行中。")
        run = BossNetworkDebugRun(output_dir, cdp_port=cdp_port)
        run.start()
        self.current = run
        return run.snapshot()

    def stop(self) -> dict[str, Any]:
        if self.current is None:
            return {"active": False, "event_count": 0, "request_count": 0, "output_path": None, "target_count": 0, "targets": []}
        self.current.stop()
        return self.current.snapshot()

    def status(self) -> dict[str, Any]:
        if self.current is None:
            return {"active": False, "event_count": 0, "request_count": 0, "output_path": None, "target_count": 0, "targets": []}
        return self.current.snapshot()

    def mark(self, name: str) -> dict[str, Any]:
        if self.current is None:
            raise RuntimeError("网络 Trace 当前未运行。")
        marker = self.current.mark(name)
        return {**self.current.snapshot(), "marker": marker}


boss_network_debug_manager = BossNetworkDebugManager()
