import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": new URL("./src/renderer", import.meta.url).pathname
    }
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "electron/**/*.test.ts"]
  }
});
