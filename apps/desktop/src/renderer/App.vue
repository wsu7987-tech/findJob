<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";
import { RouterView } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import SettingsDrawer from "@/components/SettingsDrawer.vue";
import { useConfigStore } from "@/stores/config";
import { useFineJobBossExecutorStore } from "@/stores/fineJobBossExecutor";
import { useNoticesStore } from "@/stores/notices";

const noticesStore = useNoticesStore();
const configStore = useConfigStore();
const executorStore = useFineJobBossExecutorStore();
const settingsOpen = ref(false);
const notices = computed(() => noticesStore.items);
const isShelllessRoute = computed(() => false);

watchEffect(() => {
  if (typeof document === "undefined") {
    return;
  }

  document.body.classList.toggle("shellless-route", isShelllessRoute.value);
});

onBeforeUnmount(() => {
  if (typeof document === "undefined") {
    return;
  }

  document.body.classList.remove("shellless-route");
});

onMounted(() => {
  void configStore.probeGenerationCapabilities();
  // 桌面端启动时主动确认一次插件连接状态。
  void executorStore.testHeartbeat().catch(() => undefined);
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
    <RouterView @open-settings="settingsOpen = true" />
  </AppShell>

  <SettingsDrawer v-if="!isShelllessRoute" v-model:open="settingsOpen" />
</template>
