import { parse, type Root, type Type } from "protobufjs";


const PROTO_FILE = `
syntax = "proto2";
message TechwolfUser {
  required int64 uid = 1;
  optional string name = 2;
  optional string company = 4;
  optional int32 source = 7;
}
message TechwolfMessageBody {
  required int32 type = 1;
  required int32 templateId = 2;
  optional string text = 3;
}
message TechwolfMessage {
  required TechwolfUser from = 1;
  required TechwolfUser to = 2;
  required int32 type = 3;
  optional int64 mid = 4;
  optional int64 time = 5;
  required TechwolfMessageBody body = 6;
  optional int64 cmid = 11;
  optional string bizId = 17;
  optional int32 bizType = 18;
  optional string securityId = 19;
}
message TechwolfMessageSync {
  required int64 clientMid = 1;
  required int64 serverMid = 2;
}
message TechwolfProtocolType6Payload {
  required int64 field1Value = 1;
  required int64 field2Value = 2;
  required int64 field3Value = 3;
  optional int32 field5Value = 5;
}
message TechwolfChatProtocol {
  required int32 type = 1;
  optional string version = 2;
  repeated TechwolfMessage messages = 3;
  repeated TechwolfMessageSync messageSync = 7;
  optional TechwolfProtocolType6Payload protocolType6Payload = 8;
  optional int32 domain = 10;
}
`;

export type DecodedChatMessage = {
  from?: { uid?: string; name?: string; company?: string; source?: number };
  to?: { uid?: string; name?: string; company?: string; source?: number };
  type?: number;
  mid?: string;
  time?: string;
  cmid?: string;
  bizId?: string;
  bizType?: number;
  securityId?: string;
  body?: { type?: number; templateId?: number; text?: string };
};

export type DecodedChatProtocol = {
  type?: number;
  messages: DecodedChatMessage[];
  messageSync: Array<{ clientMid?: string; serverMid?: string }>;
  protocolType6Payload?: {
    field1Value?: string;
    field2Value?: string;
    field3Value?: string;
    field5Value?: number;
  };
};

export class BossChatProtocol {
  private readonly root: Root;
  private readonly protocol: Type;

  constructor() {
    this.root = parse(PROTO_FILE, { keepCase: false }).root;
    this.protocol = this.root.lookupType("TechwolfChatProtocol");
  }

  decode(bytes: Uint8Array): DecodedChatProtocol {
    const decoded = this.protocol.decode(bytes);
    return this.protocol.toObject(decoded, {
      longs: String,
      enums: Number,
      defaults: false,
      arrays: true,
      objects: true
    }) as DecodedChatProtocol;
  }

  encodeText(input: {
    fromUid: string;
    toUid: string;
    encryptToUid: string;
    friendSource: number;
    clientMid: string;
    text: string;
  }): Uint8Array {
    const payload = {
      type: 1,
      messages: [{
        from: { uid: input.fromUid, source: 0 },
        to: { uid: input.toUid, name: input.encryptToUid, source: input.friendSource },
        type: 1,
        mid: input.clientMid,
        cmid: input.clientMid,
        time: String(Date.now()),
        body: { type: 1, templateId: 1, text: input.text }
      }]
    };
    const message = this.protocol.fromObject(payload);
    const error = this.protocol.verify(message);
    if (error) throw new Error(`BOSS 文本消息校验失败：${error}`);
    return this.protocol.encode(message).finish();
  }

  /** messageSync 字段仅为参考 schema 的离线解码夹具，等待 native trace 校验。 */
  encodeMessageSync(clientMid: string, serverMid: string): Uint8Array {
    const message = this.protocol.fromObject({ type: 5, messageSync: [{ clientMid, serverMid }] });
    const error = this.protocol.verify(message);
    if (error) throw new Error(`BOSS messageSync 校验失败：${error}`);
    return this.protocol.encode(message).finish();
  }

  encodeResume(input: {
    field1Value: string;
    field2Value: string;
    field3Value: string;
  }): Uint8Array {
    // field 3 仅确认是动态时间类值，保持中性字段名。
    const message = this.protocol.fromObject({
      type: 6,
      protocolType6Payload: {
        field1Value: input.field1Value,
        field2Value: input.field2Value,
        field3Value: input.field3Value,
        field5Value: 0
      }
    });
    const error = this.protocol.verify(message);
    if (error) throw new Error(`BOSS 简历消息校验失败：${error}`);
    return this.protocol.encode(message).finish();
  }
}

export const bossChatProtocol = new BossChatProtocol();
