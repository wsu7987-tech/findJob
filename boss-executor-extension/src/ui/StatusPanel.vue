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
const nowMs = ref(Date.now());
const panel = ref<HTMLElement | null>(null);
const collapsed = ref(false);
const position = ref<{ left: number; top: number } | null>(null);
const PANEL_UI_KEY = "finejobBossExecutorPanelUiV1";
let countdownTimer: number | null = null;
let dragging: { offsetX: number; offsetY: number } | null = null;

const displayAction = computed(() => props.status.executor.currentAction ?? props.status.executor.queue[0] ?? null);
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

const verificationRemaining = (action: FineJobQueueAction) => {
  if (!action.verification_due_at) return 0;
  return Math.max(0, Math.ceil((new Date(action.verification_due_at).getTime() - nowMs.value) / 1000));
};

const actionLabel = (action: FineJobQueueAction) => {
  if (action.execution_state === "request_accepted") {
    if (action.verification_state === "waiting_refresh") {
      return `平台已受理，${verificationRemaining(action)}秒后刷新验证`;
    }
    if (action.verification_state === "refreshing") return "正在刷新当前岗位页面";
    if (action.verification_state === "waiting_snapshot") return "正在确认是否已建立沟通";
    if (action.verification_state === "pending") return "已提交，页面暂未确认";
    return "平台已受理，待后续验证";
  }
  if (action.verification_state === "page_confirmed") return "页面验证成功";
  if (action.verification_state === "manual_confirmed") return "人工核验完成";
  if (action.execution_state === "unknown_after_dispatch") return "未知错误";
  return action.execution_state;
};

onMounted(() => {
  countdownTimer = window.setInterval(() => { nowMs.value = Date.now(); }, 1000);
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
  if (countdownTimer !== null) window.clearInterval(countdownTimer);
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
      <div>
        <dt>当前页面</dt>
        <dd>{{ pageLabel(status.bossSnapshot?.pageKind) }}</dd>
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
      <div class="actions">
        <button class="primary" :disabled="busy" @click="run(() => controller.control('allow'))">允许自动打招呼</button>
        <button :disabled="busy" @click="run(() => controller.control('pause'))">暂停</button>
        <button :disabled="busy" @click="run(() => controller.control('resume'))">恢复队列</button>
        <button class="danger" :disabled="busy" @click="run(() => controller.control('emergency_stop'))">紧急停止</button>
        <button
          v-if="status.executor.currentAction"
          :disabled="busy"
          @click="returnCurrentToReview"
        >退回确认</button>
      </div>
      <p class="detail">
        {{ displayAction
          ? `${status.executor.currentAction ? "当前" : "下一项"}：${displayAction.job_title} · ${actionLabel(displayAction)}`
          : "当前打招呼队列为空" }}
      </p>
      <p v-if="status.executor.lastResult" class="detail">上次结果：{{ status.executor.lastResult }}</p>
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
    <p v-if="uiError" class="detail">{{ uiError }}</p>
    </div>
  </section>
</template>
