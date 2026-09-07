<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import { api } from "@/services/api";
import { formatDateTime } from "@/services/format";
import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobPlatformSessionsStore } from "@/stores/fineJobPlatformSessions";

const platformStore = useFineJobPlatformSessionsStore();
const captureStore = useFineJobBossCaptureStore();
const networkDebug = ref<Awaited<ReturnType<typeof api.getFineJobBossNetworkDebugStatus>> | null>(null);
const networkDebugLoading = ref(false);
const networkDebugError = ref<string | null>(null);
const customMarker = ref("");
let networkDebugPollTimer: number | null = null;
const canOpenNetworkDebugFile = computed(() => Boolean(window.desktopBridge));

const markerPresets = [
  { label: "发送消息前", marker: "before_send" },
  { label: "发送消息后", marker: "after_send" },
  { label: "发送简历前", marker: "before_resume" },
  { label: "发送简历后", marker: "after_resume" },
  { label: "投递前", marker: "before_apply" },
  { label: "投递后", marker: "after_apply" }
] as const;

const statusType = computed(() => {
  if (platformStore.bossSession?.status === "ready") return "success";
  if (platformStore.bossSession?.status === "invalid") return "danger";
  return "warning";
});

const statusLabel = computed(() => {
  if (platformStore.bossSession?.status === "ready") return "已登录";
  if (platformStore.bossSession?.status === "invalid") return "登录失效";
  return "等待登录";
});

const stopNetworkDebugPolling = () => {
  if (networkDebugPollTimer === null) return;
  window.clearInterval(networkDebugPollTimer);
  networkDebugPollTimer = null;
};

// 只有后端报告 Trace active 时才保持轮询，停止后立即释放 timer。
const startNetworkDebugPolling = () => {
  if (networkDebugPollTimer !== null || networkDebug.value?.active !== true) return;
  networkDebugPollTimer = window.setInterval(() => {
    void loadNetworkDebugStatus();
  }, 2000);
};

const syncNetworkDebugPolling = () => {
  if (networkDebug.value?.active === true) {
    startNetworkDebugPolling();
  } else {
    stopNetworkDebugPolling();
  }
};

const loadNetworkDebugStatus = async () => {
  try {
    networkDebug.value = await api.getFineJobBossNetworkDebugStatus();
    syncNetworkDebugPolling();
  } catch (errorValue) {
    networkDebugError.value = (errorValue as Error).message || "读取网络监听状态失败。";
  }
};

onMounted(() => {
  void Promise.all([
    platformStore.load(),
    captureStore.loadStatus(),
    loadNetworkDebugStatus()
  ]);
});

onBeforeUnmount(stopNetworkDebugPolling);

const startNetworkDebug = async () => {
  networkDebugLoading.value = true;
  networkDebugError.value = null;
  try {
    networkDebug.value = await api.startFineJobBossNetworkDebug();
    syncNetworkDebugPolling();
    ElMessage.success("网络监听已开始，请在专用 Chrome 中操作 BOSS 页面");
  } catch (errorValue) {
    networkDebugError.value = (errorValue as Error).message || "启动网络监听失败。";
    ElMessage.error(networkDebugError.value);
  } finally {
    networkDebugLoading.value = false;
  }
};

const stopNetworkDebug = async () => {
  networkDebugLoading.value = true;
  networkDebugError.value = null;
  try {
    networkDebug.value = await api.stopFineJobBossNetworkDebug();
    syncNetworkDebugPolling();
    if (networkDebug.value.output_path) {
      ElMessage.success("网络监听已停止，JSON 文件已生成");
    } else {
      ElMessage.warning("网络监听已停止，但没有生成输出文件");
    }
  } catch (errorValue) {
    networkDebugError.value = (errorValue as Error).message || "停止网络监听失败。";
    ElMessage.error(networkDebugError.value);
  } finally {
    networkDebugLoading.value = false;
  }
};

const markNetworkDebug = async (marker: string) => {
  if (networkDebug.value?.active !== true) return;
  networkDebugLoading.value = true;
  networkDebugError.value = null;
  try {
    // Marker 只调用现有 mark API，不触发任何平台页面动作。
    networkDebug.value = await api.markFineJobBossNetworkDebug({ marker });
    syncNetworkDebugPolling();
    ElMessage.success("Trace 标记已添加");
  } catch (errorValue) {
    networkDebugError.value = (errorValue as Error).message || "添加 Trace 标记失败。";
    ElMessage.error(networkDebugError.value);
  } finally {
    networkDebugLoading.value = false;
  }
};

const addCustomMarker = async () => {
  const marker = customMarker.value.trim();
  if (!marker) {
    ElMessage.warning("请输入自定义 Marker");
    return;
  }
  await markNetworkDebug(marker);
  if (!networkDebugError.value) customMarker.value = "";
};

const openNetworkDebugFile = async () => {
  const outputPath = networkDebug.value?.output_path;
  if (!outputPath || !window.desktopBridge) return;
  const errorMessage = await window.desktopBridge.openPath(outputPath);
  if (errorMessage) ElMessage.error(errorMessage);
};

const openLoginWindow = async () => {
  try {
    await platformStore.openBossLoginWindow();
    await captureStore.loadStatus();
    ElMessage.success("FineJob 专用 Chrome 已打开");
  } catch {
    ElMessage.error(platformStore.error ?? "打开 BOSS 登录窗口失败");
  }
};

const checkLoginStatus = async () => {
  try {
    const response = await platformStore.checkBossLoginStatus();
    if (response.session.status === "ready") {
      ElMessage.success("BOSS 登录状态可用");
    } else {
      ElMessage.warning(response.detail || "未检测到有效登录状态");
    }
  } catch {
    ElMessage.error(platformStore.error ?? "检测 BOSS 登录状态失败");
  }
};
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Platform Session</p>
        <h1>平台登录</h1>
        <p class="secondary-text">
          BOSS 登录保存在 FineJob 专用 Chrome profile 中，岗位采集和后续投递会复用同一会话。
        </p>
      </div>
      <el-tag :type="statusType" size="large">{{ statusLabel }}</el-tag>
    </div>

    <el-alert
      v-if="platformStore.error"
      type="error"
      title="平台登录操作失败"
      :description="platformStore.error"
      show-icon
    />

    <section v-loading="platformStore.loading" class="page-panel platform-session-card">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">BOSS</p>
          <h2>BOSS直聘登录状态</h2>
        </div>
        <span class="secondary-text">
          最后检测：{{ platformStore.bossSession?.last_checked_at
            ? formatDateTime(platformStore.bossSession.last_checked_at)
            : "尚未检测" }}
        </span>
      </div>

      <dl class="session-summary">
        <div>
          <dt>当前状态</dt>
          <dd><el-tag :type="statusType">{{ statusLabel }}</el-tag></dd>
        </div>
        <div>
          <dt>登录浏览器</dt>
          <dd>FineJob 专用 Chrome</dd>
        </div>
        <div>
          <dt>浏览器进程</dt>
          <dd>{{ captureStore.status?.running ? "已启动" : "未启动" }}</dd>
        </div>
        <div>
          <dt>状态说明</dt>
          <dd>{{ platformStore.bossSession?.status_detail || "请打开浏览器完成登录。" }}</dd>
        </div>
      </dl>

      <div class="platform-actions">
        <el-button type="primary" :loading="platformStore.openingLogin" @click="openLoginWindow">
          打开专用 Chrome
        </el-button>
        <el-button
          type="success"
          :disabled="!captureStore.status?.running"
          :loading="platformStore.checking"
          @click="checkLoginStatus"
        >
          检测登录状态
        </el-button>
      </div>
    </section>

    <section class="page-panel network-debug-card">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Developer Tool</p>
          <h2>CDP / Protocol Trace</h2>
        </div>
        <el-tag :type="networkDebug?.active ? 'success' : 'info'">
          {{ networkDebug?.active ? "正在记录" : "未记录" }}
        </el-tag>
      </div>

      <p class="secondary-text">
        控制专用 Chrome 中的 BOSS Protocol Trace，并保留后端生成的原始证据状态。
      </p>

      <el-alert
        v-if="networkDebugError"
        type="error"
        title="Protocol Trace 操作失败"
        :description="networkDebugError"
        show-icon
      />

      <el-alert
        v-if="networkDebug?.error_message"
        type="warning"
        title="Trace 状态提示"
        :description="networkDebug.error_message"
        show-icon
      />

      <dl class="network-debug-summary">
        <div>
          <dt>Trace ID</dt>
          <dd>{{ networkDebug?.trace_id || "未生成" }}</dd>
        </div>
        <div>
          <dt>Evidence</dt>
          <dd>
            <el-tag :type="networkDebug ? (networkDebug.evidence_complete ? 'success' : 'danger') : 'info'">
              {{ networkDebug ? (networkDebug.evidence_complete ? "证据完整" : "证据不完整") : "待读取" }}
            </el-tag>
          </dd>
        </div>
        <div>
          <dt>Events</dt>
          <dd>{{ networkDebug?.event_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>HTTP</dt>
          <dd>{{ networkDebug?.request_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>WS Frames</dt>
          <dd>{{ networkDebug?.frame_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>Markers</dt>
          <dd>{{ networkDebug?.marker_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>Dropped</dt>
          <dd>{{ networkDebug?.dropped_event_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>目标页面</dt>
          <dd>{{ networkDebug?.target_count ?? 0 }}</dd>
        </div>
        <div class="network-debug-gaps">
          <dt>Gap reasons</dt>
          <dd>
            <span v-if="networkDebug?.gap_reasons?.length">{{ networkDebug.gap_reasons.join("、") }}</span>
            <span v-else>无</span>
          </dd>
        </div>
        <div class="network-debug-output">
          <dt>Output</dt>
          <dd>{{ networkDebug?.output_path || "停止 Trace 后生成" }}</dd>
        </div>
      </dl>

      <div class="platform-actions">
        <el-button
          type="primary"
          :loading="networkDebugLoading"
          :disabled="networkDebug?.active === true || networkDebugLoading"
          @click="startNetworkDebug"
        >
          开始 Trace
        </el-button>
        <el-button
          type="warning"
          :loading="networkDebugLoading"
          :disabled="networkDebug?.active !== true || networkDebugLoading"
          @click="stopNetworkDebug"
        >
          停止 Trace
        </el-button>
        <el-button
          v-if="networkDebug?.output_path"
          :disabled="!canOpenNetworkDebugFile"
          @click="openNetworkDebugFile"
        >
          打开 Trace
        </el-button>
      </div>

      <div class="marker-group">
        <div class="marker-group-title">消息</div>
        <div class="marker-actions">
          <el-button
            v-for="preset in markerPresets.slice(0, 2)"
            :key="preset.marker"
            :disabled="networkDebug?.active !== true || networkDebugLoading"
            @click="markNetworkDebug(preset.marker)"
          >
            {{ preset.label }}
          </el-button>
        </div>
      </div>

      <div class="marker-group">
        <div class="marker-group-title">简历</div>
        <div class="marker-actions">
          <el-button
            v-for="preset in markerPresets.slice(2, 4)"
            :key="preset.marker"
            :disabled="networkDebug?.active !== true || networkDebugLoading"
            @click="markNetworkDebug(preset.marker)"
          >
            {{ preset.label }}
          </el-button>
        </div>
      </div>

      <div class="marker-group">
        <div class="marker-group-title">投递</div>
        <div class="marker-actions">
          <el-button
            v-for="preset in markerPresets.slice(4, 6)"
            :key="preset.marker"
            :disabled="networkDebug?.active !== true || networkDebugLoading"
            @click="markNetworkDebug(preset.marker)"
          >
            {{ preset.label }}
          </el-button>
        </div>
      </div>

      <div class="marker-group custom-marker-group">
        <div class="marker-group-title">自定义 Marker</div>
        <div class="custom-marker-actions">
          <el-input v-model="customMarker" placeholder="输入 Marker 名称" :disabled="networkDebug?.active !== true" />
          <el-button
            type="primary"
            :disabled="networkDebug?.active !== true || networkDebugLoading"
            @click="addCustomMarker"
          >
            添加标记
          </el-button>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.platform-session-card,
.session-summary,
.network-debug-card {
  display: grid;
  gap: 18px;
}

.session-summary {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 0;
}

.session-summary > div {
  display: grid;
  gap: 6px;
}

.session-summary dt {
  color: var(--el-text-color-secondary);
}

.session-summary dd {
  margin: 0;
}

.network-debug-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin: 0;
}

.network-debug-summary > div {
  display: grid;
  gap: 6px;
}

.network-debug-summary dt {
  color: var(--el-text-color-secondary);
}

.network-debug-summary dd {
  margin: 0;
  word-break: break-all;
}

.network-debug-output {
  grid-column: 1 / -1;
}

.network-debug-gaps {
  grid-column: 1 / -1;
}

.marker-group {
  display: grid;
  gap: 10px;
}

.marker-group-title {
  color: var(--el-text-color-secondary);
  font-weight: 600;
}

.marker-actions,
.custom-marker-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.custom-marker-actions .el-input {
  max-width: 360px;
}

@media (max-width: 720px) {
  .session-summary,
  .network-debug-summary {
    grid-template-columns: 1fr;
  }

  .network-debug-output,
  .network-debug-gaps {
    grid-column: auto;
  }
}
</style>
