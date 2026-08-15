import { describe, expect, it } from "vitest";

import { createQuitState } from "./quit-state";

describe("createQuitState", () => {
  it("stops hiding the main window to tray once app quit has begun", () => {
    const quitState = createQuitState();

    expect(quitState.shouldHideMainWindowOnClose(true)).toBe(true);

    quitState.beginQuit();

    expect(quitState.shouldHideMainWindowOnClose(true)).toBe(false);
  });

  it("does not hide the main window when close-to-tray is disabled", () => {
    const quitState = createQuitState();

    expect(quitState.shouldHideMainWindowOnClose(false)).toBe(false);
  });
});
