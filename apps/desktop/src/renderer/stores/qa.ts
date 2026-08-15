import { ref } from "vue";
import { defineStore } from "pinia";

import { ApiError, NetworkError, api } from "../services/api";
import type {
  QAAnswerRequestPayload,
  QAAnswerResponse,
  QARewriteMeta,
  QASessionDetail,
  QASessionSummary
} from "../types";

const fallbackRewrite = (rewrittenQuestion: string): QARewriteMeta => ({
  rewritten_question: rewrittenQuestion,
  requires_history: false,
  used_history: false,
  intent: "answer",
  risk_flags: ["legacy_session"],
  confidence: 0,
  strategy: "legacy"
});

export const useQaStore = defineStore("qa", () => {
  const result = ref<QAAnswerResponse | null>(null);
  const sessions = ref<QASessionSummary[]>([]);
  const sessionDetail = ref<QASessionDetail | null>(null);
  const selectedSessionId = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const endpointUnavailable = ref(false);
  const connectionUnavailable = ref(false);

  const answer = async (payload: QAAnswerRequestPayload) => {
    loading.value = true;
    error.value = null;

    try {
      result.value = await api.answerQuestion(payload);
      selectedSessionId.value = result.value.session_id;
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;
      return result.value;
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      result.value = null;
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const loadSessions = async () => {
    sessions.value = (await api.listQaSessions()).items;
    return sessions.value;
  };

  const loadSession = async (sessionId: string) => {
    sessionDetail.value = await api.getQaSession(sessionId);
    selectedSessionId.value = sessionId;
    const latestAssistantMessage = [...sessionDetail.value.messages]
      .reverse()
      .find((message) => message.role === "assistant" && message.answer_status);
    result.value = latestAssistantMessage
      ? {
          session_id: sessionDetail.value.session_id,
          mode: sessionDetail.value.mode,
          rewritten_question:
            latestAssistantMessage.rewritten_question ??
            latestAssistantMessage.question ??
            sessionDetail.value.last_question ??
            sessionDetail.value.title,
          rewrite:
            latestAssistantMessage.rewrite ??
            fallbackRewrite(
              latestAssistantMessage.rewritten_question ??
                latestAssistantMessage.question ??
                sessionDetail.value.last_question ??
                sessionDetail.value.title
            ),
          question:
            latestAssistantMessage.question ??
            sessionDetail.value.last_question ??
            sessionDetail.value.title,
          answer: latestAssistantMessage.content,
          answer_status: latestAssistantMessage.answer_status ?? "grounded",
          confidence: latestAssistantMessage.confidence ?? 0,
          applied_filters: latestAssistantMessage.applied_filters ?? {},
          citations: latestAssistantMessage.citations ?? [],
          used_grounded_items: latestAssistantMessage.used_grounded_items ?? [],
          suggested_queries: latestAssistantMessage.suggested_queries ?? [],
          verification: latestAssistantMessage.verification ?? undefined,
          retry_count: latestAssistantMessage.retry_count ?? 0
        }
      : null;
    return sessionDetail.value;
  };

  const deleteSession = async (sessionId: string) => {
    const response = await api.deleteQaSession(sessionId);
    if (response.deleted) {
      sessions.value = sessions.value.filter((entry) => entry.session_id !== sessionId);
      if (selectedSessionId.value === sessionId) {
        selectedSessionId.value = null;
        sessionDetail.value = null;
        result.value = null;
      }
    }
    return response.deleted;
  };

  const reset = () => {
    result.value = null;
    sessionDetail.value = null;
    selectedSessionId.value = null;
    error.value = null;
    endpointUnavailable.value = false;
    connectionUnavailable.value = false;
  };

  return {
    result,
    sessions,
    sessionDetail,
    selectedSessionId,
    loading,
    error,
    endpointUnavailable,
    connectionUnavailable,
    answer,
    loadSessions,
    loadSession,
    deleteSession,
    reset
  };
});
