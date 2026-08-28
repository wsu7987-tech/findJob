from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.errors import AppError


MCP_CONTRACT_VERSION = "v1"
INTERNAL_API_VERSION = "v1"
CAPABILITIES_VERSION = "v1"


@dataclass(slots=True)
class CodexRuntime:
    run_id: str
    token: str
    expires_at: datetime
    revoked: bool = False


class CodexRuntimeRegistry:
    """在后端内存中维护 FineJob Codex 运行身份。"""

    def __init__(self) -> None:
        self._runtimes_by_id: dict[str, CodexRuntime] = {}
        self._runtimes_by_token: dict[str, CodexRuntime] = {}

    def create(self) -> CodexRuntime:
        now = datetime.now(UTC)
        self._discard_expired(now)
        runtime = CodexRuntime(
            run_id=f"codex_run_{secrets.token_urlsafe(12)}",
            token=secrets.token_urlsafe(36),
            expires_at=now + timedelta(hours=12),
        )
        # 每次启动独立登记运行身份，避免新会话影响其他仍在执行的会话。
        self._runtimes_by_id[runtime.run_id] = runtime
        self._runtimes_by_token[runtime.token] = runtime
        return runtime

    def require(self, token: str) -> CodexRuntime:
        runtime = self._runtimes_by_token.get(token)
        if runtime is None or runtime.revoked:
            raise AppError(
                status_code=401,
                error_category="CODEX_RUNTIME_UNAUTHORIZED",
                error_message="Codex MCP 运行凭证无效或已经过期。",
            )
        if runtime.expires_at <= datetime.now(UTC):
            self.revoke(runtime.run_id)
            raise AppError(
                status_code=401,
                error_category="CODEX_RUNTIME_UNAUTHORIZED",
                error_message="Codex MCP 运行凭证无效或已经过期。",
            )
        return runtime

    def revoke(self, run_id: str) -> None:
        runtime = self._runtimes_by_id.pop(run_id, None)
        if runtime is None:
            return
        runtime.revoked = True
        self._runtimes_by_token.pop(runtime.token, None)

    def _discard_expired(self, now: datetime) -> None:
        expired_ids = [
            run_id
            for run_id, runtime in self._runtimes_by_id.items()
            if runtime.expires_at <= now
        ]
        for run_id in expired_ids:
            self.revoke(run_id)
