<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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
const canOpenNetworkDebugFile = computed(() => Boolean(window.desktopBridge));

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

onMounted(() => {
  void Promise.all([
    platformStore.load(),
    captureStore.loadStatus(),
    loadNetworkDebugStatus()
  ]);
});

const loadNetworkDebugStatus = async () => {
  try {
    networkDebug.value = await api.getFineJobBossNetworkDebugStatus();
  } catch (errorValue) {
    networkDebugError.value = (errorValue as Error).message || "读取网络监听状态失败。";
  }
};

const startNetworkDebug = async () => {
  networkDebugLoading.value = true;
  networkDebugError.value = null;
  try {
    networkDebug.value = await api.startFineJobBossNetworkDebug();
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

    <!-- <section class="page-panel network-debug-card">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Developer Tool</p>
          <h2>CDP 网络监听</h2>
        </div>
        <el-tag :type="networkDebug?.active ? 'success' : 'info'">
          {{ networkDebug?.active ? "监听中" : "未监听" }}
        </el-tag>
      </div>

      <p class="secondary-text">
        记录专用 Chrome 中已完成的网络请求，并将接口地址、请求方法、状态码和响应正文写入 JSON 文件。
      </p>

      <el-alert
        v-if="networkDebugError"
        type="error"
        title="网络监听操作失败"
        :description="networkDebugError"
        show-icon
      />

      <dl class="network-debug-summary">
        <div>
          <dt>已记录请求</dt>
          <dd>{{ networkDebug?.request_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>监听页面</dt>
          <dd>{{ networkDebug?.target_count ?? 0 }}</dd>
        </div>
        <div class="network-debug-output">
          <dt>JSON 文件</dt>
          <dd>{{ networkDebug?.output_path || "停止监听后生成" }}</dd>
        </div>
      </dl>

      <div class="platform-actions">
        <el-button
          type="primary"
          :loading="networkDebugLoading"
          :disabled="networkDebug?.active === true"
          @click="startNetworkDebug"
        >
          开始监听
        </el-button>
        <el-button
          type="warning"
          :loading="networkDebugLoading"
          :disabled="networkDebug?.active !== true"
          @click="stopNetworkDebug"
        >
          停止监听并生成 JSON
        </el-button>
        <el-button
          v-if="networkDebug?.output_path"
          :disabled="!canOpenNetworkDebugFile"
          @click="openNetworkDebugFile"
        >
          打开 JSON 文件
        </el-button>
      </div>
    </section> -->
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
</style>
