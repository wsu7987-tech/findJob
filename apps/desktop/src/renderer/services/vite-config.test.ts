import { describe, expect, it } from "vitest";

import viteConfig from "../../../vite.config";

describe("vite production config", () => {
  it("uses a relative base so Electron can load built assets over file://", () => {
    expect(viteConfig.base).toBe("./");
  });
});
