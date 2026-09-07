import { describe, expect, it } from "vitest";

import { MqttPacketDecodeError, decodeMqttPackets } from "../src/platform/boss/chat/mqtt-packet";

const remainingLength = (length: number): number[] => {
  const bytes: number[] = [];
  let value = length;
  do {
    let byte = value % 128;
    value = Math.floor(value / 128);
    if (value) byte |= 128;
    bytes.push(byte);
  } while (value);
  return bytes;
};

const publish = (payload: number[], options: { qos?: number; retain?: boolean; dup?: boolean; packetId?: number } = {}): number[] => {
  const qos = options.qos ?? 1;
  const topic = [0, 4, 99, 104, 97, 116];
  const packetId = qos > 0 ? [0, options.packetId ?? 7] : [];
  const header = 0x30 | (options.dup ? 8 : 0) | (qos << 1) | (options.retain ? 1 : 0);
  const body = [...topic, ...packetId, ...payload];
  return [header, ...remainingLength(body.length), ...body];
};

describe("MQTT packet 边界", () => {
  it("保留 QoS、packetId、DUP、retain 与 packetEnd", () => {
    const [packet] = decodeMqttPackets(Uint8Array.from(publish([1, 2], { qos: 1, packetId: 42, dup: true, retain: true })));
    expect(packet).toMatchObject({ type: "publish", topic: "chat", qos: 1, packetId: 42, dup: true, retain: true, packetStart: 0 });
    expect(packet?.payload).toEqual(Uint8Array.from([1, 2]));
    expect(packet?.packetEnd).toBe(12);
  });

  it("支持 QoS 0 和多字节 Remaining Length", () => {
    const body = Array.from({ length: 140 }, (_, index) => index);
    const [packet] = decodeMqttPackets(Uint8Array.from(publish(body, { qos: 0 })));
    expect(packet).toMatchObject({ type: "publish", qos: 0, remainingLength: 146 });
    expect(packet?.packetId).toBeUndefined();
    expect(packet?.payload).toEqual(Uint8Array.from(body));
  });

  it("一个 WebSocket frame 的首包 payload 不吞入后续 MQTT packet", () => {
    const bytes = Uint8Array.from([...publish([9, 8]), 0x40, 0x02, 0x00, 0x07, 0xd0, 0x00]);
    const packets = decodeMqttPackets(bytes);
    expect(packets.map((packet) => packet.type)).toEqual(["publish", "puback", "pingresp"]);
    expect(packets[0]?.payload).toEqual(Uint8Array.from([9, 8]));
    expect(packets[1]).toMatchObject({ packetId: 7, packetStart: packets[0]?.packetEnd });
  });

  it("支持连续 PUBLISH 和 unknown packet", () => {
    const packets = decodeMqttPackets(Uint8Array.from([...publish([1]), ...publish([2]), 0x20, 0x00]));
    expect(packets.map((packet) => packet.type)).toEqual(["publish", "publish", "unknown"]);
    expect(packets[0]?.payload).toEqual(Uint8Array.from([1]));
    expect(packets[1]?.payload).toEqual(Uint8Array.from([2]));
  });

  it.each([
    ["truncated Remaining Length", [0x30, 0x80]],
    ["oversized Remaining Length", [0x30, 0xff, 0xff, 0xff, 0x80]],
    ["packetEnd overflow", [0x30, 0x08, 0x00, 0x04, 99]],
    ["topic length overflow", [0x30, 0x02, 0x00, 0x04]],
    ["missing QoS packetId", [0x32, 0x06, 0x00, 0x04, 99, 104, 97, 116]],
    ["invalid QoS", [0x36, 0x06, 0x00, 0x04, 99, 104, 97, 116]],
    ["trailing malformed packet", [...publish([1]), 0x40, 0x02, 0x00]]
  ])("对 %s fail closed", (_label, bytes) => {
    expect(() => decodeMqttPackets(Uint8Array.from(bytes))).toThrow(MqttPacketDecodeError);
  });
});
