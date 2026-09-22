from __future__ import annotations

from queue import Empty

import pytest

from backend.app.services.fine_job.smart_capture_events import SmartCaptureEventBroker


def test_capture_stream_keeps_only_latest_snapshot() -> None:
    broker = SmartCaptureEventBroker()
    subscriber = broker.subscribe("capture-a")

    broker.publish("capture-a", {"smart_capture_id": "capture-a", "state_version": 1})
    broker.publish("capture-a", {"smart_capture_id": "capture-a", "state_version": 2})

    assert subscriber.get_nowait()["state_version"] == 2
    with pytest.raises(Empty):
        subscriber.get_nowait()


def test_current_pointer_stream_is_separate_from_capture_stream() -> None:
    broker = SmartCaptureEventBroker()
    capture_subscriber = broker.subscribe("capture-a")
    current_subscriber = broker.subscribe_current()
    snapshot = {"smart_capture_id": "capture-a", "state_version": 3}

    broker.publish("capture-a", snapshot)

    assert capture_subscriber.get_nowait() == snapshot
    with pytest.raises(Empty):
        current_subscriber.get_nowait()

    broker.publish_current(snapshot)
    assert current_subscriber.get_nowait() == snapshot
