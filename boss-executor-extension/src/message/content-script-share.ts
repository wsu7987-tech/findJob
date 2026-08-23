import type { Adapter, Message, OnMessage, SendMessage } from "comctx";

const PAGE_MESSAGE_EVENT = "fine-job:boss-executor:page-message:v1";

// 适配自 boss-helper：使用注入脚本元素承载 ISOLATED 与 MAIN World 的序列化消息。
export class ScriptElementAdapter implements Adapter {
  constructor(private readonly script: HTMLScriptElement) {}

  sendMessage: SendMessage = (message) => {
    this.script.dispatchEvent(
      new CustomEvent(PAGE_MESSAGE_EVENT, { detail: JSON.stringify(message) })
    );
  };

  onMessage: OnMessage = (callback) => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (typeof detail !== "string") return;
      try {
        callback(JSON.parse(detail) as Partial<Message>);
      } catch {
        // 页面脚本可能伪造同名事件；无效载荷必须被忽略，不能破坏执行器通信。
      }
    };
    this.script.addEventListener(PAGE_MESSAGE_EVENT, handler);
    return () => this.script.removeEventListener(PAGE_MESSAGE_EVENT, handler);
  };
}
