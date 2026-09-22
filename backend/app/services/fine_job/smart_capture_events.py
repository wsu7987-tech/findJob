from __future__ import annotations

from queue import Empty, Full, Queue
from threading import RLock


class SmartCaptureEventBroker:
    """向指定 Smart Capture 和 current 指针订阅者推送最新快照。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Queue[dict[str, object]]]] = {}
        self._current_subscribers: set[Queue[dict[str, object]]] = set()
        self._lock = RLock()

    def subscribe(self, smart_capture_id: str) -> Queue[dict[str, object]]:
        subscriber: Queue[dict[str, object]] = Queue(maxsize=1)
        with self._lock:
            self._subscribers.setdefault(smart_capture_id, set()).add(subscriber)
        return subscriber

    def subscribe_current(self) -> Queue[dict[str, object]]:
        subscriber: Queue[dict[str, object]] = Queue(maxsize=1)
        with self._lock:
            self._current_subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, smart_capture_id: str, subscriber: Queue[dict[str, object]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(smart_capture_id)
            if subscribers is None:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(smart_capture_id, None)

    def unsubscribe_current(self, subscriber: Queue[dict[str, object]]) -> None:
        with self._lock:
            self._current_subscribers.discard(subscriber)

    def publish(self, smart_capture_id: str, snapshot: dict[str, object]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(smart_capture_id, set()))
        self._publish_latest(subscribers, snapshot)

    def publish_current(self, snapshot: dict[str, object]) -> None:
        with self._lock:
            subscribers = list(self._current_subscribers)
        self._publish_latest(subscribers, snapshot)

    @staticmethod
    def _publish_latest(
        subscribers: list[Queue[dict[str, object]]],
        snapshot: dict[str, object],
    ) -> None:
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(snapshot)
            except Full:
                # 队列只保留最新状态，慢连接下一次读取时仍可得到当前快照。
                try:
                    subscriber.get_nowait()
                except Empty:
                    pass
                try:
                    subscriber.put_nowait(snapshot)
                except Full:
                    continue


smart_capture_event_broker = SmartCaptureEventBroker()
