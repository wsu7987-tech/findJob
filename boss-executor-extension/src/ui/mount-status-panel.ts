import { createApp } from "vue";

import type { FrameworkStatus } from "../executor/framework-mode";
import type { ExecutorPanelController } from "../finejob/types";
import StatusPanel from "./StatusPanel.vue";

const PANEL_HOST_ID = "fine-job-boss-executor-framework";

export const mountStatusPanel = (
  status: FrameworkStatus,
  controller: ExecutorPanelController
): (() => void) => {
  const existing = document.getElementById(PANEL_HOST_ID);
  if (existing) return () => undefined;

  const host = document.createElement("div");
  host.id = PANEL_HOST_ID;
  const shadowRoot = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    .finejob-panel { position: fixed; right: 18px; bottom: 18px; z-index: 2147483647;
      width: 328px; padding: 14px; border: 1px solid rgba(94,234,212,.35);
      border-radius: 12px; color: #e5e7eb; background: rgba(17,24,39,.96);
      box-shadow: 0 12px 36px rgba(0,0,0,.28); font: 12px/1.5 system-ui, sans-serif; }
    header { color: #5eead4; font-size: 14px; font-weight: 700; }
    p { margin: 6px 0 0; }
    .success { color: #86efac; }
    dl { margin: 10px 0 0; }
    dl div { display: flex; justify-content: space-between; gap: 12px; padding: 2px 0; }
    dt { color: #9ca3af; } dd { margin: 0; color: #d1d5db; }
    dd[data-state='ready'] { color: #86efac; }
    dd[data-state='checking'] { color: #fde68a; }
    dd[data-state='error'] { color: #fca5a5; }
    dd[data-state='waiting'] { color: #fde68a; }
    dd[data-state='unsupported'], dd[data-state='mismatch'], dd[data-state='unavailable'] { color: #fca5a5; }
    .probe { margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(156,163,175,.22); }
    .probe-title { color: #5eead4; font-weight: 700; }
    .page, .detail { color: #9ca3af; overflow-wrap: anywhere; }
    button, input { font: inherit; }
    input { box-sizing: border-box; width: 100%; margin-top: 8px; padding: 6px 8px;
      color: #e5e7eb; background: #111827; border: 1px solid #4b5563; border-radius: 6px; }
    .actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    button { padding: 5px 8px; border: 1px solid #4b5563; border-radius: 6px;
      color: #e5e7eb; background: #1f2937; cursor: pointer; }
    button.primary { color: #052e2b; background: #5eead4; border-color: #5eead4; }
    button.danger { color: #fecaca; border-color: #ef4444; }
    .queue { max-height: 150px; overflow: auto; margin: 6px 0 0; padding-left: 18px; }
  `;

  const mountPoint = document.createElement("div");
  shadowRoot.append(style, mountPoint);
  document.documentElement.append(host);
  const app = createApp(StatusPanel, { status, controller });
  app.mount(mountPoint);

  // 页面卸载时同步释放 Vue 实例，避免站内导航或扩展重载留下旧状态。
  return () => {
    app.unmount();
    host.remove();
  };
};
