from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock

from backend.app.errors import AppError


class CutoverPhase(StrEnum):
    PRE_CUTOVER = "pre_cutover"
    POST_CUTOVER = "post_cutover"


class ExecutionAuthority(StrEnum):
    WORKFLOW_PIPELINE = "workflow_pipeline"
    SMART_CAPTURE = "smart_capture"


@dataclass
class CutoverGuard:
    """集中表达迁移期唯一执行 authority 与旧入口保护。"""

    phase: CutoverPhase = CutoverPhase.PRE_CUTOVER
    legacy_callback_invocations: int = 0
    _live_children: dict[str, ExecutionAuthority] = field(default_factory=dict, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def assert_workflow_live_allowed(self, *, operation: str) -> None:
        """Task09 前允许旧生产链；Cutover 后旧链只能走历史读取。"""
        if self.phase == CutoverPhase.POST_CUTOVER:
            raise AppError(
                409,
                "WORKFLOW_LIVE_EXECUTION_DISABLED",
                f"Cutover 后禁止旧 Workflow live execution：{operation}。",
            )

    def observe_legacy_capture_callback(self, *, capture_id: str) -> None:
        """记录旧 callback 触达；Cutover 后立即阻止其继续进入 live Engine。"""
        self.legacy_callback_invocations += 1
        self.assert_workflow_live_allowed(operation=f"capture callback {capture_id}")

    def assert_single_live_authority(
        self,
        *,
        child_ref: str,
        requested_authority: ExecutionAuthority,
        production_authority: ExecutionAuthority,
        allow_independent: bool = False,
    ) -> None:
        """校验同一 live child 的启动请求没有越过当前唯一 authority。"""
        if requested_authority != production_authority:
            raise AppError(
                409,
                "LIVE_EXECUTION_AUTHORITY_CONFLICT",
                f"Smart Capture {child_ref} 不能由第二套执行 authority 启动。",
            )
        if (
            self.phase == CutoverPhase.PRE_CUTOVER
            and production_authority == ExecutionAuthority.SMART_CAPTURE
            and not allow_independent
        ):
            raise AppError(
                409,
                "SMART_CAPTURE_LIVE_EXECUTION_NOT_CUTOVER",
                "Task09 前 Smart Capture seam 不能成为 linked child 的 production authority。",
            )

    def claim_live_start(
        self,
        *,
        child_ref: str,
        requested_authority: ExecutionAuthority,
        production_authority: ExecutionAuthority | None = None,
        allow_independent: bool = False,
    ) -> None:
        """为一个 live child 领取唯一启动权，防止同一 child 重复启动。"""
        production_authority = production_authority or (
            ExecutionAuthority.WORKFLOW_PIPELINE
            if self.phase == CutoverPhase.PRE_CUTOVER
            else ExecutionAuthority.SMART_CAPTURE
        )
        self.assert_single_live_authority(
            child_ref=child_ref,
            requested_authority=requested_authority,
            production_authority=production_authority,
            allow_independent=allow_independent,
        )
        with self._lock:
            if child_ref in self._live_children:
                raise AppError(
                    409,
                    "LIVE_CHILD_ALREADY_STARTED",
                    f"Smart Capture {child_ref} 已有执行单元，不能重复启动。",
                )
            self._live_children[child_ref] = requested_authority

    def release_live_start(self, *, child_ref: str) -> None:
        """终态回调释放启动权，允许后续合法恢复或下一批次接续。"""
        with self._lock:
            self._live_children.pop(child_ref, None)


# Task 09 后生产默认由 Smart Capture 执行；测试可显式切回迁移前阶段验证旧链特征。
_runtime_guard = CutoverGuard(phase=CutoverPhase.POST_CUTOVER)


def get_runtime_cutover_guard() -> CutoverGuard:
    return _runtime_guard


def configure_runtime_cutover_phase(phase: CutoverPhase) -> None:
    _runtime_guard.phase = phase


def reset_runtime_cutover_guard() -> None:
    """测试和应用重载恢复 Task 09 后的生产执行权。"""
    _runtime_guard.phase = CutoverPhase.POST_CUTOVER
    _runtime_guard.legacy_callback_invocations = 0
    with _runtime_guard._lock:
        _runtime_guard._live_children.clear()
