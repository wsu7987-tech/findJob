import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { NetworkError, api } from "@/services/api";
import type { WebSessionProfile, WebSessionProfileCreateRequest } from "@/types";

export const useWebSessionProfilesStore = defineStore("webSessionProfiles", () => {
  const profiles = ref<WebSessionProfile[]>([]);
  const loading = ref(false);
  const saving = ref(false);
  const loggingInProfileId = ref<string | null>(null);
  const deletingProfileId = ref<string | null>(null);
  const error = ref<string | null>(null);
  const connectionUnavailable = ref(false);

  const profileOptions = computed(() =>
    profiles.value.map((profile) => ({
      label: `${profile.name} · ${profile.mode === "browser_profile" ? "浏览器" : "应用内"}`,
      value: profile.id
    }))
  );

  const loadProfiles = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.listWebSessionProfiles();
      profiles.value = response.profiles;
      connectionUnavailable.value = false;
      return response.profiles;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const createProfile = async (payload: WebSessionProfileCreateRequest) => {
    saving.value = true;
    error.value = null;
    try {
      const response = await api.createWebSessionProfile(payload);
      profiles.value = [response.profile, ...profiles.value.filter((item) => item.id !== response.profile.id)];
      connectionUnavailable.value = false;
      return response.profile;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  const startManagedLogin = async (profileId: string, loginUrl?: string | null) => {
    loggingInProfileId.value = profileId;
    error.value = null;
    try {
      const response = await api.startWebSessionProfileLogin(profileId, {
        login_url: loginUrl ?? null
      });
      profiles.value = profiles.value.map((item) => (item.id === profileId ? response.profile : item));
      connectionUnavailable.value = false;
      return response.profile;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      loggingInProfileId.value = null;
    }
  };

  const deleteProfile = async (profileId: string) => {
    deletingProfileId.value = profileId;
    error.value = null;
    try {
      await api.deleteWebSessionProfile(profileId);
      profiles.value = profiles.value.filter((item) => item.id !== profileId);
      connectionUnavailable.value = false;
      return true;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      deletingProfileId.value = null;
    }
  };

  return {
    profiles,
    loading,
    saving,
    loggingInProfileId,
    deletingProfileId,
    error,
    connectionUnavailable,
    profileOptions,
    loadProfiles,
    createProfile,
    startManagedLogin,
    deleteProfile
  };
});
