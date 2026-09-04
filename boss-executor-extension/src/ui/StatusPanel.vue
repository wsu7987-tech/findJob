<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { browser } from "#imports";
import type { ComponentState, FrameworkStatus } from "../executor/framework-mode";
import type { ExecutorPanelController, FineJobQueueAction } from "../finejob/types";
import type { BossProbeState } from "../platform/boss/types";

const props = defineProps<{
  status: FrameworkStatus;
  controller: ExecutorPanelController;
}>();

const pairingCode = ref("");
const busy = ref(false);
const uiError = ref("");
const panel = ref<HTMLElement | null>(null);
const collapsed = ref(false);
const queueExpanded = ref(false);
const position = ref<{ left: number; top: number } | null>(null);
const PANEL_UI_KEY = "finejobBossExecutorPanelUiV1";
let dragging: { offsetX: number; offsetY: number } | null = null;

// 默认只展示任务列表前3项，展开后查看完整列表。
const visibleQueue = computed(() => queueExpanded.value ? props.status.executor.queue : props.status.executor.queue.slice(0, 3));
const queueHasMore = computed(() => props.status.executor.queue.length > 3);
const queueRunning = computed(() => props.status.executor.executor?.queue_state === "running");
const panelStyle = computed(() => position.value ? {
  left: `${position.value.left}px`,
  top: `${position.value.top}px`,
  right: "auto",
  bottom: "auto"
} : {});

const persistPanelUi = async () => {
  await browser.storage.local.set({
    [PANEL_UI_KEY]: {
      collapsed: collapsed.value,
      position: position.value
    }
  });
};

const clampPosition = () => {
  if (!position.value || !panel.value) return;
  const rect = panel.value.getBoundingClientRect();
  position.value = {
    left: Math.max(8, Math.min(position.value.left, window.innerWidth - rect.width - 8)),
    top: Math.max(8, Math.min(position.value.top, window.innerHeight - rect.height - 8))
  };
};

const toggleCollapsed = async () => {
  collapsed.value = !collapsed.value;
  await nextTick();
  clampPosition();
  await persistPanelUi();
};

const onPointerMove = (event: PointerEvent) => {
  if (!dragging) return;
  position.value = {
    left: event.clientX - dragging.offsetX,
    top: event.clientY - dragging.offsetY
  };
  clampPosition();
};

const stopDragging = () => {
  if (!dragging) return;
  dragging = null;
  window.removeEventListener("pointermove", onPointerMove);
  window.removeEventListener("pointerup", stopDragging);
  void persistPanelUi();
};

const startDragging = (event: PointerEvent) => {
  if ((event.target as HTMLElement).closest("button") || !panel.value) return;
  const rect = panel.value.getBoundingClientRect();
  position.value = { left: rect.left, top: rect.top };
  dragging = { offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top };
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", stopDragging, { once: true });
};

const run = async (operation: () => Promise<void>) => {
  busy.value = true;
  uiError.value = "";
  try {
    await operation();
  } catch (error) {
    uiError.value = (error as Error).message;
  } finally {
    busy.value = false;
  }
};

const pair = () => run(async () => {
  if (!pairingCode.value.trim()) throw new Error("请输入FineJob生成的配对码");
  await props.controller.pair(pairingCode.value);
  pairingCode.value = "";
});

const label = (state: ComponentState) =>
  ({ checking: "检查中", ready: "正常", error: "异常" })[state];

const probeLabel = (state: BossProbeState) =>
  ({
    ready: "正常",
    waiting: "等待",
    unsupported: "不支持",
    mismatch: "错配",
    unavailable: "不可执行"
  })[state];

const actionLabel = (action: FineJobQueueAction) => {
  if (action.execution_state === "failed") return "执行失败";
  if (action.execution_state === "unknown") return "结果未知";
  if (action.execution_state === "running") return "执行中";
  return "待处理";
};

onMounted(() => {
  void browser.storage.local.get(PANEL_UI_KEY).then(async (stored) => {
    const ui = stored[PANEL_UI_KEY] as {
      collapsed?: boolean;
      position?: { left: number; top: number } | null;
    } | undefined;
    collapsed.value = Boolean(ui?.collapsed);
    position.value = ui?.position ?? null;
    await nextTick();
    clampPosition();
  });
  window.addEventListener("resize", clampPosition);
});
onBeforeUnmount(() => {
  stopDragging();
  window.removeEventListener("resize", clampPosition);
});
</script>

<template>
  <section
    ref="panel"
    class="finejob-panel"
    :class="{ 'finejob-panel--collapsed': collapsed }"
    :style="panelStyle"
    aria-label="FineJob BOSS 执行器状态"
  >
    <header class="panel-header" @pointerdown="startDragging">
      <span>FineJob</span>
      <span v-if="collapsed" class="compact-status">
        {{ status.executor.connected ? "● 已连接" : "○ 未连接" }} · 队列 {{ status.executor.queue.length }}
      </span>
      <button
        class="icon-button"
        type="button"
        :aria-label="collapsed ? '展开 FineJob 面板' : '缩小 FineJob 面板'"
        :title="collapsed ? '展开' : '缩小'"
        @click.stop="toggleCollapsed"
      >{{ collapsed ? "展开" : "缩小" }}</button>
    </header>
    <div v-if="!collapsed" class="panel-body">
    <dl>
      <div>
        <dt>FineJob</dt>
        <dd :data-state="status.executor.connected ? 'ready' : 'error'">
          {{ status.executor.connected ? "已连接" : status.executor.paired ? "连接失败" : "未配对" }}
          <span v-if="status.executor.paired" class="inline-actions">
            <button :disabled="busy" @click="run(() => controller.testHeartbeat())">心跳测试</button>
            <button :disabled="busy" @click="run(() => controller.disconnect())">断开</button>
          </span>
        </dd>
      </div>
      <div>
        <dt>自动打招呼</dt>
        <dd :data-state="status.executor.executor?.queue_state === 'running' ? 'ready' : 'checking'">
          {{ status.executor.executor?.queue_state === "running" ? "运行中" : "已暂停" }}
        </dd>
      </div>
      <div>
        <dt>自动代聊</dt>
        <dd :data-state="status.executor.chat?.eventOutboxBlocked ? 'error' : status.executor.chat?.listenEnabled ? 'ready' : 'checking'">
          {{ status.executor.chat?.listenEnabled ? `监听中 · 待上传 ${status.executor.chat.eventOutboxCount}` : "未监听" }}
        </dd>
      </div>
      <div>
        <dt>队列</dt>
        <dd>{{ status.executor.queue.length }} 项</dd>
      </div>
    </dl>
    <section v-if="!status.executor.paired" class="probe">
      <p class="probe-title">与FineJob配对</p>
      <input v-model="pairingCode" inputmode="numeric" maxlength="20" placeholder="输入FineJob配对码" />
      <div class="actions">
        <button class="primary" :disabled="busy" @click="pair">配对</button>
      </div>
    </section>
    <section v-else class="probe">
      <p class="probe-title">执行控制</p>
      <span>
        <button
          v-if="queueRunning"
          :disabled="busy"
          @click="run(() => controller.control('pause'))"
        >暂停</button>
        <button
          v-else
          class="primary"
          :disabled="busy"
          @click="run(() => controller.control('start'))"
        >开始</button>
      </span>

      <p class="detail">{{ status.executor.detail }}</p>
      <p v-if="!status.executor.queue.length" class="detail">当前没有待处理任务</p>
      <div v-else class="queue">
        <p v-for="action in visibleQueue" :key="action.id" class="detail">
          {{ action.job_title }} · {{ actionLabel(action) }}
        </p>
        <button v-if="queueHasMore" @click="queueExpanded = !queueExpanded">
          {{ queueExpanded ? "收起" : `展开其余 ${status.executor.queue.length - 3} 项` }}
        </button>
      </div>
    </section>
    <details class="probe">
      <summary class="probe-title">诊断详情</summary>
      <dl>
        <div><dt>Background</dt><dd :data-state="status.background">{{ label(status.background) }}</dd></div>
        <div><dt>Content</dt><dd :data-state="status.content">{{ label(status.content) }}</dd></div>
        <div><dt>Main World</dt><dd :data-state="status.mainWorld">{{ label(status.mainWorld) }}</dd></div>
        <div><dt>岗位识别</dt><dd :data-state="status.bossProbe">{{ probeLabel(status.bossProbe) }}</dd></div>
        <div><dt>代聊待上传</dt><dd :data-state="status.executor.chat?.eventOutboxBlocked ? 'error' : 'ready'">{{ status.executor.chat?.eventOutboxCount ?? 0 }} 条</dd></div>
        <div><dt>代聊结果待回传</dt><dd :data-state="status.executor.chat?.resultOutboxCount ? 'checking' : 'ready'">{{ status.executor.chat?.resultOutboxCount ?? 0 }} 条</dd></div>
      </dl>
      <p class="detail">{{ status.detail }}</p>
      <p v-if="status.executor.chat?.lastError" class="detail">自动代聊：{{ status.executor.chat.lastError }}</p>
    </details>
    <p v-if="uiError" class="detail">{{ uiError }}</p>
    </div>
  </section>
</template>
