from __future__ import annotations

import base64
from html import unescape
import json
import random
import re
import time
from pathlib import Path
from typing import Callable

from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine


def _text(value: object) -> str:
    return str(value or "").strip()


def _text_from_html(value: str) -> str:
    """将已定位的 HTML 节点内容还原为普通文本。"""
    without_tags = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _first_html_value(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    return _text_from_html(match.group(1)) if match else ""


def _first_html_multiline_value(html: str, pattern: str) -> str:
    """保留职位描述中的换行，供详情页和后续评估直接使用。"""
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    value = re.sub(r"<br\s*/?>", "\n", match.group(1), flags=re.IGNORECASE)
    value = re.sub(r"</(?:p|li|div)\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return "\n".join(line.strip() for line in unescape(value).splitlines() if line.strip())


def _welfare_tags(html: str) -> list[str]:
    """读取详情页 job-tags 中的福利标签，并按出现顺序去重。"""
    values: list[str] = []
    for section in re.findall(r'<div[^>]*class="[^"]*job-tags[^"]*"[^>]*>(.*?)</div>', html, flags=re.IGNORECASE | re.DOTALL):
        for item in re.findall(r'<span[^>]*>(.*?)</span>', section, flags=re.IGNORECASE | re.DOTALL):
            tag = _text_from_html(item)
            if tag and tag not in values:
                values.append(tag)
    return values


def _parse_job_fields(html: str) -> dict[str, str]:
    """按 BOSS 详情页原始 HTML 的已验证结构解析岗位字段。"""
    sidebar_start = html.find('<div class="sider-company">')
    sidebar_end = html.find('class="job-detail-section job-detail-company"', sidebar_start)
    sidebar = html[sidebar_start:sidebar_end] if sidebar_start >= 0 and sidebar_end > sidebar_start else ""
    work_address = _first_html_value(
        html,
        r'<div[^>]*class="[^"]*location-address[^"]*"[^>]*>(.*?)</div>',
    )
    city = _first_html_value(html, r'<a[^>]*class="[^"]*text-city[^"]*"[^>]*>(.*?)</a>')
    return {
        "title": _first_html_value(html, r"job_name\s*:\s*'([^']*)'"),
        "salary": _first_html_value(html, r"job_salary\s*:\s*'([^']*)'"),
        "company_name": _first_html_value(html, r"company\s*:\s*'([^']*)'"),
        "company_scale": _first_html_value(sidebar, r'icon-scale[^>]*></i>\s*(.*?)</p>'),
        "company_industry": _first_html_value(sidebar, r'icon-industry[^>]*></i>\s*<a[^>]*>(.*?)</a>'),
        "company_stage": _first_html_value(sidebar, r'icon-stage[^>]*></i>\s*(.*?)</p>'),
        # 聊天岗位的地点优先保存可用于线下面试的完整工作地址。
        "location": work_address or city,
        "experience": _first_html_value(html, r'<span[^>]*class="[^"]*text-experiece[^"]*"[^>]*>(.*?)</span>'),
        "degree": _first_html_value(html, r'<span[^>]*class="[^"]*text-degree[^"]*"[^>]*>(.*?)</span>'),
        "work_address": work_address,
        "boss_active_status": _first_html_value(html, r'<span[^>]*class="[^"]*boss-active-time[^"]*"[^>]*>(.*?)</span>'),
        "welfare": " | ".join(_welfare_tags(html)),
        "jd": _first_html_multiline_value(
            html,
            r'<div[^>]*class="detail-content-header"[^>]*>\s*<h3>\s*职位描述\s*</h3>.*?<div[^>]*class="[^"]*job-sec-text[^"]*"[^>]*>(.*?)</div>',
        ),
    }


def _capture_detail_html(cdp: engine.CDPSession, sid: str, url: str) -> tuple[str, str]:
    """监听指定详情页的 Document 响应，读取原始 HTML 响应体。"""
    cdp.send("Network.enable", {}, sid)
    cdp.events.clear()
    cdp.send("Page.navigate", {"url": url}, sid)
    request_urls: dict[str, str] = {}
    finished: set[str] = set()
    deadline = time.time() + 20
    while time.time() < deadline:
        cdp.drain_events(0.4)
        for event in cdp.events:
            params = event.get("params") or {}
            request_id = _text(params.get("requestId"))
            if event.get("method") == "Network.responseReceived" and request_id:
                response = params.get("response") or {}
                response_url = _text(response.get("url"))
                if response_url == url and _text(response.get("mimeType")).startswith("text/html"):
                    request_urls[request_id] = response_url
            elif event.get("method") == "Network.loadingFinished" and request_id:
                finished.add(request_id)
        for request_id, response_url in request_urls.items():
            if request_id not in finished:
                continue
            result = cdp.send("Network.getResponseBody", {"requestId": request_id}, sid)
            payload = result.get("result") or {}
            body = payload.get("body") or ""
            if payload.get("base64Encoded"):
                body = base64.b64decode(body).decode("utf-8", errors="replace")
            if body:
                return response_url, body
    raise RuntimeError("未捕获到指定 BOSS 详情页的 HTML 响应")


def _wait_and_scroll(cdp: engine.CDPSession, sid: str) -> None:
    """保持原详情采集的页面等待和滚动行为。"""
    time.sleep(random.uniform(5, 10))
    for _ in range(random.randint(3, 7)):
        delta = -random.randint(80, 200) if random.random() < 0.12 else random.randint(200, 600)
        cdp.eval_js(f"window.scrollBy(0,{delta})", sid)
        time.sleep(random.uniform(2.0, 5.0) if random.random() < 0.35 else random.uniform(0.8, 1.8))
    if random.random() < 0.5:
        cdp.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": random.randint(200, 800), "y": random.randint(200, 600)},
            sid,
        )
        time.sleep(random.uniform(0.5, 1.5))


def fetch_job_detail(
    job: dict[str, object],
    *,
    output_path: Path | None = None,
    cdp_port: int = engine.DEFAULT_CDP_PORT,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """独立获取聊天岗位详情并输出统一历史岗位字段。"""
    encrypt_job_id = _text(job.get("encrypt_job_id"))
    job_link = _text(job.get("job_link"))
    if not encrypt_job_id or not job_link:
        raise ValueError("聊天岗位缺少 BOSS 加密岗位标识或详情地址")

    callback_id = _text(job.get("job_id"))
    if progress_callback:
        progress_callback({
            "stage": "details_collecting",
            "status": "collecting",
            "current": 1,
            "total": 1,
            "job_id": callback_id,
            "title": _text(job.get("title")),
            "company": _text(job.get("company_name") or job.get("boss_name")),
            "message": "正在读取 BOSS 岗位详情。",
        })

    cdp = engine.CDPSession(cdp_port)
    tid: str | None = None
    sid: str | None = None
    try:
        tid, sid = engine.create_page_session(cdp)
        response_url, html = _capture_detail_html(cdp, sid, job_link)
        _wait_and_scroll(cdp, sid)
        fields = _parse_job_fields(html)
        if not fields["title"] or not fields["jd"]:
            raise RuntimeError("详情页缺少岗位名称或职位描述，未写入空详情数据")
        result = {
            "job_id": callback_id,
            "encrypt_job_id": encrypt_job_id,
            "job_link": job_link,
            "url": response_url,
            **fields,
        }
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress_callback:
            progress_callback({
                "stage": "details_collecting",
                "status": "completed",
                "current": 1,
                "total": 1,
                "job_id": callback_id,
                "title": result["title"],
                "company": result["company_name"],
                "detail": result,
                "message": f"岗位详情采集完成：{result['title']}",
            })
        return result
    except Exception as exc:
        if progress_callback:
            progress_callback({
                "stage": "details_collecting",
                "status": "failed",
                "current": 1,
                "total": 1,
                "job_id": callback_id,
                "title": _text(job.get("title")),
                "company": _text(job.get("company_name") or job.get("boss_name")),
                "error": str(exc),
                "message": f"岗位详情采集失败：{exc}",
            })
        raise
    finally:
        if sid:
            try:
                cdp.send("Network.disable", {}, sid)
            except Exception:
                pass
        if tid:
            try:
                cdp.send("Target.closeTarget", {"targetId": tid})
            except Exception:
                pass
        cdp.close()
