import { request } from "@/services/api";
import type {
  CandidateProfile,
  AIDerivedResumePreview,
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
  ProfileQARevision,
  ProfileQuestion,
  ProfileResumeVersion,
  ProfileSource,
  ProfileVersions,
  ResumeAnalysisIssue,
  ResumeAnalysisOperationId,
  ResumeAnalysisRun,
  ResumeFamily,
  ResumeDeleteImpact,
  ResumeSearchKeyword,
  ResumeStrategy
} from "@/profile-types";

export const profileApi = {
  listProfiles: () => request<{ profiles: CandidateProfile[] }>("/api/fine-job/profiles"),
  listSources: (profileId: string) =>
    request<{ sources: ProfileSource[] }>(`/api/fine-job/profiles/${profileId}/sources`),
  createTextSource: (profileId: string, payload: Record<string, unknown>) =>
    request<{ source: ProfileSource }>(`/api/fine-job/profiles/${profileId}/sources/text`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  createFileSource: (profileId: string, filePath: string) =>
    request<{ source: ProfileSource }>(`/api/fine-job/profiles/${profileId}/sources/file`, {
      method: "POST",
      body: JSON.stringify({ file_path: filePath, enabled: true })
    }),
  listResumeFamilies: (profileId: string) =>
    request<{ resume_families: ResumeFamily[] }>(`/api/fine-job/profiles/${profileId}/resume-families`),
  importPdfResume: (profileId: string, filePath: string, name = "") =>
    request<{ resume_family: ResumeFamily }>(`/api/fine-job/profiles/${profileId}/resume-families/from-pdf`, {
      method: "POST",
      body: JSON.stringify({ file_path: filePath, name: name || null, target_role_family: "" })
    }),
  importDerivedPdfResume: (
    profileId: string,
    resumeFamilyId: string,
    filePath: string,
    name = "",
    derivedReason = ""
  ) => request<{ resume_version: ProfileResumeVersion }>(
    `/api/fine-job/profiles/${profileId}/resume-families/${resumeFamilyId}/derived-from-pdf`,
    {
      method: "POST",
      body: JSON.stringify({ file_path: filePath, name: name || null, derived_reason: derivedReason })
    }
  ),
  updateEditableContent: (sourceId: string, content: string, expectedSourceVersion: number) =>
    request<{ source: ProfileSource }>(`/api/fine-job/profiles/sources/${sourceId}/editable-content`, {
      method: "PUT",
      body: JSON.stringify({ content, expected_source_version: expectedSourceVersion })
    }),
  updateNormalizedMarkdown: (sourceId: string, content: string, expectedContentVersion: number) =>
    request<{ source: ProfileSource }>(`/api/fine-job/profiles/sources/${sourceId}/normalized-markdown`, {
      method: "PUT",
      body: JSON.stringify({ content, expected_content_version: expectedContentVersion })
    }),
  startResumeAnalysis: (
    profileId: string,
    resumeFamilyId: string,
    resumeVersionId: string,
    sourceIds: string[],
    operationIds: ResumeAnalysisOperationId[],
    executionPath: "structured" | "codex_workspace"
  ) => request<{ analysis_run: ResumeAnalysisRun }>(
    `/api/fine-job/profiles/${profileId}/resume-families/${resumeFamilyId}/analysis-runs`,
    {
      method: "POST",
      body: JSON.stringify({
        source_ids: sourceIds,
        resume_version_id: resumeVersionId,
        operation_ids: operationIds,
        pipeline_mode: operationIds.length === 1 ? "single" : "chained",
        execution_path: executionPath
      })
    }
  ),
  getResumeAnalysisRun: (runId: string) =>
    request<{ analysis_run: ResumeAnalysisRun }>(`/api/fine-job/profiles/resume-analysis-runs/${runId}`),
  getLatestResumeAnalysisRun: (resumeFamilyId: string) =>
    request<{ analysis_run: ResumeAnalysisRun }>(
      `/api/fine-job/profiles/resume-families/${resumeFamilyId}/analysis-runs/latest`
    ),
  cancelResumeAnalysisRun: (runId: string) =>
    request<{ analysis_run: ResumeAnalysisRun }>(`/api/fine-job/profiles/resume-analysis-runs/${runId}/cancel`, {
      method: "POST"
    }),
  retryResumeAnalysisRun: (runId: string) =>
    request<{ analysis_run: ResumeAnalysisRun }>(`/api/fine-job/profiles/resume-analysis-runs/${runId}/retry`, {
      method: "POST"
    }),
  listResumeIssues: (resumeFamilyId: string) =>
    request<{ issues: ResumeAnalysisIssue[] }>(`/api/fine-job/profiles/resume-families/${resumeFamilyId}/issues`),
  updateResumeIssueStatus: (issueId: string, status: "resolved" | "dismissed") =>
    request<ResumeAnalysisIssue>(`/api/fine-job/profiles/resume-analysis-issues/${issueId}/status`, {
      method: "PUT",
      body: JSON.stringify({ status })
    }),
  listIssues: (profileId: string) =>
    request<{ issues: ProfileIssue[] }>(`/api/fine-job/profiles/${profileId}/issues`),
  answerIssue: (profileId: string, issueId: string, answerText: string) =>
    request<{ issue: ProfileIssue }>(`/api/fine-job/profiles/${profileId}/issues/${issueId}/answers`, {
      method: "POST",
      body: JSON.stringify({ answer_text: answerText })
    }),
  updateIssueChangeSet: (profileId: string, issueId: string, changes: Record<string, unknown>) =>
    request<{ issue: ProfileIssue }>(`/api/fine-job/profiles/${profileId}/issues/${issueId}/change-set`, {
      method: "PATCH",
      body: JSON.stringify({ changes })
    }),
  applyIssue: (profileId: string, issueId: string) =>
    request<{ issue: ProfileIssue }>(`/api/fine-job/profiles/${profileId}/issues/${issueId}/apply`, { method: "POST" }),
  setIssueStatus: (profileId: string, issueId: string, issueStatus: "dismissed" | "pending") =>
    request<{ issue: ProfileIssue }>(`/api/fine-job/profiles/${profileId}/issues/${issueId}/status`, {
      method: "POST",
      body: JSON.stringify({ status: issueStatus })
    }),
  listResumeStrategies: (resumeFamilyId: string) =>
    request<{ strategies: ResumeStrategy[] }>(`/api/fine-job/profiles/resume-families/${resumeFamilyId}/strategies`),
  updateResumeStrategy: (strategy: ResumeStrategy, name: string, content: Record<string, unknown>) =>
    request<ResumeStrategy>(`/api/fine-job/profiles/resume-strategies/${strategy.id}`, {
      method: "PUT",
      body: JSON.stringify({ name, content, expected_version: strategy.version })
    }),
  listResumeSearchKeywords: (resumeFamilyId: string) =>
    request<{ keywords: ResumeSearchKeyword[] }>(
      `/api/fine-job/profiles/resume-families/${resumeFamilyId}/search-keywords`
    ),
  replaceResumeSearchKeywords: (
    resumeFamilyId: string,
    keywords: Array<{ keyword: string; reason: string; enabled: boolean }>
  ) => request<{ keywords: ResumeSearchKeyword[] }>(
    `/api/fine-job/profiles/resume-families/${resumeFamilyId}/search-keywords`,
    { method: "PUT", body: JSON.stringify({ keywords }) }
  ),
  deleteSource: (sourceId: string) =>
    request<void>(`/api/fine-job/profiles/sources/${sourceId}`, { method: "DELETE" }),
  cleanSource: (sourceId: string) =>
    request<{ source: ProfileSource }>(`/api/fine-job/profiles/sources/${sourceId}/clean`, { method: "POST" }),
  runAnalysis: (profileId: string, sourceIds: string[]) =>
    request<{ analysis_run: ProfileAnalysisRun }>(`/api/fine-job/profiles/${profileId}/analysis-runs/async`, {
      method: "POST",
      body: JSON.stringify({ source_ids: sourceIds })
    }),
  getLatestAnalysisRun: (profileId: string) =>
    request<{ analysis_run: ProfileAnalysisRun }>(`/api/fine-job/profiles/${profileId}/analysis-runs/latest`),
  getAnalysisRun: (runId: string) =>
    request<{ analysis_run: ProfileAnalysisRun }>(`/api/fine-job/profiles/analysis-runs/${runId}`),
  cancelAnalysisRun: (runId: string) =>
    request<{ analysis_run: ProfileAnalysisRun }>(`/api/fine-job/profiles/analysis-runs/${runId}/cancel`, {
      method: "POST"
    }),
  autoApplyAnalysisFacts: (runId: string) =>
    request<{ analysis_run: ProfileAnalysisRun }>(`/api/fine-job/profiles/analysis-runs/${runId}/auto-apply-facts`, {
      method: "POST"
    }),
  retryAnalysisRun: (runId: string) =>
    request<{ analysis_run: ProfileAnalysisRun }>(`/api/fine-job/profiles/analysis-runs/${runId}/retry`, {
      method: "POST"
    }),
  listAnalysisItems: (runId: string) =>
    request<{ items: ProfileAnalysisItem[] }>(`/api/fine-job/profiles/analysis-runs/${runId}/items`),
  decideAnalysisItem: (itemId: string, decision: "accepted" | "rejected" | "deferred", expectedStatus: string) =>
    request<{ item: ProfileAnalysisItem }>(`/api/fine-job/profiles/analysis-items/${itemId}/${decision}`, {
      method: "POST",
      body: JSON.stringify({ expected_status: expectedStatus, decision_note: null })
    }),
  applyAnalysisItems: (runId: string, itemIds: string[], expectedVersions: ProfileVersions) =>
    request<{ items: ProfileAnalysisItem[] }>(`/api/fine-job/profiles/analysis-runs/${runId}/apply`, {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds, expected_versions: expectedVersions })
    }),
  listFacts: (profileId: string) =>
    request<{ facts: ProfileFact[]; facts_version: number }>(`/api/fine-job/profiles/${profileId}/facts`),
  createFact: (profileId: string, payload: Record<string, unknown>) =>
    request<{ fact: ProfileFact }>(`/api/fine-job/profiles/${profileId}/facts`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateFact: (factId: string, payload: Record<string, unknown>) =>
    request<{ fact: ProfileFact }>(`/api/fine-job/profiles/facts/${factId}`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  updateFactResumeLinks: (profileId: string, factId: string, resumeVersionIds: string[], appliesToAllResumes: boolean) =>
    request<{ fact: ProfileFact }>(`/api/fine-job/profiles/${profileId}/facts/${factId}/resume-links`, {
      method: "PUT",
      body: JSON.stringify({ resume_version_ids: resumeVersionIds, applies_to_all_resumes: appliesToAllResumes })
    }),
  deleteFact: (factId: string) =>
    request<void>(`/api/fine-job/profiles/facts/${factId}`, { method: "DELETE" }),
  listQuestions: (profileId: string) =>
    request<{ questions: ProfileQuestion[]; questions_version: number }>(`/api/fine-job/profiles/${profileId}/questions`),
  createQuestion: (profileId: string, payload: Record<string, unknown>) =>
    request<{ question: ProfileQuestion }>(`/api/fine-job/profiles/${profileId}/questions`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateQuestion: (questionId: string, payload: Record<string, unknown>) =>
    request<{ question: ProfileQuestion }>(`/api/fine-job/profiles/questions/${questionId}`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  updateQuestionResumeLinks: (profileId: string, questionId: string, resumeVersionIds: string[], appliesToAllResumes: boolean) =>
    request<{ question: ProfileQuestion }>(`/api/fine-job/profiles/${profileId}/qa/${questionId}/resume-links`, {
      method: "PUT",
      body: JSON.stringify({ resume_version_ids: resumeVersionIds, applies_to_all_resumes: appliesToAllResumes })
    }),
  listQATemplates: (profileId: string) =>
    request<{ templates: ProfileQATemplate[] }>(`/api/fine-job/profiles/${profileId}/qa-templates`),
  createQATemplate: (profileId: string, payload: Record<string, unknown>) =>
    request<{ template: ProfileQATemplate }>(`/api/fine-job/profiles/${profileId}/qa-templates`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateQATemplate: (profileId: string, templateId: string, payload: Record<string, unknown>) =>
    request<{ template: ProfileQATemplate }>(`/api/fine-job/profiles/${profileId}/qa-templates/${templateId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteQATemplate: (profileId: string, templateId: string) =>
    request<void>(`/api/fine-job/profiles/${profileId}/qa-templates/${templateId}`, { method: "DELETE" }),
  deleteQuestion: (questionId: string) =>
    request<void>(`/api/fine-job/profiles/questions/${questionId}`, { method: "DELETE" }),
  listQuestionRevisions: (profileId: string, questionId: string) =>
    request<{ revisions: ProfileQARevision[] }>(
      `/api/fine-job/profiles/${profileId}/questions/${questionId}/revisions`
    ),
  previewQuestionAnswer: (
    profileId: string,
    questionId: string,
    payload: { resume_version_id: string; instructions: string }
  ) => request<{ question_id: string; resume_version_id: string; answer: string }>(
    `/api/fine-job/profiles/${profileId}/questions/${questionId}/ai-answer-preview`,
    { method: "POST", body: JSON.stringify(payload) }
  ),
  listAnswers: (questionId: string) =>
    request<{ answer_variants: ProfileAnswerVariant[] }>(`/api/fine-job/profiles/questions/${questionId}/answers`),
  createAnswer: (questionId: string, payload: Record<string, unknown>) =>
    request<{ answer_variant: ProfileAnswerVariant }>(`/api/fine-job/profiles/questions/${questionId}/answers`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  confirmAnswer: (answerId: string) =>
    request<{ answer_variant: ProfileAnswerVariant }>(`/api/fine-job/profiles/answers/${answerId}/confirm`, { method: "POST" }),
  listResumeVersions: (profileId: string) =>
    request<{ resume_versions: ProfileResumeVersion[] }>(`/api/fine-job/profiles/${profileId}/resume-versions`),
  createResumeVersion: (profileId: string, payload: Record<string, unknown>) =>
    request<{ resume_version: ProfileResumeVersion }>(`/api/fine-job/profiles/${profileId}/resume-versions`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  previewAIDerivedResume: (profileId: string, payload: Record<string, unknown>) =>
    request<AIDerivedResumePreview>(`/api/fine-job/profiles/${profileId}/resume-versions/ai-derived-preview`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateResumeVersion: (resumeVersionId: string, payload: Record<string, unknown>) =>
    request<{ resume_version: ProfileResumeVersion }>(`/api/fine-job/profiles/resume-versions/${resumeVersionId}`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  confirmResumeVersion: (resumeVersionId: string) =>
    request<{ resume_version: ProfileResumeVersion }>(`/api/fine-job/profiles/resume-versions/${resumeVersionId}/confirm`, { method: "POST" }),
  setResumeVersionAsBase: (resumeVersionId: string) =>
    request<{ resume_version: ProfileResumeVersion }>(`/api/fine-job/profiles/resume-versions/${resumeVersionId}/set-as-base`, { method: "POST" }),
  deleteResumeVersion: (resumeVersionId: string) =>
    request<void>(`/api/fine-job/profiles/resume-versions/${resumeVersionId}`, { method: "DELETE" }),
  getResumeDeleteImpact: (profileId: string, resumeVersionId: string) =>
    request<ResumeDeleteImpact>(`/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/delete-impact`),
  deleteResumeVersionV3: (
    profileId: string,
    resumeVersionId: string,
    payload: {
      action: "delete_version" | "promote_then_delete" | "delete_family";
      promote_resume_version_id: string | null;
      profile_data_action: "delete" | "move_to_pending";
    }
  ) => request<{
    deleted_resume_version_ids: string[];
    deleted_source_ids: string[];
    deleted_fact_ids: string[];
    deleted_question_ids: string[];
    pending_issue_ids: string[];
    promoted_resume_version_id: string | null;
  }>(`/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}`, {
    method: "DELETE",
    body: JSON.stringify(payload)
  }),
  listCampaigns: (profileId: string) =>
    request<{ campaigns: ProfileCampaign[] }>(`/api/fine-job/profiles/${profileId}/campaigns`),
  createCampaign: (profileId: string, payload: Record<string, unknown>) =>
    request<{ campaign: ProfileCampaign }>(`/api/fine-job/profiles/${profileId}/campaigns`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  deleteCampaign: (campaignId: string) =>
    request<void>(`/api/fine-job/profiles/campaigns/${campaignId}`, { method: "DELETE" }),
  getContext: (profileId: string, view: ProfileContext["view"], resumeFamilyId = "") =>
    request<{ context: ProfileContext }>(
      `/api/fine-job/profiles/${profileId}/context?view=${view}&resume_family_id=${encodeURIComponent(resumeFamilyId)}`
    ),
  getContextHead: (profileId: string, resumeVersionId: string, view: ProfileContextView) =>
    request<{ context: ProfileContextHead }>(
      `/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/contexts/${view}`
    ),
  regenerateContext: (profileId: string, resumeVersionId: string, view: ProfileContextView) =>
    request<{ context: ProfileContextHead }>(
      `/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/contexts/${view}/regenerate`,
      { method: "POST" }
    ),
  createContextDraft: (profileId: string, resumeVersionId: string, view: ProfileContextView, content: string) =>
    request<{ context: ProfileContextHead }>(
      `/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/contexts/${view}/drafts`,
      { method: "POST", body: JSON.stringify({ content }) }
    ),
  updateContextDraft: (profileId: string, resumeVersionId: string, view: ProfileContextView, revisionId: string, content: string) =>
    request<{ context: ProfileContextHead }>(
      `/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/contexts/${view}/drafts/${revisionId}`,
      { method: "PATCH", body: JSON.stringify({ content }) }
    ),
  saveContextDraft: (profileId: string, resumeVersionId: string, view: ProfileContextView, revisionId: string) =>
    request<{ context: ProfileContextHead }>(
      `/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/contexts/${view}/drafts/${revisionId}/save`,
      { method: "POST" }
    ),
  restoreContextRevision: (profileId: string, resumeVersionId: string, view: ProfileContextView, revisionId: string) =>
    request<{ context: ProfileContextHead }>(
      `/api/fine-job/profiles/${profileId}/resume-versions/${resumeVersionId}/contexts/${view}/revisions/${revisionId}/restore`,
      { method: "POST" }
    )
};
