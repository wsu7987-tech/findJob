import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "#imports": new URL("./tests/mocks/imports.ts", import.meta.url).pathname
    }
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"]
  }
});
