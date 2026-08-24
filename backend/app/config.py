from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PERSISTED_CONFIG_KEYS = {
    "app_data_dir",
    "sqlite_path",
    "qdrant_path",
    "output_root",
    "summary_output_dir",
    "report_output_dir",
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "llm_api_key",
    "embedding_provider",
    "embedding_model",
    "embedding_base_url",
    "embedding_api_key",
    "fetch_concurrency",
    "llm_concurrency",
    "embedding_concurrency",
    "fetch_timeout_seconds",
    "llm_timeout_seconds",
    "reasoning_executor",
    "codex_cli_path",
    "codex_model",
    "codex_reasoning_effort",
    "codex_timeout_seconds",
    "embedding_timeout_seconds",
    "fetch_user_agent",
    "quick_capture_hotkey",
    "quick_capture_screenshot_hotkey",
    "close_to_tray",
    "quick_capture_always_on_top",
}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


@dataclass(slots=True)
class AppConfig:
    app_data_dir: Path
    local_config_path: Path
    sqlite_path: Path
    qdrant_path: Path
    output_root: Path
    summary_output_dir: Path
    report_output_dir: Path
    llm_provider: str | None
    llm_model: str | None
    llm_base_url: str | None
    llm_api_key: str | None
    embedding_provider: str | None
    embedding_model: str | None
    embedding_base_url: str | None
    embedding_api_key: str | None
    fetch_concurrency: int
    llm_concurrency: int
    embedding_concurrency: int
    fetch_timeout_seconds: int
    llm_timeout_seconds: int
    embedding_timeout_seconds: int
    fetch_user_agent: str
    quick_capture_hotkey: str | None
    quick_capture_screenshot_hotkey: str | None
    close_to_tray: bool
    quick_capture_always_on_top: bool
    reasoning_executor: str = "llm"
    codex_cli_path: str = "codex"
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None
    codex_timeout_seconds: int = 300

    def missing_runtime_fields(self) -> list[str]:
        missing: list[str] = []
        required_fields: list[str] = []
        if self.reasoning_executor == "llm":
            required_fields.extend(["llm_provider", "llm_model"])
        required_fields.extend(["embedding_provider", "embedding_model"])
        for field_name in required_fields:
            if not getattr(self, field_name):
                missing.append(field_name)
        return missing


def load_config() -> AppConfig:
    base_app_data_dir = Path(
        _env("KNOWLEDGE_CURATOR_APP_DATA_DIR", ".local/app-data") or ""
    )
    local_config_path = base_app_data_dir / "config.user.json"
    local_overrides = read_persisted_config(local_config_path)

    app_data_dir = Path(
        _resolve_value(
            local_overrides,
            "app_data_dir",
            _env("KNOWLEDGE_CURATOR_APP_DATA_DIR", ".local/app-data"),
        )
        or ""
    )
    output_root = Path(
        _resolve_value(
            local_overrides,
            "output_root",
            _env("KNOWLEDGE_CURATOR_OUTPUT_ROOT", str(app_data_dir / "outputs")),
        )
        or ""
    )

    return AppConfig(
        app_data_dir=app_data_dir,
        local_config_path=local_config_path,
        sqlite_path=Path(
            _resolve_value(
                local_overrides,
                "sqlite_path",
                _env(
                    "KNOWLEDGE_CURATOR_SQLITE_PATH",
                    str(app_data_dir / "knowledge-curator.db"),
                ),
            )
            or ""
        ),
        qdrant_path=Path(
            _resolve_value(
                local_overrides,
                "qdrant_path",
                _env("KNOWLEDGE_CURATOR_QDRANT_PATH", str(app_data_dir / "qdrant")),
            )
            or ""
        ),
        output_root=output_root,
        summary_output_dir=Path(
            _resolve_value(
                local_overrides,
                "summary_output_dir",
                _env(
                    "KNOWLEDGE_CURATOR_SUMMARY_OUTPUT_DIR",
                    str(output_root / "summaries"),
                ),
            )
            or ""
        ),
        report_output_dir=Path(
            _resolve_value(
                local_overrides,
                "report_output_dir",
                _env(
                    "KNOWLEDGE_CURATOR_REPORT_OUTPUT_DIR",
                    str(output_root / "reports"),
                ),
            )
            or ""
        ),
        llm_provider=_resolve_value(
            local_overrides,
            "llm_provider",
            _env("KNOWLEDGE_CURATOR_LLM_PROVIDER"),
        ),
        llm_model=_resolve_value(
            local_overrides,
            "llm_model",
            _env("KNOWLEDGE_CURATOR_LLM_MODEL"),
        ),
        llm_base_url=_resolve_value(
            local_overrides,
            "llm_base_url",
            _env("KNOWLEDGE_CURATOR_LLM_BASE_URL"),
        ),
        llm_api_key=_resolve_value(
            local_overrides,
            "llm_api_key",
            _env("KNOWLEDGE_CURATOR_LLM_API_KEY"),
        ),
        embedding_provider=_resolve_value(
            local_overrides,
            "embedding_provider",
            _env("KNOWLEDGE_CURATOR_EMBEDDING_PROVIDER"),
        ),
        embedding_model=_resolve_value(
            local_overrides,
            "embedding_model",
            _env("KNOWLEDGE_CURATOR_EMBEDDING_MODEL"),
        ),
        embedding_base_url=_resolve_value(
            local_overrides,
            "embedding_base_url",
            _env("KNOWLEDGE_CURATOR_EMBEDDING_BASE_URL"),
        ),
        embedding_api_key=_resolve_value(
            local_overrides,
            "embedding_api_key",
            _env("KNOWLEDGE_CURATOR_EMBEDDING_API_KEY"),
        ),
        fetch_concurrency=int(
            _resolve_value(
                local_overrides,
                "fetch_concurrency",
                _env("KNOWLEDGE_CURATOR_FETCH_CONCURRENCY", "3"),
            )
            or "3"
        ),
        llm_concurrency=int(
            _resolve_value(
                local_overrides,
                "llm_concurrency",
                _env("KNOWLEDGE_CURATOR_LLM_CONCURRENCY", "2"),
            )
            or "2"
        ),
        embedding_concurrency=int(
            _resolve_value(
                local_overrides,
                "embedding_concurrency",
                _env("KNOWLEDGE_CURATOR_EMBEDDING_CONCURRENCY", "2"),
            )
            or "2"
        ),
        fetch_timeout_seconds=int(
            _resolve_value(
                local_overrides,
                "fetch_timeout_seconds",
                _env("KNOWLEDGE_CURATOR_FETCH_TIMEOUT_SECONDS", "30"),
            )
            or "30"
        ),
        llm_timeout_seconds=int(
            _resolve_value(
                local_overrides,
                "llm_timeout_seconds",
                _env("KNOWLEDGE_CURATOR_LLM_TIMEOUT_SECONDS", "90"),
            )
            or "90"
        ),
        embedding_timeout_seconds=int(
            _resolve_value(
                local_overrides,
                "embedding_timeout_seconds",
                _env("KNOWLEDGE_CURATOR_EMBEDDING_TIMEOUT_SECONDS", "60"),
            )
            or "60"
        ),
        fetch_user_agent=_resolve_value(
            local_overrides,
            "fetch_user_agent",
            _env(
                "KNOWLEDGE_CURATOR_FETCH_USER_AGENT",
                "KnowledgeCurator/0.1 (+https://localhost)",
            ),
        )
        or "KnowledgeCurator/0.1 (+https://localhost)",
        quick_capture_hotkey=_resolve_value(
            local_overrides,
            "quick_capture_hotkey",
            _env("KNOWLEDGE_CURATOR_QUICK_CAPTURE_HOTKEY", "CommandOrControl+Shift+Space"),
        ),
        quick_capture_screenshot_hotkey=_resolve_value(
            local_overrides,
            "quick_capture_screenshot_hotkey",
            _env("KNOWLEDGE_CURATOR_QUICK_CAPTURE_SCREENSHOT_HOTKEY", "CommandOrControl+Shift+4"),
        ),
        close_to_tray=_resolve_bool(
            local_overrides,
            "close_to_tray",
            _env("KNOWLEDGE_CURATOR_CLOSE_TO_TRAY"),
            default=True,
        ),
        quick_capture_always_on_top=_resolve_bool(
            local_overrides,
            "quick_capture_always_on_top",
            _env("KNOWLEDGE_CURATOR_QUICK_CAPTURE_ALWAYS_ON_TOP"),
            default=True,
        ),
        reasoning_executor=(
            _resolve_value(
                local_overrides,
                "reasoning_executor",
                _env("FINE_JOB_REASONING_EXECUTOR", "llm"),
            )
            or "llm"
        ),
        codex_cli_path=(
            _resolve_value(
                local_overrides,
                "codex_cli_path",
                _env("FINE_JOB_CODEX_CLI_PATH", "codex"),
            )
            or "codex"
        ),
        codex_model=_resolve_value(
            local_overrides,
            "codex_model",
            _env("FINE_JOB_CODEX_MODEL"),
        ),
        codex_reasoning_effort=_resolve_value(
            local_overrides,
            "codex_reasoning_effort",
            _env("FINE_JOB_CODEX_REASONING_EFFORT"),
        ),
        codex_timeout_seconds=int(
            _resolve_value(
                local_overrides,
                "codex_timeout_seconds",
                _env("FINE_JOB_CODEX_TIMEOUT_SECONDS", "300"),
            )
            or "300"
        ),
    )


def read_persisted_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if key in PERSISTED_CONFIG_KEYS}


def write_persisted_config(path: Path, values: dict[str, Any]) -> None:
    sanitized = {
        key: value for key, value in values.items() if key in PERSISTED_CONFIG_KEYS
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _resolve_value(
    local_overrides: dict[str, Any],
    key: str,
    env_value: str | None,
) -> Any:
    if key in local_overrides:
        return local_overrides[key]
    return env_value


def _resolve_bool(
    local_overrides: dict[str, Any],
    key: str,
    env_value: str | None,
    *,
    default: bool,
) -> bool:
    value = _resolve_value(local_overrides, key, env_value)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default
