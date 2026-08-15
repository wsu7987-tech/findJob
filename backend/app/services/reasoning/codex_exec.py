from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.app.errors import AppError


SUPPORTED_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_VERSION_PATTERN = re.compile(r"(?:codex-cli\s+)?(?P<version>\d+\.\d+\.\d+)", re.IGNORECASE)
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,})\b"), "[REDACTED]"),
    (re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
)


@dataclass(slots=True)
class CodexCliStatus:
    ok: bool
    status: str
    cli_path: str | None
    cli_version: str | None
    authenticated: bool
    detail: str
    error_category: str | None = None


@dataclass(slots=True)
class CodexExecResult:
    output: dict[str, Any]
    cli_path: str
    cli_version: str | None
    model: str | None
    reasoning_effort: str | None
    usage: dict[str, Any] | None
    event_count: int


def resolve_codex_executable(cli_path: str) -> str:
    candidate = (cli_path or "codex").strip()
    if not candidate or any(character in candidate for character in ("\x00", "\r", "\n")):
        raise AppError(
            status_code=400,
            error_category="CODEX_PATH_INVALID",
            error_message="Codex CLI path is invalid.",
        )

    path_candidate = Path(candidate).expanduser()
    has_path_separator = any(separator in candidate for separator in ("/", "\\"))
    if path_candidate.is_absolute() or has_path_separator:
        if not path_candidate.is_file():
            raise AppError(
                status_code=400,
                error_category="CODEX_NOT_INSTALLED",
                error_message="Codex CLI executable was not found at the configured path.",
            )
        return str(path_candidate.resolve())

    resolved = shutil.which(candidate)
    if not resolved:
        raise AppError(
            status_code=400,
            error_category="CODEX_NOT_INSTALLED",
            error_message="Codex CLI is not installed or is not available on PATH.",
        )
    return str(Path(resolved).resolve())


def validate_codex_options(*, model: str | None, reasoning_effort: str | None) -> None:
    if model and not _MODEL_ID_PATTERN.fullmatch(model):
        raise AppError(
            status_code=400,
            error_category="CODEX_MODEL_INVALID",
            error_message="Codex model ID contains unsupported characters.",
        )
    if reasoning_effort and reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        raise AppError(
            status_code=400,
            error_category="CODEX_REASONING_EFFORT_UNSUPPORTED",
            error_message=f"Unsupported Codex reasoning effort: {reasoning_effort}.",
        )


def build_codex_command(
    *,
    executable: str,
    workdir: Path,
    schema_path: Path,
    model: str | None,
    reasoning_effort: str | None,
) -> list[str]:
    validate_codex_options(model=model, reasoning_effort=reasoning_effort)
    command = [
        executable,
        "exec",
        "--json",
        "--color",
        "never",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema_path),
        "--cd",
        str(workdir),
    ]
    if model:
        command.extend(["--model", model])
    if reasoning_effort:
        command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
    command.append("-")
    return command


def check_codex_cli(cli_path: str, *, timeout_seconds: int = 10) -> CodexCliStatus:
    try:
        executable = resolve_codex_executable(cli_path)
        version_process = _run_probe([executable, "--version"], timeout_seconds=timeout_seconds)
    except AppError as exc:
        return CodexCliStatus(
            ok=False,
            status="invalid",
            cli_path=None,
            cli_version=None,
            authenticated=False,
            detail=exc.error_message,
            error_category=exc.error_category,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CodexCliStatus(
            ok=False,
            status="failed",
            cli_path=None,
            cli_version=None,
            authenticated=False,
            detail=f"Failed to start Codex CLI: {_redact(str(exc))}",
            error_category="CODEX_PROCESS_FAILED",
        )

    version_output = f"{version_process.stdout}\n{version_process.stderr}".strip()
    version_match = _VERSION_PATTERN.search(version_output)
    version = version_match.group("version") if version_match else None
    if version_process.returncode != 0:
        return CodexCliStatus(
            ok=False,
            status="failed",
            cli_path=executable,
            cli_version=version,
            authenticated=False,
            detail=_process_detail(version_output, fallback="Codex CLI version check failed."),
            error_category="CODEX_PROCESS_FAILED",
        )

    help_process = _run_probe(
        [executable, "exec", "--help"],
        timeout_seconds=timeout_seconds,
    )
    help_output = f"{help_process.stdout}\n{help_process.stderr}"
    required_flags = ("--json", "--output-schema", "--ignore-user-config")
    if help_process.returncode != 0 or any(flag not in help_output for flag in required_flags):
        return CodexCliStatus(
            ok=False,
            status="invalid",
            cli_path=executable,
            cli_version=version,
            authenticated=False,
            detail="The installed Codex CLI does not support the required exec options.",
            error_category="CODEX_VERSION_UNSUPPORTED",
        )

    login_process = _run_probe(
        [executable, "login", "status"],
        timeout_seconds=timeout_seconds,
    )
    login_output = f"{login_process.stdout}\n{login_process.stderr}".strip()
    if login_process.returncode != 0:
        return CodexCliStatus(
            ok=False,
            status="failed",
            cli_path=executable,
            cli_version=version,
            authenticated=False,
            detail=_process_detail(login_output, fallback="Codex CLI is not logged in."),
            error_category="CODEX_NOT_AUTHENTICATED",
        )

    return CodexCliStatus(
        ok=True,
        status="ready",
        cli_path=executable,
        cli_version=version,
        authenticated=True,
        detail="Codex CLI is installed and authenticated.",
    )


def run_codex_exec(
    *,
    cli_path: str,
    prompt: str,
    output_schema: dict[str, Any],
    model: str | None,
    reasoning_effort: str | None,
    timeout_seconds: int,
    cancellation_check: Callable[[], bool] | None = None,
) -> CodexExecResult:
    executable = resolve_codex_executable(cli_path)
    validate_codex_options(model=model, reasoning_effort=reasoning_effort)
    if not prompt.strip():
        raise AppError(
            status_code=400,
            error_category="CODEX_INPUT_INVALID",
            error_message="Codex prompt must not be empty.",
        )

    with tempfile.TemporaryDirectory(prefix="fine-job-codex-") as temporary_directory:
        workdir = Path(temporary_directory)
        schema_path = workdir / "output-schema.json"
        schema_path.write_text(
            json.dumps(output_schema, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        command = build_codex_command(
            executable=executable,
            workdir=workdir,
            schema_path=schema_path,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=creation_flags,
            )
            stdout, stderr = _communicate_with_control(
                process,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
                cancellation_check=cancellation_check,
            )
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            raise AppError(
                status_code=504,
                error_category="CODEX_TIMEOUT",
                error_message=f"Codex execution exceeded {timeout_seconds} seconds.",
            ) from exc
        except OSError as exc:
            raise AppError(
                status_code=502,
                error_category="CODEX_PROCESS_FAILED",
                error_message=f"Failed to start Codex CLI: {_redact(str(exc))}",
            ) from exc

    if process.returncode != 0:
        category = _classify_process_failure(stderr)
        raise AppError(
            status_code=502,
            error_category=category,
            error_message=_process_detail(stderr, fallback="Codex execution failed."),
        )

    events, final_message, usage = _parse_jsonl_events(stdout)
    try:
        parsed_output = json.loads(final_message)
    except json.JSONDecodeError as exc:
        raise AppError(
            status_code=502,
            error_category="CODEX_OUTPUT_SCHEMA_INVALID",
            error_message="Codex final response is not a JSON object.",
        ) from exc
    if not isinstance(parsed_output, dict):
        raise AppError(
            status_code=502,
            error_category="CODEX_OUTPUT_SCHEMA_INVALID",
            error_message="Codex final response is not a JSON object.",
        )

    version_match = _VERSION_PATTERN.search(_probe_version(executable))
    return CodexExecResult(
        output=parsed_output,
        cli_path=executable,
        cli_version=version_match.group("version") if version_match else None,
        model=model,
        reasoning_effort=reasoning_effort,
        usage=usage,
        event_count=len(events),
    )


def _communicate_with_control(
    process: subprocess.Popen[str],
    *,
    prompt: str,
    timeout_seconds: int,
    cancellation_check: Callable[[], bool] | None,
) -> tuple[str, str]:
    if cancellation_check is None:
        return process.communicate(input=prompt, timeout=timeout_seconds)

    deadline = time.monotonic() + timeout_seconds
    first_attempt = True
    while True:
        if cancellation_check():
            _terminate_process_tree(process)
            raise AppError(
                status_code=409,
                error_category="CODEX_CANCELLED",
                error_message="Codex execution was cancelled by the user.",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        try:
            return process.communicate(
                input=prompt if first_attempt else None,
                timeout=min(0.25, remaining),
            )
        except subprocess.TimeoutExpired:
            first_attempt = False


def _run_probe(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout_seconds,
        check=False,
    )


def _probe_version(executable: str) -> str:
    try:
        process = _run_probe([executable, "--version"], timeout_seconds=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{process.stdout}\n{process.stderr}"


def _parse_jsonl_events(
    stdout: str,
) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    final_message: str | None = None
    usage: dict[str, Any] | None = None
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AppError(
                status_code=502,
                error_category="CODEX_EVENT_INVALID",
                error_message=f"Codex emitted invalid JSONL at line {line_number}.",
            ) from exc
        if not isinstance(event, dict):
            raise AppError(
                status_code=502,
                error_category="CODEX_EVENT_INVALID",
                error_message=f"Codex emitted a non-object event at line {line_number}.",
            )
        events.append(event)
        event_type = str(event.get("type") or "")
        item = event.get("item")
        if (
            event_type == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            final_message = item["text"]
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = dict(event["usage"])

    if final_message is None:
        raise AppError(
            status_code=502,
            error_category="CODEX_EVENT_INVALID",
            error_message="Codex JSONL stream did not contain a final agent message.",
        )
    return events, final_message, usage


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        process.kill()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _classify_process_failure(stderr: str) -> str:
    normalized = stderr.lower()
    if "not logged in" in normalized or "login" in normalized and "required" in normalized:
        return "CODEX_NOT_AUTHENTICATED"
    if "rate limit" in normalized or "too many requests" in normalized:
        return "CODEX_RATE_LIMITED"
    if "model" in normalized and any(token in normalized for token in ("unknown", "unavailable", "not found")):
        return "CODEX_MODEL_UNAVAILABLE"
    if "reasoning" in normalized and "unsupported" in normalized:
        return "CODEX_REASONING_EFFORT_UNSUPPORTED"
    return "CODEX_PROCESS_FAILED"


def _process_detail(value: str, *, fallback: str) -> str:
    redacted = _redact(value).strip()
    if not redacted:
        return fallback
    return redacted[:1200]


def _redact(value: str) -> str:
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
