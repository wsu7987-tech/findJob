import { defineConfig } from "wxt";

import packageJson from "./package.json";

const bossMatches = ["*://zhipin.com/*", "*://*.zhipin.com/*"];

export default defineConfig({
  srcDir: "src",
  outDirTemplate: "{{browser}}-mv{{manifestVersion}}",
  modules: ["@wxt-dev/module-vue"],
  manifest: {
    name: packageJson.displayName,
    description: "FineJob 的BOSS默认招呼串行执行器；真实动作受插件权限和队列状态双重控制。",
    version: packageJson.version,
    permissions: ["storage", "tabs"],
    host_permissions: [...bossMatches, "http://127.0.0.1:8000/*"],
    web_accessible_resources: [
      {
        resources: ["boss.js"],
        matches: bossMatches
      }
    ]
  }
});
