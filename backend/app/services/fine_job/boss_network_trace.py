from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse


TRACE_MARKER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SENSITIVE_KEY_PARTS = {
    "authorization",
    "cookie",
    "password",
    "proxy_authorization",
    "resume",
    "securityid",
    "set_cookie",
    "encryptjobid",
    "token",
    "zp_token",
    "wt",
    "x_token",
}
DYNAMIC_DIFF_KEYS = {
    "capturedAt",
    "cdpTimestamp",
    "clientMid",
    "finishedAt",
    "markerTimestamp",
    "packetId",
    "requestId",
    "sequence",
    "serverMid",
    "sessionId",
    "startedAt",
    "targetId",
    "timeOffsetMs",
    "traceId",
    "ts",
}
TECHWOLF_OBSERVATION_TOPICS = {"chat"}
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:token|secret|security|session|authorization|credential|encrypt|password)",
    re.IGNORECASE,
)
FINAL_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:token|secret|security|session|authorization|credential|encrypt|password)",
    re.IGNORECASE,
)
TITLE_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:token|secret|security|session|authorization|credential|encrypt|password)",
    re.IGNORECASE,
)
STRUCTURAL_STRING_FIELDS = {"headerKeys", "queryKeys", "requestBodyKeys", "gapReasons"}
SEMANTIC_ALIAS_GROUPS = {
    "mid": "messageMid",
    "serverMid": "messageMid",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(category: str) -> dict[str, str]:
    return {"category": category}


def _mask_identifier(value: Any) -> Any:
    """保留 forensic Trace 中已解析的协议标识值。"""
    return value


def _looks_sensitive_identifier(value: str) -> bool:
    """识别路径、topic 和业务码中不应保留的标识值。"""
    text = value.strip()
    if not text:
        return False
    return bool(
        SENSITIVE_VALUE_PATTERN.search(text)
        or text.isdigit()
        or (any(char.isalpha() for char in text) and any(char.isdigit() for char in text))
    )


def _safe_path(path: str) -> str:
    segments = path.split("/")
    return "/".join(
        "<masked>" if _looks_sensitive_identifier(segment) else segment
        for segment in segments
    ) or "/"


def _url_metadata(url: Any) -> dict[str, Any]:
    parsed = urlparse(str(url or ""))
    return {
        "url": str(url or ""),
        "host": parsed.hostname or "",
        "path": parsed.path or "/",
        "queryKeys": sorted(parse_qs(parsed.query, keep_blank_values=True)),
        "query": parse_qs(parsed.query, keep_blank_values=True),
    }


def _body_keys(post_data: Any) -> list[str]:
    if not isinstance(post_data, str) or not post_data:
        return []
    try:
        parsed = json.loads(post_data)
    except (TypeError, ValueError):
        return sorted(parse_qs(post_data, keep_blank_values=True))
    return sorted(str(key) for key in parsed) if isinstance(parsed, dict) else []


def _business_code(body: Any) -> Any:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return None
    if not isinstance(body, dict):
        return None
    for key in ("code", "businessCode", "business_code"):
        value = body.get(key)
        if isinstance(value, (int, float, str, bool)) or value is None:
            if key in body:
                return value
    return None


def _header_keys(headers: Any) -> list[str]:
    if not isinstance(headers, dict):
        return []
    return sorted(str(key) for key in headers)


def _safe_topic(topic: str) -> str:
    return topic


def _final_safe_string(value: str, field_name: str) -> str:
    """在最终输出前遮蔽仍可能穿透字段级处理的敏感字符串。"""
    if field_name in STRUCTURAL_STRING_FIELDS:
        return value
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value):
        return "[已隐藏]"
    if re.search(r"(?<!\d)1\d{10}(?!\d)", value):
        return "[已隐藏]"
    pattern = TITLE_SENSITIVE_VALUE_PATTERN if field_name == "title" else FINAL_SENSITIVE_VALUE_PATTERN
    return "[已隐藏]" if pattern.search(value) else value


def sanitize_normalized_output(value: Any, *, field_name: str = "") -> Any:
    """递归处理 normalized JSON 的最终输出层，保留安全的协议结构。"""
    if isinstance(value, dict):
        return {
            key: sanitize_normalized_output(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_normalized_output(item, field_name=field_name) for item in value]
    if isinstance(value, str):
        return _final_safe_string(value, field_name)
    return value


class TraceDecodeError(ValueError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _read_varint(data: bytes, offset: int, *, max_bytes: int = 10) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(max_bytes):
        if offset >= len(data):
            raise TraceDecodeError("truncated_varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise TraceDecodeError("varint_too_long")


def _protobuf_fields(data: bytes) -> list[tuple[int, int, Any]]:
    fields: list[tuple[int, int, Any]] = []
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number <= 0:
            raise TraceDecodeError("invalid_protobuf_field")
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise TraceDecodeError("truncated_fixed64")
            value, offset = data[offset:end], end
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise TraceDecodeError("truncated_length_delimited")
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise TraceDecodeError("truncated_fixed32")
            value, offset = data[offset:end], end
        else:
            raise TraceDecodeError("unsupported_wire_type")
        fields.append((field_number, wire_type, value))
    return fields


def _first_varint(fields: list[tuple[int, int, Any]], field_number: int) -> int | None:
    for number, wire_type, value in fields:
        if number == field_number and wire_type == 0:
            return int(value)
    return None


def _decode_user(data: bytes) -> dict[str, Any]:
    fields = _protobuf_fields(data)
    uid = _first_varint(fields, 1)
    return {"uid": _mask_identifier(uid)}


def _decode_body(data: bytes) -> dict[str, Any]:
    fields = _protobuf_fields(data)
    result: dict[str, Any] = {"bodyType": _first_varint(fields, 1)}
    text_values = [value for number, wire_type, value in fields if number == 3 and wire_type == 2]
    if text_values:
        result["text"] = text_values[0].decode("utf-8", errors="replace")
    return result


def _decode_message(data: bytes) -> dict[str, Any]:
    fields = _protobuf_fields(data)
    result: dict[str, Any] = {
        "messageType": _first_varint(fields, 3),
        "mid": _first_varint(fields, 4),
        "clientMid": _first_varint(fields, 11),
    }
    for number, wire_type, value in fields:
        if wire_type != 2:
            continue
        if number == 1:
            result["from"] = _decode_user(value)
        elif number == 2:
            result["to"] = _decode_user(value)
        elif number == 6:
            result["body"] = _decode_body(value)
        elif number == 17:
            result["bizId"] = value.decode("utf-8", errors="replace")
        elif number == 19:
            result["securityId"] = value.decode("utf-8", errors="replace")
    return result


def _decode_message_sync(data: bytes) -> dict[str, Any]:
    fields = _protobuf_fields(data)
    return {
        "clientMid": _first_varint(fields, 1),
        "serverMid": _first_varint(fields, 2),
    }


def decode_techwolf(payload: bytes) -> dict[str, Any]:
    """按最小字段知识解释 Techwolf，不读取或返回聊天正文。"""
    try:
        fields = _protobuf_fields(payload)
        messages = [
            _decode_message(value)
            for number, wire_type, value in fields
            if number == 3 and wire_type == 2
        ]
        message_sync = [
            _decode_message_sync(value)
            for number, wire_type, value in fields
            if number == 7 and wire_type == 2
        ]
        return {
            "decodeStatus": "decoded",
            "protocolType": _first_varint(fields, 1),
            "messages": messages,
            "messageSync": message_sync,
        }
    except TraceDecodeError as exc:
        return {"decodeStatus": "failed", "error": _safe_error(exc.category)}


MQTT_PACKET_NAMES = {
    1: "CONNECT",
    2: "CONNACK",
    3: "PUBLISH",
    4: "PUBACK",
    5: "PUBREC",
    6: "PUBREL",
    7: "PUBCOMP",
    8: "SUBSCRIBE",
    9: "SUBACK",
    10: "UNSUBSCRIBE",
    11: "UNSUBACK",
    12: "PINGREQ",
    13: "PINGRESP",
    14: "DISCONNECT",
}


def _read_mqtt_binary(
    payload: bytes,
    cursor: int,
    packet_end: int,
    *,
    allow_empty: bool = False,
) -> tuple[bytes, int]:
    if cursor + 2 > packet_end:
        raise TraceDecodeError("mqtt_control_payload_truncated")
    size = int.from_bytes(payload[cursor:cursor + 2], "big")
    cursor += 2
    if (not allow_empty and size == 0) or cursor + size > packet_end:
        raise TraceDecodeError("mqtt_control_payload_invalid")
    return payload[cursor:cursor + size], cursor + size


def _read_mqtt_text(
    payload: bytes,
    cursor: int,
    packet_end: int,
    *,
    allow_empty: bool = False,
) -> tuple[str, int]:
    value, cursor = _read_mqtt_binary(
        payload,
        cursor,
        packet_end,
        allow_empty=allow_empty,
    )
    try:
        return value.decode("utf-8", errors="strict"), cursor
    except UnicodeDecodeError as exc:
        raise TraceDecodeError("mqtt_control_payload_invalid") from exc


def _read_mqtt_packet_id(payload: bytes, body_start: int, packet_end: int) -> int:
    if body_start + 2 > packet_end:
        raise TraceDecodeError("mqtt_control_packet_length")
    packet_id = int.from_bytes(payload[body_start:body_start + 2], "big")
    if packet_id == 0:
        raise TraceDecodeError("mqtt_packet_id_zero")
    return packet_id


def _validate_connect(payload: bytes, body_start: int, packet_end: int) -> None:
    if packet_end - body_start < 10:
        raise TraceDecodeError("mqtt_connect_structure")
    protocol_name, cursor = _read_mqtt_text(payload, body_start, packet_end)
    if protocol_name != "MQTT" or cursor + 4 > packet_end:
        raise TraceDecodeError("mqtt_connect_structure")
    protocol_level = payload[cursor]
    cursor += 1
    if protocol_level != 4:
        raise TraceDecodeError("mqtt_connect_structure")
    flags = payload[cursor]
    cursor += 1
    if flags & 0x01:
        raise TraceDecodeError("mqtt_connect_flags")
    will_enabled = bool(flags & 0x04)
    will_qos = (flags >> 3) & 0x03
    if (not will_enabled and (will_qos or flags & 0x20)) or will_qos == 3:
        raise TraceDecodeError("mqtt_connect_flags")
    if flags & 0x40 and not flags & 0x80:
        raise TraceDecodeError("mqtt_connect_flags")
    if cursor + 2 > packet_end:
        raise TraceDecodeError("mqtt_connect_structure")
    # Keep Alive 是 CONNECT 可变头的两个字节，之后才是 Client Identifier。
    cursor += 2
    _client_id, cursor = _read_mqtt_text(
        payload,
        cursor,
        packet_end,
        allow_empty=True,
    )
    if not _client_id and not flags & 0x02:
        raise TraceDecodeError("mqtt_connect_structure")
    if will_enabled:
        _will_topic, cursor = _read_mqtt_text(payload, cursor, packet_end)
        _will_message, cursor = _read_mqtt_binary(
            payload,
            cursor,
            packet_end,
            allow_empty=True,
        )
    if flags & 0x80:
        _username, cursor = _read_mqtt_text(payload, cursor, packet_end, allow_empty=True)
    if flags & 0x40:
        _password, cursor = _read_mqtt_binary(
            payload,
            cursor,
            packet_end,
            allow_empty=True,
        )
    if cursor != packet_end:
        raise TraceDecodeError("mqtt_connect_structure")


def _decode_mqtt_packets(payload: bytes) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    packets: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    offset = 0
    packet_index = 0
    while offset < len(payload):
        packet_start = offset
        try:
            header = payload[offset]
            offset += 1
            remaining_length, body_start = _read_varint(payload, offset, max_bytes=4)
            packet_end = body_start + remaining_length
            if packet_end > len(payload):
                raise TraceDecodeError("mqtt_packet_end_overflow")
            packet_type = header >> 4
            flags = header & 0x0F
            if packet_type not in MQTT_PACKET_NAMES:
                raise TraceDecodeError("mqtt_invalid_packet_type")
            required_flags = {6: 0x02, 8: 0x02, 10: 0x02}
            if packet_type in required_flags and flags != required_flags[packet_type]:
                raise TraceDecodeError("mqtt_invalid_fixed_header_flags")
            if packet_type not in {3, 6, 8, 10} and flags != 0:
                raise TraceDecodeError("mqtt_invalid_fixed_header_flags")
            qos = (header >> 1) & 0x03
            if packet_type == 3 and qos == 3:
                raise TraceDecodeError("mqtt_reserved_qos")
            packet: dict[str, Any] = {
                "packetIndex": packet_index,
                "packetType": packet_type,
                "packetName": MQTT_PACKET_NAMES.get(packet_type, "UNKNOWN"),
                "fixedHeader": header,
                "dup": bool(header & 0x08),
                "qos": qos,
                "retain": bool(header & 0x01),
                "remainingLength": remaining_length,
                "packetStart": packet_start,
                "bodyStart": body_start,
                "packetEnd": packet_end,
                "packetLength": packet_end - packet_start,
                "rawPacketBase64": base64.b64encode(payload[packet_start:packet_end]).decode("ascii"),
            }
            if packet_type == 3:
                cursor = body_start
                if cursor + 2 > packet_end:
                    raise TraceDecodeError("mqtt_topic_length_truncated")
                topic_length = int.from_bytes(payload[cursor:cursor + 2], "big")
                cursor += 2
                if topic_length == 0:
                    raise TraceDecodeError("mqtt_topic_empty")
                topic_end = cursor + topic_length
                if topic_end > packet_end:
                    raise TraceDecodeError("mqtt_topic_overflow")
                topic = payload[cursor:topic_end].decode("utf-8", errors="replace")
                packet["topic"] = _safe_topic(topic)
                packet["_techwolf_topic"] = topic in TECHWOLF_OBSERVATION_TOPICS
                cursor = topic_end
                if qos > 0:
                    if cursor + 2 > packet_end:
                        raise TraceDecodeError("mqtt_packet_id_truncated")
                    packet_id = int.from_bytes(payload[cursor:cursor + 2], "big")
                    if packet_id == 0:
                        raise TraceDecodeError("mqtt_packet_id_zero")
                    packet["packetId"] = packet_id
                    cursor += 2
                packet["payloadStart"] = cursor
                packet["payloadLength"] = packet_end - cursor
                packet["payloadBase64"] = base64.b64encode(payload[cursor:packet_end]).decode("ascii")
                packet["_payload"] = payload[cursor:packet_end]
            elif packet_type == 1:
                _validate_connect(payload, body_start, packet_end)
            elif packet_type == 2:
                if remaining_length != 2:
                    raise TraceDecodeError("mqtt_connack_length")
                acknowledge_flags = payload[body_start]
                return_code = payload[body_start + 1]
                if acknowledge_flags & 0xFE or return_code > 5:
                    raise TraceDecodeError("mqtt_connack_structure")
                if return_code and acknowledge_flags:
                    raise TraceDecodeError("mqtt_connack_structure")
                packet["sessionPresent"] = bool(acknowledge_flags & 0x01)
                packet["returnCode"] = return_code
            elif packet_type in {4, 5, 6, 7, 11}:
                if remaining_length != 2:
                    raise TraceDecodeError("mqtt_control_packet_length")
                packet["packetId"] = _read_mqtt_packet_id(payload, body_start, packet_end)
            elif packet_type in {8, 10}:
                if remaining_length < 5:
                    raise TraceDecodeError("mqtt_control_packet_length")
                packet["packetId"] = _read_mqtt_packet_id(payload, body_start, packet_end)
                cursor = body_start + 2
                count = 0
                while cursor < packet_end:
                    _topic, cursor = _read_mqtt_text(payload, cursor, packet_end)
                    if packet_type == 8:
                        if cursor >= packet_end or payload[cursor] > 2:
                            raise TraceDecodeError("mqtt_subscribe_payload")
                        cursor += 1
                    count += 1
                if count == 0:
                    raise TraceDecodeError("mqtt_control_packet_length")
                packet["subscriptionCount"] = count
            elif packet_type == 9:
                if remaining_length < 3:
                    raise TraceDecodeError("mqtt_control_packet_length")
                packet["packetId"] = _read_mqtt_packet_id(payload, body_start, packet_end)
                if any(code not in {0, 1, 2, 0x80} for code in payload[body_start + 2:packet_end]):
                    raise TraceDecodeError("mqtt_suback_payload")
            elif packet_type in {12, 13, 14} and remaining_length != 0:
                raise TraceDecodeError("mqtt_control_packet_length")
            packets.append(packet)
            offset = packet_end
            packet_index += 1
        except TraceDecodeError as exc:
            errors.append(_safe_error(exc.category))
            break
    return packets, errors


def decode_mqtt_frame(payload: bytes) -> dict[str, Any]:
    """独立解析一个 WebSocket message 中的 MQTT 多包边界与固定头。"""
    packets, errors = _decode_mqtt_packets(payload)
    safe_packets = [
        {key: value for key, value in packet.items() if key != "_payload"}
        for packet in packets
    ]
    status = "decoded" if not errors else ("partial" if packets else "failed")
    result: dict[str, Any] = {"decodeStatus": status, "packets": safe_packets}
    if errors:
        result["errors"] = errors
    return result


def _normalized_for_diff(
    value: Any,
    aliases: dict[str, dict[str, int]] | None = None,
) -> Any:
    aliases = aliases if aliases is not None else {}
    if isinstance(value, dict):
        return {
            key: _dynamic_alias(SEMANTIC_ALIAS_GROUPS.get(key, key), item, aliases)
            if key in DYNAMIC_DIFF_KEYS or key in SEMANTIC_ALIAS_GROUPS
            else _normalized_for_diff(item, aliases)
            for key, item in sorted(value.items())
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_normalized_for_diff(item, aliases) for item in value]
    return value


def _dynamic_alias(key: str, value: Any, aliases: dict[str, dict[str, int]]) -> str:
    """为每类动态标识分配稳定别名，以比较其相等关系。"""
    values = aliases.setdefault(key, {})
    identity = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if identity not in values:
        values[identity] = len(values) + 1
    return f"<{key}:{values[identity]}>"


def trace_evidence_status(trace: Any) -> dict[str, Any]:
    """从 trace 诊断统一得出下游可用的证据完整性状态。"""
    if not isinstance(trace, dict):
        return {"evidenceComplete": True, "gapReasons": []}
    reasons = [str(reason) for reason in trace.get("gapReasons") or []]
    diagnostics = trace.get("diagnostics") or {}
    cdp = diagnostics.get("cdpEventBuffer") or {}
    checks = {
        "dropped_events": cdp.get("dropped_events", diagnostics.get("dropped_events")),
        "cursor_overflow_events": cdp.get(
            "cursor_overflow_events", diagnostics.get("cursor_overflow_events")
        ),
        "droppedTraceRecords": diagnostics.get("droppedTraceRecords"),
        "droppedRequestMetadata": diagnostics.get("droppedRequestMetadata"),
        "droppedWebSocketMetadata": diagnostics.get("droppedWebSocketMetadata"),
        "droppedRequestMeta": cdp.get("droppedRequestMeta", diagnostics.get("droppedRequestMeta")),
    }
    for key, value in checks.items():
        if int(value or 0) > 0 and key not in reasons:
            reasons.append(key)
    if trace.get("evidenceComplete") is False and not reasons:
        reasons.append("trace_marked_incomplete")
    return {"evidenceComplete": not reasons, "gapReasons": reasons}


def diff_normalized_traces(expected: Any, actual: Any) -> dict[str, Any]:
    """比较 normalized trace，动态值归一化后仍保留字段存在性与协议结构。"""
    expected_events = expected.get("events", []) if isinstance(expected, dict) else expected
    actual_events = actual.get("events", []) if isinstance(actual, dict) else actual
    left = _normalized_for_diff(expected_events)
    right = _normalized_for_diff(actual_events)
    mismatches: list[dict[str, Any]] = []
    expected_evidence = trace_evidence_status(expected)
    actual_evidence = trace_evidence_status(actual)
    gap_reasons = list(dict.fromkeys(
        [f"expected:{reason}" for reason in expected_evidence["gapReasons"]]
        + [f"actual:{reason}" for reason in actual_evidence["gapReasons"]]
    ))

    def compare(first: Any, second: Any, path: str) -> None:
        if type(first) is not type(second):
            mismatches.append({"path": path, "expected": first, "actual": second})
            return
        if isinstance(first, dict):
            for key in sorted(set(first) | set(second)):
                if key not in first or key not in second:
                    mismatches.append({
                        "path": f"{path}.{key}",
                        "expected": first.get(key, "<missing>"),
                        "actual": second.get(key, "<missing>"),
                    })
                else:
                    compare(first[key], second[key], f"{path}.{key}")
        elif isinstance(first, list):
            if len(first) != len(second):
                mismatches.append({"path": f"{path}.length", "expected": len(first), "actual": len(second)})
            for index, (first_item, second_item) in enumerate(zip(first, second)):
                compare(first_item, second_item, f"{path}[{index}]")
        elif first != second:
            mismatches.append({"path": path, "expected": first, "actual": second})

    compare(left, right, "events")
    if gap_reasons:
        mismatches.append({"path": "evidenceComplete", "expected": True, "actual": False})
    return {
        "matches": not mismatches,
        "mismatches": mismatches,
        "evidenceComplete": not gap_reasons,
        "gapReasons": gap_reasons,
        "nativeEvidenceUpgradeAllowed": False,
    }


def adapt_sender_dry_run_normalized(sender_normalized: dict[str, Any]) -> dict[str, Any]:
    """将 extension dry-run 摘要转换为可与 Trace 投影比较的结构。"""
    sender_normalized = sender_normalized if isinstance(sender_normalized, dict) else {}
    techwolf = sender_normalized.get("techwolf") or {}
    techwolf = techwolf if isinstance(techwolf, dict) else {}
    messages = [
        {
            "clientMid": item.get("clientMid"),
            "bodyType": item.get("bodyType"),
        }
        for item in techwolf.get("messages") or []
        if isinstance(item, dict)
    ]
    valid = (
        sender_normalized.get("transport") == "mqtt"
        and isinstance(sender_normalized.get("topic"), str)
        and bool(sender_normalized.get("topic"))
        and bool(messages)
    )
    return {
        "source": "sender_dry_run_adapter",
        "events": ([{
            "transport": sender_normalized.get("transport"),
            "topic": sender_normalized.get("topic"),
            "qos": sender_normalized.get("qos"),
            "retain": sender_normalized.get("retain"),
            "dup": sender_normalized.get("dup"),
            "techwolf": {
                "protocolType": techwolf.get("protocolType"),
                "messageCount": len(messages),
                "messages": messages,
            },
        }] if valid else []),
        "evidenceComplete": valid,
        "gapReasons": [] if valid else ["sender_dry_run_input_missing"],
        "nativeEvidenceUpgradeAllowed": False,
    }


def adapt_trace_for_sender_dry_run_diff(trace: dict[str, Any]) -> dict[str, Any]:
    """投影 sent PUBLISH Trace，移除 sender dry-run 无法生成的 CDP 包装字段。"""
    events: list[dict[str, Any]] = []
    for event in trace.get("events") or []:
        if not isinstance(event, dict) or (event.get("rawEvidence") or {}).get("direction") != "sent":
            continue
        mqtt = event.get("mqtt") or {}
        interpretations = {
            item.get("packetIndex"): item
            for item in event.get("techwolf") or []
            if isinstance(item, dict)
        }
        for packet in mqtt.get("packets") or []:
            if packet.get("packetType") != 3:
                continue
            interpretation = interpretations.get(packet.get("packetIndex")) or {}
            messages = [
                {
                    "clientMid": message.get("clientMid"),
                    "bodyType": (message.get("body") or {}).get("bodyType"),
                }
                for message in interpretation.get("messages") or []
                if isinstance(message, dict)
            ]
            events.append({
                "transport": "mqtt",
                "topic": packet.get("topic"),
                "qos": packet.get("qos"),
                "retain": packet.get("retain"),
                "dup": packet.get("dup"),
                "techwolf": {
                    "protocolType": interpretation.get("protocolType"),
                    "messageCount": len(messages),
                    "messages": messages,
                },
            })
    evidence = trace_evidence_status(trace)
    if not events and "sent_publish_missing" not in evidence["gapReasons"]:
        evidence["gapReasons"].append("sent_publish_missing")
        evidence["evidenceComplete"] = False
    return {
        "source": "trace_sender_dry_run_adapter",
        "events": events,
        **evidence,
        "nativeEvidenceUpgradeAllowed": False,
    }


def filter_trace_marker_window(
    trace: dict[str, Any],
    marker_name: str,
    *,
    before_ms: int = 2000,
    after_ms: int = 2000,
) -> dict[str, Any]:
    """从落盘后的 normalized trace 按 marker 前后时间窗筛选事件。"""
    marker = next(
        (
            item
            for item in reversed(trace.get("markers") or [])
            if isinstance(item, dict) and item.get("name") == marker_name
        ),
        None,
    )
    if marker is None:
        raise ValueError(f"未找到 marker：{marker_name}")
    center = float(marker.get("timeOffsetMs") or 0)
    lower = center - max(0, before_ms)
    upper = center + max(0, after_ms)
    events = [
        item
        for item in trace.get("events") or []
        if isinstance(item, dict)
        and lower <= float(item.get("timeOffsetMs") or 0) <= upper
    ]
    return {"events": events, **trace_evidence_status(trace)}


def filter_trace_marker_range(
    trace: dict[str, Any],
    before_marker: str,
    after_marker: str,
    *,
    before_ms: int = 2000,
    after_ms: int = 2000,
) -> dict[str, Any]:
    """筛选 before marker 前 N 毫秒至 after marker 后 N 毫秒的事件。"""
    markers = [item for item in trace.get("markers") or [] if isinstance(item, dict)]
    start = next((item for item in markers if item.get("name") == before_marker), None)
    end = next((item for item in reversed(markers) if item.get("name") == after_marker), None)
    if start is None or end is None:
        raise ValueError("marker range 缺少起点或终点。")
    lower = float(start.get("timeOffsetMs") or 0) - max(0, before_ms)
    upper = float(end.get("timeOffsetMs") or 0) + max(0, after_ms)
    if upper < lower:
        raise ValueError("marker range 的终点早于起点。")
    events = [
        item
        for item in trace.get("events") or []
        if isinstance(item, dict)
        and lower <= float(item.get("timeOffsetMs") or 0) <= upper
    ]
    return {"events": events, **trace_evidence_status(trace)}


def candidate_http_requests_from_events(
    source: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    """从脱敏事件中选择 marker 区间内的状态变更 HTTP 请求。"""
    if isinstance(source, dict):
        events = source.get("events") or []
        evidence = trace_evidence_status(source)
    else:
        events = source
        evidence = {"evidenceComplete": True, "gapReasons": []}
    candidates = [
        event
        for event in events
        if event.get("transport") == "http"
        and str(event.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    confidence = (
        "HIGH"
        if evidence["evidenceComplete"] and len(candidates) == 1
        else "MEDIUM" if candidates else "LOW"
    )
    return {
        "candidates": [
        {
            "method": item.get("method"),
            "host": item.get("host"),
            "path": item.get("path"),
            "queryKeys": item.get("queryKeys", []),
            "requestBodyKeys": item.get("requestBodyKeys", []),
            "status": item.get("status"),
            "businessCode": item.get("businessCode"),
            "whySelected": ["marker window 内出现", "状态变更 HTTP 方法"],
            "confidence": confidence,
        }
        for item in candidates
        ],
        **evidence,
        "nativeEvidenceUpgradeAllowed": False,
    }


def render_candidate_http_report(marker: str, candidate_result: dict[str, Any]) -> dict[str, Any]:
    """将候选接口渲染为可审阅文本，不执行请求或页面动作。"""
    lines = [f"## Marker: {marker}", ""]
    candidates = candidate_result.get("candidates") or []
    evidence_complete = bool(candidate_result.get("evidenceComplete"))
    gap_reasons = candidate_result.get("gapReasons") or []
    lines.extend([
        f"Evidence complete: {str(evidence_complete).lower()}",
        f"Evidence gaps: {', '.join(gap_reasons) or '(none)'}",
        "",
    ])
    if not candidates:
        return {**candidate_result, "marker": marker, "report": "\n".join([*lines, "未发现候选状态变更请求。"]) }
    for index, candidate in enumerate(candidates, start=1):
        lines.extend([
            f"### Candidate {index}",
            "",
            f"Method: {candidate.get('method')}",
            f"Host: {candidate.get('host')}",
            f"Path: {candidate.get('path')}",
            f"Query keys: {', '.join(candidate.get('queryKeys') or []) or '(none)'}",
            f"Body keys: {', '.join(candidate.get('requestBodyKeys') or []) or '(none)'}",
            f"Response: HTTP {candidate.get('status')}; business code {candidate.get('businessCode')}",
            f"Confidence: {candidate.get('confidence')}",
            "",
        ])
    return {**candidate_result, "marker": marker, "report": "\n".join(lines).rstrip()}


class NetworkTraceCapture:
    """消费既有 CDP Network 事件并生成 raw-first forensic Trace。"""

    MAX_RECORDS = 20000
    MAX_REQUESTS = 4096
    MAX_WEB_SOCKETS = 4096

    def __init__(self, sessions: dict[str, str]) -> None:
        self.trace_id = str(uuid.uuid4())
        self.sessions = sessions
        self.records: list[dict[str, Any]] = []
        self.markers: list[dict[str, Any]] = []
        self.requests: dict[tuple[str, str], dict[str, Any]] = {}
        self.web_sockets: dict[tuple[str, str], dict[str, Any]] = {}
        self.dropped_records = 0
        self.dropped_request_metadata = 0
        self.dropped_web_socket_metadata = 0
        self._sequence = 0
        self._active_marker: str | None = None
        self._lock = threading.Lock()
        self._origin_monotonic = time.monotonic()

    def mark(self, name: str) -> dict[str, Any]:
        name = str(name or "").strip()
        if not TRACE_MARKER_PATTERN.fullmatch(name):
            raise ValueError("marker 仅允许 1 至 64 位字母、数字、下划线和连字符。")
        with self._lock:
            marker = {
                "name": name,
                "markerTimestamp": _now(),
                "_monotonic": time.monotonic(),
            }
            marker["timeOffsetMs"] = round(
                (marker["_monotonic"] - self._origin_monotonic) * 1000,
                3,
            )
            self.markers.append(marker)
            self._active_marker = name
            return {key: value for key, value in marker.items() if not key.startswith("_")}

    def _append(self, record: dict[str, Any], event: dict[str, Any]) -> None:
        self._sequence += 1
        record.setdefault("traceId", self.trace_id)
        record.setdefault("sequence", int(event.get("_finejobSequence") or self._sequence))
        record.setdefault("capturedAt", _now())
        record.setdefault("cdpTimestamp", (event.get("params") or {}).get("timestamp"))
        record.setdefault("marker", self._active_marker)
        record["_monotonic"] = time.monotonic()
        record["timeOffsetMs"] = round(
            (record["_monotonic"] - self._origin_monotonic) * 1000,
            3,
        )
        if len(self.records) >= self.MAX_RECORDS:
            self.records.pop(0)
            self.dropped_records += 1
        self.records.append(record)

    @staticmethod
    def _bounded_store(
        store: dict[tuple[str, str], dict[str, Any]],
        key: tuple[str, str],
        value: dict[str, Any],
        limit: int,
    ) -> bool:
        evicted = False
        if key not in store and len(store) >= limit:
            store.pop(next(iter(store)))
            evicted = True
        store[key] = value
        return evicted

    def process_event(
        self,
        event: dict[str, Any],
        *,
        response_body: Any = None,
        response_body_base64_encoded: bool = False,
    ) -> None:
        session_id = str(event.get("sessionId") or "")
        if session_id not in self.sessions:
            return
        method = str(event.get("method") or "")
        params = event.get("params") or {}
        request_id = str(params.get("requestId") or "")
        target_id = self.sessions[session_id]
        common = {
            "targetId": target_id,
            "sessionId": session_id,
            "requestId": request_id,
        }
        with self._lock:
            if method == "Network.requestWillBeSent" and request_id:
                request = params.get("request") or {}
                if self._bounded_store(self.requests, (session_id, request_id), {
                    **common,
                    **_url_metadata(request.get("url")),
                    "method": request.get("method"),
                    "requestHeaders": request.get("headers") or {},
                    "requestBody": request.get("postData"),
                    "requestBodyKeys": _body_keys(request.get("postData")),
                    "marker": self._active_marker,
                    "startedTimestamp": params.get("timestamp"),
                }, self.MAX_REQUESTS):
                    self.dropped_request_metadata += 1
            elif method == "Network.responseReceived" and request_id:
                response = params.get("response") or {}
                key = (session_id, request_id)
                if key not in self.requests and self._bounded_store(
                    self.requests, key, {**common}, self.MAX_REQUESTS
                ):
                    self.dropped_request_metadata += 1
                meta = self.requests[key]
                response_url = _url_metadata(response.get("url"))
                for key, value in response_url.items():
                    meta.setdefault(key, value)
                meta["status"] = response.get("status")
                meta["responseHeaders"] = response.get("headers") or {}
            elif method == "Network.loadingFinished" and request_id:
                meta = self.requests.pop((session_id, request_id), {**common})
                self._append({
                    "transport": "http",
                    **meta,
                    "responseBody": response_body,
                    "responseBodyBase64Encoded": response_body_base64_encoded,
                    "businessCode": _business_code(response_body),
                }, event)
            elif method == "Network.webSocketCreated" and request_id:
                socket_meta = {**common, **_url_metadata(params.get("url"))}
                if self._bounded_store(
                    self.web_sockets, (session_id, request_id), socket_meta, self.MAX_WEB_SOCKETS
                ):
                    self.dropped_web_socket_metadata += 1
                self._append({"transport": "websocket", "event": "created", **socket_meta}, event)
            elif method == "Network.webSocketWillSendHandshakeRequest" and request_id:
                request = params.get("request") or {}
                socket_meta = self.web_sockets.get((session_id, request_id), common)
                self._append({
                    "transport": "websocket",
                    "event": "handshakeRequest",
                    **socket_meta,
                    "requestHeaders": request.get("headers") or {},
                    "headerKeys": _header_keys(request.get("headers")),
                }, event)
            elif method == "Network.webSocketHandshakeResponseReceived" and request_id:
                response = params.get("response") or {}
                socket_meta = self.web_sockets.get((session_id, request_id), common)
                self._append({
                    "transport": "websocket",
                    "event": "handshakeResponse",
                    **socket_meta,
                    "status": response.get("status"),
                    "responseHeaders": response.get("headers") or {},
                    "headerKeys": _header_keys(response.get("headers")),
                }, event)
            elif method in {"Network.webSocketFrameSent", "Network.webSocketFrameReceived"} and request_id:
                self._record_websocket_frame(event, common, method, params)
            elif method in {"Network.webSocketFrameError", "Network.webSocketClosed"} and request_id:
                socket_meta = self.web_sockets.pop((session_id, request_id), common)
                self._append({
                    "transport": "websocket",
                    "event": "frameError" if method.endswith("FrameError") else "closed",
                    **socket_meta,
                }, event)

    def _record_websocket_frame(
        self,
        event: dict[str, Any],
        common: dict[str, Any],
        method: str,
        params: dict[str, Any],
    ) -> None:
        response = params.get("response") or {}
        opcode = int(response.get("opcode") or 0)
        payload_data = response.get("payloadData")
        decode_error: dict[str, str] | None = None
        try:
            if opcode == 2:
                frame_payload = base64.b64decode(str(payload_data or ""), validate=True)
            else:
                frame_payload = str(payload_data or "").encode("utf-8")
        except (TypeError, ValueError):
            frame_payload = b""
            decode_error = _safe_error("invalid_cdp_frame_payload")

        packets, mqtt_errors = _decode_mqtt_packets(frame_payload) if decode_error is None else ([], [])
        safe_packets: list[dict[str, Any]] = []
        techwolf: list[dict[str, Any]] = []
        for packet in packets:
            packet_payload = packet.get("_payload", b"")
            safe_packets.append({key: value for key, value in packet.items() if key != "_payload"})
            if packet.get("packetType") == 3 and packet.get("_techwolf_topic"):
                techwolf.append({
                    "packetIndex": packet.get("packetIndex"),
                    "topic": packet.get("topic"),
                    "rawProtobufBase64": base64.b64encode(packet_payload).decode("ascii"),
                    **decode_techwolf(packet_payload),
                })
        mqtt_status = "decoded" if not mqtt_errors else ("partial" if packets else "failed")
        mqtt: dict[str, Any] = {"decodeStatus": mqtt_status, "packets": safe_packets}
        if mqtt_errors:
            mqtt["errors"] = mqtt_errors
        socket_meta = self.web_sockets.get((common["sessionId"], common["requestId"]), common)
        record: dict[str, Any] = {
            "transport": "websocket",
            "event": "frame",
            **socket_meta,
            "rawEvidence": {
                "direction": "sent" if method.endswith("Sent") else "received",
                "opcode": opcode,
                "payloadLength": len(frame_payload),
                "payloadEncoding": "base64" if opcode == 2 else "utf-8",
                "payloadData": payload_data,
            },
            "mqtt": mqtt,
            "techwolf": techwolf,
        }
        if decode_error:
            record["rawEvidence"]["decodeError"] = decode_error
        self._append(record, event)

    def events_in_marker_window(
        self,
        marker_name: str,
        *,
        before_ms: int = 2000,
        after_ms: int = 2000,
    ) -> list[dict[str, Any]]:
        marker = next((item for item in reversed(self.markers) if item["name"] == marker_name), None)
        if marker is None:
            raise ValueError(f"未找到 marker：{marker_name}")
        lower = marker["_monotonic"] - max(0, before_ms) / 1000
        upper = marker["_monotonic"] + max(0, after_ms) / 1000
        events = [self._public_record(item) for item in self.records if lower <= item["_monotonic"] <= upper]
        return {"events": events, **self.evidence_status()}

    def candidate_http_requests(
        self,
        marker_name: str,
        *,
        before_ms: int = 2000,
        after_ms: int = 2000,
    ) -> dict[str, Any]:
        window = self.events_in_marker_window(
            marker_name,
            before_ms=before_ms,
            after_ms=after_ms,
        )
        return candidate_http_requests_from_events(window)

    @staticmethod
    def _public_record(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if not key.startswith("_")}

    def export(
        self,
        *,
        cdp_diagnostics: dict[str, Any] | None = None,
        gap_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            diagnostics = {
                "traceRecordCapacity": self.MAX_RECORDS,
                "droppedTraceRecords": self.dropped_records,
                "requestMetadataCapacity": self.MAX_REQUESTS,
                "requestMetadataBuffered": len(self.requests),
                "droppedRequestMetadata": self.dropped_request_metadata,
                "webSocketMetadataCapacity": self.MAX_WEB_SOCKETS,
                "webSocketMetadataBuffered": len(self.web_sockets),
                "droppedWebSocketMetadata": self.dropped_web_socket_metadata,
                "cdpEventBuffer": cdp_diagnostics or {},
            }
            evidence = trace_evidence_status({
                "diagnostics": diagnostics,
                "gapReasons": gap_reasons or [],
            })
            payload = {
                "version": 5,
                "traceId": self.trace_id,
                "rawPayloadSaved": True,
                "redactionEnabled": False,
                "markers": [
                    {key: value for key, value in marker.items() if not key.startswith("_")}
                    for marker in self.markers
                ],
                "events": [self._public_record(record) for record in self.records],
                "diagnostics": diagnostics,
                **evidence,
                "nativeEvidenceUpgradeAllowed": False,
            }
            return payload

    def evidence_status(self) -> dict[str, Any]:
        return trace_evidence_status(self.export())
