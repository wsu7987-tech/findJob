<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import type {
  ProfileContextView,
  ProfileExternalUse,
  ProfileFact,
  ProfileIssue,
  ProfileQATemplate,
  ProfileQARevision,
  ProfileQuestion,
  ProfileResumeVersion,
  ProfileSource,
  ResumeAnalysisOperationId,
  ResumeDeleteImpact
} from "@/profile-types";
import { chooseFile, getCodexBridge, hasFilePicker } from "@/services/desktop-bridge";
import { profileApi } from "@/services/profile-api";
import { useFineJobProfilesStore } from "@/stores/fineJobProfiles";

const store = useFineJobProfilesStore();
const router = useRouter();
const supportsFilePicker = hasFilePicker();
const supportsCodexWorkspace = Boolean(getCodexBridge());
const activeTab = ref("content");

const operationOptions: Array<{ id: ResumeAnalysisOperationId; label: string; description: string }> = [
  { id: "clean_content", label: "内容清洗", description: "生成规范 Markdown，并保留原始识别稿" },
  { id: "extract_facts", label: "正式事实", description: "有依据且无冲突的事实直接确认" },
  { id: "extract_qa", label: "动态 QA", description: "提取有答案的正式 QA，缺口进入待处理" },
  { id: "generate_filter_strategy", label: "岗位筛选策略", description: "结果写入现有策略管理" },
  { id: "generate_recommendation_strategy", label: "建议投递策略", description: "关联同一份具体简历" },
  { id: "generate_search_keywords", label: "搜索词组", description: "写入筛选策略下的有序搜索词" }
];
// 业务层保留稳定枚举值，界面统一显示中文披露级别。
const externalUseOptions: Array<{ value: ProfileExternalUse; label: string }> = [
  { value: "prohibited", label: "禁止披露" },
  { value: "summary_only", label: "仅摘要" },
  { value: "allowed", label: "允许披露" }
];
const externalUseLabel = (value: string) => externalUseOptions.find((item) => item.value === value)?.label ?? value;
const selectedOperations = ref<ResumeAnalysisOperationId[]>(operationOptions.map((item) => item.id));
const analysisResumeId = ref("");

const contentDrawerOpen = ref(false);
const selectedSource = ref<ProfileSource | null>(null);
const editableText = ref("");
const normalizedMarkdown = ref("");

const factDialogOpen = ref(false);
const selectedFact = ref<ProfileFact | null>(null);
const factForm = reactive({
  domain: "basic",
  entity_type: "candidate",
  entity_id: "candidate",
  field_key: "",
  value_text: "",
  external_use: "prohibited" as ProfileExternalUse,
  applies_to_all_resumes: false,
  resume_version_ids: [] as string[]
});

const questionDialogOpen = ref(false);
const selectedQuestion = ref<ProfileQuestion | null>(null);
const questionForm = reactive({
  question_key: "",
  question_text: "",
  reason: "",
  final_answer: "",
  required_stage: "chat",
  priority: "medium",
  external_use: "prohibited",
  applies_to_all_resumes: false,
  resume_version_ids: [] as string[]
});
const qaHistoryDialogOpen = ref(false);
const qaHistoryQuestion = ref<ProfileQuestion | null>(null);
const qaRevisions = ref<ProfileQARevision[]>([]);
const qaAiDialogOpen = ref(false);
const qaAiQuestion = ref<ProfileQuestion | null>(null);
const qaAiForm = reactive({ resume_version_id: "", instructions: "" });

const templateDialogOpen = ref(false);
const selectedTemplate = ref<ProfileQATemplate | null>(null);
const templateForm = reactive({
  question_key: "",
  question_text: "",
  reason: "",
  answer_type: "text",
  required_stage: "chat",
  priority: "medium",
  writes_to_field: "",
  enabled: true,
  sort_order: 0
});

const issueAnswers = reactive<Record<string, string>>({});
const issueChanges = reactive<Record<string, string>>({});

const resumeDialogOpen = ref(false);
const editingResume = ref<ProfileResumeVersion | null>(null);
const resumeForm = reactive({
  resume_family_id: "",
  name: "",
  parent_version_id: "",
  target_job_id: "",
  derived_reason: "",
  content: ""
});
const derivedUploadDialogOpen = ref(false);
const derivedUploadForm = reactive({ file_path: "", resume_family_id: "", name: "", derived_reason: "" });
const aiDerivedDialogOpen = ref(false);
const aiDerivedForm = reactive({
  source_resume_version_id: "",
  target_job_id: "",
  job_title: "",
  jd_text: "",
  instructions: "",
  name: "",
  derived_reason: "",
  content: ""
});
const compareDialogOpen = ref(false);
const compareTarget = ref<ProfileResumeVersion | null>(null);
const compareSource = ref<ProfileResumeVersion | null>(null);

const deleteDialogOpen = ref(false);
const deletingResume = ref<ProfileResumeVersion | null>(null);
const deleteImpact = ref<ResumeDeleteImpact | null>(null);
const deleteForm = reactive({
  action: "delete_version" as "delete_version" | "promote_then_delete" | "delete_family",
  promote_resume_version_id: "",
  profile_data_action: "move_to_pending" as "delete" | "move_to_pending"
});

const contextResumeId = ref("");
const contextView = ref<ProfileContextView>("full");
const contextText = ref("");

const resumeFamilySections = computed(() => store.resumeFamilies.map((family) => {
  const versions = store.resumeVersions.filter((item) => item.resume_family_id === family.id);
  const base = versions.find((item) => item.id === family.base_version_id)
    ?? versions.find((item) => item.current_role === "base")
    ?? null;
  return { family, base, derived: versions.filter((item) => item.id !== base?.id) };
}));
const unresolvedIssues = computed(() => store.issues.filter((item) => !["resolved", "dismissed"].includes(item.status)));

onMounted(async () => {
  await store.load();
  for (const issue of store.issues) {
    const changeSet = issue.change_sets.find((item) => item.status === "draft");
    if (changeSet) issueChanges[issue.id] = JSON.stringify(changeSet.changes, null, 2);
  }
  const firstBase = store.resumeVersions.find((item) => item.current_role === "base") ?? store.resumeVersions[0];
  analysisResumeId.value = firstBase?.id ?? "";
  contextResumeId.value = firstBase?.id ?? "";
});

watch([contextResumeId, contextView], () => {
  if (activeTab.value === "context") void loadContext();
});
watch(activeTab, (tab) => {
  if (tab === "context" && contextResumeId.value) void loadContext();
});

const versionLabel = (versionId: string | null) => {
  const version = store.resumeVersions.find((item) => item.id === versionId);
  if (!version) return "未关联";
  return `${version.name}${version.current_role === "base" ? "（基础）" : "（派生）"}`;
};

const relationLabels = (resumeVersionIds: string[], appliesToAll: boolean) => {
  if (appliesToAll) return ["全部简历"];
  return resumeVersionIds.map((id) => versionLabel(id));
};

const uploadPdf = async () => {
  const filePath = await chooseFile({ title: "选择简历 PDF", filters: [{ name: "PDF", extensions: ["pdf"] }] });
  if (!filePath) return;
  try {
    await store.importPdfResume(filePath);
    const newest = store.resumeVersions.find((item) => item.source_id && store.sources.find((source) => source.id === item.source_id)?.file_path === filePath)
      ?? store.resumeVersions[0];
    analysisResumeId.value = newest?.id ?? analysisResumeId.value;
    ElMessage.success("已建立资料源、基础简历和简历组；AI 分析由你手动启动");
  } catch {
    ElMessage.error(store.error ?? "PDF 导入失败");
  }
};

const openContent = (source: ProfileSource) => {
  selectedSource.value = source;
  editableText.value = source.editable_text || source.recognized_text || source.raw_text;
  normalizedMarkdown.value = source.normalized_markdown || "";
  contentDrawerOpen.value = true;
};

const saveEditable = async () => {
  if (!selectedSource.value || !editableText.value.trim()) return ElMessage.warning("识别稿不能为空");
  try {
    await store.saveEditableContent(selectedSource.value, editableText.value);
    selectedSource.value = store.sources.find((item) => item.id === selectedSource.value?.id) ?? null;
    ElMessage.success("识别稿已保存，依赖旧内容的结果会显示为需更新");
  } catch {
    ElMessage.error(store.error ?? "识别稿保存失败");
  }
};

const saveNormalized = async () => {
  if (!selectedSource.value || !normalizedMarkdown.value.trim()) return ElMessage.warning("清洗稿不能为空");
  try {
    await store.saveNormalizedMarkdown(selectedSource.value, normalizedMarkdown.value);
    ElMessage.success("清洗稿已保存");
  } catch {
    ElMessage.error(store.error ?? "清洗稿保存失败");
  }
};

const deleteSource = async (source: ProfileSource) => {
  if (source.resume_version_id) {
    const version = store.resumeVersions.find((item) => item.id === source.resume_version_id);
    if (version) {
      contentDrawerOpen.value = false;
      await openDeleteResume(version);
      return;
    }
  }
  try {
    await ElMessageBox.confirm(`确认删除资料“${source.title}”？`, "删除识别稿与清洗稿", { type: "warning" });
    await store.removeSource(source.id);
    contentDrawerOpen.value = false;
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(store.error ?? "资料删除失败");
  }
};

const runAnalysis = async (executionPath: "structured" | "codex_workspace") => {
  if (!analysisResumeId.value) return ElMessage.warning("请选择具体简历");
  if (!selectedOperations.value.length) return ElMessage.warning("请至少选择一项分析");
  if (executionPath === "codex_workspace" && !supportsCodexWorkspace) return ElMessage.error("Codex 对话工作台只在桌面端可用");
  try {
    const run = await store.startResumeAnalysis(analysisResumeId.value, selectedOperations.value, executionPath);
    if (!run) return;
    if (executionPath === "codex_workspace") {
      await router.push({
        name: "fine-job-codex",
        query: {
          task: "resume-analysis-v3",
          profile_id: run.profile_id,
          resume_family_id: run.resume_family_id,
          resume_version_id: run.resume_version_id,
          run_id: run.id,
          source_ids: run.source_ids.join(","),
          operation_ids: run.operation_ids.join(",")
        }
      });
      return;
    }
    ElMessage.success("分析已结束：正式结果进入对应页面，疑点进入待处理");
  } catch {
    ElMessage.error(store.error ?? "简历分析失败");
  }
};

const retryAnalysis = async () => {
  try {
    const run = await store.retryResumeAnalysis();
    if (run?.execution_path === "codex_workspace") {
      await router.push({ name: "fine-job-codex", query: { task: "resume-analysis-v3", run_id: run.id } });
    }
  } catch {
    ElMessage.error(store.error ?? "重试分析失败");
  }
};

const openFact = (fact: ProfileFact | null = null) => {
  selectedFact.value = fact;
  Object.assign(factForm, fact ? {
    domain: fact.domain,
    entity_type: fact.entity_type,
    entity_id: fact.entity_id,
    field_key: fact.field_key,
    value_text: typeof fact.value === "string" ? fact.value : JSON.stringify(fact.value, null, 2),
    external_use: fact.external_use,
    applies_to_all_resumes: fact.applies_to_all_resumes,
    resume_version_ids: [...fact.resume_version_ids]
  } : {
    domain: "basic", entity_type: "candidate", entity_id: "candidate", field_key: "", value_text: "",
    external_use: "prohibited", applies_to_all_resumes: false,
    resume_version_ids: analysisResumeId.value ? [analysisResumeId.value] : []
  });
  factDialogOpen.value = true;
};

const saveFact = async () => {
  if (!factForm.field_key.trim() || !factForm.value_text.trim()) return ElMessage.warning("请填写字段和值");
  if (!factForm.applies_to_all_resumes && !factForm.resume_version_ids.length) return ElMessage.warning("请选择关联简历，或明确设为全部简历");
  const firstVersion = store.resumeVersions.find((item) => item.id === factForm.resume_version_ids[0]);
  try {
    await store.saveFact(selectedFact.value, {
      scope_type: firstVersion?.resume_family_id ? "resume_family" : "general",
      scope_id: firstVersion?.resume_family_id ?? null,
      domain: factForm.domain,
      entity_type: factForm.entity_type,
      entity_id: factForm.entity_id,
      field_key: factForm.field_key,
      value: parseValue(factForm.value_text),
      source_type: selectedFact.value?.source_type ?? "manual",
      status: "confirmed",
      confidence: 1,
      external_use: factForm.external_use,
      sensitivity: selectedFact.value?.sensitivity ?? "normal",
      confirmed_by: "user",
      applies_to_all_resumes: factForm.applies_to_all_resumes,
      resume_version_ids: factForm.applies_to_all_resumes ? [] : factForm.resume_version_ids
    });
    factDialogOpen.value = false;
  } catch {
    ElMessage.error(store.error ?? "事实保存失败");
  }
};

const removeFact = async (fact: ProfileFact) => {
  try {
    await ElMessageBox.confirm(`确认删除事实“${fact.field_key}”？`, "删除正式事实", { type: "warning" });
    await store.removeFact(fact.id);
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(store.error ?? "事实删除失败");
  }
};

const openQuestion = (question: ProfileQuestion | null = null) => {
  selectedQuestion.value = question;
  Object.assign(questionForm, question ? {
    question_key: question.question_key,
    question_text: question.question_text,
    reason: question.reason,
    final_answer: typeof question.final_answer === "string" ? question.final_answer : JSON.stringify(question.final_answer ?? ""),
    required_stage: question.required_stage,
    priority: question.priority,
    external_use: question.external_use,
    applies_to_all_resumes: question.applies_to_all_resumes,
    resume_version_ids: [...question.resume_version_ids]
  } : {
    question_key: "", question_text: "", reason: "", final_answer: "", required_stage: "chat",
    priority: "medium", external_use: "prohibited", applies_to_all_resumes: false,
    resume_version_ids: analysisResumeId.value ? [analysisResumeId.value] : []
  });
  questionDialogOpen.value = true;
};

const saveQuestion = async () => {
  if (!questionForm.question_key.trim() || !questionForm.question_text.trim() || !questionForm.final_answer.trim()) {
    return ElMessage.warning("正式 QA 需要问题标识、问题和已确认回答");
  }
  if (!questionForm.applies_to_all_resumes && !questionForm.resume_version_ids.length) return ElMessage.warning("请选择关联简历，或明确设为全部简历");
  const firstVersion = store.resumeVersions.find((item) => item.id === questionForm.resume_version_ids[0]);
  try {
    await store.saveQuestion(selectedQuestion.value, {
      scope_type: firstVersion?.resume_family_id ? "resume_family" : "general",
      scope_id: firstVersion?.resume_family_id ?? null,
      question_key: questionForm.question_key,
      question_text: questionForm.question_text,
      reason: questionForm.reason,
      origin: selectedQuestion.value?.origin ?? "user",
      answer_type: selectedQuestion.value?.answer_type ?? "text",
      required_stage: questionForm.required_stage,
      priority: questionForm.priority,
      proposed_answer: null,
      final_answer: questionForm.final_answer,
      status: "confirmed",
      external_use: questionForm.external_use,
      enabled: true,
      confirmed_by: "user",
      applies_to_all_resumes: questionForm.applies_to_all_resumes,
      resume_version_ids: questionForm.applies_to_all_resumes ? [] : questionForm.resume_version_ids
    });
    questionDialogOpen.value = false;
  } catch {
    ElMessage.error(store.error ?? "QA 保存失败");
  }
};

const removeQuestion = async (question: ProfileQuestion) => {
  try {
    await ElMessageBox.confirm(`确认删除 QA“${question.question_text}”？`, "删除正式 QA", { type: "warning" });
    await store.removeQuestion(question.id);
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(store.error ?? "QA 删除失败");
  }
};

const openQAHistory = async (question: ProfileQuestion) => {
  if (!store.selectedProfile) return;
  qaHistoryQuestion.value = question;
  qaRevisions.value = (
    await profileApi.listQuestionRevisions(store.selectedProfile.id, question.id)
  ).revisions;
  qaHistoryDialogOpen.value = true;
};

const openQAOrganize = (question: ProfileQuestion) => {
  qaAiQuestion.value = question;
  qaAiForm.resume_version_id = question.resume_version_ids[0]
    || analysisResumeId.value
    || store.resumeVersions[0]?.id
    || "";
  qaAiForm.instructions = "";
  qaAiDialogOpen.value = true;
};

const generateQAAnswerPreview = async () => {
  if (!store.selectedProfile || !qaAiQuestion.value || !qaAiForm.resume_version_id) {
    return ElMessage.warning("请选择用于整理答案的具体简历");
  }
  try {
    const preview = await profileApi.previewQuestionAnswer(
      store.selectedProfile.id,
      qaAiQuestion.value.id,
      qaAiForm
    );
    const question = qaAiQuestion.value;
    qaAiDialogOpen.value = false;
    openQuestion(question);
    questionForm.final_answer = preview.answer;
    ElMessage.info("AI 整理结果已放入编辑框，保存后才会替换正式答案");
  } catch {
    ElMessage.error(store.error ?? "AI 整理 QA 失败");
  }
};

const openTemplate = (template: ProfileQATemplate | null = null) => {
  selectedTemplate.value = template;
  Object.assign(templateForm, template ? {
    question_key: template.question_key,
    question_text: template.question_text,
    reason: template.reason,
    answer_type: template.answer_type,
    required_stage: template.required_stage,
    priority: template.priority,
    writes_to_field: template.writes_to_field ?? "",
    enabled: template.enabled,
    sort_order: template.sort_order
  } : {
    question_key: "", question_text: "", reason: "", answer_type: "text", required_stage: "chat",
    priority: "medium", writes_to_field: "", enabled: true, sort_order: store.qaTemplates.length
  });
  templateDialogOpen.value = true;
};

const saveTemplate = async () => {
  if (!templateForm.question_key.trim() || !templateForm.question_text.trim()) return ElMessage.warning("请填写模板标识和问题");
  try {
    await store.saveQATemplate(selectedTemplate.value?.id ?? null, { ...templateForm, writes_to_field: templateForm.writes_to_field || null });
    templateDialogOpen.value = false;
  } catch {
    ElMessage.error(store.error ?? "QA 模板保存失败");
  }
};

const answerIssue = async (issue: ProfileIssue) => {
  const answer = issueAnswers[issue.id]?.trim();
  if (!answer) return ElMessage.warning("请先填写回答");
  try {
    await store.answerIssue(issue.id, answer);
    const updated = store.issues.find((item) => item.id === issue.id);
    const changeSet = updated?.change_sets.find((item) => item.status === "draft");
    if (changeSet) issueChanges[issue.id] = JSON.stringify(changeSet.changes, null, 2);
    ElMessage.success("原始回答已保存，AI 已整理为待确认变更");
  } catch {
    ElMessage.error(store.error ?? "回答整理失败");
  }
};

const applyIssue = async (issue: ProfileIssue) => {
  try {
    const changes = JSON.parse(issueChanges[issue.id] || "{}");
    await store.updateIssueChangeSet(issue.id, changes);
    await store.applyIssue(issue.id);
    ElMessage.success("变更已应用到正式事实或 QA");
  } catch (value) {
    ElMessage.error(value instanceof SyntaxError ? "变更预览必须是有效 JSON" : (store.error ?? "变更应用失败"));
  }
};

const openNewDerived = () => {
  editingResume.value = null;
  const family = store.resumeFamilies[0];
  const parent = store.resumeVersions.find((item) => item.id === family?.base_version_id);
  Object.assign(resumeForm, {
    resume_family_id: family?.id ?? "",
    name: "",
    parent_version_id: parent?.id ?? "",
    target_job_id: "",
    derived_reason: "",
    content: parent?.content ?? ""
  });
  resumeDialogOpen.value = true;
};

const openEditResume = (version: ProfileResumeVersion) => {
  editingResume.value = version;
  Object.assign(resumeForm, {
    resume_family_id: version.resume_family_id ?? "",
    name: version.name,
    parent_version_id: version.parent_version_id ?? "",
    target_job_id: version.target_job_id ?? "",
    derived_reason: version.derived_reason,
    content: version.content
  });
  resumeDialogOpen.value = true;
};

const saveResume = async () => {
  const family = store.resumeFamilies.find((item) => item.id === resumeForm.resume_family_id);
  if (!family || !resumeForm.name.trim()) return ElMessage.warning("请选择简历组并填写版本名称");
  const current = editingResume.value;
  try {
    const derivedFromId = current?.derived_from_version_id ?? resumeForm.parent_version_id ?? family.base_version_id;
    const payload = {
      resume_family_id: family.id,
      name: resumeForm.name,
      parent_version_id: current?.current_role === "base" ? current.parent_version_id : (resumeForm.parent_version_id || family.base_version_id),
      version_type: current?.version_type ?? "manual_variant",
      current_role: current?.current_role ?? "derived",
      origin_type: current?.origin_type ?? "manual_copy",
      derived_from_version_id: derivedFromId,
      target_job_id: resumeForm.target_job_id || null,
      target_job_snapshot: current?.target_job_snapshot ?? {},
      derived_reason: resumeForm.derived_reason,
      based_on_content_version: family.content_version,
      role_family: family.target_role_family,
      source_id: current?.source_id ?? null,
      campaign_id: null,
      content: resumeForm.content,
      fact_ids: current?.fact_ids ?? [],
      is_default: false,
      ...(current ? { expected_content_version: current.content_version } : {})
    };
    if (current) await store.updateResumeVersion(current.id, payload);
    else await store.addResumeVersion(payload);
    resumeDialogOpen.value = false;
  } catch {
    ElMessage.error(store.error ?? "简历版本保存失败");
  }
};

const openDerivedUpload = async () => {
  const filePath = await chooseFile({ title: "选择派生简历 PDF", filters: [{ name: "PDF", extensions: ["pdf"] }] });
  if (!filePath) return;
  Object.assign(derivedUploadForm, { file_path: filePath, resume_family_id: "", name: "", derived_reason: "" });
  derivedUploadDialogOpen.value = true;
};

const saveDerivedUpload = async () => {
  if (!derivedUploadForm.resume_family_id) return ElMessage.warning("请选择所属简历组");
  try {
    await store.importDerivedPdfResume(
      derivedUploadForm.resume_family_id,
      derivedUploadForm.file_path,
      derivedUploadForm.name,
      derivedUploadForm.derived_reason
    );
    derivedUploadDialogOpen.value = false;
  } catch {
    ElMessage.error(store.error ?? "派生简历上传失败");
  }
};

const openAIDerived = () => {
  const source = store.resumeVersions.find((item) => item.current_role === "base") ?? store.resumeVersions[0];
  Object.assign(aiDerivedForm, {
    source_resume_version_id: source?.id ?? "",
    target_job_id: "",
    job_title: "",
    jd_text: "",
    instructions: "",
    name: "",
    derived_reason: "",
    content: ""
  });
  aiDerivedDialogOpen.value = true;
};

const generateAIDerivedPreview = async () => {
  if (!aiDerivedForm.source_resume_version_id || !aiDerivedForm.jd_text.trim()) {
    return ElMessage.warning("请选择来源简历并填写 JD");
  }
  try {
    const preview = await store.previewAIDerivedResume({
      source_resume_version_id: aiDerivedForm.source_resume_version_id,
      target_job_id: aiDerivedForm.target_job_id || null,
      target_job_snapshot: { title: aiDerivedForm.job_title.trim(), jd: aiDerivedForm.jd_text.trim() },
      jd_text: aiDerivedForm.jd_text,
      instructions: aiDerivedForm.instructions
    });
    if (!preview) return;
    aiDerivedForm.name = preview.suggested_name;
    aiDerivedForm.derived_reason = preview.derived_reason;
    aiDerivedForm.content = preview.content;
    ElMessage.success("AI 派生预览已生成，请编辑确认后保存");
  } catch {
    ElMessage.error(store.error ?? "AI 派生预览生成失败");
  }
};

const saveAIDerivedResume = async () => {
  const source = store.resumeVersions.find((item) => item.id === aiDerivedForm.source_resume_version_id);
  const family = store.resumeFamilies.find((item) => item.id === source?.resume_family_id);
  if (!source || !family || !aiDerivedForm.name.trim() || !aiDerivedForm.content.trim()) {
    return ElMessage.warning("请先生成预览并填写版本名称和内容");
  }
  try {
    await store.addResumeVersion({
      resume_family_id: family.id,
      name: aiDerivedForm.name.trim(),
      parent_version_id: source.id,
      version_type: "jd_tailored",
      current_role: "derived",
      origin_type: "ai_derived",
      derived_from_version_id: source.id,
      target_job_id: aiDerivedForm.target_job_id || null,
      target_job_snapshot: { title: aiDerivedForm.job_title.trim(), jd: aiDerivedForm.jd_text.trim() },
      derived_reason: aiDerivedForm.derived_reason.trim(),
      based_on_content_version: family.content_version,
      role_family: family.target_role_family,
      source_id: null,
      campaign_id: null,
      content: aiDerivedForm.content,
      fact_ids: [],
      is_default: false
    });
    aiDerivedDialogOpen.value = false;
    ElMessage.success("AI 派生简历已保存为草稿");
  } catch {
    ElMessage.error(store.error ?? "AI 派生简历保存失败");
  }
};

const setAsBaseResume = async (version: ProfileResumeVersion) => {
  try {
    await ElMessageBox.confirm(`将“${version.name}”设为基础简历？原基础简历会保留为派生版本。`, "设置基础简历", { type: "warning" });
    await store.setResumeVersionAsBase(version.id);
  } catch (value) {
    if (value !== "cancel" && value !== "close") ElMessage.error(store.error ?? "基础简历设置失败");
  }
};

const confirmResume = async (version: ProfileResumeVersion) => {
  try {
    await store.confirmResumeVersion(version.id);
    ElMessage.success("简历草稿已确认");
  } catch {
    ElMessage.error(store.error ?? "简历确认失败");
  }
};

const openResumeCompare = (version: ProfileResumeVersion, mode: "base" | "source") => {
  const family = store.resumeFamilies.find((item) => item.id === version.resume_family_id);
  const sourceId = mode === "source"
    ? (version.derived_from_version_id || version.parent_version_id)
    : family?.base_version_id;
  compareTarget.value = version;
  compareSource.value = store.resumeVersions.find((item) => item.id === sourceId) ?? null;
  compareDialogOpen.value = true;
};

async function openDeleteResume(version: ProfileResumeVersion) {
  if (!store.selectedProfile) return;
  try {
    deletingResume.value = version;
    deleteImpact.value = await profileApi.getResumeDeleteImpact(store.selectedProfile.id, version.id);
    deleteForm.action = deleteImpact.value.is_base && deleteImpact.value.derived_versions.length
      ? "promote_then_delete"
      : "delete_version";
    deleteForm.promote_resume_version_id = String(deleteImpact.value.derived_versions[0]?.id ?? "");
    deleteForm.profile_data_action = "move_to_pending";
    deleteDialogOpen.value = true;
  } catch {
    ElMessage.error(store.error ?? "无法读取删除影响");
  }
}

const confirmDeleteResume = async () => {
  if (!deletingResume.value) return;
  if (deleteForm.action === "promote_then_delete" && !deleteForm.promote_resume_version_id) return ElMessage.warning("请选择接任的基础简历");
  try {
    await store.removeResumeVersionV3(deletingResume.value.id, {
      action: deleteForm.action,
      promote_resume_version_id: deleteForm.promote_resume_version_id || null,
      profile_data_action: deleteForm.profile_data_action
    });
    deleteDialogOpen.value = false;
    ElMessage.success(deleteForm.profile_data_action === "delete" ? "简历及专属资料已删除" : "简历已删除，未删除的专属资料已转入待处理");
  } catch {
    ElMessage.error(store.error ?? "简历删除失败");
  }
};

const loadContext = async () => {
  if (!contextResumeId.value) return;
  try {
    await store.loadContextHead(contextResumeId.value, contextView.value);
    contextText.value = store.contextHead?.draft_revision?.content
      ?? store.contextHead?.current_revision?.content
      ?? "";
  } catch {
    ElMessage.error(store.error ?? "上下文读取失败");
  }
};

const regenerateContext = async () => {
  if (!contextResumeId.value) return ElMessage.warning("请选择具体简历");
  try {
    await store.regenerateContext(contextResumeId.value, contextView.value);
    contextText.value = store.contextHead?.draft_revision?.content ?? "";
    ElMessage.success("已生成新草稿，当前已保存版本未被覆盖");
  } catch {
    ElMessage.error(store.error ?? "上下文重新生成失败");
  }
};

const saveContext = async () => {
  if (!contextResumeId.value || !contextText.value.trim()) return ElMessage.warning("上下文内容不能为空");
  try {
    await store.saveContextContent(contextResumeId.value, contextView.value, contextText.value);
    contextText.value = store.contextHead?.current_revision?.content ?? contextText.value;
    ElMessage.success("该用途的上下文已保存为当前版本");
  } catch {
    ElMessage.error(store.error ?? "上下文保存失败");
  }
};

const restoreContext = async (revisionId: string) => {
  if (!contextResumeId.value) return;
  await store.restoreContextRevision(contextResumeId.value, contextView.value, revisionId);
  contextText.value = store.contextHead?.current_revision?.content ?? "";
};

const parseValue = (value: string) => {
  const text = value.trim();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
};
const formatValue = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const operationLabel = (id: string) => operationOptions.find((item) => item.id === id)?.label ?? id;
const statusLabel = (status: string) => ({
  queued: "等待", running: "执行中", organizing: "AI 整理中", awaiting_confirmation: "待确认变更",
  succeeded: "完成", failed: "失败", blocked: "依赖阻塞", cancelled: "已取消", completed: "全部完成",
  partial_failed: "部分失败", stale: "需更新", confirmed: "已确认", pending: "待回答",
  resolved: "已解决", dismissed: "已忽略"
}[status] ?? status);
</script>

<template>
  <section class="page-stack fine-job-page profile-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">求职资料 V3</p>
        <h1>简历分析与资料管理</h1>
        <p class="secondary-text">资料、具体简历、正式事实、QA、策略和上下文按版本贯通；页面中不再存在全局“当前简历组”。</p>
      </div>
      <div class="card-actions">
        <el-button :disabled="!supportsFilePicker" type="primary" @click="uploadPdf">上传基础简历 PDF</el-button>
        <el-button :loading="store.loading" @click="store.load()">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="store.error" type="error" title="求职资料操作失败" :description="store.error" show-icon />

    <section class="page-panel" v-loading="store.loading">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="识别稿与清洗稿" name="content">
          <el-table :data="store.sources">
            <el-table-column prop="title" label="资料名称" min-width="220" />
            <el-table-column label="关联简历" min-width="220"><template #default="{ row }">{{ versionLabel(row.resume_version_id) }}</template></el-table-column>
            <el-table-column prop="recognizer_name" label="识别方式" width="140" />
            <el-table-column prop="source_version" label="内容版本" width="100" />
            <el-table-column prop="status" label="状态" width="110" />
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button link type="primary" @click="openContent(row)">编辑</el-button>
                <el-button link type="danger" @click="deleteSource(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!store.sources.length" description="上传 PDF 后会立即建立识别稿、基础简历和简历组" />
        </el-tab-pane>

        <el-tab-pane label="AI 分析" name="analysis">
          <div class="analysis-target">
            <span>本次分析的具体简历</span>
            <el-select v-model="analysisResumeId" style="width: 360px" placeholder="请选择具体简历">
              <el-option v-for="version in store.resumeVersions" :key="version.id" :label="versionLabel(version.id)" :value="version.id" />
            </el-select>
          </div>
          <div class="operation-grid">
            <label v-for="option in operationOptions" :key="option.id" class="operation-card">
              <el-checkbox v-model="selectedOperations" :value="option.id"><strong>{{ option.label }}</strong></el-checkbox>
              <span>{{ option.description }}</span>
            </label>
          </div>
          <div class="tab-toolbar">
            <el-button type="success" :loading="store.analyzing" @click="runAnalysis('structured')">执行所选分析</el-button>
            <el-button type="primary" :disabled="!supportsCodexWorkspace" @click="runAnalysis('codex_workspace')">在 Codex 对话窗口分析</el-button>
            <el-button v-if="store.resumeAnalysisRun && ['queued', 'running'].includes(store.resumeAnalysisRun.status)" type="danger" plain @click="store.cancelResumeAnalysis()">取消</el-button>
            <el-button v-if="store.resumeAnalysisRun && ['failed', 'partial_failed', 'cancelled'].includes(store.resumeAnalysisRun.status)" @click="retryAnalysis">重试失败节点</el-button>
          </div>
          <el-alert type="info" :closable="false" title="上传只建立资料关系，不会自动执行完整 AI 分析。再次生成策略会先进入策略管理的变更预览。" />
          <div v-if="store.resumeAnalysisRun" class="run-panel">
            <div class="run-heading"><strong>最近运行</strong><el-tag>{{ statusLabel(store.resumeAnalysisRun.status) }}</el-tag><span>{{ versionLabel(store.resumeAnalysisRun.resume_version_id) }}</span></div>
            <div v-for="node in store.resumeAnalysisRun.operations" :key="node.id" class="node-row">
              <span>{{ node.sequence_no }}. {{ operationLabel(node.operation_id) }}</span>
              <el-tag :type="node.status === 'succeeded' ? 'success' : node.status === 'failed' ? 'danger' : 'info'">{{ statusLabel(node.status) }}</el-tag>
              <span class="node-error">{{ node.error_message || '' }}</span>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="`待处理 (${unresolvedIssues.length})`" name="issues">
          <article v-for="issue in store.issues" :key="issue.id" class="issue-card">
            <div class="run-heading"><strong>{{ issue.title }}</strong><el-tag>{{ statusLabel(issue.status) }}</el-tag><span class="secondary-text">{{ versionLabel(issue.resume_version_id) }}</span></div>
            <p>{{ issue.description }}</p>
            <blockquote v-if="issue.source_excerpt">{{ issue.source_excerpt }}</blockquote>
            <template v-if="issue.status === 'pending'">
              <el-input v-model="issueAnswers[issue.id]" type="textarea" :rows="4" placeholder="填写你的原始回答；保存后 AI 会整理成事实或 QA 变更供你确认" />
              <div class="card-actions issue-actions"><el-button type="primary" :loading="store.saving" @click="answerIssue(issue)">保存回答并让 AI 整理</el-button><el-button @click="store.setIssueStatus(issue.id, 'dismissed')">忽略</el-button></div>
            </template>
            <template v-else-if="issue.status === 'awaiting_confirmation'">
              <el-alert type="warning" :closable="false" title="请核对 AI 整理结果；只有点击确认应用后才会修改正式资料。" />
              <el-input v-model="issueChanges[issue.id]" type="textarea" :rows="12" placeholder="变更预览 JSON" />
              <div class="card-actions issue-actions"><el-button type="success" @click="applyIssue(issue)">确认并应用</el-button><el-button @click="store.setIssueStatus(issue.id, 'pending')">重新回答</el-button></div>
            </template>
            <div v-else-if="issue.status === 'dismissed'" class="card-actions"><el-button link @click="store.setIssueStatus(issue.id, 'pending')">重新打开</el-button></div>
          </article>
          <el-empty v-if="!store.issues.length" description="没有疑点、冲突或缺失信息" />
        </el-tab-pane>

        <el-tab-pane :label="`正式事实 (${store.facts.length})`" name="facts">
          <div class="tab-toolbar"><el-button type="primary" @click="openFact()">新建事实</el-button></div>
          <el-table :data="store.facts">
            <el-table-column prop="field_key" label="字段" min-width="150" />
            <el-table-column label="值" min-width="260"><template #default="{ row }">{{ formatValue(row.value) }}</template></el-table-column>
            <el-table-column label="关联具体简历" min-width="280"><template #default="{ row }"><el-tag v-for="label in relationLabels(row.resume_version_ids, row.applies_to_all_resumes)" :key="label" class="relation-tag">{{ label }}</el-tag></template></el-table-column>
            <el-table-column label="披露级别" width="130"><template #default="{ row }">{{ externalUseLabel(row.external_use) }}</template></el-table-column>
            <el-table-column label="操作" width="130"><template #default="{ row }"><el-button link type="primary" @click="openFact(row)">编辑</el-button><el-button link type="danger" @click="removeFact(row)">删除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`动态 QA (${store.questions.length})`" name="qa">
          <div class="tab-toolbar"><el-button type="primary" @click="openQuestion()">新建正式 QA</el-button><el-button @click="openTemplate()">新建提取模板</el-button></div>
          <h3>正式 QA</h3>
          <el-table :data="store.questions">
            <el-table-column prop="question_text" label="问题" min-width="240" />
            <el-table-column label="唯一正式回答" min-width="250"><template #default="{ row }">{{ formatValue(row.final_answer) }}</template></el-table-column>
            <el-table-column label="关联具体简历" min-width="260"><template #default="{ row }"><el-tag v-for="label in relationLabels(row.resume_version_ids, row.applies_to_all_resumes)" :key="label" class="relation-tag">{{ label }}</el-tag></template></el-table-column>
            <el-table-column label="操作" min-width="250"><template #default="{ row }"><el-button link type="primary" @click="openQuestion(row)">编辑</el-button><el-button link @click="openQAOrganize(row)">AI 整理</el-button><el-button link @click="openQAHistory(row)">修订历史</el-button><el-button link type="danger" @click="removeQuestion(row)">删除</el-button></template></el-table-column>
          </el-table>
          <h3 class="section-title">QA 提取模板</h3>
          <p class="secondary-text">模板只指导 AI 找哪些问题，不会作为一条没有答案的正式 QA。</p>
          <el-table :data="store.qaTemplates">
            <el-table-column prop="question_text" label="模板问题" min-width="260" />
            <el-table-column prop="required_stage" label="使用阶段" width="120" />
            <el-table-column prop="priority" label="优先级" width="100" />
            <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.enabled ? '启用' : '停用' }}</template></el-table-column>
            <el-table-column label="操作" width="130"><template #default="{ row }"><el-button link type="primary" @click="openTemplate(row)">编辑</el-button><el-button link type="danger" @click="store.removeQATemplate(row.id)">删除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="简历派生版本" name="versions">
          <div class="tab-toolbar"><el-button type="primary" @click="openAIDerived">AI 生成派生简历</el-button><el-button @click="openNewDerived">从现有简历复制派生</el-button><el-button :disabled="!supportsFilePicker" @click="openDerivedUpload">上传派生简历</el-button></div>
          <article v-for="section in resumeFamilySections" :key="section.family.id" class="resume-family-card">
            <div class="resume-family-heading">
              <div><span class="secondary-text">简历组：{{ section.family.name }}</span><h3>{{ section.base?.name || '缺少基础简历' }}</h3></div>
              <div v-if="section.base" class="card-actions"><el-tag type="success">基础简历</el-tag><el-button link type="primary" @click="openEditResume(section.base)">查看/编辑</el-button><el-button link type="danger" @click="openDeleteResume(section.base)">删除</el-button></div>
            </div>
            <el-table :data="section.derived">
              <el-table-column prop="name" label="派生简历" min-width="190" />
              <el-table-column label="来源" width="130"><template #default="{ row }">{{ row.origin_type === 'ai_derived' ? 'AI 派生' : row.origin_type === 'upload_derived' ? '上传' : '手动复制' }}</template></el-table-column>
              <el-table-column prop="derived_reason" label="派生原因" min-width="220" />
              <el-table-column prop="status" label="状态" width="100" />
              <el-table-column label="操作" min-width="390"><template #default="{ row }"><el-button link type="primary" @click="openEditResume(row)">查看/编辑</el-button><el-button v-if="row.status === 'draft'" link type="success" @click="confirmResume(row)">确认</el-button><el-button link @click="openResumeCompare(row, 'base')">与基础对比</el-button><el-button link @click="openResumeCompare(row, 'source')">与来源对比</el-button><el-button link type="primary" @click="setAsBaseResume(row)">设为基础</el-button><el-button link type="danger" @click="openDeleteResume(row)">删除</el-button></template></el-table-column>
            </el-table>
            <el-empty v-if="!section.derived.length" description="暂无派生简历" :image-size="60" />
          </article>
          <el-empty v-if="!resumeFamilySections.length" description="上传基础简历后自动创建简历组" />
          <el-alert type="info" :closable="false" title="“基础简历”和未来投递默认简历是两个独立概念；投递能力尚未上线，已预留默认投递版本字段。" />
        </el-tab-pane>

        <el-tab-pane label="AI 上下文" name="context">
          <div class="tab-toolbar">
            <el-select v-model="contextResumeId" style="width: 330px" placeholder="选择具体简历"><el-option v-for="version in store.resumeVersions" :key="version.id" :label="versionLabel(version.id)" :value="version.id" /></el-select>
            <el-radio-group v-model="contextView"><el-radio-button value="full">完整</el-radio-button><el-radio-button value="search">岗位搜索</el-radio-button><el-radio-button value="evaluation">岗位评估</el-radio-button><el-radio-button value="chat">沟通</el-radio-button></el-radio-group>
            <el-tag v-if="store.contextHead?.stale" type="warning">当前版本已旧</el-tag>
            <el-tag v-else-if="store.contextHead?.current_revision" type="success">当前版本已保存</el-tag>
          </div>
          <el-alert type="info" :closable="false" title="打开页面只读取已保存内容。没有上下文的业务任务会自动生成并继续；旧上下文会先询问是否重新生成。" />
          <el-input v-model="contextText" type="textarea" :rows="24" class="context-editor" placeholder="当前没有已保存上下文。点击重新生成，或直接编辑后保存。" />
          <div class="tab-toolbar"><el-button @click="loadContext">重新读取</el-button><el-button type="primary" plain :loading="store.saving" @click="regenerateContext">重新生成草稿</el-button><el-button type="success" :loading="store.saving" @click="saveContext">保存为当前版本</el-button></div>
          <div v-if="store.contextHead?.history.length" class="context-history"><h3>历史版本</h3><div v-for="revision in store.contextHead.history" :key="revision.id" class="history-row"><span>v{{ revision.revision }} · {{ revision.source_type }} · {{ revision.updated_at }}</span><el-button link @click="restoreContext(revision.id)">恢复为当前版本</el-button></div></div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-drawer v-model="contentDrawerOpen" :title="selectedSource?.title || '编辑简历内容'" size="760px">
      <div class="drawer-heading"><span>{{ versionLabel(selectedSource?.resume_version_id ?? null) }}</span><el-button v-if="selectedSource" type="danger" plain @click="deleteSource(selectedSource)">删除资料</el-button></div>
      <h3>可编辑识别稿</h3><p class="secondary-text">这是识别后的原始依据。保存不会自动执行 AI 分析。</p>
      <el-input v-model="editableText" type="textarea" :rows="16" /><div class="drawer-actions"><el-button type="primary" :loading="store.saving" @click="saveEditable">保存识别稿</el-button></div>
      <h3>AI 清洗稿</h3><p class="secondary-text">可由“内容清洗”生成，也可手工编辑保存。</p>
      <el-input v-model="normalizedMarkdown" type="textarea" :rows="16" /><div class="drawer-actions"><el-button type="primary" :loading="store.saving" @click="saveNormalized">保存清洗稿</el-button></div>
    </el-drawer>

    <el-dialog v-model="factDialogOpen" :title="selectedFact ? '编辑正式事实' : '新建正式事实'" width="720px">
      <el-form label-position="top"><div class="form-grid"><el-form-item label="领域"><el-input v-model="factForm.domain" /></el-form-item><el-form-item label="字段"><el-input v-model="factForm.field_key" /></el-form-item><el-form-item label="实体类型"><el-input v-model="factForm.entity_type" /></el-form-item><el-form-item label="实体标识"><el-input v-model="factForm.entity_id" /></el-form-item></div><el-form-item label="值（文本或 JSON）"><el-input v-model="factForm.value_text" type="textarea" :rows="6" /></el-form-item><el-form-item label="关联具体简历"><el-select v-model="factForm.resume_version_ids" multiple :disabled="factForm.applies_to_all_resumes" style="width: 100%"><el-option v-for="version in store.resumeVersions" :key="version.id" :label="versionLabel(version.id)" :value="version.id" /></el-select></el-form-item><el-form-item><el-checkbox v-model="factForm.applies_to_all_resumes">由用户明确设为适用于全部简历</el-checkbox></el-form-item><el-form-item label="披露级别"><el-select v-model="factForm.external_use"><el-option v-for="option in externalUseOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select></el-form-item></el-form>
      <template #footer><el-button @click="factDialogOpen = false">取消</el-button><el-button type="primary" @click="saveFact">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="questionDialogOpen" :title="selectedQuestion ? '编辑正式 QA' : '新建正式 QA'" width="720px">
      <el-form label-position="top"><div class="form-grid"><el-form-item label="问题标识"><el-input v-model="questionForm.question_key" /></el-form-item><el-form-item label="使用阶段"><el-select v-model="questionForm.required_stage"><el-option label="搜索" value="search" /><el-option label="招呼" value="greeting" /><el-option label="投递" value="application" /><el-option label="沟通" value="chat" /><el-option label="面试" value="interview" /></el-select></el-form-item></div><el-form-item label="问题"><el-input v-model="questionForm.question_text" /></el-form-item><el-form-item label="唯一正式回答"><el-input v-model="questionForm.final_answer" type="textarea" :rows="6" /></el-form-item><el-form-item label="用途说明"><el-input v-model="questionForm.reason" /></el-form-item><el-form-item label="关联具体简历"><el-select v-model="questionForm.resume_version_ids" multiple :disabled="questionForm.applies_to_all_resumes" style="width: 100%"><el-option v-for="version in store.resumeVersions" :key="version.id" :label="versionLabel(version.id)" :value="version.id" /></el-select></el-form-item><el-form-item><el-checkbox v-model="questionForm.applies_to_all_resumes">由用户明确设为适用于全部简历</el-checkbox></el-form-item><el-form-item label="披露级别"><el-select v-model="questionForm.external_use"><el-option label="禁止披露" value="prohibited" /><el-option label="仅摘要" value="summary_only" /><el-option label="允许披露" value="allowed" /></el-select></el-form-item></el-form>
      <template #footer><el-button @click="questionDialogOpen = false">取消</el-button><el-button type="primary" @click="saveQuestion">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="qaAiDialogOpen" :title="`AI 整理：${qaAiQuestion?.question_text || ''}`" width="650px">
      <el-form label-position="top">
        <el-form-item label="使用具体简历">
          <el-select v-model="qaAiForm.resume_version_id" style="width: 100%">
            <el-option v-for="version in store.resumeVersions.filter(item => qaAiQuestion?.applies_to_all_resumes || qaAiQuestion?.resume_version_ids.includes(item.id))" :key="version.id" :label="versionLabel(version.id)" :value="version.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="整理要求"><el-input v-model="qaAiForm.instructions" type="textarea" :rows="4" placeholder="例如：保持事实不变，改成简洁规范回答" /></el-form-item>
        <el-alert type="info" :closable="false" title="生成结果会先进入正式 QA 编辑框，由你保存后才成为当前答案。" />
      </el-form>
      <template #footer><el-button @click="qaAiDialogOpen = false">取消</el-button><el-button type="primary" @click="generateQAAnswerPreview">生成预览</el-button></template>
    </el-dialog>

    <el-dialog v-model="qaHistoryDialogOpen" :title="`答案修订：${qaHistoryQuestion?.question_text || ''}`" width="720px">
      <el-timeline>
        <el-timeline-item v-for="revision in qaRevisions" :key="revision.id" :timestamp="revision.created_at" placement="top">
          <div class="revision-card"><el-tag :type="revision.status === 'current' ? 'success' : 'info'">v{{ revision.revision }} · {{ revision.status === 'current' ? '当前' : '历史' }}</el-tag><span class="secondary-text">{{ revision.source_type }}</span><p>{{ formatValue(revision.answer) }}</p></div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!qaRevisions.length" description="暂无答案修订" />
    </el-dialog>

    <el-dialog v-model="templateDialogOpen" :title="selectedTemplate ? '编辑 QA 提取模板' : '新建 QA 提取模板'" width="650px">
      <el-form label-position="top"><el-form-item label="模板标识"><el-input v-model="templateForm.question_key" /></el-form-item><el-form-item label="需要 AI 寻找的问题"><el-input v-model="templateForm.question_text" /></el-form-item><el-form-item label="用途"><el-input v-model="templateForm.reason" /></el-form-item><div class="form-grid"><el-form-item label="使用阶段"><el-select v-model="templateForm.required_stage"><el-option label="搜索" value="search" /><el-option label="沟通" value="chat" /><el-option label="投递" value="application" /><el-option label="面试" value="interview" /></el-select></el-form-item><el-form-item label="优先级"><el-select v-model="templateForm.priority"><el-option label="高" value="high" /><el-option label="中" value="medium" /><el-option label="低" value="low" /></el-select></el-form-item></div><el-form-item label="可写入字段"><el-input v-model="templateForm.writes_to_field" /></el-form-item><el-form-item><el-switch v-model="templateForm.enabled" active-text="启用" inactive-text="停用" /></el-form-item></el-form>
      <template #footer><el-button @click="templateDialogOpen = false">取消</el-button><el-button type="primary" @click="saveTemplate">保存模板</el-button></template>
    </el-dialog>

    <el-dialog v-model="resumeDialogOpen" :title="editingResume ? '查看/编辑简历版本' : '从现有简历复制派生'" width="760px">
      <el-form label-position="top"><el-form-item label="所属简历组"><el-select v-model="resumeForm.resume_family_id" :disabled="Boolean(editingResume)" style="width: 100%"><el-option v-for="family in store.resumeFamilies" :key="family.id" :label="family.name" :value="family.id" /></el-select></el-form-item><div class="form-grid"><el-form-item label="版本名称"><el-input v-model="resumeForm.name" /></el-form-item><el-form-item label="派生自"><el-select v-model="resumeForm.parent_version_id" :disabled="editingResume?.current_role === 'base'" style="width: 100%"><el-option v-for="version in store.resumeVersions.filter(item => item.resume_family_id === resumeForm.resume_family_id)" :key="version.id" :label="versionLabel(version.id)" :value="version.id" /></el-select></el-form-item></div><el-form-item label="目标岗位标识（可选）"><el-input v-model="resumeForm.target_job_id" /></el-form-item><el-form-item label="派生原因"><el-input v-model="resumeForm.derived_reason" /></el-form-item><el-form-item label="简历内容"><el-input v-model="resumeForm.content" type="textarea" :rows="18" /></el-form-item></el-form>
      <template #footer><el-button @click="resumeDialogOpen = false">取消</el-button><el-button type="primary" @click="saveResume">保存版本</el-button></template>
    </el-dialog>

    <el-dialog v-model="derivedUploadDialogOpen" title="上传派生简历" width="650px">
      <el-form label-position="top"><el-form-item label="文件"><el-input v-model="derivedUploadForm.file_path" disabled /></el-form-item><el-form-item label="明确归入哪个简历组"><el-select v-model="derivedUploadForm.resume_family_id" style="width: 100%"><el-option v-for="family in store.resumeFamilies" :key="family.id" :label="family.name" :value="family.id" /></el-select></el-form-item><el-form-item label="版本名称"><el-input v-model="derivedUploadForm.name" placeholder="默认使用文件名" /></el-form-item><el-form-item label="派生原因"><el-input v-model="derivedUploadForm.derived_reason" type="textarea" :rows="3" /></el-form-item></el-form>
      <template #footer><el-button @click="derivedUploadDialogOpen = false">取消</el-button><el-button type="primary" @click="saveDerivedUpload">上传并关联</el-button></template>
    </el-dialog>

    <el-dialog v-model="aiDerivedDialogOpen" title="AI 生成派生简历" width="860px">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="来源简历"><el-select v-model="aiDerivedForm.source_resume_version_id" style="width: 100%"><el-option v-for="version in store.resumeVersions" :key="version.id" :label="versionLabel(version.id)" :value="version.id" /></el-select></el-form-item>
          <el-form-item label="目标岗位名称"><el-input v-model="aiDerivedForm.job_title" /></el-form-item>
          <el-form-item label="目标岗位标识（可选）"><el-input v-model="aiDerivedForm.target_job_id" /></el-form-item>
          <el-form-item label="版本名称"><el-input v-model="aiDerivedForm.name" placeholder="生成预览后可编辑" /></el-form-item>
        </div>
        <el-form-item label="岗位 JD"><el-input v-model="aiDerivedForm.jd_text" type="textarea" :rows="8" /></el-form-item>
        <el-form-item label="派生要求"><el-input v-model="aiDerivedForm.instructions" type="textarea" :rows="3" placeholder="例如：突出 Agent 工程和交付经验，保持事实不变" /></el-form-item>
        <el-form-item label="派生原因"><el-input v-model="aiDerivedForm.derived_reason" /></el-form-item>
        <el-form-item label="AI 生成预览（保存前可编辑）"><el-input v-model="aiDerivedForm.content" type="textarea" :rows="20" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="aiDerivedDialogOpen = false">取消</el-button><el-button :loading="store.saving" @click="generateAIDerivedPreview">生成/重新生成预览</el-button><el-button type="primary" :disabled="!aiDerivedForm.content.trim()" @click="saveAIDerivedResume">保存为草稿派生版本</el-button></template>
    </el-dialog>

    <el-dialog v-model="compareDialogOpen" :title="`简历对比：${compareSource?.name || '未找到来源'} → ${compareTarget?.name || ''}`" width="1100px">
      <div class="resume-compare-grid">
        <section><h3>{{ compareSource?.name || '来源版本不可用' }}</h3><el-input :model-value="compareSource?.content || ''" type="textarea" :rows="28" readonly /></section>
        <section><h3>{{ compareTarget?.name }}</h3><el-input :model-value="compareTarget?.content || ''" type="textarea" :rows="28" readonly /></section>
      </div>
    </el-dialog>

    <el-dialog v-model="deleteDialogOpen" :title="`删除简历：${deletingResume?.name || ''}`" width="680px">
      <el-alert type="warning" :closable="false" title="删除会同时移除该简历版本及其识别稿/清洗稿。请决定专属事实和 QA 的处理方式。" />
      <el-form label-position="top" class="delete-form">
        <el-form-item v-if="deleteImpact?.is_base && deleteImpact.derived_versions.length" label="基础简历处理"><el-radio-group v-model="deleteForm.action"><el-radio value="promote_then_delete">选择派生简历接任后删除</el-radio><el-radio value="delete_family">删除整个简历组</el-radio></el-radio-group></el-form-item>
        <el-form-item v-if="deleteForm.action === 'promote_then_delete'" label="接任基础简历"><el-select v-model="deleteForm.promote_resume_version_id" style="width: 100%"><el-option v-for="version in deleteImpact?.derived_versions || []" :key="String(version.id)" :label="String(version.name)" :value="String(version.id)" /></el-select></el-form-item>
        <el-form-item label="与被删简历专属的事实和 QA"><el-radio-group v-model="deleteForm.profile_data_action"><el-radio value="delete">一起删除</el-radio><el-radio value="move_to_pending">保留内容并转入待处理，稍后重新关联</el-radio></el-radio-group></el-form-item>
        <p class="secondary-text">专属事实 {{ deleteImpact?.exclusive_fact_ids.length || 0 }} 条，专属 QA {{ deleteImpact?.exclusive_question_ids.length || 0 }} 条；共享资料只移除本简历关联。</p>
      </el-form>
      <template #footer><el-button @click="deleteDialogOpen = false">取消</el-button><el-button type="danger" @click="confirmDeleteResume">确认删除</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.run-heading, .tab-toolbar, .drawer-actions, .analysis-target, .resume-family-heading, .drawer-heading { display: flex; align-items: center; gap: 12px; }
.analysis-target { margin-bottom: 16px; }
.tab-toolbar { margin: 16px 0; flex-wrap: wrap; }
.operation-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }
.operation-card, .issue-card, .run-panel { padding: 14px; border: 1px solid var(--el-border-color-light); border-radius: 10px; }
.operation-card { display: flex; flex-direction: column; gap: 6px; cursor: pointer; }
.operation-card span, .node-error { color: var(--el-text-color-secondary); font-size: 13px; }
.run-panel, .section-title { margin-top: 20px; }
.node-row { display: grid; grid-template-columns: minmax(180px, 1fr) 90px minmax(180px, 2fr); gap: 12px; align-items: center; padding: 10px 0; border-top: 1px solid var(--el-border-color-lighter); }
.issue-card { display: grid; gap: 12px; margin-bottom: 12px; }
.issue-card p, .issue-card blockquote { margin: 0; line-height: 1.6; }
.issue-actions { margin-top: 0; }
.relation-tag { margin: 2px 6px 2px 0; }
.drawer-actions { justify-content: flex-end; margin: 10px 0 24px; }
.drawer-heading, .resume-family-heading { justify-content: space-between; }
.resume-family-card { margin-bottom: 16px; padding: 16px; border: 1px solid var(--el-border-color-light); border-radius: 10px; }
.resume-family-heading { margin-bottom: 12px; }
.resume-family-heading h3 { margin: 4px 0 0; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.context-editor { margin-top: 16px; font-family: var(--app-font-mono, monospace); }
.context-history { margin-top: 20px; }
.history-row { display: flex; justify-content: space-between; align-items: center; padding: 9px 0; border-top: 1px solid var(--el-border-color-lighter); }
.delete-form { margin-top: 16px; }
.resume-compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 760px) {
  .analysis-target, .resume-family-heading, .drawer-heading { align-items: stretch; flex-direction: column; }
  .node-row, .form-grid, .resume-compare-grid { grid-template-columns: 1fr; }
}
</style>
