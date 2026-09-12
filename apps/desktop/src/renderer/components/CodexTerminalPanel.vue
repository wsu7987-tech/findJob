<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import CodexTerminal from "@/components/CodexTerminal.vue";
import { useCodexTerminalState, type CodexTerminalHandle } from "@/services/codex-terminal-state";

const props = defineProps<{
  visible: boolean;
}>();

const { terminal, terminalSize } = useCodexTerminalState();
const terminalRef = ref<CodexTerminalHandle | null>(null);
const copyMessage = ref("");
const route = useRoute();
const router = useRouter();

const isWorkflowTask = computed(() => route.query.task === "deep-job-search");

const showClipboardMessage = (successMessage: string, failureMessage: string, success: boolean) => {
  copyMessage.value = success ? successMessage : failureMessage;
  globalThis.setTimeout(() => {
    copyMessage.value = "";
  }, 1800);
};

const copySelection = async () => {
  showClipboardMessage(
    "已复制",
    "请先选择要复制的内容",
    Boolean(await terminal.value?.copySelection())
  );
};

const copyAll = async () => {
  showClipboardMessage(
    "已复制",
    "当前没有可复制的会话内容",
    Boolean(await terminal.value?.copyAll())
  );
};

const paste = async () => {
  showClipboardMessage(
    "已粘贴",
    "剪贴板没有可粘贴的文本",
    Boolean(await terminal.value?.paste())
  );
};

const clearTerminal = () => terminal.value?.clear();

const returnToTaskCockpit = async () => {
  const workflowRunId = String(route.query.workflow_run_id || "").trim();
  if (!workflowRunId) return;
  await router.push({
    name: "fine-job-task-cockpit",
    query: { workflow_run_id: workflowRunId }
  });
};

onMounted(() => {
  terminal.value = terminalRef.value;
});

onBeforeUnmount(() => {
  // 全局面板销毁时释放共享引用，避免其他页面调用已失效的终端对象。
  if (terminal.value === terminalRef.value) {
    terminal.value = null;
  }
});

watch(
  () => terminalRef.value,
  (handle) => {
    terminal.value = handle;
  }
);

watch(
  () => props.visible,
  async (name) => {
    if (!name) return;
    await nextTick();
    terminal.value?.fit();
  }
);
</script>

<template>
  <section v-show="props.visible" class="surface-card terminal-card">
    <div class="terminal-toolbar">
      <span class="secondary-text">拖动选择文本后可按 Ctrl/Cmd+C 复制</span>
      <div class="card-actions">
        <el-button v-if="isWorkflowTask" @click="returnToTaskCockpit">返回任务驾驶舱</el-button>
        <span v-if="copyMessage" class="secondary-text">{{ copyMessage }}</span>
        <el-button :disabled="!terminal" @click="paste">粘贴</el-button>
        <el-button :disabled="!terminal" @click="clearTerminal">Clear</el-button>
        <el-button :disabled="!terminal" @click="copySelection">复制选中内容</el-button>
        <el-button :disabled="!terminal" @click="copyAll">复制全部会话</el-button>
      </div>
    </div>
    <CodexTerminal ref="terminalRef" @ready="terminalSize = $event" />
  </section>
</template>

<style scoped>
.terminal-card {
  padding: 10px;
}

.terminal-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 2px 10px;
}
</style>
