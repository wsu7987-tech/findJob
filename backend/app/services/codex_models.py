from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.errors import AppError
from backend.app.services.reasoning.codex_exec import resolve_codex_executable


MODEL_LIST_TIMEOUT_SECONDS = 15


def list_codex_models(cli_path: str) -> dict[str, object]:
    """调用 Codex CLI 的模型目录命令并转换成桌面端使用的结构。"""
    executable = resolve_codex_executable(cli_path)
    command = _build_command(executable)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=MODEL_LIST_TIMEOUT_SECONDS,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            status_code=504,
            error_category="CODEX_MODEL_LIST_TIMEOUT",
            error_message="Codex 模型目录获取超时。",
        ) from exc
    except OSError as exc:
        raise AppError(
            status_code=502,
            error_category="CODEX_MODEL_LIST_PROCESS_FAILED",
            error_message=f"无法启动 Codex 模型目录命令：{exc}",
        ) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise AppError(
            status_code=502,
            error_category="CODEX_MODEL_LIST_PROCESS_FAILED",
            error_message=f"Codex 模型目录命令执行失败：{detail or '未知错误'}",
        )

    payload = _parse_json_output(completed.stdout)
    raw_models = _extract_models(payload)
    models = [_normalize_model(item) for item in raw_models]
    models = [model for model in models if model is not None]
    models.sort(key=lambda model: str(model["id"]).lower())
    return {
        "capability": "codex-models",
        "models": models,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def _build_command(executable: str) -> list[str]:
    path = Path(executable)
    if os.name == "nt" and path.suffix.lower() == ".ps1":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, "debug", "models"]
    return [executable, "debug", "models"]


def _parse_json_output(output: str) -> object:
    text = output.strip()
    if not text:
        raise AppError(
            status_code=502,
            error_category="CODEX_MODEL_LIST_INVALID_RESPONSE",
            error_message="Codex 模型目录命令没有输出 JSON。",
        )

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise AppError(
        status_code=502,
        error_category="CODEX_MODEL_LIST_INVALID_RESPONSE",
        error_message="Codex 模型目录输出不是有效 JSON。",
    )


def _extract_models(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("models", "data", "items", "model_catalog"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _normalize_model(value: Any) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    model_id = _first_string(value, "id", "slug", "model_id", "model")
    if not model_id:
        return None
    label = _first_string(value, "display_name", "label", "title", "name") or model_id
    reasoning_efforts = _normalize_string_list(
        value.get("reasoning_efforts")
        or value.get("supported_reasoning_efforts")
        or value.get("reasoning_levels")
    )
    return {
        "id": model_id,
        "label": label,
        "reasoning_efforts": reasoning_efforts,
    }


def _first_string(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
