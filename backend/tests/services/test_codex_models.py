from __future__ import annotations

import subprocess

from backend.app.services import codex_models


def test_list_codex_models_runs_debug_models_and_normalizes_catalog(monkeypatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(codex_models, "resolve_codex_executable", lambda _path: "codex.exe")

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"models":[{"slug":"gpt-5.6-luna","display_name":"GPT-5.6 Luna","reasoning_efforts":["low","high"]}]}',
            stderr="",
        )

    monkeypatch.setattr(codex_models.subprocess, "run", fake_run)

    result = codex_models.list_codex_models("codex")

    assert commands == [["codex.exe", "debug", "models"]]
    assert result["models"] == [
        {
            "id": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna",
            "reasoning_efforts": ["low", "high"],
        }
    ]
