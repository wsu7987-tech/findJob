import { defineProxy } from "comctx";

import { defineBackground } from "#imports";

import {
  BACKGROUND_NAMESPACE,
  BackgroundService,
  ProvideBackgroundAdapter
} from "../message/background";
import { fineJobExecutorClient } from "../finejob/client";
import { bossChatCoordinator } from "../finejob/chat-coordinator";

export default defineBackground({
  main() {
    // Background 负责服务入口、执行凭证和 FineJob 任务列表。
    const [provideBackgroundService] = defineProxy(() => new BackgroundService(), {
      namespace: BACKGROUND_NAMESPACE
    });
    provideBackgroundService(new ProvideBackgroundAdapter());
    void fineJobExecutorClient.start().catch((error) => {
      console.error("[FineJob BOSS 执行器] 后端通信启动失败", error);
    });
    void bossChatCoordinator.start().catch((error) => {
      console.error("[FineJob BOSS 执行器] 自动代聊协调器启动失败", error);
    });
  }
});
