<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import { formatDateTime } from "@/services/format";
import {
  canConfirmFineJobChatReply,
  fineJobChatConfirmBlocker,
  fineJobChatSendStatusLabel
} from "@/services/fineJobChatPolicy";
import { useFineJobBossChatStore } from "@/stores/fineJobBossChat";
import type { FineJobChatSession } from "@/types";


const store = useFineJobBossChatStore();
const router = useRouter();
const instruction = ref("");
const finalText = ref("");
const expandedMessages = ref<Record<string, boolean>>({});
const messagePreviewNeedsExpand = ref<Record<string, boolean>>({});
const messagePreviewElements = new Map<string, HTMLElement>();
const editorDrafts = ref<Record<string, {
  instruction: string;
  finalText: string;
  taskId: string;
  sourceUpdatedAt: string;
  dirty: boolean;
}>>({});

const session = computed(() => store.detail?.session ?? null);
const task = computed(() => store.currentTask);
const latestAction = computed(() => store.detail?.send_actions[0] ?? null);
const selectedLeader = computed(() => {
  const leaders = store.runtime?.leaders;
  if (leaders?.length && session.value) {
    return leaders.find((item) => item.account_uid === session.value?.account_uid) ?? null;
  }
  if (!store.runtime?.leader_tab_id || !store.runtime.leader_lease_expires_at) return null;
  return {
    account_uid: session.value?.account_uid ?? "",
    executor_id: store.runtime.leader_executor_id ?? "",
    tab_id: store.runtime.leader_tab_id,
    leader_epoch: store.runtime.leader_epoch,
    lease_expires_at: store.runtime.leader_lease_expires_at,
    updated_at: store.runtime.updated_at
  };
});
const leaderAvailable = computed(() => Boolean(
  selectedLeader.value?.tab_id
    && new Date(selectedLeader.value.lease_expires_at).getTime() > Date.now()
));
const resumeFacts = computed(() => {
  const value = task.value?.context?.resume_facts;
  return Array.isArray(value) ? value as Array<Record<string, unknown>> : [];
});
const canConfirm = computed(() => canConfirmFineJobChatReply({
  runtime: store.runtime,
  session: session.value,
  task: task.value,
  finalText: finalText.value,
  leaderAvailable: leaderAvailable.value
}));
const confirmBlocker = computed(() => fineJobChatConfirmBlocker({
  runtime: store.runtime,
  session: session.value,
  task: task.value,
  finalText: finalText.value,
  leaderAvailable: leaderAvailable.value
}));

const saveEditor = (sessionId = store.selectedSessionId, dirty = true) => {
  if (!sessionId) return;
  editorDrafts.value[sessionId] = {
    instruction: instruction.value,
    finalText: finalText.value,
    taskId: task.value?.id ?? "",
    sourceUpdatedAt: task.value?.updated_at ?? "",
    dirty
  };
};

const restoreEditor = () => {
  const sessionId = store.selectedSessionId;
  if (!sessionId) {
    instruction.value = "";
    finalText.value = "";
    return;
  }
  const saved = editorDrafts.value[sessionId];
  const currentTaskId = task.value?.id ?? "";
  if (saved?.taskId === currentTaskId && (
    saved.dirty || saved.sourceUpdatedAt === (task.value?.updated_at ?? "")
  )) {
    instruction.value = saved.instruction;
    finalText.value = saved.finalText;
    return;
  }
  instruction.value = saved?.instruction ?? "";
  finalText.value = task.value?.final_text || task.value?.draft_text || "";
  saveEditor(sessionId, false);
};

const markEditorDirty = () => saveEditor(store.selectedSessionId, true);

watch(
  () => [store.selectedSessionId, task.value?.id, task.value?.updated_at],
  restoreEditor,
  { immediate: true }
);

const sendStatusLabel = fineJobChatSendStatusLabel;
const warningLabel = (warning: string) => ({
  send_contact_info: "联系方式",
  send_commitment_reply: "薪资、到岗或承诺",
  send_interview_decision: "面试安排",
  salary: "薪资",
  interview_time: "面试时间"
})[warning] ?? warning;
const latestMessageStatusLabel = (value?: number | null) => ({
  0: "【new】",
  1: "【已送达】",
  2: "【已读】"
}[value ?? -1] ?? "");

const selectSession = async (item: FineJobChatSession) => {
  saveEditor();
  try {
    await store.loadDetail(item.id);
    restoreEditor();
  } catch {
    ElMessage.error(store.error ?? "会话加载失败");
  }
};

const applySessionFilters = async () => {
  try {
    await store.loadList();
  } catch {
    ElMessage.error(store.error ?? "会话筛选失败");
  }
};

const updateFlag = async (
  field: "listen_enabled" | "generation_enabled" | "send_enabled",
  value: string | number | boolean
) => {
  try {
    await store.updateRuntime({ [field]: Boolean(value) });
  } catch {
    ElMessage.error(store.error ?? "运行设置保存失败");
  }
};
const updateListen = (value: string | number | boolean) => updateFlag("listen_enabled", value);
const updateGeneration = (value: string | number | boolean) => updateFlag("generation_enabled", value);
const updateSend = (value: string | number | boolean) => updateFlag("send_enabled", value);

const updateTrigger = async () => {
  if (!store.runtime) return;
  const interval = store.runtime.trigger_mode === "interval"
    ? (store.runtime.interval_minutes || 30)
    : 0;
  try {
    await store.updateRuntime({
      trigger_mode: store.runtime.trigger_mode,
      interval_minutes: interval
    });
  } catch {
    ElMessage.error(store.error ?? "处理周期保存失败");
  }
};

const updateInterval = async () => {
  if (!store.runtime) return;
  try {
    await store.updateRuntime({ interval_minutes: store.runtime.interval_minutes });
  } catch {
    ElMessage.error(store.error ?? "处理周期保存失败");
  }
};

const checkNow = async () => {
  try {
    const generated = await store.checkNow();
    ElMessage.success(generated ? `已生成 ${generated} 条待确认回复` : "当前没有到期的待生成回复");
  } catch {
    ElMessage.error(store.error ?? "立即检查失败");
  }
};

const refreshFriendList = async () => {
  try {
    const result = await store.refreshFriendList();
    ElMessage.success(`消息列表已更新，共 ${result.count} 条，${result.changed_count} 条消息有变化`);
  } catch {
    ElMessage.error(store.error ?? "消息列表更新失败");
  }
};

const refreshJob = async () => {
  try {
    const result = await store.updateJob();
    if (result.action === "view") {
      await router.push({
        name: "fine-job-capture-history",
        query: { history_id: result.history_job_id }
      });
      return;
    }
    ElMessage.success("岗位详情获取任务已启动，历史岗位记录已创建");
  } catch {
    ElMessage.error(store.error ?? "岗位详情获取失败");
  }
};

const startBatchUpdate = async () => {
  try {
    const task = await store.startBatchUpdate();
    ElMessage.success(`已开始批量更新 ${task.total} 条聊天记录`);
  } catch {
    ElMessage.error(store.error ?? "批量更新启动失败");
  }
};

const batchProgressPercentage = computed(() => {
  const progress = store.batchProgress;
  if (!progress?.total) return 0;
  return Math.round((progress.current / progress.total) * 100);
});

const viewJob = async () => {
  if (!session.value?.job_id) return;
  await router.push({
    name: "fine-job-capture-history",
    query: { history_id: session.value.job_id }
  });
};

const rejectJob = async () => {
  try {
    await store.rejectJob();
    ElMessage.success("投递状态已更新为已被拒绝");
  } catch {
    ElMessage.error(store.error ?? "投递状态更新失败");
  }
};

const refreshHistory = async () => {
  try {
    const result = await store.refreshHistory();
    ElMessage.success(`聊天消息已获取，本次新增 ${result.inserted_count} 条`);
  } catch {
    ElMessage.error(store.error ?? "聊天消息获取失败");
  }
};

const loadMoreHistory = async () => {
  try {
    const result = await store.loadMoreHistory();
    ElMessage.success(`已获取更早消息，本次新增 ${result.inserted_count} 条`);
  } catch {
    ElMessage.error(store.error ?? "获取更多消息失败");
  }
};

const toggleMessageExpanded = (sessionId: string) => {
  if (!messagePreviewNeedsExpand.value[sessionId] && !expandedMessages.value[sessionId]) return;
  expandedMessages.value[sessionId] = !expandedMessages.value[sessionId];
};

const setMessagePreviewElement = (sessionId: string, element: unknown) => {
  if (element instanceof HTMLElement) {
    messagePreviewElements.set(sessionId, element);
  } else {
    messagePreviewElements.delete(sessionId);
  }
};

const measureMessagePreviews = async () => {
  await nextTick();
  const nextNeedsExpand: Record<string, boolean> = {};
  for (const [sessionId, element] of messagePreviewElements) {
    const wasExpanded = Boolean(expandedMessages.value[sessionId]);
    if (wasExpanded) element.classList.remove("session-card__preview--expanded");
    nextNeedsExpand[sessionId] = element.scrollHeight > element.clientHeight + 1;
    if (wasExpanded) element.classList.add("session-card__preview--expanded");
  }
  messagePreviewNeedsExpand.value = nextNeedsExpand;
};

watch(
  () => store.sessions.map((item) => `${item.id}:${item.latest_message_content || ""}`),
  measureMessagePreviews,
  { immediate: true }
);

const generate = async (regenerate = false) => {
  try {
    await store.generate(instruction.value, regenerate);
    if (store.selectedSessionId && editorDrafts.value[store.selectedSessionId]) {
      editorDrafts.value[store.selectedSessionId].dirty = false;
    }
    restoreEditor();
    ElMessage.success(regenerate ? "已基于最新本地上下文重新生成" : "AI 回复已生成，请编辑后确认");
  } catch {
    ElMessage.error(store.error ?? "AI 回复生成失败");
  }
};

const confirm = async () => {
  try {
    await ElMessageBox.confirm(
      "确认后插件会向 BOSS 提交这条消息。“已提交发送”表示 MQTT 已确认提交，不等同于招聘方已经阅读。",
      "确认发送",
      { type: "warning", confirmButtonText: "确认提交" }
    );
  } catch {
    return;
  }
  try {
    await store.confirm(finalText.value.trim());
    if (store.selectedSessionId) delete editorDrafts.value[store.selectedSessionId];
    ElMessage.success("回复已进入发送队列");
  } catch {
    ElMessage.error(store.error ?? "确认发送失败；若有新消息，请重新生成");
  }
};

const cancel = async () => {
  try {
    await store.cancel();
    ElMessage.success("草稿已取消");
  } catch {
    ElMessage.error(store.error ?? "取消草稿失败");
  }
};

const pauseAll = async () => {
  try {
    await store.updateRuntime({ generation_enabled: false });
    ElMessage.success("已暂停自动生成，消息监听和人工确认发送开关保持原值");
  } catch {
    ElMessage.error(store.error ?? "暂停失败");
  }
};

const emergencyStop = async () => {
  try {
    await store.updateRuntime({ listen_enabled: false, generation_enabled: false, send_enabled: false });
    ElMessage.warning("自动代聊监听、生成和发送权限均已关闭");
  } catch {
    ElMessage.error(store.error ?? "紧急停止失败");
  }
};

onMounted(() => {
  void store.load().catch(() => ElMessage.error(store.error ?? "自动代聊加载失败"));
});
onBeforeUnmount(() => {
  saveEditor();
  store.stopBatchPolling();
});
</script>

<template>
  <section class="page-stack fine-job-page boss-chat-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">BOSS Chat Copilot</p>
        <h1>自动代聊</h1>
        <p class="secondary-text">监听新消息、生成回复草稿；每条真实发送都必须由你编辑并确认。</p>
      </div>
      <div class="heading-actions">
        <el-button type="primary" :loading="store.mutating" @click="refreshFriendList">更新聊天列表</el-button>
        <el-button :loading="store.mutating" @click="checkNow">处理待生成任务</el-button>
        <el-button @click="pauseAll">暂停生成</el-button>
        <el-button type="danger" plain @click="emergencyStop">紧急停止</el-button>
      </div>
    </div>

    <el-alert v-if="store.error" type="error" show-icon title="自动代聊操作失败" :description="store.error" />

    <section v-if="store.batchSummary" class="page-panel batch-summary-panel">
      <span>待更新聊天：{{ store.batchSummary.pending_chat_count }} 条</span>
      <span>
        本次批量：
        <el-input-number
          v-model="store.batchSize"
          :min="1"
          :max="Math.min(store.batchSummary.pending_chat_count, store.batchSummary.batch_limit)"
          :disabled="!store.batchSummary.pending_chat_count || Boolean(store.batchProgress) || store.mutating"
          controls-position="right"
          size="small"
        />
        条
      </span>
      <span>待采集岗位：{{ store.batchSummary.pending_job_count }} 条</span>
      <el-button
        class="batch-summary-panel__action"
        type="primary"
        plain
        size="small"
        :disabled="!store.batchSummary.queued_chat_count || Boolean(store.batchProgress) || store.mutating"
        :loading="store.mutating"
        @click="startBatchUpdate"
      >批量更新聊天记录</el-button>
    </section>

    <section v-if="store.batchProgress" class="page-panel batch-progress-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Batch Update</p>
          <h2>批量更新进度</h2>
        </div>
      </div>
      <el-progress :percentage="batchProgressPercentage" />
      <p>{{ store.batchProgress.message }}</p>
      <p v-if="store.batchProgress.current_session_name">
        当前会话：{{ store.batchProgress.current_session_name }}
        <template v-if="store.batchProgress.current_job_title"> · {{ store.batchProgress.current_job_title }}</template>
      </p>
      <div class="batch-progress-metrics">
        <span>聊天已更新 {{ store.batchProgress.chat_completed }}</span>
        <span>岗位已采集 {{ store.batchProgress.job_completed }}</span>
        <span>岗位已跳过 {{ store.batchProgress.job_skipped }}</span>
        <span>失败 {{ store.batchProgress.failed }}</span>
      </div>
    </section>

    <section class="page-panel runtime-panel">
      <div class="runtime-item">
        <span>监听新消息</span>
        <el-switch
          :model-value="store.runtime?.listen_enabled ?? false"
          @change="updateListen"
        />
      </div>
      <div class="runtime-item">
        <span>自动生成草稿</span>
        <el-switch
          :model-value="store.runtime?.generation_enabled ?? false"
          @change="updateGeneration"
        />
      </div>
      <div class="runtime-item">
        <span>允许人工确认发送</span>
        <el-switch
          :model-value="store.runtime?.send_enabled ?? false"
          @change="updateSend"
        />
      </div>
      <div class="runtime-item runtime-item--wide">
        <span>触发方式</span>
        <el-select v-if="store.runtime" v-model="store.runtime.trigger_mode" @change="updateTrigger">
          <el-option label="收到后立即生成" value="immediate" />
          <el-option label="按周期批量处理" value="interval" />
          <el-option label="仅手动生成" value="manual" />
        </el-select>
        <el-select
          v-if="store.runtime?.trigger_mode === 'interval'"
          v-model="store.runtime.interval_minutes"
          @change="updateInterval"
        >
          <el-option label="5 分钟" :value="5" />
          <el-option label="10 分钟" :value="10" />
          <el-option label="30 分钟" :value="30" />
          <el-option label="60 分钟" :value="60" />
        </el-select>
      </div>
      <div class="runtime-summary">
        <el-tag :type="leaderAvailable ? 'success' : 'info'">
          {{ leaderAvailable ? `当前账号领导标签页在线 · epoch ${selectedLeader?.leader_epoch}` : "等待当前账号的 BOSS 领导标签页" }}
        </el-tag>
        <span>BOSS 账号：{{ session?.account_uid || "等待消息" }}</span>
      </div>
      <div class="runtime-actions">
        <el-link href="https://www.zhipin.com/web/geek/chat" target="_blank" type="primary">打开 BOSS 沟通页</el-link>
      </div>
    </section>

    <section class="chat-workbench page-panel">
      <aside class="session-list">
        <div class="section-heading">
          <h2>会话</h2>
          <el-button link :loading="store.loading" @click="store.load">刷新</el-button>
        </div>
        <div class="session-filters">
          <el-input
            v-model="store.searchQuery"
            clearable
            placeholder="搜索联系人、公司、岗位或消息"
            @keyup.enter="applySessionFilters"
            @clear="applySessionFilters"
          />
        </div>
        <button
          v-for="item in store.sessions"
          :key="item.id"
          class="session-card"
          :class="{ 'session-card--active': item.id === store.selectedSessionId }"
          type="button"
          @click="selectSession(item)"
        >
          <span class="session-card__head">
            <span class="session-card__company">
              <span class="session-card__title">{{ item.company_name || "未知公司" }}</span>
              <span
                v-if="item.message_update_required || !item.has_local_messages"
                class="session-card__update-dot"
                aria-label="消息待更新"
              />
            </span>
            <span class="session-card__hr">{{ item.peer_name || `联系人 ${item.peer_uid}` }}</span>
          </span>
          <el-tag
            v-if="item.platform_relation_type === 1"
            class="session-card__relation-badge"
            size="small"
            type="primary"
          >new</el-tag>
          <el-tag
            v-else-if="item.platform_relation_type === 2"
            class="session-card__relation-badge"
            size="small"
            type="danger"
          >REJECT</el-tag>
          <small v-if="item.job_title" class="session-card__job-title">{{ item.job_title }}</small>
          <el-tag v-else size="small" type="info" effect="plain">岗位未返回</el-tag>
          <span
            class="session-card__message-row"
            :class="{ 'session-card__message-row--expandable': messagePreviewNeedsExpand[item.id] }"
            @click.stop="toggleMessageExpanded(item.id)"
          >
            <span
              class="session-card__preview"
              :ref="(element) => setMessagePreviewElement(item.id, element)"
              :class="[
                item.latest_message_direction === 'inbound'
                  ? 'session-card__preview--inbound'
                  : 'session-card__preview--outbound',
                { 'session-card__preview--expanded': expandedMessages[item.id] }
              ]"
            >
              <span
                v-if="latestMessageStatusLabel(item.platform_latest_message_status)"
                class="session-card__message-tag"
              >{{ latestMessageStatusLabel(item.platform_latest_message_status) }}</span>
              <span>{{ item.latest_message_content || "暂无文本消息" }}</span>
            </span>
            <span
              v-if="messagePreviewNeedsExpand[item.id]"
              class="session-card__expand-icon"
              role="button"
              tabindex="0"
              :aria-label="expandedMessages[item.id] ? '收起最新消息' : '展开最新消息'"
              :aria-expanded="expandedMessages[item.id]"
              @click.stop="toggleMessageExpanded(item.id)"
              @keydown.enter.prevent.stop="toggleMessageExpanded(item.id)"
              @keydown.space.prevent.stop="toggleMessageExpanded(item.id)"
            >{{ expandedMessages[item.id] ? "⌃" : "⌄" }}</span>
          </span>
          <span class="session-card__meta">
            <el-tag v-if="item.message_update_required" size="small" type="warning">消息需更新</el-tag>
            <span v-if="item.platform_latest_message_at" class="session-card__time secondary-text">
              {{ formatDateTime(item.platform_latest_message_at) }}
            </span>
            <b v-if="item.unhandled_count">{{ item.unhandled_count }} 条待处理</b>
          </span>
        </button>
        <el-button v-if="store.nextOffset !== null" class="load-more" plain @click="store.loadMore">继续加载</el-button>
        <el-empty v-if="!store.sessions.length" description="尚未观察到新消息" :image-size="72" />
      </aside>

      <main class="conversation-panel">
        <template v-if="session">
          <div class="section-heading conversation-heading">
            <div>
              <div class="conversation-contact">
                <h2>{{ session.company_name || "未知公司" }}</h2>
                <span class="conversation-contact__hr">{{ session.peer_name || session.peer_uid }}</span>
              </div>
              <p v-if="session.job_title">{{ session.job_title }}</p>
              <div class="identity-tags">
                <el-tag v-if="session.message_update_required" size="small" type="warning">消息需更新</el-tag>
              </div>
              <div class="identity-tags">
                <el-tag size="small" :type="session.job_context_state === 'linked' ? 'success' : 'info'">
                  {{ session.job_context_state === 'linked' ? '已关联本地岗位' : '本地岗位未关联' }}
                </el-tag>
              </div>
            </div>
            <div class="heading-actions">
              <el-button
                v-if="session.job_context_state === 'linked'"
                size="small"
                type="primary"
                @click="viewJob"
              >查看岗位</el-button>
              <el-button
                v-else
                size="small"
                :loading="store.mutating"
                @click="refreshJob"
              >更新岗位</el-button>
              <el-button
                v-if="session.job_context_state === 'linked'"
                size="small"
                type="danger"
                plain
                :loading="store.mutating"
                @click="rejectJob"
              >已被拒绝</el-button>
              <el-button
                v-if="(store.detail?.message_count ?? 0) === 0"
                type="primary"
                size="small"
                :loading="store.mutating"
                @click="refreshHistory"
              >获取消息</el-button>
              <el-button
                v-else-if="session.message_update_required"
                type="primary"
                size="small"
                :loading="store.mutating"
                @click="refreshHistory"
              >更新</el-button>
            </div>
          </div>
          <el-alert
            v-if="session.status === 'unsupported'"
            type="warning"
            :closable="false"
            title="聊天对象身份尚未补全；允许生成和编辑草稿，发送保持关闭。"
          />
          <el-alert
            v-if="store.detail?.messages_truncated"
            type="info"
            :closable="false"
            :title="`当前显示最近 ${store.detail.messages.length} 条，本地共 ${store.detail.message_count} 条消息`"
          />
          <div class="message-timeline">
            <article
              v-for="message in store.detail?.messages"
              :key="message.id"
              class="message-bubble"
              :class="`message-bubble--${message.direction}`"
            >
              <small class="message-bubble__meta">{{ message.direction === "inbound" ? session.peer_name || "HR" : "我" }} · {{ formatDateTime(message.sent_at) }}</small>
              <p>{{ message.content || `[${message.message_type}]` }}</p>
            </article>
          </div>
          <div v-if="session.history_has_more" class="message-more-actions">
            <el-button
              plain
              :loading="store.mutating"
              @click="loadMoreHistory"
            >获取更多</el-button>
          </div>
        </template>
        <el-empty v-else description="选择一个会话查看本地消息" />
      </main>

      <aside class="reply-panel">
        <h2>AI 回复草稿</h2>
        <el-input
          v-model="instruction"
          type="textarea"
          :rows="2"
          placeholder="可选：本次回复的临时要求"
          @input="markEditorDirty"
        />
        <div class="reply-actions">
          <el-button
            type="primary"
            plain
            :disabled="!session || session.status === 'human_takeover' || session.status === 'paused'"
            :loading="store.mutating"
            @click="generate(false)"
          >生成 AI 回复</el-button>
          <el-button
            :disabled="!task || session?.status !== 'active'"
            :loading="store.mutating"
            @click="generate(true)"
          >重新生成</el-button>
        </div>
        <el-tag v-if="task" :type="task.status === 'failed' ? 'danger' : 'info'">{{ task.status }}</el-tag>
        <el-alert
          v-if="task?.decision && task.decision !== 'reply'"
          type="warning"
          :closable="false"
          :title="task.decision === 'manual' ? '建议人工补充后回复' : 'AI 建议当前不回复'"
          :description="task.decision_reason || '请根据实际情况处理'"
        />
        <el-alert
          v-if="task?.generation_error"
          type="error"
          :closable="false"
          :description="task.generation_error"
        />
        <el-input
          v-model="finalText"
          type="textarea"
          :rows="8"
          maxlength="5000"
          show-word-limit
          placeholder="生成后在这里进行二次编辑"
          @input="markEditorDirty"
        />
        <div class="reply-actions">
          <el-button type="primary" :disabled="!canConfirm" :loading="store.mutating" @click="confirm">确认发送</el-button>
          <el-button :disabled="!task" @click="cancel">取消草稿</el-button>
        </div>
        <p v-if="confirmBlocker" class="confirm-blocker">{{ confirmBlocker }}</p>

        <div v-if="task?.warnings?.length" class="warning-card">
          <strong>发送前重点核对</strong>
          <div class="warning-tags">
            <el-tag v-for="warning in task.warnings" :key="warning" type="warning" size="small">
              {{ warningLabel(warning) }}
            </el-tag>
          </div>
        </div>

        <details v-if="task?.facts_used?.length" class="context-facts">
          <summary>AI 声明使用的事实（{{ task.facts_used.length }}）</summary>
          <ul><li v-for="fact in task.facts_used" :key="fact">{{ fact }}</li></ul>
        </details>

        <div v-if="latestAction" class="send-result">
          <strong>{{ sendStatusLabel(latestAction.status) }}</strong>
          <span>{{ latestAction.error_message || latestAction.status_code }}</span>
        </div>

        <details v-if="resumeFacts.length" class="context-facts">
          <summary>本次使用的已确认简历事实（{{ resumeFacts.length }}）</summary>
          <ul>
            <li v-for="(fact, index) in resumeFacts" :key="index">
              {{ fact.fact_key }}：{{ fact.fact_value }}
            </li>
          </ul>
        </details>
      </aside>
    </section>
  </section>
</template>

<style scoped>
.heading-actions,
.reply-actions,
.runtime-summary,
.runtime-actions,
.session-card__meta,
.identity-tags {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.runtime-panel {
  display: grid;
  grid-template-columns: repeat(3, minmax(170px, 1fr));
  gap: 14px;
}

.runtime-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.runtime-item--wide,
.runtime-summary,
.runtime-actions {
  grid-column: 1 / -1;
}

.runtime-actions {
  justify-content: flex-end;
}

.runtime-item--wide {
  justify-content: flex-start;
}

.runtime-item--wide > span {
  flex: 0 0 64px;
  white-space: nowrap;
}

.runtime-item--wide :deep(.el-select) {
  flex: 1 1 220px;
}

.chat-workbench {
  display: grid;
  grid-template-columns: minmax(230px, 0.75fr) minmax(360px, 1.4fr) minmax(300px, 1fr);
  gap: 0;
  min-height: 620px;
  max-height: 1000px;
  padding: 0;
  overflow: hidden;
}

.session-list,
.conversation-panel,
.reply-panel {
  min-width: 0;
  padding: 20px;
  max-height: 1000px;
  overflow: auto;
}

.conversation-panel,
.reply-panel {
  border-left: 1px solid var(--el-border-color-lighter);
}

.section-heading,
.conversation-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.section-heading h2,
.reply-panel h2,
.conversation-heading p {
  margin: 0;
}

.conversation-contact {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.conversation-contact__hr {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
}

.session-card {
  position: relative;
  width: 100%;
  display: grid;
  gap: 5px;
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  background: var(--el-bg-color);
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.session-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.session-card__relation-badge {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 1;
}

.session-card__hr {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
}

.session-filters {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.load-more {
  width: 100%;
  margin-top: 10px;
}

.session-card--active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.session-card__title {
  font-weight: 700;
}

.session-card__company {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.session-card__update-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--el-color-danger);
}

.session-card__preview,
.session-card small {
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-card .session-card__job-title {
  color: var(--el-text-color-primary);
  font-weight: 400;
}

.session-card__preview {
  display: -webkit-box;
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  word-break: break-word;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.session-card__message-row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}

.session-card__message-row--expandable {
  cursor: pointer;
}

.session-card__preview--expanded {
  display: block;
  overflow: visible;
  -webkit-line-clamp: unset;
}

.session-card__preview--inbound {
  color: var(--el-color-danger);
}

.session-card__preview--outbound {
  color: var(--el-color-primary);
}

.session-card__message-tag {
  display: inline;
  margin-right: 6px;
  font-size: 12px;
  font-weight: 700;
}

.session-card__expand-icon {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  font-size: 16px;
  line-height: 1.25;
  cursor: pointer;
  user-select: none;
}

.session-card__relation-badge {
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
  line-height: 14px;
  pointer-events: none;
}

.session-card__time {
  font-size: 11px;
}

.message-timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 18px;
  max-height: 510px;
  overflow: auto;
}

.batch-summary-panel,
.batch-progress-metrics {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.batch-summary-panel__action {
  margin-left: auto;
}

.batch-progress-panel {
  display: grid;
  gap: 12px;
}

.batch-progress-panel p {
  margin: 0;
}

.message-more-actions {
  display: flex;
  justify-content: center;
  margin-top: 14px;
}

.message-bubble {
  max-width: 78%;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--el-fill-color-light);
}

.message-bubble--outbound {
  align-self: flex-end;
  background: var(--el-color-primary-light-9);
}

.message-bubble p {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.message-bubble__meta {
  font-size: 11px;
}

.message-bubble--inbound p {
  color: var(--el-color-danger);
}

.message-bubble--outbound p {
  color: var(--el-color-primary);
}

.reply-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.send-result,
.context-facts,
.warning-card {
  padding: 12px;
  border-radius: 10px;
  background: var(--el-fill-color-light);
}

.warning-card {
  display: grid;
  gap: 8px;
  border: 1px solid var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
}

.warning-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.confirm-blocker {
  margin: 0;
  color: var(--el-color-warning-dark-2);
}

.send-result {
  display: grid;
  gap: 4px;
}

@media (max-width: 1250px) {
  .chat-workbench { grid-template-columns: 250px 1fr; }
  .reply-panel { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--el-border-color-lighter); }
}

@media (max-width: 820px) {
  .runtime-panel,
  .chat-workbench { grid-template-columns: 1fr; }
  .conversation-panel,
  .reply-panel { border-left: 0; border-top: 1px solid var(--el-border-color-lighter); }
  .session-list { max-height: 420px; overflow: auto; }
}
</style>
