import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type {
  FineJobJobActionEvidence,
  FineJobJobActionGenerateDraftsResponse,
  FineJobJobActionItem,
  FineJobJobActionListResponse,
  FineJobJobActionPriority,
  FineJobJobActionPrimaryAction,
  FineJobJobActionReplyTask,
  FineJobJobActionState,
  FineJobJobActionSummary,
  FineJobJobActionType
} from "@/types";

const ACTION_TYPES = new Set<FineJobJobActionType>([
  "respond_interview",
  "send_resume",
  "reply_recruiter",
  "review_draft",
  "followup_recruiter",
  "ask_rejection_reason"
]);
const ACTION_PRIORITIES = new Set<FineJobJobActionPriority>([
  "urgent",
  "high",
  "normal",
  "low"
]);
const ACTION_STATES = new Set<FineJobJobActionState>([
  "active",
  "snoozed",
  "dismissed",
  "completed"
]);

type ActionFilter = FineJobJobActionType | "";
type PriorityFilter = FineJobJobActionPriority | "";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

const readActionType = (value: unknown): FineJobJobActionType | null =>
  typeof value === "string" && ACTION_TYPES.has(value as FineJobJobActionType)
    ? value as FineJobJobActionType
    : null;

const readPriority = (value: unknown): FineJobJobActionPriority | null =>
  typeof value === "string" && ACTION_PRIORITIES.has(value as FineJobJobActionPriority)
    ? value as FineJobJobActionPriority
    : null;

const readState = (value: unknown): FineJobJobActionState | null =>
  typeof value === "string" && ACTION_STATES.has(value as FineJobJobActionState)
    ? value as FineJobJobActionState
    : null;

const normalizePrimaryAction = (value: unknown): FineJobJobActionPrimaryAction | null => {
  if (!isRecord(value) || value.type !== "open_chat" || !isNonEmptyString(value.label)) {
    return null;
  }
  if (value.route_name !== "fine-job-chat" || !isRecord(value.query)) return null;

  const query: Record<string, string> = {};
  for (const [key, queryValue] of Object.entries(value.query)) {
    if (typeof queryValue !== "string") return null;
    query[key] = queryValue;
  }

  const actionKind = value.action_kind;
  if (actionKind !== undefined && actionKind !== null && ![
    "reply",
    "followup",
    "ask_rejection_reason"
  ].includes(String(actionKind))) return null;

  const replyTaskId = value.reply_task_id;
  if (replyTaskId !== undefined && replyTaskId !== null && typeof replyTaskId !== "string") {
    return null;
  }

  return {
    type: "open_chat",
    label: value.label,
    route_name: "fine-job-chat",
    query,
    action_kind: actionKind as FineJobJobActionPrimaryAction["action_kind"],
    reply_task_id: replyTaskId as string | null | undefined
  };
};

const normalizeReplyTask = (value: unknown): FineJobJobActionReplyTask | null => {
  if (!isRecord(value)
    || !isNonEmptyString(value.id)
    || value.status !== "awaiting_review"
    || !["reply", "followup", "ask_rejection_reason"].includes(String(value.action_kind))
    || !isNonEmptyString(value.based_on_message_id)
    || typeof value.based_on_session_version !== "number"
    || typeof value.draft_text !== "string"
    || typeof value.final_text !== "string"
    || !isNonEmptyString(value.updated_at)) {
    return null;
  }
  return {
    id: value.id,
    action_kind: value.action_kind as FineJobJobActionReplyTask["action_kind"],
    status: "awaiting_review",
    based_on_message_id: value.based_on_message_id,
    based_on_session_version: value.based_on_session_version,
    draft_text: value.draft_text,
    final_text: value.final_text,
    generated_at: typeof value.generated_at === "string" ? value.generated_at : null,
    updated_at: value.updated_at
  };
};

const normalizeEvidence = (value: unknown): FineJobJobActionEvidence => {
  const source = isRecord(value) ? value : {};
  const triggerType = ["message", "activity_event", "reply_task"].includes(
    String(source.trigger_type)
  ) ? source.trigger_type as FineJobJobActionEvidence["trigger_type"] : "activity_event";
  const messageIds = Array.isArray(source.message_ids)
    ? source.message_ids.filter((item): item is string => typeof item === "string")
    : [];
  const activityEventIds = Array.isArray(source.activity_event_ids)
    ? source.activity_event_ids.filter((item): item is string => typeof item === "string")
    : [];
  return {
    trigger_type: triggerType,
    trigger_id: typeof source.trigger_id === "string" ? source.trigger_id : "",
    message_ids: messageIds,
    activity_event_ids: activityEventIds,
    attention_insight_id: typeof source.attention_insight_id === "string"
      ? source.attention_insight_id
      : null
  };
};

const normalizeItem = (value: unknown): FineJobJobActionItem | null => {
  if (!isRecord(value)
    || !isNonEmptyString(value.action_key)
    || !isNonEmptyString(value.job_id)
    || !isNonEmptyString(value.session_id)) {
    return null;
  }
  const actionType = readActionType(value.action_type);
  const priority = readPriority(value.priority_tier);
  const state = readState(value.state);
  const primaryAction = normalizePrimaryAction(value.primary_action);
  if (!actionType || !priority || !state || !primaryAction) return null;

  const secondaryActions = Array.isArray(value.secondary_actions)
    ? value.secondary_actions.filter((item): item is FineJobJobActionItem["secondary_actions"][number] =>
      ["snooze", "dismiss", "complete", "restore"].includes(String(item))
    )
    : [];
  const overdueSeconds = typeof value.overdue_seconds === "number"
    && Number.isFinite(value.overdue_seconds)
    ? Math.max(0, Math.floor(value.overdue_seconds))
    : 0;

  return {
    action_key: value.action_key,
    job_id: value.job_id,
    session_id: value.session_id,
    action_type: actionType,
    priority_tier: priority,
    title: isNonEmptyString(value.title) ? value.title : "岗位名称待补充",
    company_name: isNonEmptyString(value.company_name) ? value.company_name : "公司名称待补充",
    stage: isNonEmptyString(value.stage) ? value.stage : "进展待确认",
    waiting_on: isNonEmptyString(value.waiting_on) ? value.waiting_on : "unknown",
    waiting_since_at: typeof value.waiting_since_at === "string" ? value.waiting_since_at : null,
    due_at: typeof value.due_at === "string" ? value.due_at : null,
    overdue_seconds: overdueSeconds,
    reason_code: typeof value.reason_code === "string" ? value.reason_code : "",
    reason_summary: isNonEmptyString(value.reason_summary)
      ? value.reason_summary
      : "当前岗位存在待处理进展。",
    evidence: normalizeEvidence(value.evidence),
    reply_task: normalizeReplyTask(value.reply_task),
    primary_action: primaryAction,
    secondary_actions: secondaryActions,
    state,
    snoozed_until: typeof value.snoozed_until === "string" ? value.snoozed_until : null
  };
};

const normalizeSummary = (value: unknown): FineJobJobActionSummary | null => {
  if (!isRecord(value)) return null;
  const keys = ["urgent", "high", "normal", "low", "snoozed"] as const;
  if (!keys.every((key) =>
    typeof value[key] === "number"
    && Number.isFinite(value[key])
    && value[key] >= 0
    && Number.isInteger(value[key])
  )) {
    return null;
  }
  return {
    urgent: value.urgent as number,
    high: value.high as number,
    normal: value.normal as number,
    low: value.low as number,
    snoozed: value.snoozed as number
  };
};

const normalizeResponseItems = (response: FineJobJobActionListResponse) => {
  let invalidCount = 0;
  const items = (Array.isArray(response.items) ? response.items : [])
    .map((item) => {
      const normalized = normalizeItem(item);
      if (!normalized) invalidCount += 1;
      return normalized;
    })
    .filter((item): item is FineJobJobActionItem => item !== null);
  return { items, invalidCount };
};

export const useFineJobJobActionsStore = defineStore("fine-job-job-actions", () => {
  const items = ref<FineJobJobActionItem[]>([]);
  const snoozedItems = ref<FineJobJobActionItem[]>([]);
  const summary = ref<FineJobJobActionSummary | null>(null);
  const actionType = ref<ActionFilter>("");
  const priority = ref<PriorityFilter>("");
  const loading = ref(false);
  const mutating = ref(false);
  const error = ref<string | null>(null);
  const invalidItemCount = ref(0);
  const batchResult = ref<FineJobJobActionGenerateDraftsResponse | null>(null);

  const query = () => {
    const params: {
      priority?: FineJobJobActionPriority;
      action_type?: FineJobJobActionType;
    } = {};
    if (priority.value) params.priority = priority.value;
    if (actionType.value) params.action_type = actionType.value;
    return params;
  };

  const load = async () => {
    loading.value = true;
    error.value = null;
    invalidItemCount.value = 0;
    try {
      // active 请求提供唯一可信的 summary，snoozed 请求只提供稍后处理列表。
      const [activeResponse, snoozedResponse] = await Promise.all([
        api.listFineJobJobActions({ status: "active", ...query() }),
        api.listFineJobJobActions({ status: "snoozed", ...query() })
      ]);
      const activeSummary = normalizeSummary(activeResponse.summary);
      if (!activeSummary) throw new Error("今日行动摘要数据异常，请刷新后重试。");
      const active = normalizeResponseItems(activeResponse);
      const snoozed = normalizeResponseItems(snoozedResponse);
      items.value = active.items;
      snoozedItems.value = snoozed.items;
      summary.value = activeSummary;
      invalidItemCount.value = active.invalidCount + snoozed.invalidCount;
      if (invalidItemCount.value > 0) {
        error.value = `有 ${invalidItemCount.value} 条行动数据格式异常，已跳过异常项。`;
      }
      return activeResponse;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      loading.value = false;
    }
  };

  const setActionType = async (value: ActionFilter) => {
    actionType.value = value;
    return load();
  };

  const setPriority = async (value: PriorityFilter) => {
    priority.value = value;
    return load();
  };

  const mutate = async <T>(operation: () => Promise<T>): Promise<T> => {
    mutating.value = true;
    error.value = null;
    try {
      const result = await operation();
      await load();
      return result;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      mutating.value = false;
    }
  };

  const snooze = async (actionKey: string, snoozedUntil: Date | string) => {
    const value = snoozedUntil instanceof Date ? snoozedUntil.toISOString() : snoozedUntil;
    return mutate(() => api.snoozeFineJobJobAction(actionKey, value));
  };

  const dismiss = async (actionKey: string) =>
    mutate(() => api.dismissFineJobJobAction(actionKey));

  const complete = async (actionKey: string) =>
    mutate(() => api.completeFineJobJobAction(actionKey));

  const restore = async (actionKey: string) =>
    mutate(() => api.restoreFineJobJobAction(actionKey));

  const generateDrafts = async (actionKeys: string[]) => {
    if (actionKeys.length === 0) throw new Error("请先选择需要生成草稿的岗位。");
    return mutate(async () => {
      const response = await api.generateFineJobJobActionDrafts(actionKeys);
      if (!response || !Array.isArray(response.results)) {
        throw new Error("批量草稿结果数据异常，请刷新后重试。");
      }
      batchResult.value = response;
      return response;
    });
  };

  return {
    items,
    snoozedItems,
    summary,
    actionType,
    priority,
    loading,
    mutating,
    error,
    invalidItemCount,
    batchResult,
    load,
    refresh: load,
    setActionType,
    setPriority,
    snooze,
    dismiss,
    complete,
    restore,
    generateDrafts
  };
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error)?.message || "今日行动加载失败。";
};
