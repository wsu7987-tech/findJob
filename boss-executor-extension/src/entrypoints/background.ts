import { defineProxy } from "comctx";

import { defineBackground } from "#imports";

import {
  BACKGROUND_NAMESPACE,
  BackgroundService,
  ProvideBackgroundAdapter
} from "../message/background";
import { fineJobExecutorClient } from "../finejob/client";

export default defineBackground({
  main() {
    // 适配 boss-helper 的 Background 服务入口；执行凭证和FineJob队列只保留在Background。
    const [provideBackgroundService] = defineProxy(() => new BackgroundService(), {
      namespace: BACKGROUND_NAMESPACE
    });
    provideBackgroundService(new ProvideBackgroundAdapter());
    void fineJobExecutorClient.start().catch((error) => {
      console.error("[FineJob BOSS 执行器] 后端通信启动失败", error);
    });
  }
});
