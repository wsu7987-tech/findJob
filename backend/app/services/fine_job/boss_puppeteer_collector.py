from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.errors import AppError
from backend.app.services.fine_job.platform_sessions import get_boss_auth_state_paths


@dataclass(slots=True)
class BossPuppeteerJob:
    keyword: str
    city: str
    job_url: str
    job_title: str
    company_name: str
    salary_text: str
    location_text: str
    experience_text: str
    education_text: str
    hr_active_text: str
    jd_text: str


def collect_boss_jobs_with_puppeteer(
    *,
    config: AppConfig,
    browser_channel: str | None,
    keyword: str,
    city: str,
    max_jobs: int = 3,
) -> list[BossPuppeteerJob]:
    repo_root = Path(__file__).resolve().parents[4]
    script_path = repo_root / "apps" / "desktop" / "scripts" / "fine-job-boss-collect.mjs"
    auth_dir = get_boss_auth_state_paths(config)["dir"]
    with tempfile.TemporaryDirectory(prefix="fine-job-boss-collect-") as temp_dir:
        output_path = Path(temp_dir) / "result.json"
        command = [
            "node",
            str(script_path),
            "--auth-dir",
            str(auth_dir),
            "--out",
            str(output_path),
            "--keyword",
            keyword,
            "--city",
            city,
            "--browser-channel",
            (browser_channel or "chrome"),
            "--max-jobs",
            str(max_jobs),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo_root / "apps" / "desktop"),
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError(
                status_code=504,
                error_category="FETCH_FAILED",
                error_message="BOSS 采集超时，请稍后重试。",
            ) from exc
        except OSError as exc:
            raise AppError(
                status_code=502,
                error_category="FETCH_FAILED",
                error_message=f"无法启动 Puppeteer BOSS 采集脚本：{exc}",
            ) from exc

        payload = _read_result_payload(output_path)
        if completed.returncode != 0 or not payload.get("ok"):
            message = str(payload.get("error") or completed.stderr or completed.stdout or "BOSS 采集失败")
            raise AppError(
                status_code=423 if "登录" in message or "验证码" in message or "风险" in message else 502,
                error_category="AUTH_REQUIRED" if "登录" in message or "验证码" in message or "风险" in message else "FETCH_FAILED",
                error_message=message,
            )
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            return []
        return [BossPuppeteerJob(**_normalize_job(item, keyword=keyword, city=city)) for item in jobs if isinstance(item, dict)]


def _read_result_payload(output_path: Path) -> dict:
    if not output_path.exists():
        return {}
    try:
        value = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_job(item: dict, *, keyword: str, city: str) -> dict[str, str]:
    return {
        "keyword": str(item.get("keyword") or keyword),
        "city": str(item.get("city") or city),
        "job_url": str(item.get("job_url") or ""),
        "job_title": str(item.get("job_title") or ""),
        "company_name": str(item.get("company_name") or ""),
        "salary_text": str(item.get("salary_text") or ""),
        "location_text": str(item.get("location_text") or ""),
        "experience_text": str(item.get("experience_text") or ""),
        "education_text": str(item.get("education_text") or ""),
        "hr_active_text": str(item.get("hr_active_text") or ""),
        "jd_text": str(item.get("jd_text") or ""),
    }
