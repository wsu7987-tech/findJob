<script setup lang="ts">
import { computed, onMounted } from "vue";
import { ElMessage } from "element-plus";

import { formatDateTime } from "@/services/format";
import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobPlatformSessionsStore } from "@/stores/fineJobPlatformSessions";

const platformStore = useFineJobPlatformSessionsStore();
const captureStore = useFineJobBossCaptureStore();

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
  void Promise.all([platformStore.load(), captureStore.loadStatus()]);
});

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
  </section>
</template>

<style scoped>
.platform-session-card,
.session-summary {
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
</style>
