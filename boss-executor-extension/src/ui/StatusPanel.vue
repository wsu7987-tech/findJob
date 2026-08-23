<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
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
let countdownTimer: number | null = null;

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
});
onBeforeUnmount(() => {
  if (countdownTimer !== null) window.clearInterval(countdownTimer);
});
</script>

<template>
  <section class="finejob-panel" aria-label="FineJob BOSS 执行器状态">
    <header>FineJob BOSS 执行器</header>
    <p class="success">框架已加载</p>
    <dl>
      <div>
        <dt>Background</dt>
        <dd :data-state="status.background">{{ label(status.background) }}</dd>
      </div>
      <div>
        <dt>Content</dt>
        <dd :data-state="status.content">{{ label(status.content) }}</dd>
      </div>
      <div>
        <dt>Main World</dt>
        <dd :data-state="status.mainWorld">{{ label(status.mainWorld) }}</dd>
      </div>
      <div>
        <dt>模式</dt>
        <dd>串行默认招呼</dd>
      </div>
      <div>
        <dt>FineJob</dt>
        <dd :data-state="status.executor.connected ? 'ready' : 'error'">
          {{ status.executor.connected ? "已连接" : status.executor.paired ? "连接失败" : "未配对" }}
        </dd>
      </div>
      <div>
        <dt>自动打招呼</dt>
        <dd :data-state="status.executor.executor?.permission_state === 'allowed' ? 'ready' : 'checking'">
          {{ status.executor.executor?.permission_state || "未授权" }}
        </dd>
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
        <button class="danger" :disabled="busy" @click="run(() => controller.control('emergency_stop'))">紧急停止</button>
      </div>
      <p class="detail">队列：{{ status.executor.executor?.queue_state || "等待连接" }}；风险：{{ status.executor.executor?.risk_state || "none" }}</p>
      <ol v-if="status.executor.queue.length" class="queue">
        <li v-for="item in status.executor.queue" :key="item.id">
          {{ item.job_title }} · {{ actionLabel(item) }}
          <button
            v-if="!['dispatch_started', 'request_accepted', 'succeeded', 'failed_after_dispatch', 'unknown_after_dispatch'].includes(item.execution_state)"
            :disabled="busy"
            @click="run(() => controller.returnToReview(item.id))"
          >退回</button>
        </li>
      </ol>
      <p v-else class="detail">当前打招呼队列为空</p>
      <p v-if="status.executor.lastResult" class="detail">上次结果：{{ status.executor.lastResult }}</p>
    </section>
    <section class="probe">
      <p class="probe-title">岗位只读识别</p>
      <dl>
        <div>
          <dt>岗位识别</dt>
          <dd :data-state="status.bossProbe">{{ probeLabel(status.bossProbe) }}</dd>
        </div>
        <div>
          <dt>页面类型</dt>
          <dd>{{ pageLabel(status.bossSnapshot?.pageKind) }}</dd>
        </div>
        <div>
          <dt>登录状态</dt>
          <dd>{{ status.bossSnapshot?.loggedIn ? "正常" : "未识别" }}</dd>
        </div>
        <div>
          <dt>岗位</dt>
          <dd>{{ status.bossSnapshot?.job?.jobName || "未识别" }}</dd>
        </div>
        <div>
          <dt>身份来源</dt>
          <dd>{{ identitySourceLabel(status.bossSnapshot?.job) }}</dd>
        </div>
        <div>
          <dt>HR</dt>
          <dd>{{ hrLabel(status.bossSnapshot?.job) }}</dd>
        </div>
        <div>
          <dt>岗位 ID</dt>
          <dd>{{ status.bossSnapshot?.job?.encryptJobId }}</dd>
        </div>
        <div>
          <dt>已沟通</dt>
          <dd>{{ contactedLabel(status.bossSnapshot?.job?.contacted) }}</dd>
        </div>
      </dl>
      <p class="detail">{{ status.bossSnapshot?.reason || "等待岗位页面数据" }}</p>
    </section>
    <p class="page">页面：{{ status.page || "等待识别" }}</p>
    <p v-if="status.detail" class="detail">{{ status.detail }}</p>
    <p v-if="uiError" class="detail">{{ uiError }}</p>
  </section>
</template>
