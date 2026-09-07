export type MqttPacketType = "publish" | "puback" | "suback" | "pingresp" | "unknown";

export type MqttPacket = {
  type: MqttPacketType;
  dup: boolean;
  qos: number;
  retain: boolean;
  remainingLength: number;
  topic?: string;
  packetId?: number;
  payload: Uint8Array;
  packetStart: number;
  packetEnd: number;
};

export type MqttPublishPacket = MqttPacket & { type: "publish"; topic: string };

export class MqttPacketDecodeError extends Error {}

const decoder = new TextDecoder();

const decodeRemainingLength = (buffer: Uint8Array, start: number): { value: number; bytes: number } => {
  let value = 0;
  let multiplier = 1;
  for (let offset = 0; offset < 4; offset += 1) {
    const encoded = buffer[start + offset];
    if (encoded === undefined) throw new MqttPacketDecodeError("MQTT Remaining Length 被截断");
    value += (encoded & 127) * multiplier;
    if ((encoded & 128) === 0) return { value, bytes: offset + 1 };
    multiplier *= 128;
  }
  throw new MqttPacketDecodeError("MQTT Remaining Length 超过四字节");
};

const packetType = (header: number): MqttPacketType => {
  switch (header >> 4) {
    case 3: return "publish";
    case 4: return "puback";
    case 9: return "suback";
    case 13: return "pingresp";
    default: return "unknown";
  }
};

const requireBytes = (start: number, length: number, end: number, label: string): void => {
  if (start + length > end) throw new MqttPacketDecodeError(`MQTT ${label} 被截断`);
};

const decodePacket = (buffer: Uint8Array, packetStart: number): MqttPacket => {
  const header = buffer[packetStart];
  if (header === undefined) throw new MqttPacketDecodeError("MQTT 固定头缺失");
  const remaining = decodeRemainingLength(buffer, packetStart + 1);
  const bodyStart = packetStart + 1 + remaining.bytes;
  const packetEnd = bodyStart + remaining.value;
  if (packetEnd > buffer.byteLength) throw new MqttPacketDecodeError("MQTT packetEnd 超出 frame 边界");
  const type = packetType(header);
  const base = {
    type,
    dup: Boolean(header & 8),
    qos: (header & 6) >> 1,
    retain: Boolean(header & 1),
    remainingLength: remaining.value,
    payload: buffer.subarray(bodyStart, packetEnd),
    packetStart,
    packetEnd
  } as MqttPacket;

  if (type === "publish") {
    if (base.qos === 3) throw new MqttPacketDecodeError("MQTT PUBLISH QoS 3 非法");
    requireBytes(bodyStart, 2, packetEnd, "PUBLISH topic length");
    const topicLength = (buffer[bodyStart]! << 8) | buffer[bodyStart + 1]!;
    const topicStart = bodyStart + 2;
    const topicEnd = topicStart + topicLength;
    requireBytes(topicStart, topicLength, packetEnd, "PUBLISH topic");
    if (topicLength === 0) throw new MqttPacketDecodeError("MQTT PUBLISH topic 为空");
    let payloadStart = topicEnd;
    let packetId: number | undefined;
    if (base.qos > 0) {
      requireBytes(payloadStart, 2, packetEnd, "PUBLISH packetId");
      packetId = (buffer[payloadStart]! << 8) | buffer[payloadStart + 1]!;
      if (packetId === 0) throw new MqttPacketDecodeError("MQTT PUBLISH packetId 非法");
      payloadStart += 2;
    }
    return {
      ...base,
      type: "publish",
      topic: decoder.decode(buffer.subarray(topicStart, topicEnd)),
      ...(packetId === undefined ? {} : { packetId }),
      payload: buffer.subarray(payloadStart, packetEnd)
    };
  }
  if (type === "puback") {
    if (remaining.value !== 2) throw new MqttPacketDecodeError("MQTT PUBACK 长度非法");
    const packetId = (buffer[bodyStart]! << 8) | buffer[bodyStart + 1]!;
    if (packetId === 0) throw new MqttPacketDecodeError("MQTT PUBACK packetId 非法");
    return { ...base, packetId };
  }
  if (type === "suback") {
    requireBytes(bodyStart, 3, packetEnd, "SUBACK");
    const packetId = (buffer[bodyStart]! << 8) | buffer[bodyStart + 1]!;
    if (packetId === 0) throw new MqttPacketDecodeError("MQTT SUBACK packetId 非法");
    return { ...base, packetId, payload: buffer.subarray(bodyStart + 2, packetEnd) };
  }
  if (type === "pingresp" && remaining.value !== 0) {
    throw new MqttPacketDecodeError("MQTT PINGRESP 长度非法");
  }
  return base;
};

/** 严格按 packetEnd 分帧，任一畸形包都会拒绝整帧。 */
export const decodeMqttPackets = (buffer: Uint8Array): MqttPacket[] => {
  const packets: MqttPacket[] = [];
  let packetStart = 0;
  while (packetStart < buffer.byteLength) {
    const packet = decodePacket(buffer, packetStart);
    packets.push(packet);
    packetStart = packet.packetEnd;
  }
  return packets;
};

/** 兼容旧调用方：只返回 frame 中的首个 PUBLISH。 */
export const decodeMqttPublish = (buffer: Uint8Array): MqttPublishPacket | null => {
  const packet = decodeMqttPackets(buffer).find((item): item is MqttPublishPacket => item.type === "publish");
  return packet ?? null;
};

export const toUint8Array = async (data: unknown): Promise<Uint8Array | null> => {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  if (data instanceof Blob) return new Uint8Array(await data.arrayBuffer());
  return null;
};
