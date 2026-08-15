from __future__ import annotations

from pathlib import Path

from backend.app.config import AppConfig, read_persisted_config, write_persisted_config
from backend.app.schemas.config import AppConfigPatchRequest


def serialize_config(config: AppConfig) -> dict[str, object]:
    # FineJob-retained service: serializes model/provider configuration used
    # by the desktop "FineJob 配置" drawer and readiness checks.
    return {
        "app_data_dir": str(config.app_data_dir),
        "sqlite_path": str(config.sqlite_path),
        "qdrant_path": str(config.qdrant_path),
        "output_root": str(config.output_root),
        "summary_output_dir": str(config.summary_output_dir),
        "report_output_dir": str(config.report_output_dir),
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "llm_base_url": config.llm_base_url,
        "llm_api_key": config.llm_api_key,
        "llm_configured": bool(config.llm_base_url and config.llm_api_key),
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "embedding_base_url": config.embedding_base_url,
        "embedding_api_key": config.embedding_api_key,
        "embedding_configured": bool(
            config.embedding_base_url and config.embedding_api_key
        ),
        "fetch_concurrency": config.fetch_concurrency,
        "llm_concurrency": config.llm_concurrency,
        "embedding_concurrency": config.embedding_concurrency,
        "fetch_timeout_seconds": config.fetch_timeout_seconds,
        "llm_timeout_seconds": config.llm_timeout_seconds,
        "embedding_timeout_seconds": config.embedding_timeout_seconds,
        "fetch_user_agent": config.fetch_user_agent,
        "quick_capture_hotkey": config.quick_capture_hotkey,
        "quick_capture_screenshot_hotkey": config.quick_capture_screenshot_hotkey,
        "close_to_tray": config.close_to_tray,
        "quick_capture_always_on_top": config.quick_capture_always_on_top,
        "reasoning_executor": config.reasoning_executor,
        "codex_cli_path": config.codex_cli_path,
        "codex_model": config.codex_model,
        "codex_reasoning_effort": config.codex_reasoning_effort,
        "codex_timeout_seconds": config.codex_timeout_seconds,
    }


def update_config(config: AppConfig, payload: AppConfigPatchRequest) -> AppConfig:
    # FineJob-retained service: updates local LLM/Embedding configuration and
    # runtime paths. Keep this path while removing legacy summary/report logic.
    updates = payload.model_dump(exclude_unset=True)

    old_output_root = config.output_root
    old_summary_output_dir = config.summary_output_dir
    old_report_output_dir = config.report_output_dir

    if "app_data_dir" in updates:
        config.app_data_dir = Path(updates["app_data_dir"])
        config.local_config_path = config.app_data_dir / "config.user.json"
    if "sqlite_path" in updates:
        config.sqlite_path = Path(updates["sqlite_path"])
    if "qdrant_path" in updates:
        config.qdrant_path = Path(updates["qdrant_path"])
    if "output_root" in updates:
        config.output_root = Path(updates["output_root"])
        if (
            "summary_output_dir" not in updates
            and old_summary_output_dir == old_output_root / "summaries"
        ):
            config.summary_output_dir = config.output_root / "summaries"
        if (
            "report_output_dir" not in updates
            and old_report_output_dir == old_output_root / "reports"
        ):
            config.report_output_dir = config.output_root / "reports"
    if "summary_output_dir" in updates:
        config.summary_output_dir = Path(updates["summary_output_dir"])
    if "report_output_dir" in updates:
        config.report_output_dir = Path(updates["report_output_dir"])
    if "llm_provider" in updates:
        config.llm_provider = _normalize_optional_text(updates["llm_provider"])
    if "llm_model" in updates:
        config.llm_model = _normalize_optional_text(updates["llm_model"])
    if "llm_base_url" in updates:
        config.llm_base_url = _normalize_optional_text(updates["llm_base_url"])
    if "llm_api_key" in updates:
        config.llm_api_key = _normalize_optional_text(updates["llm_api_key"])
    if "embedding_provider" in updates:
        config.embedding_provider = _normalize_optional_text(updates["embedding_provider"])
    if "embedding_model" in updates:
        config.embedding_model = _normalize_optional_text(updates["embedding_model"])
    if "embedding_base_url" in updates:
        config.embedding_base_url = _normalize_optional_text(updates["embedding_base_url"])
    if "embedding_api_key" in updates:
        config.embedding_api_key = _normalize_optional_text(updates["embedding_api_key"])
    if "fetch_concurrency" in updates:
        config.fetch_concurrency = int(updates["fetch_concurrency"])
    if "llm_concurrency" in updates:
        config.llm_concurrency = int(updates["llm_concurrency"])
    if "embedding_concurrency" in updates:
        config.embedding_concurrency = int(updates["embedding_concurrency"])
    if "fetch_timeout_seconds" in updates:
        config.fetch_timeout_seconds = int(updates["fetch_timeout_seconds"])
    if "llm_timeout_seconds" in updates:
        config.llm_timeout_seconds = int(updates["llm_timeout_seconds"])
    if "embedding_timeout_seconds" in updates:
        config.embedding_timeout_seconds = int(updates["embedding_timeout_seconds"])
    if "fetch_user_agent" in updates:
        config.fetch_user_agent = (
            _normalize_optional_text(updates["fetch_user_agent"])
            or "KnowledgeCurator/0.1 (+https://localhost)"
        )
    if "quick_capture_hotkey" in updates:
        config.quick_capture_hotkey = _normalize_optional_text(updates["quick_capture_hotkey"])
    if "quick_capture_screenshot_hotkey" in updates:
        config.quick_capture_screenshot_hotkey = _normalize_optional_text(
            updates["quick_capture_screenshot_hotkey"]
        )
    if "close_to_tray" in updates:
        config.close_to_tray = bool(updates["close_to_tray"])
    if "quick_capture_always_on_top" in updates:
        config.quick_capture_always_on_top = bool(updates["quick_capture_always_on_top"])
    if "reasoning_executor" in updates:
        config.reasoning_executor = str(updates["reasoning_executor"] or "llm")
    if "codex_cli_path" in updates:
        config.codex_cli_path = _normalize_optional_text(updates["codex_cli_path"]) or "codex"
    if "codex_model" in updates:
        config.codex_model = _normalize_optional_text(updates["codex_model"])
    if "codex_reasoning_effort" in updates:
        config.codex_reasoning_effort = _normalize_optional_text(
            updates["codex_reasoning_effort"]
        )
    if "codex_timeout_seconds" in updates:
        config.codex_timeout_seconds = int(updates["codex_timeout_seconds"])

    config.app_data_dir.mkdir(parents=True, exist_ok=True)
    config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    config.qdrant_path.mkdir(parents=True, exist_ok=True)
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.summary_output_dir.mkdir(parents=True, exist_ok=True)
    config.report_output_dir.mkdir(parents=True, exist_ok=True)
    return config


def persist_config_updates(config: AppConfig, payload: AppConfigPatchRequest) -> None:
    # FineJob-retained service: persists model settings to the local user config.
    updates = payload.model_dump(exclude_unset=True)
    persisted = read_persisted_config(config.local_config_path)
    for key in updates:
        persisted[key] = _serialize_field(config, key)
    write_persisted_config(config.local_config_path, persisted)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _serialize_field(config: AppConfig, key: str) -> object:
    value = getattr(config, key)
    if isinstance(value, Path):
        return str(value)
    return value
