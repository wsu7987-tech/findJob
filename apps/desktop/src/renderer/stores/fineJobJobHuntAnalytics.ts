import { defineStore } from "pinia";
import { ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import {
  getAnalyticsPresetRange,
  ANALYTICS_TIME_ZONE
} from "@/services/fineJobJobHuntAnalytics";
import type {
  FineJobAnalyticsPreset,
  FineJobAnalyticsGranularity,
  FineJobAnalyticsMetric,
  FineJobContactOrigin,
  FineJobJobHuntAnalyticsJobsResponse,
  FineJobRejectionReasonSource,
  FineJobJobHuntAnalyticsResponse
} from "@/types";

const defaultRange = () => getAnalyticsPresetRange("last7");

export const useFineJobJobHuntAnalyticsStore = defineStore("fine-job-job-hunt-analytics", () => {
  const initialRange = defaultRange();
  const preset = ref<FineJobAnalyticsPreset>("last7");
  const fromDate = ref(initialRange.from);
  const toDate = ref(initialRange.to);
  const customRange = ref<[string, string] | null>(null);
  const contactOrigin = ref<FineJobContactOrigin | "">("");
  const granularity = ref<FineJobAnalyticsGranularity>("auto");
  const data = ref<FineJobJobHuntAnalyticsResponse | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const detailData = ref<FineJobJobHuntAnalyticsJobsResponse | null>(null);
  const detailLoading = ref(false);
  const detailError = ref<string | null>(null);

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      data.value = await api.getFineJobJobHuntAnalytics({
        from: fromDate.value,
        to: toDate.value,
        timezone: ANALYTICS_TIME_ZONE,
        granularity: granularity.value,
        contact_origin: contactOrigin.value || null
      });
      return data.value;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      loading.value = false;
    }
  };

  const selectPreset = async (value: FineJobAnalyticsPreset) => {
    preset.value = value;
    if (value === "custom") return;
    const range = getAnalyticsPresetRange(value);
    fromDate.value = range.from;
    toDate.value = range.to;
    customRange.value = null;
    return load();
  };

  const applyCustomRange = async (value: string[] | null) => {
    if (!value || value.length !== 2 || !value[0] || !value[1]) return;
    preset.value = "custom";
    customRange.value = [value[0], value[1]];
    fromDate.value = value[0];
    toDate.value = value[1];
    return load();
  };

  const setContactOrigin = async (value: FineJobContactOrigin | "") => {
    contactOrigin.value = value;
    return load();
  };

  const setGranularity = async (value: FineJobAnalyticsGranularity) => {
    granularity.value = value;
    return load();
  };

  const loadDetails = async (params: {
    metric: FineJobAnalyticsMetric;
    rejection_reason_source?: FineJobRejectionReasonSource;
    rejection_reason_category?: string;
  }) => {
    detailLoading.value = true;
    detailError.value = null;
    detailData.value = null;
    try {
      detailData.value = await api.getFineJobJobHuntAnalyticsJobs({
        metric: params.metric,
        from: fromDate.value,
        to: toDate.value,
        timezone: ANALYTICS_TIME_ZONE,
        contact_origin: contactOrigin.value || null,
        rejection_reason_source: params.rejection_reason_source,
        rejection_reason_category: params.rejection_reason_category
      });
      return detailData.value;
    } catch (value) {
      detailError.value = mapDetailError(value);
      throw value;
    } finally {
      detailLoading.value = false;
    }
  };

  const clearDetails = () => {
    detailData.value = null;
    detailError.value = null;
  };

  return {
    preset,
    fromDate,
    toDate,
    customRange,
    contactOrigin,
    granularity,
    data,
    loading,
    error,
    detailData,
    detailLoading,
    detailError,
    load,
    refresh: load,
    selectPreset,
    applyCustomRange,
    setContactOrigin,
    setGranularity,
    loadDetails,
    clearDetails
  };
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error)?.message || "求职分析数据加载失败。";
};

const mapDetailError = (value: unknown) => {
  if (value instanceof ApiError || value instanceof NetworkError) return value.message;
  return (value as Error)?.message || "岗位明细加载失败。";
};
