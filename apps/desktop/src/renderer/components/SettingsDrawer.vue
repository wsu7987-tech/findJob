<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { FolderOpened, InfoFilled, Loading } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";

import { chooseDirectory, hasDirectoryPicker, updateShellConfig } from "@/services/desktop-bridge";
import { formatDateTime } from "@/services/format";
import { buildSettingsSavePayload } from "@/services/settingsShellConfig";
import { useConfigStore } from "@/stores/config";
import { useNoticesStore } from "@/stores/notices";
import type { AppConfigPayload } from "@/types";
import EndpointNotice from "./EndpointNotice.vue";
import SystemStatusChip from "./SystemStatusChip.vue";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "update:open", value: boolean): void;
}>();

const configStore = useConfigStore();
const noticesStore = useNoticesStore();
const supportsDirectoryPicker = hasDirectoryPicker();

const form = reactive({
  output_root: "",
  summary_output_dir: "",
  report_output_dir: "",
  llm_provider: "",
  llm_model: "",
  llm_base_url: "",
  llm_api_key: "",
  embedding_provider: "",
  embedding_model: "",
  embedding_base_url: "",
  embedding_api_key: "",
  fetch_concurrency: 1,
  llm_concurrency: 1,
  embedding_concurrency: 1,
  close_to_tray: true,
  quick_capture_always_on_top: true,
  reasoning_executor: "llm" as "llm" | "codex-cli",
  codex_cli_path: "codex",
  codex_model: "",
  codex_reasoning_effort: "",
  codex_timeout_seconds: 300
});

const reasoningExecutorOptions = [
  { label: "现有 LLM 服务", value: "llm" },
  { label: "本机 Codex CLI", value: "codex-cli" }
] as const;

const codexReasoningEffortOptions = [
  { label: "跟随 Codex 默认", value: "" },
  { label: "Minimal", value: "minimal" },
  { label: "Low", value: "low" },
  { label: "Medium", value: "medium" },
  { label: "High", value: "high" },
  { label: "XHigh（模型支持时）", value: "xhigh" }
] as const;

const llmProviderOptions = [
  { label: "OpenAI", value: "openai" },
  { label: "OpenAI Compatible", value: "openai-compatible" },
  { label: "Stub，本地测试", value: "stub-llm" }
] as const;

const embeddingProviderOptions = [
  { label: "OpenAI", value: "openai" },
  { label: "OpenAI Compatible", value: "openai-compatible" },
  { label: "Stub，本地测试", value: "stub-embedding" }
] as const;

const normalizeLlmProvider = (value: string | null | undefined) => {
  const normalized = value?.trim().toLowerCase() ?? "";
  if (normalized === "deepseek") {
    return "openai-compatible";
  }
  return value?.trim() ?? "";
};

const normalizeEmbeddingProvider = (value: string | null | undefined) => {
  const normalized = value?.trim().toLowerCase() ?? "";
  if (["qianwen", "qwen", "dashscope", "aliyun"].includes(normalized)) {
    return "openai-compatible";
  }
  return value?.trim() ?? "";
};

const isLoadingState = computed(() => configStore.loading && !configStore.hasLoaded);
const canEdit = computed(
  () => !configStore.loading && !configStore.connectionUnavailable && !configStore.endpointUnavailable
);
const selectedGenerationStatus = computed(() =>
  form.reasoning_executor === "codex-cli" ? configStore.codexStatus : configStore.llmStatus
);
const codexCheckFeedback = computed(() => {
  const state = configStore.codexStatus;
  if (state.status === "unknown" || state.status === "checking") {
    return null;
  }

  const metadata = [
    state.authenticated === true ? "已登录" : state.authenticated === false ? "未登录" : null,
    state.cliVersion ? `CLI ${state.cliVersion}` : null,
    state.model ? `模型 ${state.model}` : "模型跟随 Codex 默认",
    state.reasoningEffort ? `推理强度 ${state.reasoningEffort}` : "推理强度跟随 Codex 默认",
    state.baseUrl ? `路径 ${state.baseUrl}` : null
  ].filter((value): value is string => Boolean(value));

  return {
    type: state.status === "ready" ? "success" : state.status === "invalid" ? "warning" : "error",
    title: state.status === "ready" ? "Codex 能力检测通过" : "Codex 能力检测未通过",
    description: [state.detail, metadata.join(" · ")].filter(Boolean).join("\n")
  } as const;
});
const generationReady = computed(
  () => configStore.backendStatus.status === "ready" && selectedGenerationStatus.value.status === "ready"
);
const llmApiKeyTouched = ref(false);
const embeddingApiKeyTouched = ref(false);
const savedFormSnapshot = ref("");

const llmApiKeyConfigured = computed(
  () => Boolean(configStore.data?.llm_configured || form.llm_api_key.trim().length > 0)
);
const embeddingApiKeyConfigured = computed(
  () => Boolean(configStore.data?.embedding_configured || form.embedding_api_key.trim().length > 0)
);
const hasUnsavedChanges = computed(() => savedFormSnapshot.value !== JSON.stringify(form));

const llmProviderHint = computed(() => {
  if (form.llm_provider === "openai-compatible") {
    return "DeepSeek、阿里百炼、本地兼容接口等 OpenAI 风格服务请选择这个选项。";
  }
  if (form.llm_provider === "openai") {
    return "官方 OpenAI 接口使用这个选项。";
  }
  if (form.llm_provider === "stub-llm") {
    return "仅用于本地联调，不具备真实岗位判断和文案生成能力。";
  }
  return "选择现有 LLM 时必须完成此配置。";
});

const embeddingProviderHint = computed(() => {
  if (form.embedding_provider === "openai-compatible") {
    return "通义千问、DashScope、本地 embedding 服务等兼容接口请选择这个选项。";
  }
  if (form.embedding_provider === "stub-embedding") {
    return "仅用于本地联调。FineJob 第一版不依赖 Embedding。";
  }
  return "Embedding 是可选增强，不配置也能开始投递。";
});

const syncFormFromStore = () => {
  Object.assign(form, {
    output_root: configStore.data?.output_root ?? "",
    summary_output_dir: configStore.data?.summary_output_dir ?? "",
    report_output_dir: configStore.data?.report_output_dir ?? "",
    llm_provider: normalizeLlmProvider(configStore.data?.llm_provider),
    llm_model: configStore.data?.llm_model ?? "",
    llm_base_url: configStore.data?.llm_base_url ?? "",
    llm_api_key: configStore.data?.llm_api_key ?? "",
    embedding_provider: normalizeEmbeddingProvider(configStore.data?.embedding_provider),
    embedding_model: configStore.data?.embedding_model ?? "",
    embedding_base_url: configStore.data?.embedding_base_url ?? "",
    embedding_api_key: configStore.data?.embedding_api_key ?? "",
    fetch_concurrency: configStore.data?.fetch_concurrency ?? 1,
    llm_concurrency: configStore.data?.llm_concurrency ?? 1,
    embedding_concurrency: configStore.data?.embedding_concurrency ?? 1,
    close_to_tray: configStore.data?.close_to_tray ?? true,
    quick_capture_always_on_top: configStore.data?.quick_capture_always_on_top ?? true,
    reasoning_executor: configStore.data?.reasoning_executor ?? "llm",
    codex_cli_path: configStore.data?.codex_cli_path ?? "codex",
    codex_model: configStore.data?.codex_model ?? "",
    codex_reasoning_effort: configStore.data?.codex_reasoning_effort ?? "",
    codex_timeout_seconds: configStore.data?.codex_timeout_seconds ?? 300
  });
  llmApiKeyTouched.value = false;
  embeddingApiKeyTouched.value = false;
  savedFormSnapshot.value = JSON.stringify(form);
};

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      return;
    }
    await configStore.load();
    syncFormFromStore();
  },
  { immediate: true }
);

const confirmDiscardChanges = async () => {
  if (!hasUnsavedChanges.value) {
    return true;
  }

  try {
    await ElMessageBox.confirm("当前配置有尚未保存的修改，关闭后会丢失。", "放弃修改？", {
      confirmButtonText: "放弃修改",
      cancelButtonText: "继续编辑",
      type: "warning"
    });
    syncFormFromStore();
    return true;
  } catch {
    return false;
  }
};

const requestClose = async () => {
  if (await confirmDiscardChanges()) {
    emit("update:open", false);
  }
};

const handleBeforeClose = async (done: () => void) => {
  if (await confirmDiscardChanges()) {
    done();
  }
};

const save = async () => {
  try {
    const payload: Partial<AppConfigPayload> = buildSettingsSavePayload(
      {
        ...form,
        quick_capture_hotkey: configStore.data?.quick_capture_hotkey ?? "",
        quick_capture_screenshot_hotkey: configStore.data?.quick_capture_screenshot_hotkey ?? ""
      },
      {
        llmApiKeyTouched: llmApiKeyTouched.value,
        embeddingApiKeyTouched: embeddingApiKeyTouched.value
      }
    );

    await configStore.save(payload);
    await updateShellConfig({
      closeToTray: form.close_to_tray,
      quickCaptureAlwaysOnTop: form.quick_capture_always_on_top
    });
    syncFormFromStore();
    noticesStore.push({
      kind: "success",
      title: "配置已保存",
      message: "FineJob 配置已同步到本地后端。"
    });
  } catch {
    noticesStore.push({
      kind: "error",
      title: "配置保存失败",
      message: configStore.error ?? "请稍后重试。"
    });
  }
};

const runLlmCheck = async () => {
  try {
    await configStore.testLlmConnection();
    noticesStore.push({
      kind: "success",
      title: "LLM 连通测试完成",
      message: configStore.llmStatus.detail
    });
  } catch {
    noticesStore.push({
      kind: "warning",
      title: "LLM 连通测试失败",
      message: configStore.llmStatus.detail
    });
  }
};

const runEmbeddingCheck = async () => {
  try {
    await configStore.testEmbeddingConnection();
    noticesStore.push({
      kind: "success",
      title: "Embedding 连通测试完成",
      message: configStore.embeddingStatus.detail
    });
  } catch {
    noticesStore.push({
      kind: "warning",
      title: "Embedding 连通测试失败",
      message: configStore.embeddingStatus.detail
    });
  }
};

const runCodexCheck = async () => {
  try {
    const result = await configStore.testCodexConnection();
    noticesStore.push({
      kind: result.ok ? "success" : "warning",
      title: result.ok ? "Codex 能力检测通过" : "Codex 能力检测未通过",
      message: configStore.codexStatus.detail
    });
  } catch {
    noticesStore.push({
      kind: "warning",
      title: "Codex CLI 检测失败",
      message: configStore.codexStatus.detail
    });
  }
};

const markLlmApiKeyTouched = () => {
  llmApiKeyTouched.value = true;
};

const markEmbeddingApiKeyTouched = () => {
  embeddingApiKeyTouched.value = true;
};

const pickDirectory = async (
  field: "output_root" | "summary_output_dir" | "report_output_dir",
  title: string
) => {
  const selected = await chooseDirectory({ title });
  if (selected) {
    form[field] = selected;
  }
};
</script>

<template>
  <el-drawer
    class="settings-drawer finejob-settings"
    :model-value="open"
    size="560px"
    title="FineJob 配置"
    :before-close="handleBeforeClose"
    @close="emit('update:open', false)"
  >
    <div class="settings-workbench">
      <section class="settings-hero">
        <div>
          <p class="panel-eyebrow">Required</p>
          <h3>先配置智能执行器，才能开始投递</h3>
          <p class="secondary-text">
            可使用现有 LLM 服务或本机已登录的 Codex CLI。所有结果仍由 FineJob 保存和展示。
          </p>
        </div>
        <el-tag size="large" :type="generationReady ? 'success' : 'warning'">
          {{ generationReady ? "执行器已就绪" : "执行器待配置" }}
        </el-tag>
      </section>

      <div class="settings-status-grid">
        <SystemStatusChip label="Backend" :state="configStore.backendStatus" />
        <SystemStatusChip
          :label="form.reasoning_executor === 'codex-cli' ? 'Codex CLI' : 'LLM 服务'"
          :state="selectedGenerationStatus"
        />
        <SystemStatusChip label="Embedding 可选" :state="configStore.embeddingStatus" />
      </div>

      <div v-if="isLoadingState" class="page-stack">
        <p class="secondary-text">正在从后端加载配置…</p>
      </div>

      <EndpointNotice
        v-else-if="configStore.connectionUnavailable"
        type="error"
        title="无法连接后端"
        detail="配置页依赖本地后端服务，请先启动后端后再重试。"
      />

      <EndpointNotice
        v-else-if="configStore.endpointUnavailable"
        type="warning"
        title="配置接口不可用"
        detail="当前后端版本还未提供配置接口，请更新后端后重试。"
      />

      <EndpointNotice
        v-else-if="configStore.error"
        type="warning"
        title="配置加载出错"
        :detail="configStore.error"
      />

      <el-form v-else label-position="top" class="settings-form">
        <section class="settings-section settings-section--required">
          <div class="settings-section__header">
            <div>
              <p class="panel-eyebrow">Reasoning</p>
              <h4>智能执行器，必需</h4>
            </div>
            <span class="secondary-text">保存后请检测，首页会根据当前执行器状态判断是否可开始。</span>
          </div>

          <el-form-item label="执行器">
            <el-select v-model="form.reasoning_executor" style="width: 100%">
              <el-option
                v-for="option in reasoningExecutorOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </el-form-item>

          <template v-if="form.reasoning_executor === 'llm'">
          <el-form-item>
            <template #label>
              <span class="setting-label">
                <span>提供方</span>
                <el-tooltip :content="llmProviderHint" effect="dark" placement="top" :show-after="150">
                  <el-icon class="setting-label__hint"><InfoFilled /></el-icon>
                </el-tooltip>
              </span>
            </template>
            <el-select v-model="form.llm_provider" placeholder="请选择 LLM 提供方" style="width: 100%">
              <el-option
                v-for="option in llmProviderOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="模型名">
            <el-input v-model="form.llm_model" name="llm_model" autocomplete="off" placeholder="例如 gpt-4.1-mini / deepseek-chat" />
          </el-form-item>

          <el-form-item label="接口地址">
            <el-input
              v-model="form.llm_base_url"
              name="llm_base_url"
              type="url"
              inputmode="url"
              autocomplete="off"
              placeholder="例如：https://api.example.com/v1"
            />
          </el-form-item>

          <el-form-item label="API Key">
            <el-input
              v-model="form.llm_api_key"
              name="llm_api_key"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="请输入 LLM API Key"
              @input="markLlmApiKeyTouched"
            />
            <div class="setting-note">
              <el-tag size="small" :type="llmApiKeyConfigured ? 'success' : 'info'">
                {{ llmApiKeyConfigured ? "当前已配置" : "尚未配置" }}
              </el-tag>
              <span class="secondary-text">留空则保持当前密钥不变。</span>
            </div>
          </el-form-item>

          <div class="setting-actions">
            <el-button type="primary" :loading="configStore.saving" :disabled="!canEdit" @click="save">
              保存配置
            </el-button>
            <el-button
              type="primary"
              plain
              :loading="configStore.llmStatus.status === 'checking'"
              :disabled="!canEdit"
              :icon="configStore.llmStatus.status === 'checking' ? Loading : undefined"
              @click="runLlmCheck"
            >
              测试 LLM
            </el-button>
            <span v-if="configStore.llmStatus.checkedAt" class="secondary-text">
              最近检测：{{ formatDateTime(configStore.llmStatus.checkedAt) }}
            </span>
          </div>
          </template>

          <template v-else>
            <el-form-item label="Codex CLI 路径">
              <el-input
                v-model="form.codex_cli_path"
                name="codex_cli_path"
                autocomplete="off"
                placeholder="codex，或 codex.exe / codex.cmd 的绝对路径"
              />
            </el-form-item>

            <el-form-item label="模型">
              <el-input
                v-model="form.codex_model"
                name="codex_model"
                autocomplete="off"
                placeholder="留空则跟随 Codex 默认模型"
              />
            </el-form-item>

            <el-form-item label="推理强度">
              <el-select v-model="form.codex_reasoning_effort" style="width: 100%">
                <el-option
                  v-for="option in codexReasoningEffortOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="单次任务超时（秒）">
              <el-input-number
                v-model="form.codex_timeout_seconds"
                :min="30"
                :max="3600"
                :step="30"
                style="width: 100%"
              />
            </el-form-item>

            <p class="secondary-text">
              FineJob 只调用本机 CLI，不读取登录凭据。首版使用临时目录、只读沙箱和结构化输出，不允许浏览器操作或自动发送。
            </p>

            <div class="setting-actions">
              <el-button type="primary" :loading="configStore.saving" :disabled="!canEdit" @click="save">
                保存配置
              </el-button>
              <el-button
                type="primary"
                plain
                :loading="configStore.codexStatus.status === 'checking'"
                :disabled="!canEdit"
                :icon="configStore.codexStatus.status === 'checking' ? Loading : undefined"
                @click="runCodexCheck"
              >
                检测 Codex CLI
              </el-button>
              <span v-if="configStore.codexStatus.checkedAt" class="secondary-text">
                最近检测：{{ formatDateTime(configStore.codexStatus.checkedAt) }}
              </span>
            </div>
            <el-alert
              v-if="codexCheckFeedback"
              class="capability-check-result"
              :title="codexCheckFeedback.title"
              :description="codexCheckFeedback.description"
              :type="codexCheckFeedback.type"
              :closable="false"
              show-icon
            />
          </template>
        </section>

        <section class="settings-section">
          <div class="settings-section__header">
            <div>
              <p class="panel-eyebrow">Local Data</p>
              <h4>本地路径</h4>
            </div>
            <span class="secondary-text">复用现有后端字段，后续会映射为简历、日志、截图和投递复盘目录。</span>
          </div>

          <el-form-item label="输出根目录">
            <el-input
              v-model="form.output_root"
              name="output_root"
              autocomplete="off"
              placeholder="例如：D:\\FineJob\\outputs"
            >
              <template v-if="supportsDirectoryPicker" #append>
                <el-button
                  :icon="FolderOpened"
                  aria-label="选择输出根目录"
                  @click="pickDirectory('output_root', '选择输出根目录')"
                />
              </template>
            </el-input>
          </el-form-item>

          <el-form-item label="分析结果目录">
            <el-input v-model="form.summary_output_dir" name="summary_output_dir" autocomplete="off">
              <template v-if="supportsDirectoryPicker" #append>
                <el-button
                  :icon="FolderOpened"
                  aria-label="选择分析结果目录"
                  @click="pickDirectory('summary_output_dir', '选择分析结果目录')"
                />
              </template>
            </el-input>
          </el-form-item>

          <el-form-item label="投递复盘目录">
            <el-input v-model="form.report_output_dir" name="report_output_dir" autocomplete="off">
              <template v-if="supportsDirectoryPicker" #append>
                <el-button
                  :icon="FolderOpened"
                  aria-label="选择投递复盘目录"
                  @click="pickDirectory('report_output_dir', '选择投递复盘目录')"
                />
              </template>
            </el-input>
          </el-form-item>
        </section>

        <section class="settings-section">
          <div class="settings-section__header">
            <div>
              <p class="panel-eyebrow">Runtime</p>
              <h4>执行强度</h4>
            </div>
            <span class="secondary-text">先保守设置。平台限频和投递策略后续会在“投递策略”页单独管理。</span>
          </div>

          <el-form-item label="页面抓取并发">
            <el-input-number v-model="form.fetch_concurrency" :min="1" :max="5" :step="1" style="width: 100%" />
          </el-form-item>

          <el-form-item label="LLM 并发">
            <el-input-number v-model="form.llm_concurrency" :min="1" :max="5" :step="1" style="width: 100%" />
          </el-form-item>

          <el-form-item label="关闭主窗口后隐藏到托盘">
            <el-switch v-model="form.close_to_tray" />
          </el-form-item>

          <el-form-item label="辅助窗口始终置顶">
            <el-switch v-model="form.quick_capture_always_on_top" />
          </el-form-item>
        </section>

        <el-collapse>
          <el-collapse-item name="embedding">
            <template #title>
              <span class="settings-collapse-title">Embedding 语义记忆，可选增强</span>
            </template>

            <section class="settings-section settings-section--nested">
              <p class="secondary-text">
                不配置 Embedding 也可以开始自动投递。它后续用于相似岗位、历史决策和 HR 问题检索。
              </p>

              <el-form-item>
                <template #label>
                  <span class="setting-label">
                    <span>Embedding 提供方</span>
                    <el-tooltip :content="embeddingProviderHint" effect="dark" placement="top" :show-after="150">
                      <el-icon class="setting-label__hint"><InfoFilled /></el-icon>
                    </el-tooltip>
                  </span>
                </template>
                <el-select
                  v-model="form.embedding_provider"
                  placeholder="请选择 Embedding 提供方"
                  style="width: 100%"
                >
                  <el-option
                    v-for="option in embeddingProviderOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </el-form-item>

              <el-form-item label="Embedding 模型">
                <el-input v-model="form.embedding_model" name="embedding_model" autocomplete="off" />
              </el-form-item>

              <el-form-item label="Embedding 接口地址">
                <el-input
                  v-model="form.embedding_base_url"
                  name="embedding_base_url"
                  type="url"
                  inputmode="url"
                  autocomplete="off"
                  placeholder="例如：https://api.example.com/v1"
                />
              </el-form-item>

              <el-form-item label="Embedding API Key">
                <el-input
                  v-model="form.embedding_api_key"
                  name="embedding_api_key"
                  type="password"
                  show-password
                  autocomplete="new-password"
                  placeholder="请输入 Embedding API Key"
                  @input="markEmbeddingApiKeyTouched"
                />
                <div class="setting-note">
                  <el-tag size="small" :type="embeddingApiKeyConfigured ? 'success' : 'info'">
                    {{ embeddingApiKeyConfigured ? "当前已配置" : "尚未配置" }}
                  </el-tag>
                  <span class="secondary-text">留空则保持当前密钥不变。</span>
                </div>
              </el-form-item>

              <el-form-item label="Embedding 并发">
                <el-input-number
                  v-model="form.embedding_concurrency"
                  :min="1"
                  :max="5"
                  :step="1"
                  style="width: 100%"
                />
              </el-form-item>

              <div class="setting-actions">
                <el-button
                  type="primary"
                  plain
                  :loading="configStore.embeddingStatus.status === 'checking'"
                  :disabled="!canEdit"
                  :icon="configStore.embeddingStatus.status === 'checking' ? Loading : undefined"
                  @click="runEmbeddingCheck"
                >
                  测试 Embedding
                </el-button>
                <span v-if="configStore.embeddingStatus.checkedAt" class="secondary-text">
                  最近检测：{{ formatDateTime(configStore.embeddingStatus.checkedAt) }}
                </span>
              </div>
            </section>
          </el-collapse-item>
        </el-collapse>
      </el-form>
    </div>

    <template #footer>
      <div class="drawer-footer">
        <el-button @click="requestClose">关闭</el-button>
        <el-button type="primary" :loading="configStore.saving" :disabled="!canEdit" @click="save">
          保存全部配置
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.settings-workbench,
.settings-form {
  display: grid;
  gap: 14px;
}

.settings-workbench :deep(.el-form-item) {
  margin-bottom: 16px;
}

.settings-hero,
.settings-section {
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: #ffffff;
  padding: 14px;
  box-shadow: var(--shadow-subtle);
}

.settings-hero {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: flex-start;
  background:
    linear-gradient(135deg, rgba(29, 107, 82, 0.1), rgba(255, 255, 255, 0.9)),
    #ffffff;
}

.settings-hero h3,
.settings-section h4 {
  margin: 0;
}

.settings-status-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.settings-section {
  display: grid;
  gap: 10px;
}

.settings-section--required {
  border-color: rgba(29, 107, 82, 0.28);
}

.settings-section--nested {
  padding: 0;
  border: 0;
  box-shadow: none;
}

.settings-section__header {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: flex-start;
}

.setting-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.setting-label__hint {
  color: var(--ink-soft);
  cursor: help;
}

.setting-note,
.setting-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 8px;
}

.capability-check-result {
  white-space: pre-line;
}

.settings-collapse-title {
  font-weight: 600;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

@media (max-width: 640px) {
  .settings-status-grid {
    grid-template-columns: 1fr;
  }
}
</style>
