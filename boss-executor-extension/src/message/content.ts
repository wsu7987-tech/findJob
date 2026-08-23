import type { BackgroundService } from "./background";
import {
  isMainWorldStatus,
  refreshFrameworkDetail,
  type FrameworkStatus,
  type MainWorldStatus
} from "../executor/framework-mode";
import { isBossReadOnlySnapshot } from "../platform/boss/types";

export const CONTENT_NAMESPACE = "fine-job:boss-executor:content:v1";

export class ContentService {
  private readonly mainWorldWaiters = new Set<() => void>();

  constructor(
    private readonly background: BackgroundService,
    private readonly status: FrameworkStatus
  ) {}

  async refreshBackground(): Promise<void> {
    try {
      const health = await this.background.health();
      this.status.background =
        health.ok && health.frameworkMode && !health.realActionsEnabled ? "ready" : "error";
    } catch {
      this.status.background = "error";
    }
    refreshFrameworkDetail(this.status);
  }

  async reportMainWorldReady(value: unknown): Promise<{ accepted: true }> {
    if (!isMainWorldStatus(value)) {
      this.status.mainWorld = "error";
      refreshFrameworkDetail(this.status);
      throw new Error("Main World 状态载荷无效");
    }

    this.status.mainWorld = "ready";
    this.status.page = value.pathname;
    refreshFrameworkDetail(this.status);
    for (const resolve of this.mainWorldWaiters) resolve();
    this.mainWorldWaiters.clear();
    return { accepted: true };
  }

  async reportBossSnapshot(value: unknown): Promise<{ accepted: true }> {
    if (!isBossReadOnlySnapshot(value)) {
      this.status.bossProbe = "unavailable";
      this.status.bossSnapshot = null;
      throw new Error("BOSS 岗位识别载荷无效");
    }

    this.status.bossProbe = value.state;
    this.status.bossSnapshot = value;
    this.status.page = value.pathname;
    return { accepted: true };
  }

  async waitForMainWorldReady(timeoutMs = 3000): Promise<void> {
    if (this.status.mainWorld === "ready") return;

    await new Promise<void>((resolve, reject) => {
      const done = () => {
        window.clearTimeout(timeoutId);
        resolve();
      };
      const timeoutId = window.setTimeout(() => {
        this.mainWorldWaiters.delete(done);
        this.status.mainWorld = "error";
        refreshFrameworkDetail(this.status);
        reject(new Error("Main World 健康检查超时"));
      }, timeoutMs);
      this.mainWorldWaiters.add(done);
    });
  }
}

export type { MainWorldStatus };
