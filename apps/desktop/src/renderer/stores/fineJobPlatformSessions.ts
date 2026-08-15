import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type { FineJobPlatformSession } from "@/types";

export const emptyBossSession = (): FineJobPlatformSession => ({
  platform: "boss",
  display_name: "BOSS 直聘",
  login_url: "https://www.zhipin.com/",
  browser_profile: "fine-job-boss",
  browser_channel: "chrome",
  status: "needs_login",
  status_detail: ""
});

export const useFineJobPlatformSessionsStore = defineStore("fineJobPlatformSessions", () => {
  const sessions = ref<FineJobPlatformSession[]>([]);
  const bossSession = ref<FineJobPlatformSession | null>(null);
  const loading = ref(false);
  const saving = ref(false);
  const openingLogin = ref(false);
  const checking = ref(false);
  const error = ref<string | null>(null);

  const bossReady = computed(() => bossSession.value?.status === "ready");

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobPlatformSessions();
      sessions.value = response.sessions;
      bossSession.value = response.sessions.find((session) => session.platform === "boss") ?? null;
      return response.sessions;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return [];
    } finally {
      loading.value = false;
    }
  };

  const saveBossSession = async (payload: FineJobPlatformSession) => {
    saving.value = true;
    error.value = null;
    try {
      const response = await api.saveFineJobPlatformSession("boss", payload);
      bossSession.value = response.session;
      await load();
      return response.session;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  const openBossLoginWindow = async () => {
    openingLogin.value = true;
    error.value = null;
    try {
      const response = await api.openFineJobBossLoginWindow();
      bossSession.value = response.session;
      await load();
      return response;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      openingLogin.value = false;
    }
  };

  const checkBossLoginStatus = async () => {
    checking.value = true;
    error.value = null;
    try {
      const response = await api.checkFineJobBossLoginStatus();
      bossSession.value = response.session;
      await load();
      return response;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      checking.value = false;
    }
  };

  return {
    sessions,
    bossSession,
    loading,
    saving,
    openingLogin,
    checking,
    error,
    bossReady,
    load,
    saveBossSession,
    openBossLoginWindow,
    checkBossLoginStatus
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "平台登录状态操作失败。";
};
