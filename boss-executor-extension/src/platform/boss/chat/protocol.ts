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
message TechwolfChatProtocol {
  required int32 type = 1;
  optional string version = 2;
  repeated TechwolfMessage messages = 3;
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
}

export const bossChatProtocol = new BossChatProtocol();
