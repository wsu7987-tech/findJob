<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { formatDateTime } from "@/services/format";
import {
  emptyBossSession,
  useFineJobPlatformSessionsStore
} from "@/stores/fineJobPlatformSessions";
import type { FineJobPlatformSession, FineJobPlatformSessionStatus } from "@/types";

const platformStore = useFineJobPlatformSessionsStore();
const form = ref<FineJobPlatformSession>(emptyBossSession());

const statusType = computed(() => {
  if (form.value.status === "ready") {
    return "success";
  }
  if (form.value.status === "invalid") {
    return "danger";
  }
  return "warning";
});

const statusLabel = computed(() => {
  if (form.value.status === "ready") {
    return "已登录";
  }
  if (form.value.status === "invalid") {
    return "登录失效";
  }
  return "未登录";
});

onMounted(async () => {
  await platformStore.load();
  form.value = cloneSession(platformStore.bossSession ?? emptyBossSession());
});

watch(
  () => platformStore.bossSession,
  (session) => {
    if (session) {
      form.value = cloneSession(session);
    }
  },
  { deep: true }
);

const saveSession = async (status?: FineJobPlatformSessionStatus) => {
  try {
    const payload = normalizeSession({
      ...form.value,
      status: status ?? form.value.status,
      status_detail:
        status === "ready"
          ? "用户手动标记当前 BOSS 登录态可用"
          : status === "invalid"
            ? "用户标记 BOSS 登录态失效"
            : form.value.status_detail
    });
    const saved = await platformStore.saveBossSession(payload);
    if (saved) {
      form.value = cloneSession(saved);
    }
    ElMessage.success("平台登录状态已保存");
  } catch {
    ElMessage.error(platformStore.error ?? "平台登录状态保存失败");
  }
};

const openLoginWindow = async () => {
  try {
    const response = await platformStore.openBossLoginWindow();
    if (response.session) {
      form.value = cloneSession(response.session);
    }
    ElMessage.success("BOSS 登录窗口已打开");
  } catch {
    ElMessage.error(platformStore.error ?? "打开 BOSS 登录窗口失败");
  }
};

const checkLoginStatus = async () => {
  try {
    const response = await platformStore.checkBossLoginStatus();
    form.value = cloneSession(response.session);
    if (response.session.status === "ready") {
      ElMessage.success("BOSS 登录状态可用");
    } else {
      ElMessage.warning(response.detail || "未检测到有效登录状态");
    }
  } catch {
    ElMessage.error(platformStore.error ?? "检测 BOSS 登录状态失败");
  }
};

const normalizeSession = (session: FineJobPlatformSession): FineJobPlatformSession => ({
  ...session,
  display_name: session.display_name.trim() || "BOSS直聘",
  login_url: session.login_url.trim() || "https://www.zhipin.com/",
  browser_profile: session.browser_profile.trim() || "fine-job-boss",
  browser_channel: session.browser_channel === "msedge" ? "msedge" : "chrome",
  status_detail: session.status_detail.trim()
});

const cloneSession = (session: FineJobPlatformSession): FineJobPlatformSession => ({ ...session });
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Platform Session</p>
        <h1>平台登录</h1>
        <p class="secondary-text">
          打开 BOSS 登录窗口并完成登录。登录成功后回到 FineJob 检测登录状态，通过后即可开始投递。
        </p>
      </div>
      <div class="card-actions">
        <el-tag :type="statusType">{{ statusLabel }}</el-tag>
        <el-button :loading="platformStore.openingLogin" @click="openLoginWindow">
          打开登录窗口
        </el-button>
        <el-button type="primary" :loading="platformStore.checking" @click="checkLoginStatus">
          检测登录状态
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="platformStore.error"
      type="error"
      title="平台登录状态操作失败"
      :description="platformStore.error"
      show-icon
    />

    <el-alert
      type="info"
      title="登录操作顺序"
      description="1. 点击打开登录窗口；2. 在新窗口完成 BOSS 登录；3. 回到 FineJob 点击检测登录状态；4. 检测成功后开始投递。"
      show-icon
      :closable="false"
    />

    <section v-loading="platformStore.loading" class="page-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">BOSS</p>
          <h2>BOSS直聘登录状态</h2>
        </div>
        <span class="secondary-text">
          最后确认：{{ form.last_checked_at ? formatDateTime(form.last_checked_at) : "暂无" }}
        </span>
      </div>

      <el-form label-position="top" class="intent-form">
        <div class="form-grid">
          <el-form-item label="平台">
            <el-input v-model="form.display_name" />
          </el-form-item>

          <el-form-item label="登录浏览器">
            <el-select v-model="form.browser_channel">
              <el-option label="Chrome" value="chrome" />
              <el-option label="Edge" value="msedge" />
            </el-select>
          </el-form-item>

          <el-form-item label="兼容标识">
            <el-input v-model="form.browser_profile" disabled />
          </el-form-item>
        </div>

        <el-form-item label="当前状态">
          <el-radio-group v-model="form.status">
            <el-radio-button label="needs_login">未登录</el-radio-button>
            <el-radio-button label="ready">已登录</el-radio-button>
            <el-radio-button label="invalid">登录失效</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="状态说明">
          <el-input
            v-model="form.status_detail"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 5 }"
            placeholder="登录状态保存成功、登录失效、需要重新登录等说明。"
          />
        </el-form-item>
      </el-form>

      <div class="platform-actions">
        <el-button type="primary" :loading="platformStore.openingLogin" @click="openLoginWindow">
          打开 BOSS 登录窗口
        </el-button>
        <el-button type="success" :loading="platformStore.checking" @click="checkLoginStatus">
          检测登录状态
        </el-button>
        <el-button :loading="platformStore.saving" @click="saveSession()">
          保存配置
        </el-button>
        <el-button :loading="platformStore.saving" @click="saveSession('needs_login')">
          标记未登录
        </el-button>
        <el-button type="danger" plain :loading="platformStore.saving" @click="saveSession('invalid')">
          标记失效
        </el-button>
      </div>
    </section>
  </section>
</template>
