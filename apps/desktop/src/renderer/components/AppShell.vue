<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import {
  ArrowRight,
  ChatDotRound,
  Clock,
  Document,
  Link,
  List,
  Monitor,
  Search,
  Setting,
  Top,
  TrendCharts,
  User,
  Wallet
} from "@element-plus/icons-vue";

import SystemStatusChip from "@/components/SystemStatusChip.vue";
import { getMainWindowState, setMainWindowAlwaysOnTop } from "@/services/desktop-bridge";
import { useConfigStore } from "@/stores/config";

const emit = defineEmits<{
  (event: "open-settings"): void;
}>();

const route = useRoute();
const configStore = useConfigStore();

const navigation = [
  { label: "投递准备", routeName: "fine-job-dashboard", icon: TrendCharts },
  { label: "简历资料", routeName: "fine-job-resumes", icon: Document },
  { label: "平台登录", routeName: "fine-job-platform", icon: Monitor },
  { label: "岗位采集", routeName: "fine-job-capture", icon: Search },
  { label: "历史采集", routeName: "fine-job-capture-history", icon: Clock },
  { label: "策略管理", routeName: "fine-job-strategy", icon: Wallet },
  { label: "运行状态", routeName: "fine-job-runs", icon: List },
  { label: "待确认", routeName: "fine-job-review", icon: User },
  { label: "自动代聊", routeName: "fine-job-chat", icon: ChatDotRound },
  { label: "动作日志", routeName: "fine-job-logs", icon: User }
];

const activeTitle = computed(() => (route.meta.title as string) ?? "AI 求职驾驶舱");
const actionSummary = computed(() =>
  configStore.generationReady
    ? "AI 能力已就绪，可开始接入求职流程。"
    : configStore.generationBlockReason || "请先完成必要配置与连通测试。"
);
const connectivityActionLabel = computed(() =>
  configStore.generationReady ? "能力已就绪" : "一键联通测试"
);
const generationStatusLabel = computed(() =>
  configStore.data?.reasoning_executor === "codex-cli" ? "Codex CLI 必需" : "LLM 必需"
);
const mainWindowAlwaysOnTop = ref(false);
const updatingMainWindowPin = ref(false);
const mainWindowPinLabel = computed(() =>
  mainWindowAlwaysOnTop.value ? "取消置顶在前面" : "置顶在前面"
);

const syncMainWindowState = async () => {
  const state = await getMainWindowState();
  mainWindowAlwaysOnTop.value = state?.alwaysOnTop ?? false;
};

const runConnectivityCheck = async () => {
  await configStore.probeGenerationCapabilities();
};

const toggleMainWindowPin = async () => {
  updatingMainWindowPin.value = true;
  try {
    const result = await setMainWindowAlwaysOnTop(!mainWindowAlwaysOnTop.value);
    mainWindowAlwaysOnTop.value = result?.alwaysOnTop ?? !mainWindowAlwaysOnTop.value;
  } finally {
    updatingMainWindowPin.value = false;
  }
};

onMounted(() => {
  void syncMainWindowState();
});
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主内容</a>
  <div class="app-shell">
    <aside class="app-shell__sidebar">
      <div class="brand-card">
        <p class="brand-card__eyebrow">FineJob</p>
        <h1>AI 求职驾驶舱</h1>
        <span class="brand-card__mode">Local Career Cockpit</span>
      </div>

      <nav class="nav-stack">
        <RouterLink
          v-for="item in navigation"
          :key="item.routeName"
          :to="{ name: item.routeName }"
          class="nav-pill"
          :class="{ 'nav-pill--active': route.name === item.routeName }"
        >
          <component :is="item.icon" aria-hidden="true" />
          <span>{{ item.label }}</span>
          <ArrowRight aria-hidden="true" />
        </RouterLink>
      </nav>

      <div class="sidebar-footnote">
        <p>当前工作区</p>
        <strong>{{ actionSummary }}</strong>
      </div>
    </aside>

    <section class="app-shell__workspace">
      <header class="system-bar">
        <div class="system-bar__context">
          <p class="app-shell__eyebrow">本地桌面端</p>
          <h2>{{ activeTitle }}</h2>
        </div>

        <div class="system-bar__status">
          <SystemStatusChip label="Backend" :state="configStore.backendStatus" />
          <SystemStatusChip
            :label="generationStatusLabel"
            :state="configStore.selectedGenerationStatus"
          />
          <SystemStatusChip label="Embedding 可选" :state="configStore.embeddingStatus" />
        </div>

        <div class="system-bar__actions">
          <el-tooltip :content="mainWindowPinLabel" placement="top">
            <el-button
              data-testid="main-window-pin"
              class="system-bar__pin"
              plain
              circle
              :aria-label="mainWindowPinLabel"
              :type="mainWindowAlwaysOnTop ? 'primary' : undefined"
              :icon="Top"
              :loading="updatingMainWindowPin"
              @click="toggleMainWindowPin"
            />
          </el-tooltip>
          <el-button
            data-testid="connectivity-check"
            type="primary"
            plain
            :icon="Link"
            :loading="configStore.probingCapabilities"
            @click="runConnectivityCheck"
          >
            {{ connectivityActionLabel }}
          </el-button>
          <el-button type="primary" :icon="Setting" @click="emit('open-settings')">
            系统配置
          </el-button>
        </div>
      </header>

      <main id="main-content" class="workspace-scroll" tabindex="-1">
        <slot />
      </main>
    </section>
  </div>
</template>

<style scoped>
.system-bar__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.system-bar__pin {
  flex-shrink: 0;
}
</style>
