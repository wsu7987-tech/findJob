from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

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
    """独立的 CDP 网络旁听会话，停止后将本次事件写入一个 JSON 文件。"""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
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

    @property
    def active(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        self.cdp = engine.CDPSession(engine.DEFAULT_CDP_PORT)
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

        self.started_at = _now()
        self.thread = threading.Thread(target=self._listen, name="boss-network-debug", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.active:
            self.error_message = self.error_message or "监听线程未能在规定时间内结束。"

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "event_count": len(self.completed_requests),
            "request_count": len(self.completed_requests),
            "output_path": str(self.output_path) if self.output_path else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "target_count": len(self.sessions),
            "targets": [
                {
                    "target_id": str(target.get("targetId") or ""),
                    "url": str(target.get("url") or ""),
                    "title": str(target.get("title") or ""),
                }
                for target in self.targets
            ],
            "error_message": self.error_message,
        }

    def _listen(self) -> None:
        try:
            while not self.stop_event.is_set():
                self._process_buffered_events()
                self.cdp.ws.settimeout(0.5)
                try:
                    raw = self.cdp.ws.recv()
                except engine.websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                self._process_event(event)
            self._process_buffered_events()
        except Exception as exc:
            self.error_message = str(exc)
        finally:
            self._finish()

    def _process_buffered_events(self) -> None:
        if not self.cdp.events:
            return
        buffered = self.cdp.events[:]
        self.cdp.events.clear()
        for event in buffered:
            self._process_event(event)

    def _process_event(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("sessionId") or "")
        if session_id not in self.sessions:
            return
        method = str(event.get("method") or "")
        params = event.get("params") or {}
        request_id = str(params.get("requestId") or "")
        if method == "Network.requestWillBeSent" and request_id:
            request = params.get("request") or {}
            self.request_meta[(session_id, request_id)] = {
                "request": _sanitize(request),
                "url": request.get("url"),
                "http_method": request.get("method"),
                "resource_type": params.get("type"),
            }
        elif method == "Network.responseReceived" and request_id:
            response = params.get("response") or {}
            meta = self.request_meta.setdefault((session_id, request_id), {})
            meta.update({
                "resource_type": params.get("type") or meta.get("resource_type"),
                "mime_type": response.get("mimeType"),
                "response": _sanitize(response),
                "response_url": response.get("url"),
                "status": response.get("status"),
            })
        elif method == "Network.loadingFinished" and request_id:
            self._record_completed_request(session_id, request_id)

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
            try:
                return json.loads(body), False, None
            except json.JSONDecodeError:
                return body, False, None
        except Exception as exc:
            return None, False, str(exc)

    def _record_completed_request(self, session_id: str, request_id: str) -> None:
        request_key = (session_id, request_id)
        if request_key in self.completed_request_ids:
            return
        self.completed_request_ids.add(request_key)
        meta = self.request_meta.get((session_id, request_id), {})
        response_body, response_body_base64_encoded, response_body_note = self._read_response_body(
            session_id, request_id
        )
        record: dict[str, Any] = {
            "captured_at": _now(),
            "target_id": self.sessions[session_id],
            "session_id": session_id,
            "request_id": request_id,
            "url": meta.get("url") or meta.get("response_url"),
            "http_method": meta.get("http_method"),
            "status": meta.get("status"),
            "request": meta.get("request", {}),
            "response": meta.get("response", {}),
            "response_body": response_body,
            "response_body_base64_encoded": response_body_base64_encoded,
        }
        if response_body_note:
            record["response_body_note"] = response_body_note
        self.completed_requests.append(record)

    def _finish(self) -> None:
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
        payload = {
            "version": 2,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "targets": self.snapshot()["targets"],
            "requests": self.completed_requests,
        }
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class BossNetworkDebugManager:
    def __init__(self) -> None:
        self.current: BossNetworkDebugRun | None = None

    def start(self, output_dir: Path) -> dict[str, Any]:
        if self.current is not None and self.current.active:
            raise RuntimeError("网络监听已经在运行中。")
        run = BossNetworkDebugRun(output_dir)
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


boss_network_debug_manager = BossNetworkDebugManager()
