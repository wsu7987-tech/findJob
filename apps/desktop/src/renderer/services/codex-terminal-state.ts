import { ref } from "vue";

export type CodexTerminalHandle = {
  clear: () => void;
  copyAll: () => Promise<boolean>;
  copySelection: () => Promise<boolean>;
  fit: () => void;
  focus: () => void;
  paste: () => Promise<boolean>;
};

const terminal = ref<CodexTerminalHandle | null>(null);
const terminalSize = ref({ cols: 120, rows: 36 });

// 工作台与全局终端面板共享同一个终端实例和尺寸，避免页面切换重新创建终端。
export const useCodexTerminalState = () => ({ terminal, terminalSize });
