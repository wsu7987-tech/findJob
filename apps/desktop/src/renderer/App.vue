<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";
import { RouterView, useRoute } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import CodexTerminalPanel from "@/components/CodexTerminalPanel.vue";
import SettingsDrawer from "@/components/SettingsDrawer.vue";
import { useConfigStore } from "@/stores/config";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useFineJobCodexStore } from "@/stores/fineJobCodex";
import { useFineJobWorkflowRunStore } from "@/stores/fineJobWorkflowRun";
import { useNoticesStore } from "@/stores/notices";
import { startFineJobWorkflowCodexController } from "@/services/fineJobWorkflowCodexController";

const noticesStore = useNoticesStore();
const configStore = useConfigStore();
const executorStore = useFineJobBossExecutorStore();
const codexStore = useFineJobCodexStore();
const workflowRunStore = useFineJobWorkflowRunStore();
const settingsOpen = ref(false);
const notices = computed(() => noticesStore.items);
const isShelllessRoute = computed(() => false);
const route = useRoute();
const showCodexTerminal = computed(() => route.name === "fine-job-codex");
const workflowCodexController = startFineJobWorkflowCodexController({
  workflowStore: workflowRunStore,
  codexStore
});

watchEffect(() => {
  if (typeof document === "undefined") {
    return;
  }

  document.body.classList.toggle("shellless-route", isShelllessRoute.value);
});

onBeforeUnmount(() => {
  workflowCodexController.stop();
  if (typeof document === "undefined") {
    return;
  }

  document.body.classList.remove("shellless-route");
});

onMounted(() => {
  void configStore.probeGenerationCapabilities();
  // 桌面端启动时主动确认一次插件连接状态。
  void executorStore.testHeartbeat().catch(() => undefined);
  workflowCodexController.start();
});
</script>

<template>
  <div
    v-if="!isShelllessRoute && notices.length"
    class="notice-tray"
    role="status"
    aria-live="polite"
    aria-atomic="false"
  >
    <el-alert
      v-for="notice in notices"
      :key="notice.id"
      :title="notice.title"
      :description="notice.message"
      :type="notice.kind"
      show-icon
      @close="noticesStore.remove(notice.id)"
    />
  </div>

  <RouterView v-if="isShelllessRoute" />

  <AppShell v-else @open-settings="settingsOpen = true">
    <!-- 终端全局挂载，页面切换时继续接收 Codex 输出并保留原有画面。 -->
    <CodexTerminalPanel :visible="showCodexTerminal" />
    <RouterView @open-settings="settingsOpen = true" />
  </AppShell>

  <SettingsDrawer v-if="!isShelllessRoute" v-model:open="settingsOpen" />
</template>
