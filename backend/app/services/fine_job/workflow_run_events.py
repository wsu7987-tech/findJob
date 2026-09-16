from __future__ import annotations

from queue import Empty, Queue
from threading import RLock
from typing import Any


class WorkflowRunEventBroker:
    """向连接中的驾驶舱推送指定 Workflow Run 的最新快照。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Queue[dict[str, object]]]] = {}
        self._lock = RLock()

    def subscribe(self, workflow_run_id: str) -> Queue[dict[str, object]]:
        subscriber: Queue[dict[str, object]] = Queue(maxsize=1)
        with self._lock:
            self._subscribers.setdefault(workflow_run_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, workflow_run_id: str, subscriber: Queue[dict[str, object]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(workflow_run_id)
            if subscribers is None:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(workflow_run_id, None)

    def publish(self, workflow_run_id: str, snapshot: dict[str, object]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(workflow_run_id, set()))
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(snapshot)
            except Exception:
                # 队列只保留最新状态，慢连接下一次读取时仍可得到当前快照。
                try:
                    subscriber.get_nowait()
                except Empty:
                    pass
                try:
                    subscriber.put_nowait(snapshot)
                except Exception:
                    continue


workflow_run_event_broker = WorkflowRunEventBroker()
