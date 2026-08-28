<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { getCodexBridge } from "@/services/desktop-bridge";

const emit = defineEmits<{
  (event: "ready", size: { cols: number; rows: number }): void;
}>();

const host = ref<HTMLElement | null>(null);
let terminal: Terminal | null = null;
let fitAddon: FitAddon | null = null;
let resizeObserver: ResizeObserver | null = null;
let removeOutputListener: (() => void) | null = null;
let removePasteListener: (() => void) | null = null;

const TERMINAL_SCROLLBACK = 100_000;

const writeClipboard = async (text: string) => {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API 不可用时使用隐藏文本框完成桌面端复制。
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    return copied;
  }
};

const copySelection = async () => {
  if (!terminal?.hasSelection()) return false;
  return writeClipboard(terminal.getSelection());
};

const copyAll = async () => {
  if (!terminal) return false;
  terminal.selectAll();
  const copied = await copySelection();
  terminal.clearSelection();
  return copied;
};

const pasteFromClipboard = async () => {
  if (!terminal) return false;
  try {
    const text = await navigator.clipboard.readText();
    if (!text) return false;
    // 由 xterm 处理括号粘贴和换行，保证多行内容按终端输入协议发送。
    terminal.paste(text);
    return true;
  } catch {
    return false;
  }
};

const handlePaste = (event: ClipboardEvent) => {
  const text = event.clipboardData?.getData("text/plain") ?? "";
  if (!terminal || !text) return;
  event.preventDefault();
  event.stopPropagation();
  terminal.paste(text);
};

const handleCustomKey = (event: KeyboardEvent) => {
  const isCopyShortcut =
    (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "c";
  if (isCopyShortcut && terminal?.hasSelection()) {
    // 有选中文本时拦截 Ctrl/Cmd+C，避免把复制误发送成 Codex 中断。
    void copySelection();
    return false;
  }

  const isPasteShortcut =
    (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "v";
  if (isPasteShortcut) {
    // 粘贴快捷键由页面读取剪贴板，避免 Electron 中隐藏输入框收不到 Ctrl/Cmd+V。
    void pasteFromClipboard();
    return false;
  }

  return true;
};

// 对外提供终端聚焦能力，供新建会话完成后把输入焦点交给终端。
const focus = () => terminal?.focus();

defineExpose({ copyAll, copySelection, focus, paste: pasteFromClipboard });

onMounted(() => {
  if (!host.value) return;
  terminal = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: "Cascadia Mono, Consolas, monospace",
    fontSize: 13,
    // 保留足够的会话滚动内容，便于回看和复制较长的 Codex 对话。
    scrollback: TERMINAL_SCROLLBACK,
    theme: { background: "#101714", foreground: "#e7efe9", cursor: "#86d29a" }
  });
  fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(host.value);
  fitAddon.fit();
  const bridge = getCodexBridge();
  terminal.attachCustomKeyEventHandler(handleCustomKey);
  host.value.addEventListener("paste", handlePaste, true);
  removePasteListener = () => host.value?.removeEventListener("paste", handlePaste, true);
  terminal.onData((data) => bridge?.writeCodex?.(data));
  removeOutputListener = bridge?.onCodexOutput?.(({ data }) => terminal?.write(data)) ?? null;
  resizeObserver = new ResizeObserver(() => {
    fitAddon?.fit();
    if (terminal) bridge?.resizeCodex?.(terminal.cols, terminal.rows);
  });
  resizeObserver.observe(host.value);
  emit("ready", { cols: terminal.cols, rows: terminal.rows });
});

onBeforeUnmount(() => {
  removeOutputListener?.();
  removePasteListener?.();
  resizeObserver?.disconnect();
  terminal?.dispose();
});
</script>

<template>
  <div ref="host" class="codex-terminal" aria-label="Codex 交互终端" />
</template>

<style scoped>
.codex-terminal {
  height: 520px;
  padding: 12px;
  overflow: hidden;
  border-radius: 12px;
  background: #101714;
}
</style>
