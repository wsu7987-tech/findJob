from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from backend.app.services.fine_job import boss_network_debug
from backend.app.services.fine_job.boss_network_trace import (
    NetworkTraceCapture,
    adapt_sender_dry_run_normalized,
    adapt_trace_for_sender_dry_run_diff,
    candidate_http_requests_from_events,
    decode_mqtt_frame,
    diff_normalized_traces,
    filter_trace_marker_range,
    filter_trace_marker_window,
    render_candidate_http_report,
)
from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine
from backend.app.services.fine_job.boss_scraper import boss_job_detail
from backend.app.services.fine_job.boss_scraper.service import BossScraperService


def _memory_cdp(*, limit: int = 4096):
    cdp = object.__new__(engine.CDPSession)
    cdp.EVENT_BUFFER_LIMIT = limit
    cdp.events = []
    cdp._event_sequence = 0
    cdp._event_dropped = 0
    cdp._cursor_overflow = 0
    cdp._pending_responses = {}
    return cdp


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        encoded.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(encoded)


def _field_varint(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _field_bytes(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _techwolf_fixture() -> bytes:
    sender = _field_varint(1, 12345678)
    receiver = _field_varint(1, 87654321)
    body = _field_varint(1, 1) + _field_bytes(3, "敏感聊天正文".encode("utf-8"))
    message = b"".join(
        [
            _field_bytes(1, sender),
            _field_bytes(2, receiver),
            _field_varint(3, 1),
            _field_varint(4, 111),
            _field_bytes(6, body),
            _field_varint(11, 111),
            _field_bytes(19, b"security-sensitive-value"),
        ]
    )
    sync = _field_varint(1, 111) + _field_varint(2, 222)
    return _field_varint(1, 1) + _field_bytes(3, message) + _field_bytes(7, sync)


def _remaining_length(value: int) -> bytes:
    return _varint(value)


def _mqtt_publish(payload: bytes, *, packet_id: int = 7) -> bytes:
    topic = b"chat"
    body = len(topic).to_bytes(2, "big") + topic + packet_id.to_bytes(2, "big") + payload
    # PUBLISH + DUP + QoS 1 + retain
    return bytes([0x3B]) + _remaining_length(len(body)) + body


# 固定二进制 fixture：MQTT 3.1.1 QoS 0 PUBLISH(topic=chat, payload=08 01)。
# 该字节序列不调用本测试的编码 helper 生成，用于观察解码边界而非 Native Evidence。
FIXED_MQTT_TECHWOLF_FIXTURE = b"\x30\x08\x00\x04chat\x08\x01"


def _event(method: str, *, sequence: int, session_id: str = "session-1", **params):
    return {
        "method": method,
        "sessionId": session_id,
        "params": params,
        "_finejobSequence": sequence,
    }


def test_event_cursor_supports_independent_consumers_and_bounded_diagnostics() -> None:
    cdp = _memory_cdp(limit=2)
    first_cursor = cdp.create_event_cursor()
    second_cursor = cdp.create_event_cursor()
    cdp._append_event({"method": "Network.requestWillBeSent", "sessionId": "session-1"})
    first, first_cursor = cdp.events_since(first_cursor)
    second, second_cursor = cdp.events_since(second_cursor)

    assert [item["_finejobSequence"] for item in first] == [1]
    assert [item["_finejobSequence"] for item in second] == [1]
    cdp._append_event({"method": "Network.webSocketFrameSent", "sessionId": "session-1"})
    cdp._append_event({"method": "Network.webSocketFrameReceived", "sessionId": "session-1"})

    first, first_cursor = cdp.events_since(first_cursor)
    assert [item["_finejobSequence"] for item in first] == [2, 3]
    assert first_cursor == 3
    assert cdp.event_buffer_diagnostics() == {
        "capacity": 2,
        "buffered": 2,
        "latest_sequence": 3,
        "dropped_events": 1,
        "cursor_overflow_events": 0,
    }
    stale, _ = cdp.events_since(0)
    assert [item["_finejobSequence"] for item in stale] == [2, 3]
    assert cdp.event_buffer_diagnostics()["cursor_overflow_events"] == 1


def test_command_responses_and_events_are_routed_separately() -> None:
    cdp = _memory_cdp()
    event = {"method": "Network.webSocketClosed", "sessionId": "session-1"}
    response = {"id": 9, "result": {"ok": True}}

    assert cdp._route_message(event) == "event"
    assert cdp._route_message(response) == "response"
    assert cdp.events == [{**event, "_finejobSequence": 1}]
    assert cdp._pending_responses == {9: response}


def test_job_and_friend_captures_read_same_buffer_without_competing() -> None:
    cdp = _memory_cdp()
    cdp.send = lambda method, params=None, sid=None, timeout=30: {
        "result": {"body": json.dumps({"source": params["requestId"]})}
    }
    cdp.drain_events = lambda duration: None
    jobs = engine.NetworkJoblistCapture(cdp, "session-1")
    friends = engine.NetworkChatFriendListCapture(cdp, "session-1")
    for event in [
        _event(
            "Network.requestWillBeSent",
            sequence=1,
            requestId="job-1",
            request={"url": "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"},
        ),
        _event("Network.loadingFinished", sequence=2, requestId="job-1"),
        _event(
            "Network.requestWillBeSent",
            sequence=3,
            requestId="friend-1",
            request={"url": "https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json"},
        ),
        _event("Network.loadingFinished", sequence=4, requestId="friend-1"),
        _event("Network.loadingFinished", sequence=5, session_id="session-2", requestId="job-foreign"),
    ]:
        cdp._append_event({key: value for key, value in event.items() if key != "_finejobSequence"})

    assert jobs.wait_next_response(timeout=0.1) == {"source": "job-1"}
    assert friends.wait_next_response(timeout=0.1) == {"source": "friend-1"}
    assert len(cdp.events) == 5


def test_detail_capture_uses_incremental_session_filtered_events() -> None:
    cdp = _memory_cdp()
    cdp._append_event(_event("Network.loadingFinished", sequence=1, session_id="session-2", requestId="old"))
    original_count = len(cdp.events)

    def send(method, params=None, sid=None, timeout=30):
        if method == "Page.navigate":
            cdp._append_event({
                "method": "Network.responseReceived",
                "sessionId": "session-1",
                "params": {
                    "requestId": "detail-1",
                    "response": {"url": params["url"], "mimeType": "text/html"},
                },
            })
            cdp._append_event({
                "method": "Network.loadingFinished",
                "sessionId": "session-1",
                "params": {"requestId": "detail-1"},
            })
        if method == "Network.getResponseBody":
            return {"result": {"body": "<html>detail</html>"}}
        return {"result": {}}

    cdp.send = send
    cdp.drain_events = lambda duration: None
    url, body = boss_job_detail._capture_detail_html(
        cdp,
        "session-1",
        "https://www.zhipin.com/job_detail/example.html",
    )

    assert url.endswith("example.html")
    assert body == "<html>detail</html>"
    assert len(cdp.events) == original_count + 2


def test_mqtt_and_techwolf_observation_keeps_layers_and_multi_packet_boundaries() -> None:
    mqtt_payload = _mqtt_publish(_techwolf_fixture()) + b"\x40\x02\x00\x07"
    decoded = decode_mqtt_frame(mqtt_payload)

    assert decoded["decodeStatus"] == "decoded"
    assert [packet["packetName"] for packet in decoded["packets"]] == ["PUBLISH", "PUBACK"]
    publish = decoded["packets"][0]
    assert publish["dup"] is True
    assert publish["qos"] == 1
    assert publish["retain"] is True
    assert publish["packetId"] == 7
    assert publish["packetEnd"] == decoded["packets"][1]["packetStart"]

    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.process_event(_event(
        "Network.webSocketCreated",
        sequence=1,
        requestId="ws-1",
        url="wss://ws6.zhipin.com/chatws?token=sensitive",
    ))
    trace.process_event(_event(
        "Network.webSocketFrameSent",
        sequence=2,
        requestId="ws-1",
        timestamp=12.5,
        response={"opcode": 2, "payloadData": base64.b64encode(mqtt_payload).decode("ascii")},
    ))
    frame = trace.export()["events"][-1]

    assert frame["rawEvidence"] == {
        "direction": "sent",
        "opcode": 2,
        "payloadLength": len(mqtt_payload),
        "payloadEncoding": "base64",
        "payloadData": base64.b64encode(mqtt_payload).decode("ascii"),
    }
    assert frame["mqtt"]["packets"][0]["topic"] == "chat"
    assert base64.b64decode(frame["mqtt"]["packets"][0]["rawPacketBase64"]) == mqtt_payload[:-4]
    assert base64.b64decode(frame["mqtt"]["packets"][0]["payloadBase64"]) == _techwolf_fixture()
    interpretation = frame["techwolf"][0]
    assert interpretation["decodeStatus"] == "decoded"
    assert interpretation["protocolType"] == 1
    assert interpretation["messages"][0]["body"]["bodyType"] == 1
    assert interpretation["messages"][0]["body"]["text"] == "敏感聊天正文"
    assert interpretation["messages"][0]["securityId"] == "security-sensitive-value"
    assert base64.b64decode(interpretation["rawProtobufBase64"]) == _techwolf_fixture()
    assert interpretation["messageSync"] == [{"clientMid": 111, "serverMid": 222}]


def test_observational_decoder_uses_fixed_binary_fixture_and_rejects_invalid_headers() -> None:
    decoded = decode_mqtt_frame(FIXED_MQTT_TECHWOLF_FIXTURE)

    assert decoded["decodeStatus"] == "decoded"
    assert decoded["packets"][0]["topic"] == "chat"
    assert decoded["packets"][0]["qos"] == 0
    assert decode_mqtt_frame(b"\x41\x02\x00\x01")["errors"] == [
        {"category": "mqtt_invalid_fixed_header_flags"}
    ]
    assert decode_mqtt_frame(b"\x30\x02\x00\x00")["errors"] == [
        {"category": "mqtt_topic_empty"}
    ]
    assert decode_mqtt_frame(b"\x40\x02\x00\x00")["errors"] == [
        {"category": "mqtt_packet_id_zero"}
    ]


def test_observational_decoder_strictly_validates_control_packet_fixtures() -> None:
    valid_connect = b"\x10\x0e\x00\x04MQTT\x04\x02\x00\x3c\x00\x02id"
    valid_connack = b"\x20\x02\x00\x00"
    valid_subscribe = b"\x82\x09\x00\x01\x00\x04chat\x01"
    valid_unsubscribe = b"\xa2\x08\x00\x01\x00\x04chat"

    assert [packet["packetName"] for packet in decode_mqtt_frame(valid_connect)["packets"]] == ["CONNECT"]
    assert decode_mqtt_frame(valid_connack)["packets"][0]["returnCode"] == 0
    assert decode_mqtt_frame(valid_subscribe)["packets"][0]["subscriptionCount"] == 1
    assert decode_mqtt_frame(valid_unsubscribe)["packets"][0]["subscriptionCount"] == 1
    assert decode_mqtt_frame(b"\x20\x00")["decodeStatus"] == "failed"
    assert decode_mqtt_frame(b"\x82\x00")["decodeStatus"] == "failed"
    assert decode_mqtt_frame(b"\xa2\x02\x00\x00")["decodeStatus"] == "failed"


@pytest.mark.parametrize(
    "payload",
    [
        b"\x10\x80\x80\x80\x80",
        b"\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c",
        b"\x10\x0e\x00\x04MQTx\x04\x02\x00\x3c\x00\x02id",
        b"\x10\x0e\x00\x04MQTT\x05\x02\x00\x3c\x00\x02id",
        b"\x10\x0e\x00\x04MQTT\x04\x01\x00\x3c\x00\x02id",
        b"\x20\x02\x01\x01",
        b"\x82\x09\x00\x00\x00\x04chat\x01",
        b"\xa2\x08\x00\x00\x00\x04chat",
        b"\x80\x09\x00\x01\x00\x04chat\x01",
    ],
)
def test_observational_decoder_keeps_connect_and_control_fail_closed(payload: bytes) -> None:
    assert decode_mqtt_frame(payload)["decodeStatus"] == "failed"


def test_bad_mqtt_keeps_raw_evidence_and_safe_failure() -> None:
    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.process_event(_event(
        "Network.webSocketFrameReceived",
        sequence=1,
        requestId="ws-1",
        response={"opcode": 2, "payloadData": base64.b64encode(b"\x30\x7f").decode("ascii")},
    ))
    frame = trace.export()["events"][0]

    assert frame["rawEvidence"]["direction"] == "received"
    assert frame["rawEvidence"]["payloadLength"] == 2
    assert frame["mqtt"]["decodeStatus"] == "failed"
    assert frame["mqtt"]["errors"] == [{"category": "mqtt_packet_end_overflow"}]


def test_protobuf_failure_keeps_frame_and_mqtt_evidence() -> None:
    mqtt_payload = _mqtt_publish(b"\x1a\x05\x08")
    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.process_event(_event(
        "Network.webSocketFrameReceived",
        sequence=1,
        requestId="ws-1",
        response={"opcode": 2, "payloadData": base64.b64encode(mqtt_payload).decode("ascii")},
    ))
    frame = trace.export()["events"][0]

    assert frame["rawEvidence"]["payloadLength"] == len(mqtt_payload)
    assert frame["mqtt"]["decodeStatus"] == "decoded"
    assert frame["mqtt"]["packets"][0]["topic"] == "chat"
    assert frame["techwolf"][0]["decodeStatus"] == "failed"


def test_http_raw_preservation_marker_window_candidates_and_capacity() -> None:
    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.MAX_RECORDS = 2
    trace.mark("before_apply")
    trace.process_event(_event(
        "Network.requestWillBeSent",
        sequence=1,
        requestId="http-1",
        timestamp=1.0,
        type="XHR",
        request={
            "url": "https://www.zhipin.com/wapi/action?securityId=secret&token=secret",
            "method": "POST",
            "postData": json.dumps({"encryptJobId": "secret", "message": "private"}),
            "headers": {"Cookie": "secret", "Authorization": "secret"},
        },
    ))
    trace.process_event(_event(
        "Network.responseReceived",
        sequence=2,
        requestId="http-1",
        timestamp=1.1,
        type="XHR",
        response={"url": "https://www.zhipin.com/wapi/action?securityId=secret", "status": 200},
    ))
    trace.process_event(
        _event("Network.loadingFinished", sequence=3, requestId="http-1", timestamp=1.2),
        response_body={"code": 0, "message": "sensitive response"},
    )
    candidates = trace.candidate_http_requests("before_apply", before_ms=1000, after_ms=1000)
    assert candidates["candidates"][0]["path"] == "/wapi/action"
    assert candidates["candidates"][0]["queryKeys"] == ["securityId", "token"]
    assert candidates["candidates"][0]["requestBodyKeys"] == ["encryptJobId", "message"]
    assert candidates["candidates"][0]["businessCode"] == 0
    http_event = trace.export()["events"][0]
    assert http_event["url"] == "https://www.zhipin.com/wapi/action?securityId=secret&token=secret"
    assert http_event["query"] == {"securityId": ["secret"], "token": ["secret"]}
    assert http_event["requestHeaders"] == {"Cookie": "secret", "Authorization": "secret"}
    assert http_event["requestBody"] == json.dumps({"encryptJobId": "secret", "message": "private"})
    assert http_event["responseBody"] == {"code": 0, "message": "sensitive response"}
    trace.mark("after_apply")
    ranged = filter_trace_marker_range(
        trace.export(),
        "before_apply",
        "after_apply",
        before_ms=1000,
        after_ms=1000,
    )
    report = render_candidate_http_report(
        "before_apply",
        candidate_http_requests_from_events(ranged),
    )
    assert "Method: POST" in report["report"]
    assert "Path: /wapi/action" in report["report"]
    trace.process_event(_event(
        "Network.webSocketWillSendHandshakeRequest",
        sequence=4,
        requestId="ws-1",
        request={"headers": {"Cookie": "secret", "Authorization": "secret", "User-Agent": "safe"}},
    ))
    trace.process_event(_event("Network.webSocketClosed", sequence=5, requestId="ws-1"))
    exported = trace.export()

    assert exported["rawPayloadSaved"] is True
    assert exported["redactionEnabled"] is False
    assert exported["diagnostics"]["droppedTraceRecords"] == 1
    assert exported["evidenceComplete"] is False
    assert "droppedTraceRecords" in exported["gapReasons"]
    assert "secret" in json.dumps(exported, ensure_ascii=False)
    assert len(filter_trace_marker_window(exported, "before_apply", before_ms=1000, after_ms=1000)["events"]) == 2


def test_normalized_diff_ignores_dynamic_values_but_keeps_protocol_differences() -> None:
    expected = {"events": [{
        "traceId": "native",
        "sequence": 1,
        "requestId": "request-a",
        "mqtt": {"packets": [{"packetType": 3, "topic": "chat", "qos": 1, "retain": True, "dup": False, "packetId": 7}]},
        "techwolf": [{"protocolType": 1, "messages": [{"body": {"bodyType": 1}}]}],
    }]}
    dynamic_only = json.loads(json.dumps(expected))
    dynamic_only["events"][0].update({"traceId": "generated", "sequence": 99, "requestId": "request-b"})
    dynamic_only["events"][0]["mqtt"]["packets"][0]["packetId"] = 88
    assert diff_normalized_traces(expected, dynamic_only)["matches"] is True
    changed = json.loads(json.dumps(dynamic_only))
    changed["events"][0]["mqtt"]["packets"][0]["topic"] = "other"
    result = diff_normalized_traces(expected, changed)
    assert result["matches"] is False
    assert any(item["path"].endswith(".topic") for item in result["mismatches"])


def test_normalized_diff_keeps_raw_trace_inputs_unchanged() -> None:
    expected = {
        "events": [{
            "requestId": "request-session-1",
            "rawEvidence": {"payloadData": "raw-token-value"},
            "mqtt": {"packets": [{"packetId": 7, "rawPacketBase64": "AQI="}]},
        }],
    }
    actual = json.loads(json.dumps(expected))
    expected_before = json.loads(json.dumps(expected))
    actual_before = json.loads(json.dumps(actual))

    diff_normalized_traces(expected, actual)

    assert expected == expected_before
    assert actual == actual_before


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("events", 1, "mqtt", "packets", 0, "packetId"), 8),
        (("events", 1, "techwolf", 0, "messageSync", 0, "clientMid"), 333),
        (("events", 2, "techwolf", 0, "messageSync", 0, "serverMid"), 444),
        (("events", 1, "requestId"), "ws-other"),
        (("events", 1, "sessionId"), "session-other"),
    ],
)
def test_normalized_diff_preserves_dynamic_identifier_correlations(path, replacement) -> None:
    expected = {"events": [
        {
            "requestId": "ws-1", "sessionId": "session-1",
            "mqtt": {"packets": [{"packetType": 3, "packetId": 7}]},
            "techwolf": [{"messageSync": [{"clientMid": 111, "serverMid": 222}]}],
        },
        {
            "requestId": "ws-1", "sessionId": "session-1",
            "mqtt": {"packets": [{"packetType": 4, "packetId": 7}]},
            "techwolf": [{"messageSync": [{"clientMid": 111, "serverMid": 222}]}],
        },
        {
            "requestId": "ws-1", "sessionId": "session-1",
            "techwolf": [{"messageSync": [{"clientMid": 111, "serverMid": 222}]}],
        },
    ]}
    actual = json.loads(json.dumps(expected))
    target = actual
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement

    assert diff_normalized_traces(expected, actual)["matches"] is False


def test_normalized_diff_correlates_message_sync_server_mid_with_remote_message_mid() -> None:
    expected = {"events": [
        {"techwolf": [{"messageSync": [{"serverMid": 222}]}]},
        {"techwolf": [{"messages": [{"mid": 222}]}]},
    ]}
    matching = json.loads(json.dumps(expected))
    mismatching = json.loads(json.dumps(expected))
    mismatching["events"][0]["techwolf"][0]["messageSync"][0]["serverMid"] = 333

    assert diff_normalized_traces(expected, matching)["matches"] is True
    assert diff_normalized_traces(expected, mismatching)["matches"] is False


def test_incomplete_evidence_blocks_diff_and_candidate_confidence() -> None:
    trace = {
        "markers": [{"name": "before_apply", "timeOffsetMs": 1}],
        "events": [{"transport": "http", "method": "POST", "timeOffsetMs": 1}],
        "diagnostics": {"droppedTraceRecords": 1, "cdpEventBuffer": {"dropped_events": 1}},
    }
    window = filter_trace_marker_window(trace, "before_apply")
    candidates = candidate_http_requests_from_events(window)
    report = render_candidate_http_report("before_apply", candidates)
    comparison = diff_normalized_traces(trace, trace)

    assert window["evidenceComplete"] is False
    assert {"droppedTraceRecords", "dropped_events"}.issubset(window["gapReasons"])
    assert candidates["candidates"][0]["confidence"] == "MEDIUM"
    assert report["evidenceComplete"] is False
    assert comparison["matches"] is False
    assert comparison["nativeEvidenceUpgradeAllowed"] is False


def test_metadata_eviction_propagates_evidence_gap_through_all_trace_consumers(tmp_path: Path) -> None:
    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.MAX_REQUESTS = 1
    trace.MAX_WEB_SOCKETS = 1
    trace.mark("before_apply")
    trace.process_event(_event(
        "Network.requestWillBeSent", sequence=1, requestId="old",
        request={"url": "https://www.zhipin.com/wapi/old", "method": "GET"},
    ))
    trace.process_event(_event(
        "Network.requestWillBeSent", sequence=2, requestId="apply",
        request={"url": "https://www.zhipin.com/wapi/apply", "method": "POST"},
    ))
    trace.process_event(_event("Network.loadingFinished", sequence=3, requestId="apply"))
    trace.process_event(_event("Network.webSocketCreated", sequence=4, requestId="ws-old", url="wss://a/chat"))
    trace.process_event(_event("Network.webSocketCreated", sequence=5, requestId="ws-new", url="wss://a/chat"))
    trace.mark("after_apply")
    exported = trace.export(cdp_diagnostics={"droppedRequestMeta": 1})

    expected_gaps = {"droppedRequestMetadata", "droppedWebSocketMetadata", "droppedRequestMeta"}
    window = filter_trace_marker_window(exported, "before_apply")
    ranged = filter_trace_marker_range(exported, "before_apply", "after_apply")
    candidates = candidate_http_requests_from_events(window)
    comparison = diff_normalized_traces(exported, exported)
    adapter = adapt_trace_for_sender_dry_run_diff(exported)
    trace_path = tmp_path / "metadata-gap.json"
    trace_path.write_text(json.dumps(exported), encoding="utf-8")
    service = BossScraperService()

    assert exported["evidenceComplete"] is False
    assert expected_gaps.issubset(exported["gapReasons"])
    assert window["evidenceComplete"] is False
    assert expected_gaps.issubset(window["gapReasons"])
    assert ranged["evidenceComplete"] is False
    assert candidates["candidates"][0]["confidence"] == "MEDIUM"
    assert comparison["matches"] is False
    assert comparison["nativeEvidenceUpgradeAllowed"] is False
    assert adapter["evidenceComplete"] is False
    assert adapter["nativeEvidenceUpgradeAllowed"] is False
    assert service.read_network_trace_window(trace_path, "before_apply")["evidenceComplete"] is False
    assert service.read_network_trace_range(trace_path, "before_apply", "after_apply")["evidenceComplete"] is False
    assert service.build_network_trace_candidate_report(trace_path, "before_apply")["evidenceComplete"] is False
    assert service.diff_network_traces(trace_path, trace_path)["matches"] is False


def test_sender_dry_run_adapter_projects_to_trace_diff_shape() -> None:
    sender = adapt_sender_dry_run_normalized({
        "transport": "mqtt", "topic": "chat", "qos": 1, "retain": True, "dup": False,
        "techwolf": {"protocolType": 1, "messages": [{"clientMid": "111", "bodyType": 1}]},
    })
    trace = adapt_trace_for_sender_dry_run_diff({
        "events": [{
            "rawEvidence": {"direction": "sent"},
            "mqtt": {"packets": [{"packetType": 3, "packetIndex": 0, "topic": "chat", "qos": 1, "retain": True, "dup": False}]},
            "techwolf": [{"packetIndex": 0, "protocolType": 1, "messages": [{"clientMid": "111", "body": {"bodyType": 1}}]}],
        }],
    })

    assert diff_normalized_traces(sender, trace)["matches"] is True


def test_sender_dry_run_adapter_rejects_empty_input_and_missing_sent_publish() -> None:
    sender = adapt_sender_dry_run_normalized({})
    trace = adapt_trace_for_sender_dry_run_diff({"events": []})
    comparison = diff_normalized_traces(sender, trace)

    assert sender["evidenceComplete"] is False
    assert "sender_dry_run_input_missing" in sender["gapReasons"]
    assert trace["evidenceComplete"] is False
    assert "sent_publish_missing" in trace["gapReasons"]
    assert comparison["matches"] is False
    assert comparison["nativeEvidenceUpgradeAllowed"] is False


def test_raw_trace_preserves_path_topic_and_business_code_identifiers() -> None:
    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.process_event(_event(
        "Network.requestWillBeSent", sequence=1, requestId="request-1",
        request={"url": "https://www.zhipin.com/wapi/a1/security-token/xy9", "method": "POST"},
    ))
    trace.process_event(_event(
        "Network.loadingFinished", sequence=2, requestId="request-1"),
        response_body={"businessCode": "token-A1"},
    )
    mqtt = b"\x30\x0d\x00\x09chat/a1/x\x08\x01"
    trace.process_event(_event(
        "Network.webSocketFrameSent", sequence=3, requestId="ws-1",
        response={"opcode": 2, "payloadData": base64.b64encode(mqtt).decode("ascii")},
    ))
    exported = trace.export()
    serialized = json.dumps(exported, ensure_ascii=False)

    assert "/wapi/a1/security-token/xy9" in serialized
    assert "chat/a1/x" in serialized
    assert "token-A1" in serialized
    assert exported["events"][0]["businessCode"] == "token-A1"


def test_auxiliary_metadata_is_cleaned_or_evicted_with_diagnostics() -> None:
    trace = NetworkTraceCapture({"session-1": "target-1"})
    trace.MAX_REQUESTS = 1
    trace.MAX_WEB_SOCKETS = 1
    trace.process_event(_event("Network.requestWillBeSent", sequence=1, requestId="one", request={"url": "https://a/x", "method": "GET"}))
    trace.process_event(_event("Network.requestWillBeSent", sequence=2, requestId="two", request={"url": "https://a/x", "method": "GET"}))
    trace.process_event(_event("Network.loadingFinished", sequence=3, requestId="two"))
    trace.process_event(_event("Network.webSocketCreated", sequence=4, requestId="ws-one", url="wss://a/chat"))
    trace.process_event(_event("Network.webSocketCreated", sequence=5, requestId="ws-two", url="wss://a/chat"))
    trace.process_event(_event("Network.webSocketClosed", sequence=6, requestId="ws-two"))
    exported = trace.export()
    diagnostics = exported["diagnostics"]

    assert trace.requests == {}
    assert trace.web_sockets == {}
    assert diagnostics["droppedRequestMetadata"] == 1
    assert diagnostics["droppedWebSocketMetadata"] == 1
    assert exported["evidenceComplete"] is False
    assert {"droppedRequestMetadata", "droppedWebSocketMetadata"}.issubset(exported["gapReasons"])


class _RunCDP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.closed = False

    def send(self, method, params=None, sid=None, timeout=30):
        self.calls.append((method, sid))
        if method == "Network.getResponseBody":
            return {"result": {"body": json.dumps({"code": 0, "private": "content"})}}
        return {"result": {}}

    def event_buffer_diagnostics(self):
        return {"capacity": 4096, "buffered": 0, "latest_sequence": 3, "dropped_events": 0, "cursor_overflow_events": 0}

    def drain_events(self, duration):
        return None

    def events_since(self, cursor, methods=None):
        return [], 3

    def close(self):
        self.closed = True


class _StartCDP(_RunCDP):
    def __init__(self) -> None:
        super().__init__()
        self.events = []
        self._cursor = 0

    def create_event_cursor(self):
        return self._cursor

    def send(self, method, params=None, sid=None, timeout=30):
        self.calls.append((method, sid))
        if method == "Target.getTargets":
            return {
                "result": {
                    "targetInfos": [{
                        "targetId": "target-1",
                        "type": "page",
                        "url": "https://www.zhipin.com/web/geek/chat",
                        "title": "chat",
                    }]
                }
            }
        return {"result": {}}


class _IdleThread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def test_boss_network_debug_start_reuses_target_attach_and_network_enable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cdp = _StartCDP()
    monkeypatch.setattr(boss_network_debug.engine, "CDPSession", lambda port: cdp)
    monkeypatch.setattr(
        boss_network_debug.engine,
        "attach_page_session",
        lambda used_cdp, target_id: "session-1",
    )
    monkeypatch.setattr(boss_network_debug.threading, "Thread", _IdleThread)
    run = boss_network_debug.BossNetworkDebugRun(tmp_path, cdp_port=9333)

    run.start()

    assert ("Target.getTargets", None) in cdp.calls
    assert ("Network.enable", "session-1") in cdp.calls
    assert run.sessions == {"session-1": "target-1"}
    assert run.trace is not None
    assert run.thread.started is True


def test_boss_network_debug_run_reuses_lifecycle_and_writes_normalized_output(tmp_path: Path) -> None:
    run = boss_network_debug.BossNetworkDebugRun(tmp_path)
    run.cdp = _RunCDP()
    run.sessions = {"session-1": "target-1"}
    run.targets = [{"targetId": "target-1", "url": "https://www.zhipin.com/web/geek/chat?token=secret", "title": "chat"}]
    run.trace = NetworkTraceCapture(run.sessions)
    run.started_at = "start"
    run._process_event(_event(
        "Network.requestWillBeSent",
        sequence=1,
        requestId="http-1",
        type="XHR",
        request={"url": "https://www.zhipin.com/wapi/test?token=secret", "method": "GET"},
    ))
    run._process_event(_event(
        "Network.responseReceived",
        sequence=2,
        requestId="http-1",
        type="XHR",
        response={"url": "https://www.zhipin.com/wapi/test", "status": 200, "mimeType": "application/json"},
    ))
    run._process_event(_event("Network.loadingFinished", sequence=3, requestId="http-1"))
    cdp = run.cdp
    run._finish()

    assert ("Network.disable", "session-1") in cdp.calls
    assert cdp.closed is True
    assert run.output_path is not None
    payload = json.loads(run.output_path.read_text(encoding="utf-8"))
    assert payload["version"] == 5
    assert payload["events"][0]["businessCode"] == 0
    assert payload["evidenceComplete"] is True
    assert payload["targets"][0]["url"] == "https://www.zhipin.com/web/geek/chat?token=secret"
    assert "content" in json.dumps(payload)


def test_final_serialized_trace_preserves_adversarial_identifiers_and_target_title(tmp_path: Path) -> None:
    run = boss_network_debug.BossNetworkDebugRun(tmp_path)
    run.cdp = _RunCDP()
    run.sessions = {"session-token-value": "target-token-value"}
    run.targets = [{
        "targetId": "target-token-value",
        "url": "https://www.zhipin.com/web/geek/chat?token=secret",
        "title": "securityId abc123 tokenXYZ",
    }]
    run.trace = NetworkTraceCapture(run.sessions)
    run.started_at = "start"
    run._process_event(_event(
        "Network.webSocketFrameSent",
        sequence=1,
        session_id="session-token-value",
        requestId="request-token-value",
        response={"opcode": 2, "payloadData": base64.b64encode(FIXED_MQTT_TECHWOLF_FIXTURE).decode("ascii")},
    ))
    run._finish()

    payload = json.loads(run.output_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "token-value" in serialized
    assert "tokenXYZ" in serialized
    assert "securityId abc123" in serialized
    assert payload["targets"][0]["title"] == "securityId abc123 tokenXYZ"
    assert payload["events"][0]["mqtt"]["packets"][0]["topic"] == "chat"


def test_final_serialized_trace_preserves_session_only_identifiers(tmp_path: Path) -> None:
    run = boss_network_debug.BossNetworkDebugRun(tmp_path)
    run.cdp = _RunCDP()
    run.sessions = {"session-only-id": "session-only-target"}
    run.targets = [{
        "targetId": "session-only-target",
        "url": "https://www.zhipin.com/web/geek/chat",
        "title": "chat",
    }]
    run.trace = NetworkTraceCapture(run.sessions)
    run.started_at = "start"
    run._process_event(_event(
        "Network.webSocketFrameSent",
        sequence=1,
        session_id="session-only-id",
        requestId="session-only-request",
        response={"opcode": 2, "payloadData": base64.b64encode(FIXED_MQTT_TECHWOLF_FIXTURE).decode("ascii")},
    ))
    run._finish()

    payload = json.loads(run.output_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "session-only-target" in serialized
    assert "session-only-id" in serialized
    assert "session-only-request" in serialized
    assert payload["gapReasons"] == []
    assert payload["events"][0]["rawEvidence"]["payloadData"] == base64.b64encode(
        FIXED_MQTT_TECHWOLF_FIXTURE
    ).decode("ascii")


def test_debug_request_meta_eviction_marks_output_and_status_incomplete(tmp_path: Path) -> None:
    run = boss_network_debug.BossNetworkDebugRun(tmp_path)
    run.MAX_REQUEST_META = 1
    run.cdp = _RunCDP()
    run.sessions = {"session-1": "target-1"}
    run.trace = NetworkTraceCapture(run.sessions)
    run.started_at = "start"
    run._process_event(_event(
        "Network.requestWillBeSent", sequence=1, requestId="one",
        request={"url": "https://www.zhipin.com/wapi/one", "method": "GET"},
    ))
    run._process_event(_event(
        "Network.requestWillBeSent", sequence=2, requestId="two",
        request={"url": "https://www.zhipin.com/wapi/two", "method": "GET"},
    ))
    run._finish()

    payload = json.loads(run.output_path.read_text(encoding="utf-8"))
    status = run.snapshot()

    assert payload["diagnostics"]["cdpEventBuffer"]["droppedRequestMeta"] == 1
    assert payload["evidenceComplete"] is False
    assert "droppedRequestMeta" in payload["gapReasons"]
    assert status["evidence_complete"] is False
    assert "droppedRequestMeta" in status["gap_reasons"]


class _TailDrainCDP(_RunCDP):
    def __init__(self) -> None:
        super().__init__()
        self.events = [
            _event(
                "Network.requestWillBeSent", sequence=1, requestId="http-1", type="XHR",
                request={"url": "https://www.zhipin.com/wapi/test", "method": "GET"},
            ),
            _event(
                "Network.responseReceived", sequence=2, requestId="http-1", type="XHR",
                response={"url": "https://www.zhipin.com/wapi/test", "mimeType": "application/json"},
            ),
            _event("Network.loadingFinished", sequence=3, requestId="http-1"),
        ]
        self._body_read = False

    def send(self, method, params=None, sid=None, timeout=30):
        self.calls.append((method, sid))
        if method == "Network.getResponseBody" and not self._body_read:
            self._body_read = True
            self.events.append(_event(
                "Network.webSocketFrameReceived", sequence=4, requestId="ws-1",
                response={"opcode": 2, "payloadData": base64.b64encode(FIXED_MQTT_TECHWOLF_FIXTURE).decode("ascii")},
            ))
            return {"result": {"body": json.dumps({"code": 0})}}
        return {"result": {}}

    def events_since(self, cursor, methods=None):
        selected = [event for event in self.events if event["_finejobSequence"] > cursor]
        return selected, len(self.events)

    def event_buffer_diagnostics(self):
        return {
            "capacity": 4096,
            "buffered": len(self.events),
            "latest_sequence": len(self.events),
            "dropped_events": 0,
            "cursor_overflow_events": 0,
        }


def test_stop_tail_drain_consumes_events_arriving_during_response_body_read(tmp_path: Path) -> None:
    run = boss_network_debug.BossNetworkDebugRun(tmp_path)
    run.cdp = _TailDrainCDP()
    run.sessions = {"session-1": "target-1"}
    run.trace = NetworkTraceCapture(run.sessions)
    run.started_at = "start"

    run._finish()

    payload = json.loads(run.output_path.read_text(encoding="utf-8"))
    assert [event.get("event") for event in payload["events"]] == [None, "frame"]
    assert payload["evidenceComplete"] is True
    assert "tail_drain_unconfirmed" not in payload["gapReasons"]


class _ContinuousTailCDP(_RunCDP):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, object]] = []

    def drain_events(self, duration):
        sequence = len(self.events) + 1
        self.events.append(_event(
            "Network.webSocketFrameReceived",
            sequence=sequence,
            requestId=f"ws-{sequence}",
            response={"opcode": 2, "payloadData": base64.b64encode(FIXED_MQTT_TECHWOLF_FIXTURE).decode("ascii")},
        ))

    def events_since(self, cursor, methods=None):
        selected = [event for event in self.events if event["_finejobSequence"] > cursor]
        return selected, len(self.events)

    def event_buffer_diagnostics(self):
        return {
            "capacity": 4096,
            "buffered": len(self.events),
            "latest_sequence": len(self.events),
            "dropped_events": 0,
            "cursor_overflow_events": 0,
        }


def test_stop_tail_drain_rejects_continuous_event_arrival(tmp_path: Path) -> None:
    run = boss_network_debug.BossNetworkDebugRun(tmp_path)
    run.cdp = _ContinuousTailCDP()
    run.sessions = {"session-1": "target-1"}
    run.trace = NetworkTraceCapture(run.sessions)
    run.started_at = "start"

    run._finish()

    payload = json.loads(run.output_path.read_text(encoding="utf-8"))
    assert payload["evidenceComplete"] is False
    assert "tail_drain_unconfirmed" in payload["gapReasons"]
