from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
import time

from backend.app.services.fine_job.capture_pacing import default_pacing


ACTIVITY_FIELDS = ("activity", "activity_scope", "activity_message", "activity_started_at", "activity_deadline_at")


class CaptureStopRequested(Exception):
    """当前执行在安全检查点结束。"""


def check_stop(should_stop=None):
    if should_stop and should_stop():
        raise CaptureStopRequested()


def interruptible_wait(seconds, should_stop=None):
    # 墙上时间只用于持久截止时间，实际等待按单调经过时间计算。
    end = time.monotonic() + max(0.0, seconds)
    while True:
        check_stop(should_stop)
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


class CaptureRuntime:
    def __init__(self, *, pacing=None, state=None, callback=None, should_stop=None, scope="list", scope_id=None):
        self.pacing = default_pacing(pacing)
        self.state = state if state is not None else {}
        self.callback = callback
        self.should_stop = should_stop
        self.scope = scope
        self.scope_id = scope_id

    def activity(self, name=None, message="", deadline=None, started=None):
        if self.callback:
            self.callback({
                "_activity_only": True,
                "_runtime_state": dict(self.state),
                "activity": name,
                "activity_scope": self.scope if name else None,
                "activity_message": message if name else "",
                "activity_started_at": (started or datetime.now(timezone.utc).isoformat()) if name else None,
                "activity_deadline_at": deadline if name else None,
                "scope_id": self.scope_id,
            })

    def arm(self, kind):
        # 每次网络采集结束只抽取一次；末项保存截止时间但不额外等待。
        seconds = random.uniform(self.pacing[f"{kind}_min_seconds"], self.pacing[f"{kind}_max_seconds"])
        now = datetime.now(timezone.utc)
        self.state[f"{kind}_cooldown"] = {
            "started_at": now.isoformat(),
            "deadline_at": (now + timedelta(seconds=seconds)).isoformat(),
        }
        self.activity()

    def before_network(self, kind):
        check_stop(self.should_stop)
        saved = self.state.get(f"{kind}_cooldown")
        if saved:
            remaining = (datetime.fromisoformat(saved["deadline_at"]) - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                self.activity("cooling", "等待下一次采集", saved["deadline_at"], saved["started_at"])
                interruptible_wait(remaining, self.should_stop)
            self.state.pop(f"{kind}_cooldown", None)
            self.activity()
        check_stop(self.should_stop)

    def wait(self, seconds):
        interruptible_wait(seconds, self.should_stop)
