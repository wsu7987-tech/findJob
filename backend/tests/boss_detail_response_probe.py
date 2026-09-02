from __future__ import annotations

import argparse
from html import unescape
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

# 直接运行测试脚本时，把项目根目录加入导入路径；不启动项目服务。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as boss


DEFAULT_OUTPUT = Path(__file__).with_name("boss_detail_response_probe_output.json")


def _text_from_html(value: str) -> str:
    """将已定位 HTML 节点的文本还原为普通文本。"""
    without_tags = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _first_html_value(html: str, pattern: str) -> str:
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    return _text_from_html(match.group(1)) if match else ""


def _first_html_multiline_value(html: str, pattern: str) -> str:
    """保留详情正文中的换行，便于直接保存和阅读 JD。"""
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
    """按详情页 HTML 已验证的字段位置返回岗位数据。"""
    sidebar_start = html.find('<div class="sider-company">')
    sidebar_end = html.find('class="job-detail-section job-detail-company"', sidebar_start)
    sidebar = html[sidebar_start:sidebar_end] if sidebar_start >= 0 and sidebar_end > sidebar_start else ""
    return {
        "job_name": _first_html_value(html, r"job_name\s*:\s*'([^']*)'"),
        "salary": _first_html_value(html, r"job_salary\s*:\s*'([^']*)'"),
        "location": _first_html_value(html, r'<a[^>]*class="[^"]*text-city[^"]*"[^>]*>(.*?)</a>'),
        "experience": _first_html_value(html, r'<span[^>]*class="[^"]*text-experiece[^"]*"[^>]*>(.*?)</span>'),
        "degree": _first_html_value(html, r'<span[^>]*class="[^"]*text-degree[^"]*"[^>]*>(.*?)</span>'),
        "company_name": _first_html_value(html, r"company\s*:\s*'([^']*)'"),
        "company_stage": _first_html_value(sidebar, r'icon-stage[^>]*></i>\s*(.*?)</p>'),
        "company_scale": _first_html_value(sidebar, r'icon-scale[^>]*></i>\s*(.*?)</p>'),
        "company_industry": _first_html_value(sidebar, r'icon-industry[^>]*></i>\s*<a[^>]*>(.*?)</a>'),
        "work_address": _first_html_value(html, r'<div[^>]*class="[^"]*location-address[^"]*"[^>]*>(.*?)</div>'),
        "boss_active_status": _first_html_value(html, r'<span[^>]*class="[^"]*boss-active-time[^"]*"[^>]*>(.*?)</span>'),
        "welfare": " | ".join(_welfare_tags(html)),
        "job_description": _first_html_multiline_value(
            html,
            r'<div[^>]*class="detail-content-header"[^>]*>\s*<h3>\s*职位描述\s*</h3>.*?<div[^>]*class="[^"]*job-sec-text[^"]*"[^>]*>(.*?)</div>',
        ),
    }


def _capture_detail_html(ws: boss.CDPSession, sid: str, url: str) -> tuple[str, str]:
    """监听指定详情页的 Document 响应并读取未经页面提取器处理的 HTML。"""
    ws.send("Network.enable", {}, sid)
    ws.events.clear()
    ws.send("Page.navigate", {"url": url}, sid)
    request_urls: dict[str, str] = {}
    finished: set[str] = set()
    deadline = time.time() + 20
    while time.time() < deadline:
        ws.drain_events(0.4)
        for event in ws.events:
            params = event.get("params") or {}
            request_id = str(params.get("requestId") or "")
            if event.get("method") == "Network.responseReceived" and request_id:
                response = params.get("response") or {}
                response_url = str(response.get("url") or "")
                if response_url == url and response.get("mimeType", "").startswith("text/html"):
                    request_urls[request_id] = response_url
            elif event.get("method") == "Network.loadingFinished" and request_id:
                finished.add(request_id)
        for request_id, response_url in request_urls.items():
            if request_id in finished:
                response = ws.send("Network.getResponseBody", {"requestId": request_id}, sid)
                body = (response.get("result") or {}).get("body") or ""
                if body:
                    return response_url, body
    raise RuntimeError("卡点：未捕获到指定详情页的 text/html 响应")


def capture(*, url: str, output_path: Path, cdp_port: int) -> dict[str, Any]:
    """按现有 CDP 详情建页流程监听 HTML，并输出目标字段。"""
    job = {"job_link": url}
    ws = boss.CDPSession(cdp_port)
    tid: str | None = None
    sid: str | None = None
    try:
        # 与正式详情采集保持同一建页、导航和可见性处理链路。
        tid, sid = boss.create_page_session(ws)
        detail_url = boss.build_detail_url(job)
        response_url, html = _capture_detail_html(ws, sid, detail_url)
        time.sleep(random.uniform(5, 10))

        # 与正式详情采集保持一致的阅读滚动，促使页面完整渲染。
        scroll_count = random.randint(3, 7)
        for _ in range(scroll_count):
            delta = -random.randint(80, 200) if random.random() < 0.12 else random.randint(200, 600)
            ws.eval_js(f"window.scrollBy(0,{delta})", sid)
            time.sleep(random.uniform(2.0, 5.0) if random.random() < 0.35 else random.uniform(0.8, 1.8))

        if random.random() < 0.5:
            ws.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": random.randint(200, 800), "y": random.randint(200, 600)},
                sid,
            )
            time.sleep(random.uniform(0.5, 1.5))

        result = {
            "requested_url": url,
            "html_response_url": response_url,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "job": _parse_job_fields(html),
        }
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        if sid:
            try:
                ws.send("Network.disable", {}, sid)
            except Exception:
                pass
        if tid:
            ws.send("Target.closeTarget", {"targetId": tid})
        ws.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="只读捕获 BOSS 岗位详情页真实网络响应")
    parser.add_argument("--url", required=True, help="BOSS 岗位详情页完整 URL")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cdp-port", type=int, default=boss.DEFAULT_CDP_PORT)
    args = parser.parse_args()
    result = capture(url=args.url, output_path=args.output, cdp_port=args.cdp_port)
    print(json.dumps({
        "output": str(args.output),
        "html_response_url": result["html_response_url"],
        "job": result["job"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
