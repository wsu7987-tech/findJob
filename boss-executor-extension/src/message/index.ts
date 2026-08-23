import { defineProxy } from "comctx";

import { CONTENT_NAMESPACE, type ContentService } from "./content";
import { ScriptElementAdapter } from "./content-script-share";

let injectedContentService: ContentService | null = null;

export const initContentService = (
  script: HTMLScriptElement | null = document.currentScript as HTMLScriptElement | null
): void => {
  if (!script) throw new Error("无法取得 MAIN World 注入脚本元素");

  const [, injectContentService] = defineProxy(() => ({}) as ContentService, {
    namespace: CONTENT_NAMESPACE
  });
  injectedContentService = injectContentService(new ScriptElementAdapter(script));
};

export const contentService = new Proxy({} as ContentService, {
  get(_target, key) {
    if (!injectedContentService) {
      throw new Error(`Content 服务尚未初始化：${String(key)}`);
    }
    const value = Reflect.get(injectedContentService, key);
    return typeof value === "function" ? value.bind(injectedContentService) : value;
  }
});
