<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { browser } from "#imports";
import type { ComponentState, FrameworkStatus } from "../executor/framework-mode";
import type { ExecutorPanelController, FineJobQueueAction } from "../finejob/types";
import type { BossJobIdentity, BossPageKind, BossProbeState } from "../platform/boss/types";

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

const queueDisplay = computed(() => {
  const actions = props.status.executor.queue;
  const current = props.status.executor.currentAction;
  if (!current || actions.some((action) => action.id === current.id)) return actions;
  return [current, ...actions];
});
// 默认只展示队列前3项，展开后查看完整队列。
const visibleQueue = computed(() => queueExpanded.value ? queueDisplay.value : queueDisplay.value.slice(0, 3));
const queueHasMore = computed(() => queueDisplay.value.length > 3);
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

const returnCurrentToReview = () => {
  const action = props.status.executor.currentAction;
  if (action) void run(() => props.controller.returnToReview(action.id));
};

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

const pageLabel = (kind?: BossPageKind) =>
  kind
    ? ({ search: "搜索", recommend: "推荐", detail: "详情", other: "其他" })[kind]
    : "等待识别";

const suffix = (value?: string) => (value ? `…${value.slice(-8)}` : "未识别");

const contactedLabel = (value?: boolean | null) =>
  value === true ? "是" : value === false ? "否" : "未知";

const identitySourceLabel = (job?: BossJobIdentity | null) => {
  if (!job) return "未识别";
  return job.identitySource === "standalone-job-info"
    ? "详情页 _jobInfo"
    : "Vue 列表与详情";
};

const hrLabel = (job?: BossJobIdentity | null) => {
  if (!job) return "未识别";
  const displayName = `${job.bossName} ${job.bossTitle}`.trim();
  return displayName || `标识 ${suffix(job.encryptBossId)}（待验证）`;
};

const actionLabel = (action: FineJobQueueAction) => {
  if (["failed_before_dispatch", "failed_after_dispatch"].includes(action.execution_state)) return "执行失败";
  if (action.execution_state === "unknown_after_dispatch") return "未知结果";
  return "待执行";
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
        <dd :data-state="status.executor.executor?.permission_state === 'allowed' ? 'ready' : 'checking'">
          {{ status.executor.executor?.permission_state === "allowed" ? "已允许" : "已暂停" }}
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
          @click="run(() => controller.control('allow'))"
        >开始</button>
      </span>


      <div class="actions">

        <button
          v-if="status.executor.currentAction"
          :disabled="busy"
          @click="returnCurrentToReview"
        >退回确认</button>
      </div>
      <p v-if="!queueDisplay.length" class="detail">当前打招呼队列为空</p>
      <div v-else class="queue">
        <p v-for="action in visibleQueue" :key="action.id" class="detail">
          {{ action.id === status.executor.currentAction?.id ? "当前" : "待执行" }}：{{ action.job_title }} · {{ actionLabel(action) }}
        </p>
        <button v-if="queueHasMore" @click="queueExpanded = !queueExpanded">
          {{ queueExpanded ? "收起" : `展开其余 ${queueDisplay.length - 3} 项` }}
        </button>
      </div>
      <p v-if="status.executor.lastResult" class="detail">上次结果：{{ status.executor.lastResult }}</p>
    </section>
    <section v-if="status.executor.failedQueue.length" class="probe">
      <div class="queue-header">
        <p class="probe-title">执行失败</p>
        <span class="inline-actions">
          <button :disabled="busy" @click="run(() => controller.retryAllFailed())">全部重试</button>
          <button :disabled="busy" @click="run(() => controller.cancelAllFailed())">全部撤销</button>
        </span>
      </div>
      <div class="queue failed-queue">
        <div v-for="action in status.executor.failedQueue" :key="action.id" class="failed-item">
          <p class="detail">{{ action.job_title }} · 执行失败</p>
          <p v-if="action.last_error" class="detail">{{ action.last_error }}</p>
          <span class="inline-actions">
            <button :disabled="busy" @click="run(() => controller.retryFailedAction(action.id))">重试</button>
            <button :disabled="busy" @click="run(() => controller.cancelFailedAction(action.id))">撤销</button>
          </span>
        </div>
      </div>
    </section>
    <details class="probe">
      <summary class="probe-title">诊断详情</summary>
      <dl>
        <div><dt>Background</dt><dd :data-state="status.background">{{ label(status.background) }}</dd></div>
        <div><dt>Content</dt><dd :data-state="status.content">{{ label(status.content) }}</dd></div>
        <div><dt>Main World</dt><dd :data-state="status.mainWorld">{{ label(status.mainWorld) }}</dd></div>
        <div><dt>岗位识别</dt><dd :data-state="status.bossProbe">{{ probeLabel(status.bossProbe) }}</dd></div>
        <div><dt>登录状态</dt><dd>{{ status.bossSnapshot?.loggedIn ? "正常" : "未识别" }}</dd></div>
        <div><dt>岗位</dt><dd>{{ status.bossSnapshot?.job?.jobName || "未识别" }}</dd></div>
        <div><dt>身份来源</dt><dd>{{ identitySourceLabel(status.bossSnapshot?.job) }}</dd></div>
        <div><dt>HR</dt><dd>{{ hrLabel(status.bossSnapshot?.job) }}</dd></div>
        <div><dt>岗位 ID</dt><dd>{{ status.bossSnapshot?.job?.encryptJobId || "未识别" }}</dd></div>
        <div><dt>已沟通</dt><dd>{{ contactedLabel(status.bossSnapshot?.job?.contacted) }}</dd></div>
        <div><dt>代聊待上传</dt><dd :data-state="status.executor.chat?.eventOutboxBlocked ? 'error' : 'ready'">{{ status.executor.chat?.eventOutboxCount ?? 0 }} 条</dd></div>
        <div><dt>代聊结果待回传</dt><dd :data-state="status.executor.chat?.resultOutboxCount ? 'checking' : 'ready'">{{ status.executor.chat?.resultOutboxCount ?? 0 }} 条</dd></div>
      </dl>
      <p class="detail">{{ status.bossSnapshot?.reason || status.detail || "等待岗位页面数据" }}</p>
      <p v-if="status.executor.chat?.lastError" class="detail">自动代聊：{{ status.executor.chat.lastError }}</p>
    </details>
    <dl class="page-status">
      <div>
        <dt>当前页面</dt>
        <dd>{{ pageLabel(status.bossSnapshot?.pageKind) }}</dd>
      </div>
    </dl>
    <p v-if="uiError" class="detail">{{ uiError }}</p>
    </div>
  </section>
</template>
