from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.app.errors import AppError
from backend.app.services.reasoning import codex_exec


def test_build_codex_command_uses_separate_validated_arguments(tmp_path: Path) -> None:
    command = codex_exec.build_codex_command(
        executable="codex.exe",
        workdir=tmp_path,
        schema_path=tmp_path / "schema.json",
        model="gpt-5.6",
        reasoning_effort="high",
    )

    assert command[0] == "codex.exe"
    assert command[-1] == "-"
    assert command[command.index("--model") + 1] == "gpt-5.6"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="high"'
    assert "--json" in command
    assert "--output-schema" in command
    assert "--sandbox" in command
    assert "read-only" in command


def test_run_codex_exec_parses_jsonl_and_passes_prompt_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    final_payload = {"answer": "ok"}
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(final_payload),
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12}}),
        ]
    )

    class FakeProcess:
        returncode = 0
        pid = 123

        def __init__(self, command, **kwargs) -> None:
            captured["command"] = command
            captured["kwargs"] = kwargs

        def communicate(self, *, input=None, timeout=None):
            captured["input"] = input
            captured["timeout"] = timeout
            return stdout, ""

        def poll(self):
            return self.returncode

    monkeypatch.setattr(codex_exec, "resolve_codex_executable", lambda _path: "codex.exe")
    monkeypatch.setattr(codex_exec.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(codex_exec, "_probe_version", lambda _path: "codex-cli 0.147.0")

    result = codex_exec.run_codex_exec(
        cli_path="codex",
        prompt="private prompt",
        output_schema={"type": "object"},
        model=None,
        reasoning_effort=None,
        timeout_seconds=90,
    )

    assert result.output == final_payload
    assert result.cli_version == "0.147.0"
    assert result.usage == {"input_tokens": 12}
    assert result.event_count == 3
    assert captured["input"] == "private prompt"
    assert "private prompt" not in captured["command"]
    assert captured["kwargs"]["shell"] is False


def test_run_codex_exec_rejects_invalid_jsonl(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        returncode = 0
        pid = 123

        def __init__(self, _command, **_kwargs) -> None:
            pass

        def communicate(self, *, input=None, timeout=None):
            return "not-json", ""

        def poll(self):
            return self.returncode

    monkeypatch.setattr(codex_exec, "resolve_codex_executable", lambda _path: "codex.exe")
    monkeypatch.setattr(codex_exec.subprocess, "Popen", FakeProcess)

    with pytest.raises(AppError) as error:
        codex_exec.run_codex_exec(
            cli_path="codex",
            prompt="prompt",
            output_schema={"type": "object"},
            model=None,
            reasoning_effort=None,
            timeout_seconds=90,
        )

    assert error.value.error_category == "CODEX_EVENT_INVALID"


def test_check_codex_cli_requires_supported_exec_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess(["codex", "--version"], 0, "codex-cli 0.147.0", ""),
            subprocess.CompletedProcess(["codex", "exec", "--help"], 0, "--json only", ""),
        ]
    )
    monkeypatch.setattr(codex_exec, "resolve_codex_executable", lambda _path: "codex.exe")
    monkeypatch.setattr(codex_exec, "_run_probe", lambda *_args, **_kwargs: next(responses))

    result = codex_exec.check_codex_cli("codex")

    assert result.ok is False
    assert result.error_category == "CODEX_VERSION_UNSUPPORTED"


def test_communicate_with_control_terminates_cancelled_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[object] = []
    checks = iter([False, True])

    class FakeProcess:
        args = ["codex", "exec"]

        def communicate(self, *, input=None, timeout=None):
            raise subprocess.TimeoutExpired(self.args, timeout)

    process = FakeProcess()
    monkeypatch.setattr(codex_exec, "_terminate_process_tree", terminated.append)

    with pytest.raises(AppError) as error:
        codex_exec._communicate_with_control(
            process,
            prompt="prompt",
            timeout_seconds=10,
            cancellation_check=lambda: next(checks),
        )

    assert error.value.error_category == "CODEX_CANCELLED"
    assert terminated == [process]
