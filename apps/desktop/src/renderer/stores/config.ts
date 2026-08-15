import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "../services/api";
import type {
  AppConfigPayload,
  CodexConnectivityCheckResponse,
  ProviderConnectivityCheckResponse
} from "../types";

const SECRET_STORAGE_PREFIX = "fine-job.config.secret.";
const SECRET_FIELDS = ["llm_api_key", "embedding_api_key"] as const;

type SecretField = (typeof SECRET_FIELDS)[number];

const getSecretStorageKey = (field: SecretField) => `${SECRET_STORAGE_PREFIX}${field}`;

const readStoredSecret = (field: SecretField) => {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage.getItem(getSecretStorageKey(field));
  } catch {
    return null;
  }
};

const writeStoredSecret = (field: SecretField, value: string | null | undefined) => {
  if (typeof window === "undefined") {
    return;
  }

  try {
    const storageKey = getSecretStorageKey(field);
    if (typeof value === "string" && value.length > 0) {
      window.localStorage.setItem(storageKey, value);
      return;
    }

    window.localStorage.removeItem(storageKey);
  } catch {
    // Ignore storage failures so settings save can still succeed.
  }
};

export interface CapabilityStatusState {
  status: "unknown" | "checking" | "ready" | "failed" | "invalid";
  detail: string;
  checkedAt: string | null;
  provider: string | null;
  model: string | null;
  baseUrl: string | null;
  errorCategory: string | null;
  cliVersion: string | null;
  authenticated: boolean | null;
  reasoningEffort: string | null;
}

const createCapabilityState = (
  partial?: Partial<CapabilityStatusState>
): CapabilityStatusState => ({
  status: "unknown",
  detail: "尚未检测",
  checkedAt: null,
  provider: null,
  model: null,
  baseUrl: null,
  errorCategory: null,
  cliVersion: null,
  authenticated: null,
  reasoningEffort: null,
  ...partial
});

const mergeStoredSecrets = (config: AppConfigPayload): AppConfigPayload => {
  const llmApiKey = config.llm_api_key ?? readStoredSecret("llm_api_key");
  const embeddingApiKey = config.embedding_api_key ?? readStoredSecret("embedding_api_key");

  return {
    ...config,
    llm_api_key: llmApiKey ?? null,
    llm_configured: Boolean(config.llm_configured || llmApiKey),
    embedding_api_key: embeddingApiKey ?? null,
    embedding_configured: Boolean(config.embedding_configured || embeddingApiKey)
  };
};

const applyCapabilityResult = (result: ProviderConnectivityCheckResponse) =>
  createCapabilityState({
    status: result.status,
    detail: result.detail,
    checkedAt: result.checked_at,
    provider: result.provider,
    model: result.model,
    baseUrl: result.base_url,
    errorCategory: result.error_category ?? null
  });

export const useConfigStore = defineStore("config", () => {
  const data = ref<AppConfigPayload | null>(null);
  const loading = ref(false);
  const saving = ref(false);
  const hasLoaded = ref(false);
  const error = ref<string | null>(null);
  const endpointUnavailable = ref(false);
  const connectionUnavailable = ref(false);
  const llmStatus = ref<CapabilityStatusState>(createCapabilityState());
  const embeddingStatus = ref<CapabilityStatusState>(createCapabilityState());
  const codexStatus = ref<CapabilityStatusState>(createCapabilityState());
  const probingCapabilities = ref(false);

  const backendStatus = computed<CapabilityStatusState>(() => {
    if (loading.value && !hasLoaded.value) {
      return createCapabilityState({ status: "checking", detail: "正在连接后端" });
    }

    if (connectionUnavailable.value) {
      return createCapabilityState({
        status: "failed",
        detail: error.value ?? "无法连接后端",
        errorCategory: "CONNECTION_UNAVAILABLE"
      });
    }

    if (endpointUnavailable.value) {
      return createCapabilityState({
        status: "failed",
        detail: error.value ?? "配置接口不可用",
        errorCategory: "ENDPOINT_UNAVAILABLE"
      });
    }

    if (!hasLoaded.value || !data.value) {
      return createCapabilityState();
    }

    return createCapabilityState({
      status: "ready",
      detail: "后端已连接"
    });
  });

  const selectedGenerationStatus = computed(() =>
    data.value?.reasoning_executor === "codex-cli" ? codexStatus.value : llmStatus.value
  );

  const generationReady = computed(
    () =>
      backendStatus.value.status === "ready" &&
      selectedGenerationStatus.value.status === "ready"
  );

  const generationBlockReason = computed(() => {
    if (backendStatus.value.status !== "ready") {
      return backendStatus.value.detail;
    }

    if (selectedGenerationStatus.value.status !== "ready") {
      return selectedGenerationStatus.value.detail || "智能执行器尚未就绪";
    }

    return "";
  });

  const applyCodexCapabilityResult = (result: CodexConnectivityCheckResponse) =>
    createCapabilityState({
      status: result.status,
      detail: result.detail,
      checkedAt: result.checked_at,
      provider: "codex-cli",
      model: result.model,
      baseUrl: result.cli_path,
      errorCategory: result.error_category ?? null,
      cliVersion: result.cli_version,
      authenticated: result.authenticated,
      reasoningEffort: result.reasoning_effort
    });

  const load = async () => {
    loading.value = true;
    error.value = null;

    try {
      data.value = mergeStoredSecrets(await api.getConfig());
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
    } finally {
      hasLoaded.value = true;
      loading.value = false;
    }
  };

  const save = async (payload: Partial<AppConfigPayload>) => {
    saving.value = true;
    error.value = null;

    try {
      const { llm_api_key, embedding_api_key, ...rest } = payload;
      const containsLlmKey = Object.prototype.hasOwnProperty.call(payload, "llm_api_key");
      const containsEmbeddingKey = Object.prototype.hasOwnProperty.call(payload, "embedding_api_key");

      let savedConfig: AppConfigPayload;
      try {
        savedConfig = await api.updateConfig(payload);
      } catch (errorValue) {
        if (
          errorValue instanceof ApiError &&
          errorValue.statusCode === 422 &&
          (containsLlmKey || containsEmbeddingKey)
        ) {
          savedConfig = await api.updateConfig(rest);
        } else {
          throw errorValue;
        }
      }

      if (containsLlmKey) {
        writeStoredSecret("llm_api_key", llm_api_key);
      }
      if (containsEmbeddingKey) {
        writeStoredSecret("embedding_api_key", embedding_api_key);
      }

      data.value = mergeStoredSecrets(savedConfig);
      llmStatus.value = createCapabilityState({
        detail: "配置已更新，请重新测试 LLM 连通性。"
      });
      embeddingStatus.value = createCapabilityState({
        detail: "配置已更新，请重新测试 Embedding 连通性。"
      });
      codexStatus.value = createCapabilityState({
        detail: "配置已更新，请重新检测 Codex CLI。"
      });
      endpointUnavailable.value = false;
      connectionUnavailable.value = false;

      return data.value;
    } catch (errorValue) {
      endpointUnavailable.value = errorValue instanceof ApiError && errorValue.endpointUnavailable;
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  const testLlmConnection = async () => {
    llmStatus.value = createCapabilityState({
      status: "checking",
      detail: "正在测试 LLM 连通性…"
    });

    try {
      const result = await api.checkLlmConnection();
      llmStatus.value = applyCapabilityResult(result);
      return result;
    } catch (errorValue) {
      llmStatus.value = createCapabilityState({
        status: "failed",
        detail: (errorValue as Error).message,
        errorCategory:
          errorValue instanceof ApiError ? errorValue.errorCategory ?? "API_ERROR" : "NETWORK_ERROR"
      });
      throw errorValue;
    }
  };

  const testEmbeddingConnection = async () => {
    embeddingStatus.value = createCapabilityState({
      status: "checking",
      detail: "正在测试 Embedding 连通性…"
    });

    try {
      const result = await api.checkEmbeddingConnection();
      embeddingStatus.value = applyCapabilityResult(result);
      return result;
    } catch (errorValue) {
      embeddingStatus.value = createCapabilityState({
        status: "failed",
        detail: (errorValue as Error).message,
        errorCategory:
          errorValue instanceof ApiError ? errorValue.errorCategory ?? "API_ERROR" : "NETWORK_ERROR"
      });
      throw errorValue;
    }
  };

  const testCodexConnection = async () => {
    codexStatus.value = createCapabilityState({
      status: "checking",
      detail: "正在检测 Codex CLI…"
    });

    try {
      const result = await api.checkCodexConnection();
      codexStatus.value = applyCodexCapabilityResult(result);
      return result;
    } catch (errorValue) {
      codexStatus.value = createCapabilityState({
        status: "failed",
        detail: (errorValue as Error).message,
        errorCategory:
          errorValue instanceof ApiError ? errorValue.errorCategory ?? "API_ERROR" : "NETWORK_ERROR"
      });
      throw errorValue;
    }
  };

  const probeGenerationCapabilities = async () => {
    if (probingCapabilities.value) {
      return;
    }
    probingCapabilities.value = true;
    try {
      await load();
      if (!data.value || endpointUnavailable.value || connectionUnavailable.value) {
        return;
      }
      const generationProbe =
        data.value.reasoning_executor === "codex-cli"
          ? testCodexConnection()
          : testLlmConnection();
      await Promise.allSettled([generationProbe, testEmbeddingConnection()]);
    } finally {
      probingCapabilities.value = false;
    }
  };

  return {
    data,
    loading,
    saving,
    hasLoaded,
    error,
    endpointUnavailable,
    connectionUnavailable,
    backendStatus,
    llmStatus,
    embeddingStatus,
    codexStatus,
    selectedGenerationStatus,
    generationReady,
    generationBlockReason,
    probingCapabilities,
    load,
    save,
    testLlmConnection,
    testEmbeddingConnection,
    testCodexConnection,
    probeGenerationCapabilities
  };
});
