import { defineStore } from "pinia";
import { computed, ref } from "vue";

import type {
  CandidateProfile,
  ProfileAnalysisItem,
  ProfileAnalysisRun,
  ProfileAnswerVariant,
  ProfileCampaign,
  ProfileContext,
  ProfileContextHead,
  ProfileContextView,
  ProfileFact,
  ProfileIssue,
  ProfileQATemplate,
  ProfileQuestion,
  ProfileResumeVersion,
  ProfileSource,
  ResumeAnalysisIssue,
  ResumeAnalysisOperationId,
  ResumeAnalysisRun,
  ResumeFamily,
  ResumeSearchKeyword,
  ResumeStrategy
} from "@/profile-types";
import { ApiError, NetworkError } from "@/services/api";
import { mapErrorCategoryLabel } from "@/services/contract";
import { profileApi } from "@/services/profile-api";

export const useFineJobProfilesStore = defineStore("fineJobProfiles", () => {
  const profiles = ref<CandidateProfile[]>([]);
  const selectedProfile = ref<CandidateProfile | null>(null);
  const sources = ref<ProfileSource[]>([]);
  const facts = ref<ProfileFact[]>([]);
  const questions = ref<ProfileQuestion[]>([]);
  const answers = ref<Record<string, ProfileAnswerVariant[]>>({});
  const resumeVersions = ref<ProfileResumeVersion[]>([]);
  const campaigns = ref<ProfileCampaign[]>([]);
  const resumeFamilies = ref<ResumeFamily[]>([]);
  const selectedResumeFamilyId = ref<string | null>(null);
  const selectedResumeVersionId = ref<string | null>(null);
  const resumeAnalysisRun = ref<ResumeAnalysisRun | null>(null);
  const resumeIssues = ref<ResumeAnalysisIssue[]>([]);
  const issues = ref<ProfileIssue[]>([]);
  const qaTemplates = ref<ProfileQATemplate[]>([]);
  const resumeStrategies = ref<ResumeStrategy[]>([]);
  const resumeSearchKeywords = ref<ResumeSearchKeyword[]>([]);
  const analysisRun = ref<ProfileAnalysisRun | null>(null);
  const analysisItems = ref<ProfileAnalysisItem[]>([]);
  const context = ref<ProfileContext | null>(null);
  const contextHead = ref<ProfileContextHead | null>(null);
  const loading = ref(false);
  const analyzing = ref(false);
  const saving = ref(false);
  const cleaningSourceId = ref<string | null>(null);
  const error = ref<string | null>(null);

  const profileId = computed(() => selectedProfile.value?.id ?? null);
  const selectedResumeFamily = computed(() =>
    resumeFamilies.value.find((item) => item.id === selectedResumeFamilyId.value) ?? null
  );
  const selectedFamilySources = computed(() =>
    sources.value.filter((item) => item.resume_family_id === selectedResumeFamilyId.value)
  );

  const load = async () => {
    loading.value = true;
    error.value = null;
    try {
      const response = await profileApi.listProfiles();
      profiles.value = response.profiles;
      selectedProfile.value = response.profiles[0] ?? null;
      if (selectedProfile.value) await refreshProfileData();
    } catch (value) {
      error.value = mapError(value);
    } finally {
      loading.value = false;
    }
  };

  const refreshProfileData = async () => {
    if (!profileId.value) return;
    const id = profileId.value;
    const [sourceResult, factResult, questionResult, resumeResult, campaignResult, familyResult, issueResult, templateResult] = await Promise.all([
      profileApi.listSources(id),
      profileApi.listFacts(id),
      profileApi.listQuestions(id),
      profileApi.listResumeVersions(id),
      profileApi.listCampaigns(id),
      profileApi.listResumeFamilies(id),
      profileApi.listIssues(id),
      profileApi.listQATemplates(id)
    ]);
    sources.value = sourceResult.sources;
    facts.value = factResult.facts;
    questions.value = questionResult.questions;
    resumeVersions.value = resumeResult.resume_versions;
    campaigns.value = campaignResult.campaigns;
    resumeFamilies.value = familyResult.resume_families;
    issues.value = issueResult.issues;
    qaTemplates.value = templateResult.templates;
    if (!resumeFamilies.value.some((item) => item.id === selectedResumeFamilyId.value)) {
      selectedResumeFamilyId.value = null;
    }
    resumeIssues.value = [];
    if (!resumeVersions.value.some((item) => item.id === selectedResumeVersionId.value)) {
      selectedResumeVersionId.value = resumeVersions.value.find((item) => item.current_role === "base")?.id
        ?? resumeVersions.value[0]?.id
        ?? null;
    }
    await reloadProfile();
  };

  const reloadProfile = async () => {
    const response = await profileApi.listProfiles();
    profiles.value = response.profiles;
    selectedProfile.value = response.profiles.find((item) => item.id === profileId.value) ?? response.profiles[0] ?? null;
  };

  const addTextSource = async (title: string, content: string, sourceType: "markdown" | "text" | "project") => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.createTextSource(profileId.value!, { title, content, source_type: sourceType, enabled: true });
      await refreshProfileData();
    });
  };

  const addFileSource = async (filePath: string) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.createFileSource(profileId.value!, filePath);
      await refreshProfileData();
    });
  };

  const importPdfResume = async (filePath: string, name = "") => {
    if (!profileId.value) return;
    await withSaving(async () => {
      const result = await profileApi.importPdfResume(profileId.value!, filePath, name);
      selectedResumeFamilyId.value = result.resume_family.id;
      await refreshProfileData();
    });
  };

  const importDerivedPdfResume = async (
    resumeFamilyId: string,
    filePath: string,
    name = "",
    derivedReason = ""
  ) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.importDerivedPdfResume(
        profileId.value!, resumeFamilyId, filePath, name, derivedReason
      );
      await refreshProfileData();
    });
  };

  const selectResumeFamily = async (resumeFamilyId: string) => {
    selectedResumeFamilyId.value = resumeFamilyId;
    await refreshResumeFamilyData();
  };

  const refreshResumeFamilyData = async () => {
    if (!selectedResumeFamilyId.value) {
      resumeAnalysisRun.value = null;
      resumeStrategies.value = [];
      resumeSearchKeywords.value = [];
      return;
    }
    const familyId = selectedResumeFamilyId.value;
    const [strategyResult, keywordResult] = await Promise.all([
      profileApi.listResumeStrategies(familyId),
      profileApi.listResumeSearchKeywords(familyId)
    ]);
    resumeStrategies.value = strategyResult.strategies;
    resumeSearchKeywords.value = keywordResult.keywords;
    try {
      resumeAnalysisRun.value = (await profileApi.getLatestResumeAnalysisRun(familyId)).analysis_run;
    } catch (value) {
      if (!(value instanceof ApiError && value.statusCode === 404)) throw value;
      resumeAnalysisRun.value = null;
    }
  };

  const saveEditableContent = async (source: ProfileSource, content: string) => {
    await withSaving(async () => {
      await profileApi.updateEditableContent(source.id, content, source.source_version);
      await refreshProfileData();
    });
  };

  const saveNormalizedMarkdown = async (source: ProfileSource, content: string) => {
    const family = resumeFamilies.value.find((item) => item.id === source.resume_family_id);
    if (!family) return;
    await withSaving(async () => {
      await profileApi.updateNormalizedMarkdown(source.id, content, family.content_version);
      await refreshProfileData();
    });
  };

  const startResumeAnalysis = async (
    resumeVersionId: string,
    operationIds: ResumeAnalysisOperationId[],
    executionPath: "structured" | "codex_workspace"
  ) => {
    if (!profileId.value) return null;
    const version = resumeVersions.value.find((item) => item.id === resumeVersionId);
    if (!version?.resume_family_id) return null;
    const sourceIds = version.source_id ? [version.source_id] : [];
    analyzing.value = true;
    error.value = null;
    try {
      const response = await profileApi.startResumeAnalysis(
        profileId.value,
        version.resume_family_id,
        resumeVersionId,
        sourceIds,
        operationIds,
        executionPath
      );
      resumeAnalysisRun.value = response.analysis_run;
      if (executionPath === "structured") {
        await waitForResumeAnalysis(response.analysis_run.id);
        await refreshProfileData();
      }
      return response.analysis_run;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      analyzing.value = false;
    }
  };

  const waitForResumeAnalysis = async (runId: string) => {
    const completedStatuses = new Set(["completed", "partial_failed", "failed", "cancelled"]);
    while (resumeAnalysisRun.value && !completedStatuses.has(resumeAnalysisRun.value.status)) {
      await delay(600);
      resumeAnalysisRun.value = (await profileApi.getResumeAnalysisRun(runId)).analysis_run;
    }
  };

  const cancelResumeAnalysis = async () => {
    if (!resumeAnalysisRun.value || !["queued", "running"].includes(resumeAnalysisRun.value.status)) return;
    resumeAnalysisRun.value = (await profileApi.cancelResumeAnalysisRun(resumeAnalysisRun.value.id)).analysis_run;
    analyzing.value = false;
  };

  const retryResumeAnalysis = async () => {
    if (!resumeAnalysisRun.value) return null;
    error.value = null;
    try {
      const response = await profileApi.retryResumeAnalysisRun(resumeAnalysisRun.value.id);
      resumeAnalysisRun.value = response.analysis_run;
      if (response.analysis_run.execution_path === "structured") {
        analyzing.value = true;
        await waitForResumeAnalysis(response.analysis_run.id);
        await refreshProfileData();
      }
      return response.analysis_run;
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      analyzing.value = false;
    }
  };

  const saveResumeSearchKeywords = async (
    keywords: Array<{ keyword: string; reason: string; enabled: boolean }>
  ) => {
    if (!selectedResumeFamilyId.value) return;
    await withSaving(async () => {
      resumeSearchKeywords.value = (
        await profileApi.replaceResumeSearchKeywords(selectedResumeFamilyId.value!, keywords)
      ).keywords;
    });
  };

  const updateResumeIssueStatus = async (issueId: string, status: "resolved" | "dismissed") => {
    const updated = await profileApi.updateResumeIssueStatus(issueId, status);
    resumeIssues.value = resumeIssues.value.map((item) => item.id === issueId ? updated : item);
  };

  const updateResumeStrategy = async (
    strategy: ResumeStrategy,
    name: string,
    content: Record<string, unknown>
  ) => {
    const updated = await profileApi.updateResumeStrategy(strategy, name, content);
    resumeStrategies.value = resumeStrategies.value.map((item) =>
      item.id === strategy.id ? updated : item
    );
  };

  const updateFactDisclosure = async (fact: ProfileFact, externalUse: string) => {
    if (!selectedProfile.value) return;
    await withSaving(async () => {
      await profileApi.updateFact(fact.id, {
        ...factPayload(fact),
        external_use: externalUse,
        expected_facts_version: selectedProfile.value!.versions.facts_version
      });
      await refreshProfileData();
    });
  };

  const removeSource = async (sourceId: string) => {
    await withSaving(async () => {
      await profileApi.deleteSource(sourceId);
      await refreshProfileData();
    });
  };

  const cleanSource = async (sourceId: string) => {
    cleaningSourceId.value = sourceId;
    error.value = null;
    try {
      await profileApi.cleanSource(sourceId);
      await refreshProfileData();
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      cleaningSourceId.value = null;
    }
  };

  const analyzeSources = async (sourceIds: string[]) => {
    if (!profileId.value || !sourceIds.length) return;
    analyzing.value = true;
    error.value = null;
    try {
      analysisRun.value = (await profileApi.runAnalysis(profileId.value, sourceIds)).analysis_run;
      await waitForAnalysis(analysisRun.value.id);
      if (analysisRun.value?.status === "failed") {
        throw new Error(formatAnalysisRunError(analysisRun.value));
      }
      if (analysisRun.value?.status === "cancelled") return;
      analysisItems.value = (await profileApi.listAnalysisItems(analysisRun.value.id)).items;
      await refreshProfileData();
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      analyzing.value = false;
    }
  };

  const waitForAnalysis = async (runId: string) => {
    const completedStatuses = new Set(["needs_confirmation", "applied", "failed", "cancelled", "stale"]);
    while (analysisRun.value && !completedStatuses.has(analysisRun.value.status)) {
      await delay(600);
      analysisRun.value = (await profileApi.getAnalysisRun(runId)).analysis_run;
    }
  };

  const cancelAnalysis = async () => {
    if (!analysisRun.value || !["pending", "running", "needs_confirmation"].includes(analysisRun.value.status)) return;
    analysisRun.value = (await profileApi.cancelAnalysisRun(analysisRun.value.id)).analysis_run;
    analyzing.value = false;
    await refreshProfileData();
  };

  const retryAnalysis = async () => {
    if (!analysisRun.value) return;
    analyzing.value = true;
    error.value = null;
    try {
      analysisRun.value = (await profileApi.retryAnalysisRun(analysisRun.value.id)).analysis_run;
      await waitForAnalysis(analysisRun.value.id);
      if (analysisRun.value?.status === "failed") throw new Error(formatAnalysisRunError(analysisRun.value));
      if (analysisRun.value?.status !== "cancelled") {
        analysisItems.value = (await profileApi.listAnalysisItems(analysisRun.value.id)).items;
        await refreshProfileData();
      }
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      analyzing.value = false;
    }
  };

  const decideItem = async (item: ProfileAnalysisItem, decision: "accepted" | "rejected" | "deferred") => {
    const updated = (await profileApi.decideAnalysisItem(item.id, decision, item.status)).item;
    analysisItems.value = analysisItems.value.map((candidate) => candidate.id === item.id ? updated : candidate);
  };

  const applyAcceptedItems = async () => {
    if (!analysisRun.value || !selectedProfile.value) return;
    const itemIds = analysisItems.value
      .filter((item) => item.status === "accepted" || item.status === "edited_and_accepted" || item.status === "apply_failed")
      .map((item) => item.id);
    if (!itemIds.length) return;
    await withSaving(async () => {
      await profileApi.applyAnalysisItems(analysisRun.value!.id, itemIds, selectedProfile.value!.versions);
      analysisItems.value = (await profileApi.listAnalysisItems(analysisRun.value!.id)).items;
      await refreshProfileData();
    });
  };

  const addFact = async (payload: Record<string, unknown>) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.createFact(profileId.value!, payload);
      await refreshProfileData();
    });
  };

  const confirmFact = async (fact: ProfileFact) => {
    if (!selectedProfile.value) return;
    await withSaving(async () => {
      await profileApi.updateFact(fact.id, {
        ...factPayload(fact),
        status: "confirmed",
        expected_facts_version: selectedProfile.value!.versions.facts_version
      });
      await refreshProfileData();
    });
  };

  const removeFact = async (factId: string) => {
    await withSaving(async () => {
      await profileApi.deleteFact(factId);
      await refreshProfileData();
    });
  };

  const saveFact = async (fact: ProfileFact | null, payload: Record<string, unknown>) => {
    if (!profileId.value || !selectedProfile.value) return;
    await withSaving(async () => {
      if (fact) {
        await profileApi.updateFact(fact.id, {
          ...factPayload(fact),
          ...payload,
          expected_facts_version: selectedProfile.value!.versions.facts_version
        });
      } else {
        await profileApi.createFact(profileId.value!, payload);
      }
      await refreshProfileData();
    });
  };

  const addQuestion = async (payload: Record<string, unknown>) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.createQuestion(profileId.value!, payload);
      await refreshProfileData();
    });
  };

  const answerQuestion = async (question: ProfileQuestion, finalAnswer: string, externalUse: string) => {
    if (!selectedProfile.value) return;
    await withSaving(async () => {
      await profileApi.updateQuestion(question.id, {
        ...questionPayload(question),
        final_answer: finalAnswer,
        status: "confirmed",
        external_use: externalUse,
        expected_questions_version: selectedProfile.value!.versions.questions_version
      });
      await refreshProfileData();
    });
  };

  const removeQuestion = async (questionId: string) => {
    await withSaving(async () => {
      await profileApi.deleteQuestion(questionId);
      await refreshProfileData();
    });
  };

  const saveQuestion = async (question: ProfileQuestion | null, payload: Record<string, unknown>) => {
    if (!profileId.value || !selectedProfile.value) return;
    await withSaving(async () => {
      if (question) {
        await profileApi.updateQuestion(question.id, {
          ...questionPayload(question),
          ...payload,
          expected_questions_version: selectedProfile.value!.versions.questions_version
        });
      } else {
        await profileApi.createQuestion(profileId.value!, payload);
      }
      await refreshProfileData();
    });
  };

  const saveQATemplate = async (templateId: string | null, payload: Record<string, unknown>) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      if (templateId) await profileApi.updateQATemplate(profileId.value!, templateId, payload);
      else await profileApi.createQATemplate(profileId.value!, payload);
      await refreshProfileData();
    });
  };

  const removeQATemplate = async (templateId: string) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.deleteQATemplate(profileId.value!, templateId);
      await refreshProfileData();
    });
  };

  const loadAnswers = async (questionId: string) => {
    answers.value = { ...answers.value, [questionId]: (await profileApi.listAnswers(questionId)).answer_variants };
  };

  const addAnswerVariant = async (questionId: string, payload: Record<string, unknown>) => {
    await withSaving(async () => {
      await profileApi.createAnswer(questionId, payload);
      await loadAnswers(questionId);
      await reloadProfile();
    });
  };

  const confirmAnswerVariant = async (questionId: string, answerId: string) => {
    await withSaving(async () => {
      await profileApi.confirmAnswer(answerId);
      await loadAnswers(questionId);
      await reloadProfile();
    });
  };

  const addResumeVersion = async (payload: Record<string, unknown>) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.createResumeVersion(profileId.value!, payload);
      await refreshProfileData();
    });
  };

  const previewAIDerivedResume = async (payload: Record<string, unknown>) => {
    if (!profileId.value) return null;
    return withSaving(async () => profileApi.previewAIDerivedResume(profileId.value!, payload));
  };

  const updateResumeVersion = async (resumeVersionId: string, payload: Record<string, unknown>) => {
    await withSaving(async () => {
      await profileApi.updateResumeVersion(resumeVersionId, payload);
      await refreshProfileData();
    });
  };

  const confirmResumeVersion = async (resumeVersionId: string) => {
    await withSaving(async () => {
      await profileApi.confirmResumeVersion(resumeVersionId);
      await refreshProfileData();
    });
  };

  const setResumeVersionAsBase = async (resumeVersionId: string) => {
    await withSaving(async () => {
      await profileApi.setResumeVersionAsBase(resumeVersionId);
      await refreshProfileData();
    });
  };

  const removeResumeVersion = async (resumeVersionId: string) => {
    await withSaving(async () => {
      await profileApi.deleteResumeVersion(resumeVersionId);
      await refreshProfileData();
    });
  };

  const removeResumeVersionV3 = async (
    resumeVersionId: string,
    payload: {
      action: "delete_version" | "promote_then_delete" | "delete_family";
      promote_resume_version_id: string | null;
      profile_data_action: "delete" | "move_to_pending";
    }
  ) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.deleteResumeVersionV3(profileId.value!, resumeVersionId, payload);
      await refreshProfileData();
    });
  };

  const answerIssue = async (issueId: string, answerText: string) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      const updated = (await profileApi.answerIssue(profileId.value!, issueId, answerText)).issue;
      issues.value = issues.value.map((item) => item.id === issueId ? updated : item);
    });
  };

  const updateIssueChangeSet = async (issueId: string, changes: Record<string, unknown>) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      const updated = (await profileApi.updateIssueChangeSet(profileId.value!, issueId, changes)).issue;
      issues.value = issues.value.map((item) => item.id === issueId ? updated : item);
    });
  };

  const applyIssue = async (issueId: string) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.applyIssue(profileId.value!, issueId);
      await refreshProfileData();
    });
  };

  const setIssueStatus = async (issueId: string, status: "dismissed" | "pending") => {
    if (!profileId.value) return;
    const updated = (await profileApi.setIssueStatus(profileId.value, issueId, status)).issue;
    issues.value = issues.value.map((item) => item.id === issueId ? updated : item);
  };

  const updateFactResumeLinks = async (factId: string, resumeVersionIds: string[], appliesToAll: boolean) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.updateFactResumeLinks(profileId.value!, factId, resumeVersionIds, appliesToAll);
      await refreshProfileData();
    });
  };

  const updateQuestionResumeLinks = async (questionId: string, resumeVersionIds: string[], appliesToAll: boolean) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.updateQuestionResumeLinks(profileId.value!, questionId, resumeVersionIds, appliesToAll);
      await refreshProfileData();
    });
  };

  const addCampaign = async (payload: Record<string, unknown>) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      await profileApi.createCampaign(profileId.value!, payload);
      await refreshProfileData();
    });
  };

  const removeCampaign = async (campaignId: string) => {
    await withSaving(async () => {
      await profileApi.deleteCampaign(campaignId);
      await refreshProfileData();
    });
  };

  const loadContext = async (view: ProfileContext["view"]) => {
    if (!profileId.value) return;
    context.value = (await profileApi.getContext(profileId.value, view, selectedResumeFamilyId.value ?? "")).context;
  };

  const loadContextHead = async (resumeVersionId: string, view: ProfileContextView) => {
    if (!profileId.value) return;
    contextHead.value = (await profileApi.getContextHead(profileId.value, resumeVersionId, view)).context;
  };

  const regenerateContext = async (resumeVersionId: string, view: ProfileContextView) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      contextHead.value = (await profileApi.regenerateContext(profileId.value!, resumeVersionId, view)).context;
    });
  };

  const updateContextDraft = async (resumeVersionId: string, view: ProfileContextView, content: string) => {
    if (!profileId.value || !contextHead.value?.draft_revision) return;
    await withSaving(async () => {
      contextHead.value = (await profileApi.updateContextDraft(
        profileId.value!, resumeVersionId, view, contextHead.value!.draft_revision!.id, content
      )).context;
    });
  };

  const saveContextDraft = async (resumeVersionId: string, view: ProfileContextView) => {
    if (!profileId.value || !contextHead.value?.draft_revision) return;
    await withSaving(async () => {
      contextHead.value = (await profileApi.saveContextDraft(
        profileId.value!, resumeVersionId, view, contextHead.value!.draft_revision!.id
      )).context;
    });
  };

  const restoreContextRevision = async (resumeVersionId: string, view: ProfileContextView, revisionId: string) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      contextHead.value = (await profileApi.restoreContextRevision(
        profileId.value!, resumeVersionId, view, revisionId
      )).context;
    });
  };

  const saveContextContent = async (resumeVersionId: string, view: ProfileContextView, content: string) => {
    if (!profileId.value) return;
    await withSaving(async () => {
      const draftResponse = contextHead.value?.draft_revision
        ? await profileApi.updateContextDraft(
          profileId.value!, resumeVersionId, view, contextHead.value.draft_revision.id, content
        )
        : await profileApi.createContextDraft(profileId.value!, resumeVersionId, view, content);
      contextHead.value = draftResponse.context;
      const draftId = draftResponse.context.draft_revision?.id;
      if (!draftId) return;
      contextHead.value = (await profileApi.saveContextDraft(
        profileId.value!, resumeVersionId, view, draftId
      )).context;
    });
  };

  const withSaving = async <T>(operation: () => Promise<T>): Promise<T> => {
    saving.value = true;
    error.value = null;
    try {
      return await operation();
    } catch (value) {
      error.value = mapError(value);
      throw value;
    } finally {
      saving.value = false;
    }
  };

  return {
    profiles,
    selectedProfile,
    sources,
    facts,
    questions,
    answers,
    resumeVersions,
    campaigns,
    resumeFamilies,
    selectedResumeFamilyId,
    selectedResumeVersionId,
    selectedResumeFamily,
    selectedFamilySources,
    resumeAnalysisRun,
    resumeIssues,
    issues,
    qaTemplates,
    resumeStrategies,
    resumeSearchKeywords,
    analysisRun,
    analysisItems,
    context,
    contextHead,
    loading,
    analyzing,
    saving,
    cleaningSourceId,
    error,
    load,
    refreshProfileData,
    addTextSource,
    addFileSource,
    importPdfResume,
    importDerivedPdfResume,
    selectResumeFamily,
    refreshResumeFamilyData,
    saveEditableContent,
    saveNormalizedMarkdown,
    startResumeAnalysis,
    cancelResumeAnalysis,
    retryResumeAnalysis,
    saveResumeSearchKeywords,
    updateResumeIssueStatus,
    updateResumeStrategy,
    updateFactDisclosure,
    removeSource,
    cleanSource,
    analyzeSources,
    cancelAnalysis,
    retryAnalysis,
    decideItem,
    applyAcceptedItems,
    addFact,
    confirmFact,
    removeFact,
    saveFact,
    addQuestion,
    answerQuestion,
    removeQuestion,
    saveQuestion,
    saveQATemplate,
    removeQATemplate,
    loadAnswers,
    addAnswerVariant,
    confirmAnswerVariant,
    addResumeVersion,
    previewAIDerivedResume,
    updateResumeVersion,
    confirmResumeVersion,
    setResumeVersionAsBase,
    removeResumeVersion,
    removeResumeVersionV3,
    answerIssue,
    updateIssueChangeSet,
    applyIssue,
    setIssueStatus,
    updateFactResumeLinks,
    updateQuestionResumeLinks,
    addCampaign,
    removeCampaign,
    loadContext,
    loadContextHead,
    regenerateContext,
    updateContextDraft,
    saveContextDraft,
    restoreContextRevision,
    saveContextContent
  };
});

const factPayload = (fact: ProfileFact) => ({
  domain: fact.domain,
  entity_type: fact.entity_type,
  entity_id: fact.entity_id,
  field_key: fact.field_key,
  value: fact.value,
  source_type: fact.source_type,
  sort_order: fact.sort_order,
  valid_from: fact.valid_from,
  valid_to: fact.valid_to,
  date_precision: fact.date_precision,
  is_current: fact.is_current,
  confidence: fact.confidence,
  status: fact.status,
  conflict_group_id: fact.conflict_group_id,
  sensitivity: fact.sensitivity,
  external_use: fact.external_use,
  disclosure_policy: fact.disclosure_policy,
  valid_until: fact.valid_until,
  scope_type: fact.scope_type,
  scope_id: fact.scope_id,
  confirmed_by: fact.confirmed_by,
  applies_to_all_resumes: fact.applies_to_all_resumes,
  resume_version_ids: fact.resume_version_ids
});

const questionPayload = (question: ProfileQuestion) => ({
  question_key: question.question_key,
  question_text: question.question_text,
  reason: question.reason,
  origin: question.origin,
  answer_type: question.answer_type,
  required_stage: question.required_stage,
  priority: question.priority,
  proposed_answer: question.proposed_answer,
  final_answer: question.final_answer,
  status: question.status,
  external_use: question.external_use,
  valid_until: question.valid_until,
  source_id: question.source_id,
  job_id: question.job_id,
  writes_to_field: question.writes_to_field,
  enabled: question.enabled,
  scope_type: question.scope_type,
  scope_id: question.scope_id,
  confirmed_by: question.confirmed_by,
  applies_to_all_resumes: question.applies_to_all_resumes,
  resume_version_ids: question.resume_version_ids
});

const mapError = (value: unknown) => {
  if (value instanceof ApiError) {
    const reason = value.details?.error_message || value.message;
    return value.errorCategory ? `错误类别：${mapErrorCategoryLabel(value.errorCategory)}；原因：${reason}` : reason;
  }
  if (value instanceof NetworkError) return value.message;
  return (value as Error).message || "求职资料操作失败。";
};

const formatAnalysisRunError = (run: ProfileAnalysisRun) => {
  const category = run.error_category || "PROFILE_ANALYSIS_FAILED";
  return `错误类别：${mapErrorCategoryLabel(category)}；原因：${run.error_message || "资料 AI 分析失败。"}`;
};

const delay = (milliseconds: number) => new Promise<void>((resolve) => globalThis.setTimeout(resolve, milliseconds));
