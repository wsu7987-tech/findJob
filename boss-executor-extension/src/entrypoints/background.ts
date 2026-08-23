import { defineProxy } from "comctx";

import { defineBackground } from "#imports";

import {
  BACKGROUND_NAMESPACE,
  BackgroundService,
  ProvideBackgroundAdapter
} from "../message/background";

export default defineBackground({
  main() {
    // 适配 boss-helper 的 Background 服务入口；框架阶段只暴露只读健康检查。
    const [provideBackgroundService] = defineProxy(() => new BackgroundService(), {
      namespace: BACKGROUND_NAMESPACE
    });
    provideBackgroundService(new ProvideBackgroundAdapter());
  }
});
