<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Delete, InfoFilled, Plus, Setting } from "@element-plus/icons-vue";
import { useRoute, useRouter } from "vue-router";

import { formatDateTime } from "@/services/format";
import {
  canConfirmFineJobChatReply,
  fineJobChatConfirmBlocker,
  fineJobChatSendStatusLabel
} from "@/services/fineJobChatPolicy";
import { resolveFineJobResumeSelection } from "@/services/fineJobResumeSelection";
import { useFineJobBossChatStore } from "@/stores/fineJobBossChat";
import type { FineJobChatMessageTransformRule, FineJobChatSession } from "@/types";


const store = useFineJobBossChatStore();
const router = useRouter();
const route = useRoute();
const instruction = ref("");
const finalText = ref("");
const selectedResumeId = ref("");
const resumeListRefreshing = ref(false);
const messageTransformDialogVisible = ref(false);
const messageTransformSaving = ref(false);
const messageTransformRules = ref<FineJobChatMessageTransformRule[]>([]);
const preferredReplyTaskId = ref<string | null>(null);
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));
const session = computed(() => store.detail?.session ?? null);
const task = computed(() => {
  const preferred = store.detail?.reply_tasks.find((item) =>
    item.id === preferredReplyTaskId.value
    && item.session_id === session.value?.id
    && item.status === "awaiting_review"
  );
  return preferred ?? store.currentTask;
});
const progress = computed(() => session.value?.progress ?? null);
const primaryAction = computed(() => progress.value?.primary_action ?? null);
const displayProgressStage = computed(() => (
  progress.value?.resume_delivery.status === "withdrawn"
    ? "resume_withdrawn"
    : progress.value?.stage ?? ""
));
const showResumeDelivery = computed(() => ![
  "resume_requested", "resume_submitted", "resume_viewed", "resume_withdrawn"
].includes(displayProgressStage.value));
const showResumePanel = computed(() => (
  progress.value?.stage === "resume_requested"
  && progress.value.waiting_on === "candidate"
));
const canMarkRejected = computed(() => Boolean(
  session.value?.job_context_state === "linked"
  && !["offer", "rejected", "closed"].includes(progress.value?.stage ?? "")
));
const canMarkInterview = computed(() => Boolean(
  session.value?.job_context_state === "linked"
  && !["offer", "rejected", "closed", "interviewing"].includes(progress.value?.stage ?? "")
));
const rejectionReasonNeedsDetail = computed(() => Boolean(
  progress.value?.stage === "rejected"
  && progress.value.outcome.rejection_party !== "candidate"
  && (
    progress.value.outcome.rejection_reason_source === "unknown"
    || ["unknown", "fit"].includes(progress.value.outcome.rejection_reason_category)
  )
));
const isCandidateRejected = computed(() => (
  progress.value?.stage === "rejected"
  && progress.value.outcome.rejection_party === "candidate"
));
const analysisButtonLabel = computed(() => (
  rejectionReasonNeedsDetail.value
    ? "分析拒绝原因"
    : "分析进展"
));
const defaultMessageActionKind = computed<"reply" | "followup" | "ask_rejection_reason">(() => {
  if (primaryAction.value?.type) return primaryAction.value.type;
  const routeActionKind = route.query.action_kind;
  if (routeActionKind === "reply" || routeActionKind === "followup" || routeActionKind === "ask_rejection_reason") {
    return routeActionKind;
  }
  if (rejectionReasonNeedsDetail.value) {
    return "ask_rejection_reason";
  }
  if (progress.value?.waiting_on === "recruiter") return "followup";
  return "reply";
});
const latestAction = computed(() => store.detail?.send_actions[0] ?? null);
const resumeAttachments = computed(() => store.resumeAttachments);
const latestResumeSendAction = computed(() => store.detail?.send_actions.find((item) => item.operation_kind === "resume") ?? null);
const resumeListLoading = computed(() => resumeListRefreshing.value);
const resumeListFailed = computed(() => Boolean(store.resumeListError));
const resumeListLoaded = computed(() => store.resumeListLoaded);
const selectedResume = computed(() => resumeAttachments.value.find((item) => item.resumeId === selectedResumeId.value) ?? null);
const canConfirmResume = computed(() => Boolean(
  session.value
  && selectedResume.value
));
const latestInsight = computed(() => store.detail?.latest_conversation_insight?.insight ?? null);
const analysisReplyDraft = computed(() => {
  const value = latestInsight.value?.reply_draft;
  if (typeof value === "string") return value.trim();
  if (isRecord(value) && typeof value.text === "string") return value.text.trim();
  return "";
});
const analysisReason = computed(() => {
  const recommendation = latestInsight.value?.ai_followup_recommendation;
  const value = isRecord(recommendation) ? recommendation.reason : "";
  return typeof value === "string" ? value : session.value?.attention_reason || "";
});
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
  finalText: finalText.value
}));
const confirmBlocker = computed(() => fineJobChatConfirmBlocker({
  runtime: store.runtime,
  session: session.value,
  task: task.value,
  finalText: finalText.value
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

watch(resumeAttachments, (attachments) => {
  selectedResumeId.value = resolveFineJobResumeSelection(attachments, selectedResumeId.value);
}, { immediate: true });

const sendStatusLabel = fineJobChatSendStatusLabel;
const warningLabel = (warning: string) => ({
  send_contact_info: "联系方式",
  send_commitment_reply: "薪资、到岗或承诺",
  send_interview_decision: "面试安排",
  salary: "薪资",
  interview_time: "面试时间"
})[warning] ?? warning;
const latestMessageStatusLabel = (value?: number | null) => ({
  0: "【已读】",
  1: "【未读】",
  2: "【已读】"
}[value ?? -1] ?? "");
const attentionLabel = (item?: FineJobChatSession | null) =>
  item?.attention_label || ({
    needs_reply: "待回复",
    needs_resume: "待发简历",
    needs_followup: "建议跟进",
    needs_rejection_reason: "建议询问",
    needs_interview_confirm: "待确认面试",
    needs_info: "待补充信息",
    waiting: "等待 HR",
    no_action: "无需处理"
  }[item?.attention_status ?? ""] ?? "");
const attentionType = (item?: FineJobChatSession | null) => ({
  needs_reply: "danger",
  needs_resume: "warning",
  needs_followup: "warning",
  needs_rejection_reason: "warning",
  needs_interview_confirm: "primary",
  needs_info: "danger",
  waiting: "info",
  no_action: "info"
}[item?.attention_status ?? ""] ?? "info") as "success" | "warning" | "danger" | "primary" | "info";
const stageLabel = (value?: string | null) => ({
  discovered: "已发现岗位", shortlisted: "已进入候选", greeted: "已打招呼",
  communicating: "沟通中", resume_requested: "待发送简历",
  resume_submitted: "已发送简历", resume_viewed: "简历已查看", resume_withdrawn: "简历已撤回",
  under_review: "用人部门评估中", interview_scheduling: "面试时间沟通中",
  interviewing: "面试阶段", offer: "已获得 Offer", rejected: "已被拒绝", closed: "岗位关闭"
}[value ?? ""] ?? "进展待分析");
const waitingLabel = (value?: string | null) => ({
  candidate: "等我回复", recruiter: "等招聘方回复", none: "当前无需回复", unknown: "等待对象待判断"
}[value ?? ""] ?? "等待对象待判断");
const contactOriginLabel = (value?: string | null) => ({
  finejob_auto: "FineJob 自动打招呼", candidate_initiated: "我在 FineJob 内主动联系",
  recruiter_initiated: "招聘方主动联系", external_candidate_initiated: "我从其他渠道主动联系",
  unknown: "沟通来源待判断"
}[value ?? ""] ?? "沟通来源待判断");
const rejectionSourceLabel = (value?: string | null) => ({
  recruiter_explicit: "招聘方明确说明", ai_inferred: "AI 推测", unknown: "未知"
}[value ?? ""] ?? "未知");
const rejectionCategoryLabel = (value?: string | null) => ({
  experience: "工作经验", education: "学历", skills: "技能不匹配",
  industry_background: "行业背景", salary: "薪资", location: "地点",
  availability: "到岗时间", position_filled: "已招到人",
  headcount_closed: "岗位已关闭", fit: "综合匹配度", other: "其他", unknown: "未知"
}[value ?? ""] ?? "未知");
const resumeDeliveryLabel = (value?: string | null) => ({
  not_started: "未发送", pending_confirmation: "待确认发送", queued: "等待发送",
  sending: "发送中", awaiting_observation: "等待平台回显", sent: "已发送",
  received: "HR 已接收", viewed: "HR 已查看", withdrawn: "已撤回",
  failed: "发送失败", unknown: "发送结果待确认"
}[value ?? ""] ?? "未发送");
const showResumeHelp = () => {
  ElMessage.info("读取当前 BOSS 账号可发送的附件；提交后在待确认列表批准，再进入执行队列。");
};
const rejectionPartyLabel = (value?: string | null) => ({
  candidate: "我拒绝", recruiter: "招聘方拒绝"
}[value ?? ""] ?? "拒绝");
const waitingDuration = computed(() => {
  const since = progress.value?.waiting_since_at;
  if (!since || progress.value?.waiting_on === "none") return "";
  const days = Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 86_400_000));
  return days > 0 ? `已等待 ${days} 天` : "等待不足 1 天";
});

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

// 配置弹窗使用独立草稿，取消时不改动已经保存的全局规则。
const openMessageTransformSettings = async () => {
  try {
    const config = store.messageTransformConfig ?? await store.loadMessageTransformConfig();
    messageTransformRules.value = config.rules.map((rule) => ({ ...rule }));
    messageTransformDialogVisible.value = true;
  } catch {
    ElMessage.error(store.error ?? "消息转义配置加载失败");
  }
};

const addMessageTransformRule = () => {
  const customRuleId = `custom-${Date.now()}`;
  messageTransformRules.value.push({
    id: customRuleId,
    label: "自定义规则",
    enabled: true,
    direction: "inbound",
    match_mode: "exact",
    pattern: "",
    output_kind: "action",
    display_content: "中立消息",
    action_type: customRuleId.replaceAll("-", "_"),
    requires_resume_sent: false,
    condition_rule_id: "",
    condition_branch: "always"
  });
};

// 条件线只能引用其他可转义为中立动作的规则，避免规则引用自身。
const conditionLineRules = (currentRuleId: string) => messageTransformRules.value.filter((rule) =>
  rule.id !== currentRuleId
  && rule.output_kind === "action"
  && Boolean(rule.action_type.trim())
);

const removeMessageTransformRule = (index: number) => {
  messageTransformRules.value.splice(index, 1);
};

const saveMessageTransformSettings = async () => {
  if (!messageTransformRules.value.length) {
    ElMessage.warning("请至少保留一条转义规则");
    return;
  }
  messageTransformSaving.value = true;
  try {
    await store.saveMessageTransformConfig(messageTransformRules.value);
    messageTransformDialogVisible.value = false;
    ElMessage.success("全局消息转义配置已保存，将用于后续同步消息");
  } catch {
    ElMessage.error(store.error ?? "消息转义配置保存失败");
  } finally {
    messageTransformSaving.value = false;
  }
};

const resetMessageTransformSettings = async () => {
  try {
    await ElMessageBox.confirm(
      "将恢复内置的全局转义规则，当前未保存的编辑也会丢失。",
      "恢复默认配置",
      { type: "warning", confirmButtonText: "恢复默认" }
    );
  } catch {
    return;
  }
  messageTransformSaving.value = true;
  try {
    const config = await store.resetMessageTransformConfig();
    messageTransformRules.value = config.rules.map((rule) => ({ ...rule }));
    ElMessage.success("已恢复默认消息转义配置");
  } catch {
    ElMessage.error(store.error ?? "恢复默认配置失败");
  } finally {
    messageTransformSaving.value = false;
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

const markManualProgress = async (
  action: "interview_scheduled" | "candidate_rejected" | "recruiter_rejected"
) => {
  try {
    await store.markManualProgress(action);
    ElMessage.success(action === "interview_scheduled" ? "已更新为面试阶段" : "已记录拒绝结果");
  } catch {
    ElMessage.error(store.error ?? "当前进展更新失败");
  }
};

const rejectJob = async () => {
  try {
    await ElMessageBox.confirm(
      "请选择拒绝方，系统会结束当前岗位流程并保留拒绝方记录。",
      "拒绝",
      {
        type: "warning",
        confirmButtonText: "招聘方拒绝",
        cancelButtonText: "我拒绝",
        distinguishCancelAndClose: true
      }
    );
    await markManualProgress("recruiter_rejected");
  } catch (error) {
    if (error === "cancel") await markManualProgress("candidate_rejected");
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

const retransformMessages = async () => {
  try {
    const result = await store.retransformMessages();
    ElMessage.success(
      result.updated_count
        ? `已重新转义 ${result.updated_count} 条消息，过滤 ${result.discarded_count} 条推广消息`
        : "当前会话没有需要重新转义的消息"
    );
  } catch {
    ElMessage.error(store.error ?? "消息重新转义失败");
  }
};

const forceRefreshHistory = async () => {
  try {
    const result = await store.forceRefreshHistory();
    ElMessage.success(
      `已强制更新：读取 ${result.fetched_count} 条，新增 ${result.inserted_count} 条，关联 ${result.reconciled_count} 条临时消息，重新转义 ${result.retransformed_count} 条`
    );
  } catch {
    ElMessage.error(store.error ?? "强制更新消息失败");
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

const generate = async (
  regenerate = false,
  actionKind: "reply" | "followup" | "ask_rejection_reason" = (
    primaryAction.value?.type ?? task.value?.action_kind ?? "reply"
  )
) => {
  try {
    const routeSessionId = typeof route.query.session_id === "string" ? route.query.session_id : "";
    const routeActionKind = typeof route.query.action_kind === "string" ? route.query.action_kind : "";
    const routeActionKey = typeof route.query.action_key === "string" ? route.query.action_key : undefined;
    const jobActionKey = !regenerate
      && routeSessionId === store.selectedSessionId
      && routeActionKind === actionKind
      ? routeActionKey
      : undefined;
    await store.generate(instruction.value, regenerate, actionKind, jobActionKey);
    if (store.selectedSessionId && editorDrafts.value[store.selectedSessionId]) {
      editorDrafts.value[store.selectedSessionId].dirty = false;
    }
    restoreEditor();
    ElMessage.success(regenerate ? "已基于最新本地上下文重新生成" : "消息草稿已生成，请编辑后确认");
  } catch {
    ElMessage.error(store.error ?? "AI 回复生成失败");
  }
};

const analyzeProgress = async () => {
  try {
    const result = await store.analyzeProgress();
    if (result.auxiliary_warning) ElMessage.warning(result.auxiliary_warning);
    else if (result.reply_task_created) ElMessage.success("当前求职进展已更新，并生成了待确认草稿");
    else ElMessage.success("当前求职进展已更新");
  } catch {
    ElMessage.error(store.error ?? "分析进展失败");
  }
};

const analyzeRules = async () => {
  try {
    await store.analyzeRules();
    ElMessage.success("已按本地聊天消息重算当前进展");
  } catch {
    ElMessage.error(store.error ?? "规则分析失败");
  }
};

const useAnalysisReplyDraft = () => {
  if (!analysisReplyDraft.value) return;
  finalText.value = analysisReplyDraft.value;
  markEditorDirty();
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

const refreshResumeAttachments = async () => {
  // 读取动作由后端同步完成，按钮只在本次 CDP 请求期间显示 loading。
  resumeListRefreshing.value = true;
  try {
    await store.refreshResumeAttachments();
    ElMessage.success("附件简历读取完成。");
  } catch {
    ElMessage.error(store.error ?? "读取附件简历失败");
  } finally {
    resumeListRefreshing.value = false;
  }
};

const confirmResume = async () => {
  const selected = resumeAttachments.value.find((item) => item.resumeId === selectedResumeId.value);
  if (!selected) {
    ElMessage.warning("请先选择一份附件简历");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认向当前招聘方发送附件简历“${selected.showName}”？发送任务将进入待确认列表，批准后进入执行队列。`,
      "发送简历",
      { type: "warning", confirmButtonText: "发送简历" }
    );
  } catch {
    return;
  }
  try {
    await store.confirmResume(selected.resumeId, selected.showName);
    ElMessage.success("简历发送任务已进入待确认列表");
  } catch {
    ElMessage.error(store.error ?? "确认发送简历失败");
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
  preferredReplyTaskId.value = typeof route.query.reply_task_id === "string"
    ? route.query.reply_task_id
    : null;
  const attention = typeof route.query.attention === "string" ? route.query.attention : "";
  if (attention) store.attentionFilter = attention;
  const waitingOn = typeof route.query.waiting_on === "string" ? route.query.waiting_on : "";
  if (waitingOn === "candidate" || waitingOn === "recruiter") {
    store.waitingOnFilter = waitingOn;
  }
  void store.load()
    .then(async () => {
      const sessionId = typeof route.query.session_id === "string" ? route.query.session_id : "";
      if (sessionId) await store.loadDetail(sessionId);
    })
    .catch(() => ElMessage.error(store.error ?? "自动代聊加载失败"));
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
      <div class="batch-summary-panel__main-row">
        <div class="batch-summary-panel__pending">
          <span>待更新聊天：{{ store.batchSummary.pending_chat_count }} 条</span>
        </div>
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
      </div>
    </section>

    <div v-if="store.batchSummary" class="page-panel batch-summary-panel__resume-row">
      <el-button
        type="primary"
        plain
        size="small"
        :disabled="resumeListLoading"
        :loading="resumeListLoading"
        @click="refreshResumeAttachments"
      >{{ resumeListLoaded ? "刷新附件简历" : "读取附件简历" }}</el-button>
      <span class="batch-summary-panel__resume-label">默认投递简历：</span>
      <!-- 顶部默认简历与右侧附件简历选择共用同一状态，保持选择联动。 -->
      <el-select
        v-if="resumeAttachments.length"
        v-model="selectedResumeId"
        size="small"
        :disabled="resumeListLoading"
        placeholder="请选择附件简历"
        class="batch-summary-panel__resume-select"
      >
        <el-option
          v-for="item in resumeAttachments"
          :key="item.resumeId"
          :label="item.showName"
          :value="item.resumeId"
        />
      </el-select>
      <span v-else class="secondary-text">请先读取附件简历</span>
    </div>

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
          <div class="session-heading-actions">
            <el-button circle :icon="Setting" aria-label="消息转义设置" @click="openMessageTransformSettings" />
            <el-button link :loading="store.loading" @click="store.load">刷新</el-button>
          </div>
        </div>
        <div class="session-filters">
          <el-input
            v-model="store.searchQuery"
            clearable
            placeholder="搜索联系人、公司、岗位或消息"
            @keyup.enter="applySessionFilters"
            @clear="applySessionFilters"
          />
          <el-select
            v-model="store.attentionFilter"
            clearable
            placeholder="处理状态"
            @change="applySessionFilters"
          >
            <el-option label="只看需要处理" value="actionable" />
            <el-option label="建议跟进" value="needs_followup" />
            <el-option label="等我回复" value="needs_reply" />
            <el-option label="建议询问原因" value="needs_rejection_reason" />
          </el-select>
          <el-select
            v-model="store.waitingOnFilter"
            clearable
            placeholder="等待对象"
            @change="applySessionFilters"
          >
            <el-option label="等我回复" value="candidate" />
            <el-option label="等招聘方回复" value="recruiter" />
          </el-select>
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
          <el-tag
            v-if="attentionLabel(item)"
            class="session-card__attention-badge"
            size="small"
            :type="attentionType(item)"
            effect="plain"
          >{{ attentionLabel(item) }}</el-tag>
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
                item.latest_message_display_kind === 'action'
                  ? 'session-card__preview--action'
                  : item.latest_message_direction === 'inbound'
                  ? 'session-card__preview--inbound'
                  : 'session-card__preview--outbound',
                { 'session-card__preview--expanded': expandedMessages[item.id] }
              ]"
            >
              <span
                v-if="item.latest_message_display_kind !== 'action' && latestMessageStatusLabel(item.platform_latest_message_status)"
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
          <div class="conversation-heading">
            <div class="conversation-identity">
              <div class="conversation-contact">
                <h2>{{ session.company_name || "未知公司" }}</h2>
                <span class="conversation-contact__hr">{{ session.peer_name || session.peer_uid }}</span>
              </div>
              <div class="conversation-job">
                <p v-if="session.job_title">{{ session.job_title }}</p>
                <el-tag v-else size="small" type="info" effect="plain">岗位未返回</el-tag>
                <el-tag v-if="session.job_context_state !== 'linked'" size="small" type="info">本地岗位未关联</el-tag>
                <el-tag v-if="session.message_update_required" size="small" type="warning">消息需更新</el-tag>
                <el-tag
                  v-if="attentionLabel(session)"
                  size="small"
                  :type="attentionType(session)"
                  effect="plain"
                >{{ attentionLabel(session) }}</el-tag>
              </div>
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
          <div class="conversation-messages">
            <div class="message-timeline">
              <article
                v-for="message in store.detail?.messages"
                :key="message.id"
                class="message-bubble"
                :class="message.display_kind === 'action' ? 'message-bubble--action' : `message-bubble--${message.direction}`"
              >
                <small class="message-bubble__meta">{{ message.display_kind === "action" ? "会话动作" : message.direction === "inbound" ? session.peer_name || "HR" : "我" }} · {{ formatDateTime(message.sent_at) }}</small>
                <p>
                  <span v-if="message.display_kind !== 'action' && latestMessageStatusLabel(message.status)" class="message-bubble__status">{{ latestMessageStatusLabel(message.status) }}</span>
                  {{ message.content || `[${message.message_type}]` }}
                </p>
              </article>
            </div>
            <div v-if="session.history_has_more" class="message-more-actions">
              <el-button
                plain
                :loading="store.mutating"
                @click="loadMoreHistory"
              >获取更多</el-button>
            </div>
          </div>
        </template>
        <el-empty v-else description="选择一个会话查看本地消息" />
      </main>

      <aside class="reply-panel">
        <template v-if="session">
          <div class="heading-actions">
          <el-button
            v-if="session?.job_context_state === 'linked'"
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
            v-if="canMarkRejected"
            size="small"
            type="danger"
            plain
            :loading="store.mutating"
            @click="rejectJob"
          >拒绝</el-button>
          <el-button
            v-if="canMarkInterview"
            size="small"
            type="primary"
            plain
            :loading="store.mutating"
            @click="markManualProgress('interview_scheduled')"
          >已约面试</el-button>
          <el-button
            size="small"
            plain
            :loading="store.mutating"
            @click="analyzeRules"
          >规则分析</el-button>
          <el-button
            v-if="!isCandidateRejected"
            size="small"
            plain
            :loading="store.mutating"
            @click="analyzeProgress"
          >{{ analysisButtonLabel }}</el-button>
          <el-button
            size="small"
            plain
            :loading="store.mutating"
            @click="retransformMessages"
          >重新转义消息</el-button>
          <el-button
            size="small"
            type="primary"
            plain
            :loading="store.mutating"
            @click="forceRefreshHistory"
          >强制更新消息</el-button>
          <el-button
            v-if="(store.detail?.message_count ?? 0) === 0"
            type="primary"
            size="small"
            :loading="store.mutating"
            @click="refreshHistory"
          >获取消息</el-button>
          <el-button
            v-else-if="session?.message_update_required"
            type="primary"
            size="small"
            :loading="store.mutating"
            @click="refreshHistory"
          >更新</el-button>
          </div>
          <section v-if="progress" class="progress-card">
          <div class="progress-card__heading">
            <div style="display:flex">
              <small>当前进展</small>
              <h3 style="margin-left:15px">{{ stageLabel(displayProgressStage) }}</h3>
            </div>
            <el-tag :type="primaryAction ? 'warning' : 'info'">
              {{ primaryAction?.label || (progress.waiting_on === "recruiter" ? "等待中，可主动生成消息" : "当前无需行动") }}
            </el-tag>
          </div>
          <div class="progress-card__facts">
            <span>{{ waitingLabel(progress.waiting_on) }}</span>
            <span v-if="waitingDuration">{{ waitingDuration }}</span>
            <span>{{ contactOriginLabel(progress.contact_origin) }}</span>
            <span v-if="showResumeDelivery">简历：{{ resumeDeliveryLabel(progress.resume_delivery.status) }}</span>
          </div>
          <p v-if="progress.followup.reason_summary">
            行动建议：{{ progress.followup.reason_summary }}
          </p>
          <p v-if="progress.stage === 'rejected' || progress.stage === 'closed'">
            {{ rejectionPartyLabel(progress.outcome.rejection_party) }}：{{ progress.outcome.rejection_reason_summary || rejectionCategoryLabel(progress.outcome.rejection_reason_category) }}
            · 来源：{{ rejectionSourceLabel(progress.outcome.rejection_reason_source) }}
          </p>
          </section>
        </template>
        <section v-if="showResumePanel" class="resume-send-panel">
          <h2>
            附件简历
            <el-tooltip
              content="读取当前 BOSS 账号可发送的附件；提交后在待确认列表批准，再进入执行队列。"
              placement="top"
            >
              <button class="resume-help" type="button" aria-label="查看附件简历说明" @click="showResumeHelp">
                <el-icon><InfoFilled /></el-icon>
              </button>
            </el-tooltip>
          </h2>
          <p v-if="resumeListLoading" class="secondary-text">正在读取附件简历…</p>
          <el-alert v-else-if="resumeListFailed" type="error" :closable="false" show-icon>
            {{ store.resumeListError || "附件简历读取失败，请刷新后重试。" }}
          </el-alert>
          <el-alert v-else-if="resumeListLoaded && !resumeAttachments.length" type="warning" :closable="false" show-icon>
            无可用附件简历
          </el-alert>
          <p v-else-if="!resumeListLoaded" class="secondary-text">尚未读取附件简历。</p>
          <el-radio-group v-if="resumeAttachments.length > 1" v-model="selectedResumeId" class="resume-attachment-list">
            <el-radio v-for="item in resumeAttachments" :key="item.resumeId" :value="item.resumeId">
              {{ item.showName }}<template v-if="item.resumeSizeDesc"> · {{ item.resumeSizeDesc }}</template>
            </el-radio>
          </el-radio-group>
          <span v-else-if="selectedResume" class="resume-selected-name">
            已选择：{{ selectedResume.showName }}<template v-if="selectedResume.resumeSizeDesc"> · {{ selectedResume.resumeSizeDesc }}</template>
          </span>
          <el-button
            v-if="resumeAttachments.length"
            type="warning"
            :disabled="!canConfirmResume"
            @click="confirmResume"
          >发送简历</el-button>
          <!-- <p v-if="selectedResumeId" class="secondary-text">encryptResumeId：{{ selectedResumeId }}</p> -->
          <p v-if="latestResumeSendAction?.status === 'queued' || latestResumeSendAction?.status === 'leased' || latestResumeSendAction?.status === 'dispatching'" class="secondary-text">简历发送进行中…</p>
          <el-alert v-else-if="latestResumeSendAction?.outcome === 'accepted'" type="success" :closable="false" show-icon>
            简历已提交传输，等待 BOSS 平台后续状态。
          </el-alert>
          <el-alert v-else-if="latestResumeSendAction?.outcome === 'unknown'" type="warning" :closable="false" show-icon>
            {{ latestResumeSendAction.error_message || "发送结果待确认，系统不会自动重试。" }}
          </el-alert>
          <el-alert v-else-if="latestResumeSendAction?.outcome === 'failed'" type="error" :closable="false" show-icon>
            {{ latestResumeSendAction.error_message || "简历发送失败。" }}
          </el-alert>
        </section>
        <h2>AI 回复草稿</h2>
        <div v-if="analysisReplyDraft" class="analysis-draft">
          <div>
            <strong>沟通草稿建议</strong>
            <span>{{ analysisReason }}</span>
          </div>
          <p>{{ analysisReplyDraft }}</p>
          <el-button size="small" plain @click="useAnalysisReplyDraft">填入编辑框</el-button>
        </div>
        <el-input
          v-model="instruction"
          type="textarea"
          :rows="2"
          placeholder="可选：本次回复的临时要求"
          @input="markEditorDirty"
        />
        <div class="reply-actions">
          <el-button
            v-if="primaryAction"
            type="primary"
            plain
            :disabled="!session"
            :loading="store.mutating"
            @click="generate(false, primaryAction.type)"
          >{{ primaryAction.label }}</el-button>
          <el-button
            :disabled="!session"
            :loading="store.mutating"
            @click="generate(false, defaultMessageActionKind)"
          >生成消息</el-button>
          <el-button
            :disabled="!task || !session"
            :loading="store.mutating"
            @click="generate(true, task?.action_kind || 'reply')"
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
          <el-button
            type="primary"
            :disabled="!finalText.trim()"
            :loading="store.mutating"
            @click="confirm"
          >确认发送</el-button>
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

    <el-dialog v-model="messageTransformDialogVisible" class="message-transform-dialog" title="消息转义规则" width="min(900px, 94vw)">
      <el-alert
        type="info"
        :closable="false"
        title="默认规则用于后续全部会话的同步消息；中立动作不会被当作 HR 发言或触发自动回复。"
      />
      <div class="message-transform-rules">
        <section v-for="(rule, index) in messageTransformRules" :key="rule.id" class="message-transform-rule">
          <div class="message-transform-rule__heading">
            <el-switch v-model="rule.enabled" active-text="启用" inactive-text="停用" />
            <el-input v-model="rule.label" placeholder="规则名称" />
            <el-button link type="danger" :icon="Delete" aria-label="删除规则" @click="removeMessageTransformRule(index)">删除</el-button>
          </div>
          <div class="message-transform-rule__fields">
            <el-select v-model="rule.direction" aria-label="消息方向">
              <el-option label="HR 侧" value="inbound" />
              <el-option label="我方" value="outbound" />
            </el-select>
            <el-select v-model="rule.match_mode" aria-label="匹配方式">
              <el-option label="精确匹配" value="exact" />
              <el-option label="包含文字" value="contains" />
              <el-option label="正则表达式" value="regex" />
            </el-select>
            <el-input v-model="rule.pattern" placeholder="匹配内容" />
            <el-select v-model="rule.output_kind" aria-label="处理方式">
              <el-option label="转为中立动作" value="action" />
              <el-option label="过滤消息" value="discard" />
            </el-select>
            <el-input v-model="rule.display_content" :disabled="rule.output_kind === 'discard'" placeholder="页面展示文案；正则可使用 \1、\2" />
          </div>
          <div class="message-transform-rule__condition">
            <el-select v-model="rule.condition_branch" aria-label="条件分支">
              <el-option label="无条件" value="always" />
              <el-option label="条件线之后" value="if" />
              <el-option label="条件线之前" value="else" />
            </el-select>
            <el-select
              v-model="rule.condition_rule_id"
              :disabled="rule.condition_branch === 'always'"
              placeholder="选择条件线"
              aria-label="条件线"
            >
              <el-option
                v-for="conditionRule in conditionLineRules(rule.id)"
                :key="conditionRule.id"
                :label="conditionRule.label"
                :value="conditionRule.id"
              />
            </el-select>
          </div>
          <el-checkbox v-model="rule.requires_resume_sent" :disabled="rule.output_kind === 'discard'">仅在此前已发送附件简历时匹配</el-checkbox>
        </section>
      </div>
      <template #footer>
        <el-button :icon="Plus" :disabled="messageTransformSaving" @click="addMessageTransformRule">新增规则</el-button>
        <el-button :disabled="messageTransformSaving" @click="resetMessageTransformSettings">恢复默认配置</el-button>
        <el-button :disabled="messageTransformSaving" @click="messageTransformDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="messageTransformSaving" @click="saveMessageTransformSettings">保存全局配置</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.heading-actions,
.reply-actions,
.runtime-summary,
.runtime-actions,
.session-card__meta,
.identity-tags,
.session-heading-actions {
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
  min-height: 300px;
  max-height: 600px;
  padding: 0;
  overflow: hidden;
}

.session-list,
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

.conversation-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 20px;
  overflow: hidden;
}

.section-heading {
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

.conversation-heading {
  display: grid;
  gap: 10px;
}

.conversation-identity {
  display: grid;
  gap: 6px;
}

.conversation-job {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
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

.session-card__preview--action {
  color: var(--el-text-color-secondary);
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

.session-card__attention-badge {
  align-self: flex-start;
}

.session-card__time {
  font-size: 11px;
}

.message-timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.conversation-messages {
  flex: 1;
  min-height: 0;
  margin-top: 18px;
  overflow-y: auto;
}

.batch-progress-metrics {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.batch-summary-panel {
  display: grid;
  gap: 14px;
}

.batch-summary-panel__main-row {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.batch-summary-panel__pending {
  display: grid;
  gap: 8px;
}

.batch-summary-panel__resume-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: nowrap;
  white-space: nowrap;
  margin: 14px 0 0;
}

.batch-summary-panel__resume-label {
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.batch-summary-panel__resume-select {
  width: 240px;
  min-width: 240px;
  flex: 0 0 240px;
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

.message-bubble--action {
  align-self: center;
  max-width: 92%;
  padding: 7px 12px;
  background: var(--el-fill-color-lighter);
  border: 1px dashed var(--el-border-color);
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

.message-bubble__status {
  margin-right: 6px;
  font-size: 12px;
  font-weight: 700;
}

.message-bubble--inbound p {
  color: var(--el-color-danger);
}

.message-bubble--outbound p {
  color: var(--el-color-primary);
}

.message-bubble--action p {
  color: var(--el-text-color-secondary);
}

.message-transform-rules {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.message-transform-rule {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
}

.message-transform-rule__heading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.message-transform-rule__fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  min-width: 0;
  gap: 8px;
}

.message-transform-rule__condition {
  display: grid;
  grid-template-columns: minmax(160px, 220px) minmax(0, 1fr);
  gap: 8px;
  min-width: 0;
}

:deep(.message-transform-dialog) {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  margin: 16px auto;
  max-height: calc(100vh - 32px);
}

:deep(.message-transform-dialog .el-dialog__body) {
  min-width: 0;
  max-height: calc(100vh - 190px);
  overflow-x: hidden;
  overflow-y: auto;
}

.message-transform-rule :deep(.el-input),
.message-transform-rule :deep(.el-select) {
  min-width: 0;
}

.reply-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.resume-send-panel,
.resume-attachment-list {
  display: grid;
  gap: 10px;
}

.resume-help {
  display: inline-flex;
  margin-left: 4px;
  padding: 0;
  border: 0;
  color: var(--el-color-info);
  background: transparent;
  cursor: pointer;
  vertical-align: middle;
}

.resume-selected-name {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.resume-attachment-list .el-radio {
  height: auto;
  white-space: normal;
}

.progress-card {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
}

.progress-card__heading,
.progress-card__facts {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.progress-card h3,
.progress-card p {
  margin: 0;
}

.progress-card__facts {
  justify-content: flex-start;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.analysis-draft {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-light);
}

.analysis-draft > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.analysis-draft p {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
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
  .message-transform-rule__heading,
  .message-transform-rule__fields,
  .message-transform-rule__condition { grid-template-columns: 1fr; }
}
</style>
