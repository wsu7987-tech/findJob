import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, NetworkError, api } from "@/services/api";
import type { FineJobResume, FineJobResumeFact, PdfDraftParserName } from "@/types";

export const useFineJobResumesStore = defineStore("fineJobResumes", () => {
  const resumes = ref<FineJobResume[]>([]);
  const selectedResume = ref<FineJobResume | null>(null);
  const loading = ref(false);
  const parsing = ref(false);
  const deleting = ref(false);
  const facts = ref<Record<string, FineJobResumeFact[]>>({});
  const factsLoading = ref(false);
  const factsSaving = ref(false);
  const error = ref<string | null>(null);

  const hasResume = computed(() => resumes.value.length > 0);

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobResumes();
      resumes.value = response.resumes;
      if (!selectedResume.value && response.resumes.length > 0) {
        selectedResume.value = response.resumes[0];
      }
    } catch (errorValue) {
      error.value = mapError(errorValue);
    } finally {
      loading.value = false;
    }
  };

  const selectResume = async (resumeId: string) => {
    loading.value = true;
    error.value = null;
    try {
      const response = await api.getFineJobResume(resumeId);
      selectedResume.value = response.resume;
      const index = resumes.value.findIndex((item) => item.id === resumeId);
      if (index >= 0) {
        resumes.value[index] = response.resume;
      }
    } catch (errorValue) {
      error.value = mapError(errorValue);
    } finally {
      loading.value = false;
    }
  };

  const createFromFile = async (
    filePath: string,
    parserName: PdfDraftParserName = "auto"
  ) => {
    parsing.value = true;
    error.value = null;
    try {
      const response = await api.createFineJobResumeFromFile({
        file_path: filePath,
        parser_name: parserName
      });
      selectedResume.value = response.resume;
      await load();
      selectedResume.value = response.resume;
      return response.resume;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      parsing.value = false;
    }
  };

  const deleteResume = async (resumeId: string) => {
    deleting.value = true;
    error.value = null;
    try {
      await api.deleteFineJobResume(resumeId);
      resumes.value = resumes.value.filter((resume) => resume.id !== resumeId);
      delete facts.value[resumeId];
      if (selectedResume.value?.id === resumeId) {
        selectedResume.value = resumes.value[0] ?? null;
      }
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      deleting.value = false;
    }
  };

  const loadFacts = async (resumeId: string) => {
    factsLoading.value = true;
    error.value = null;
    try {
      const response = await api.listFineJobResumeFacts(resumeId);
      facts.value = { ...facts.value, [resumeId]: response.facts };
      return response.facts;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      return [];
    } finally {
      factsLoading.value = false;
    }
  };

  const extractFacts = async (resumeId: string) => {
    factsLoading.value = true;
    error.value = null;
    try {
      const response = await api.extractFineJobResumeFacts(resumeId);
      facts.value = { ...facts.value, [resumeId]: response.facts };
      return response.facts;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      factsLoading.value = false;
    }
  };

  const saveFacts = async (resumeId: string, nextFacts: FineJobResumeFact[]) => {
    factsSaving.value = true;
    error.value = null;
    try {
      const response = await api.saveFineJobResumeFacts(resumeId, {
        facts: nextFacts.map((fact) => ({ ...fact, user_confirmed: true }))
      });
      facts.value = { ...facts.value, [resumeId]: response.facts };
      return response.facts;
    } catch (errorValue) {
      error.value = mapError(errorValue);
      throw errorValue;
    } finally {
      factsSaving.value = false;
    }
  };

  return {
    resumes,
    selectedResume,
    loading,
    parsing,
    deleting,
    facts,
    factsLoading,
    factsSaving,
    error,
    hasResume,
    load,
    selectResume,
    createFromFile,
    deleteResume,
    loadFacts,
    extractFacts,
    saveFacts
  };
});

const mapError = (errorValue: unknown) => {
  if (errorValue instanceof ApiError || errorValue instanceof NetworkError) {
    return errorValue.message;
  }
  return (errorValue as Error).message || "简历操作失败。";
};
