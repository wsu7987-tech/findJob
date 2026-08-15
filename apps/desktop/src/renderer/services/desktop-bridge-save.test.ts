import { describe, expect, it, vi } from "vitest";

import { saveTextFile } from "./desktop-bridge";


describe("desktop bridge save helper", () => {
  it("delegates saving text files to the Electron bridge", async () => {
    const saveTextFileMock = vi.fn().mockResolvedValue("D:\\KnowledgeCurator\\outputs\\demo.md");

    await expect(
      saveTextFile(
        {
          title: "保存解析结果",
          defaultPath: "D:\\KnowledgeCurator\\outputs\\demo.md",
          content: "# Demo",
          filters: [{ name: "Markdown", extensions: ["md"] }]
        },
        {
          desktopBridge: {
            saveTextFile: saveTextFileMock
          }
        }
      )
    ).resolves.toBe("D:\\KnowledgeCurator\\outputs\\demo.md");

    expect(saveTextFileMock).toHaveBeenCalledWith({
      title: "保存解析结果",
      defaultPath: "D:\\KnowledgeCurator\\outputs\\demo.md",
      content: "# Demo",
      filters: [{ name: "Markdown", extensions: ["md"] }]
    });
  });
});
