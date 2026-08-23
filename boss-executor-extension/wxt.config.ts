import { defineConfig } from "wxt";

import packageJson from "./package.json";

const bossMatches = ["*://zhipin.com/*", "*://*.zhipin.com/*"];

export default defineConfig({
  srcDir: "src",
  outDirTemplate: "{{browser}}-mv{{manifestVersion}}",
  modules: ["@wxt-dev/module-vue"],
  manifest: {
    name: packageJson.displayName,
    description: "FineJob 的只读 BOSS 浏览器执行器框架；当前版本不执行真实动作。",
    version: packageJson.version,
    permissions: [],
    host_permissions: bossMatches,
    web_accessible_resources: [
      {
        resources: ["boss.js"],
        matches: bossMatches
      }
    ]
  }
});
