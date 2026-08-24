export type MqttPublishPacket = {
  topic: string;
  payload: Uint8Array;
  qos: number;
  retain: boolean;
};

const decodeRemainingLength = (buffer: Uint8Array, start: number) => {
  let value = 0;
  let multiplier = 1;
  let index = start;
  let encoded = 0;
  do {
    encoded = buffer[index] ?? 0;
    index += 1;
    value += (encoded & 127) * multiplier;
    multiplier *= 128;
    if (multiplier > 128 ** 4) throw new Error("MQTT remaining length 无效");
  } while ((encoded & 128) !== 0);
  return { value, bytes: index - start };
};

export const decodeMqttPublish = (buffer: Uint8Array): MqttPublishPacket | null => {
  const header = buffer[0];
  if (header === undefined || header >> 4 !== 3) return null;
  const remaining = decodeRemainingLength(buffer, 1);
  const topicStart = 1 + remaining.bytes;
  const topicLength = ((buffer[topicStart] ?? 0) << 8) | (buffer[topicStart + 1] ?? 0);
  const topicBytesStart = topicStart + 2;
  const topicEnd = topicBytesStart + topicLength;
  if (topicEnd > buffer.length) return null;
  const topic = new TextDecoder().decode(buffer.subarray(topicBytesStart, topicEnd));
  const qos = (header & 6) >> 1;
  const payloadStart = topicEnd + (qos > 0 ? 2 : 0);
  if (payloadStart > buffer.length) return null;
  return {
    topic,
    payload: buffer.subarray(payloadStart),
    qos,
    retain: Boolean(header & 1)
  };
};

export const toUint8Array = async (data: unknown): Promise<Uint8Array | null> => {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  if (data instanceof Blob) return new Uint8Array(await data.arrayBuffer());
  return null;
};
