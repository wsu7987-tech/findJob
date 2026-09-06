<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";

import { getCodexBridge } from "@/services/desktop-bridge";
import { formatDateTime } from "@/services/format";
import { useFineJobCodexStore } from "@/stores/fineJobCodex";
import { useFineJobJobHuntRefreshStore } from "@/stores/fineJobJobHuntRefresh";
import type { FineJobJobHuntRefreshRun } from "@/types";


const store = useFineJobJobHuntRefreshStore();
const codexStore = useFineJobCodexStore();
const router = useRouter();
const selectedDate = ref<Date | null>(null);
const submittingCodex = ref(false);

const run = computed(() => store.currentRun);
const runIsActive = computed(() => run.value?.status === "pending" || run.value?.status === "running");
const codexReady = computed(() => {
  const bridge = getCodexBridge();
  return codexStore.status === "running"
    && Boolean(codexStore.runId)
    && typeof bridge?.submitCodexPrompt === "function";
});
const codexStatusLabel = computed(() => ({
  idle: "未启动",
  starting: "正在启动",
  running: "已就绪",
  exited: "已退出",
  failed: "启动失败"
}[codexStore.status] ?? codexStore.status));
const scopeIsOld = computed(() => Boolean(
  store.scope
    && Date.now() - new Date(store.scope.scope_generated_at).getTime() > 30 * 60 * 1_000
));
const canStart = computed(() => Boolean(
  store.scope
    && store.scope.selected_since_time === store.selectedSinceTime
    && store.hasExecutableWorkflow
    && codexReady.value
    && !runIsActive.value
    && !store.starting
    && !submittingCodex.value
));
const canResubmit = computed(() => Boolean(
  run.value
    && !["completed", "failed", "cancelled"].includes(run.value.status)
    && (
      run.value.resume_available
      || run.value.current_step === "waiting_codex"
      || run.value.current_step === "waiting_analysis_prepare"
      || run.value.current_step === "waiting_analysis_save"
      || run.value.current_step === "waiting_completion"
    )
    && codexReady.value
    && !store.starting
    && !submittingCodex.value
));
const canCancel = computed(() => Boolean(
  run.value
    && ["pending", "running", "completed_with_errors"].includes(run.value.status)
));

const statusLabel = (value?: string) => ({
  pending: "等待",
  running: "运行中",
  completed: "完成",
  completed_with_errors: "部分失败",
  failed: "失败",
  cancelled: "已取消",
  succeeded: "完成",
  skipped: "未执行"
}[value ?? ""] ?? value ?? "未开始");

const statusType = (value?: string) => ({
  completed: "success",
  succeeded: "success",
  completed_with_errors: "warning",
  failed: "danger",
  running: "primary"
}[value ?? ""] ?? "info") as "success" | "warning" | "danger" | "primary" | "info";

const hasAnalysisOutput = (target?: FineJobJobHuntRefreshRun | null) => Boolean(
  target?.workflow_options.analyze_conversations
    || target?.workflow_options.generate_missing_suggestions
    || target?.workflow_options.generate_reply_drafts
    || target?.workflow_options.generate_followup_recommendations
);

const stepLabel = (value?: string) => {
  if (!value) return "等待开始";
  if (value === "waiting_codex") return "等待 Codex 接收任务";
  if (value === "waiting_chat_messages" || value.includes("chat_session") || value === "refresh_chat_messages") {
    return "正在更新聊天消息";
  }
  if (value === "waiting_related_jobs" || value.includes("related_job") || value === "refresh_related_job") {
    return "正在采集岗位与 JD";
  }
  if (value === "prepare_analysis") return "正在准备分析上下文";
  if (value === "waiting_analysis_prepare") return "等待准备分析上下文";
  if (value === "waiting_analysis_save") return "等待保存分析结果";
  if (value === "waiting_completion") return "等待汇总任务结果";
  if (value === "completed") return "任务完成";
  if (value === "cancelled") return "任务已取消";
  return value;
};

const setSince = (date: Date) => {
  const normalized = new Date(date);
  normalized.setMilliseconds(0);
  selectedDate.value = normalized;
  store.selectedSinceTime = normalized.toISOString().replace(".000Z", "Z");
  store.invalidateScope();
};

const chooseRange = (kind: "last" | "day" | "days3" | "days7") => {
  if (kind === "last" && store.context?.last_successful_completed_at) {
    setSince(new Date(store.context.last_successful_completed_at));
    return;
  }
  const hours = { last: 24, day: 24, days3: 72, days7: 168 }[kind];
  setSince(new Date(Date.now() - hours * 60 * 60 * 1_000));
};

const updateCustomTime = (value: Date | null) => {
  if (value) setSince(value);
};

const discoverScope = async () => {
  if (!selectedDate.value) return;
  try {
    await store.discoverScope();
    ElMessage.success("已获取本次更新范围");
  } catch {
    ElMessage.error(store.error ?? "获取更新范围失败");
  }
};

const remaining = (target: FineJobJobHuntRefreshRun, key: "chat_messages" | "related_jobs") =>
  Math.max(0, target.progress[key].total - target.progress[key].completed);

const numeric = (value: unknown) => Number.isFinite(Number(value)) ? Number(value) : 0;

const analysisProgress = (target: FineJobJobHuntRefreshRun) => {
  const analysis = (target.summary.analysis ?? {}) as Record<string, unknown>;
  const conversationEnabled = target.workflow_options.analyze_conversations
    || target.workflow_options.generate_reply_drafts
    || target.workflow_options.generate_followup_recommendations;
  const conversationDone = conversationEnabled
    ? numeric(target.summary.conversations_analyzed ?? analysis.analyzed)
      + numeric(target.summary.conversations_skipped ?? analysis.skipped)
      + numeric(target.summary.conversation_analysis_failed ?? analysis.failed)
    : 0;
  const conversationTotal = conversationEnabled
    ? numeric(analysis.total ?? target.scope.counts.sessions_in_scope)
    : 0;
  const evaluationDone = target.workflow_options.generate_missing_suggestions
    ? numeric(target.summary.missing_suggestions_generated ?? analysis.generated_evaluation)
      + numeric(target.summary.missing_suggestions_skipped ?? analysis.evaluation_jobs_skipped)
    : 0;
  const evaluationTotal = target.workflow_options.generate_missing_suggestions
    ? numeric(target.summary.missing_suggestions_total ?? analysis.evaluation_jobs_total)
    : 0;
  return {
    completed: conversationDone + evaluationDone,
    total: conversationTotal + evaluationTotal
  };
};

const analysisPrompt = (target: FineJobJobHuntRefreshRun) => [
  target.workflow_options.analyze_conversations
    ? "本次需要分析沟通状态并保存 Conversation Insight。"
    : "",
  target.workflow_options.generate_missing_suggestions
    ? "本次需要为 Scope 中缺失投递建议的已采集岗位生成投递建议，结果只保存为正式 evaluation，不触发投递或打招呼。"
    : "",
  target.workflow_options.generate_reply_drafts
    ? "本次需要生成回复草稿并保存展示；不得创建发送动作，不调用 BOSS 发送接口。"
    : "",
  target.workflow_options.generate_followup_recommendations
    ? "本次需要生成跟进建议；推进建议只保存 attention_status/recommendation，不创建正式待执行任务。"
    : ""
].filter(Boolean).join(" ");

const unifiedAnalysisPrompt = (target: FineJobJobHuntRefreshRun) => [
  "分析相关勾选项启用时，数据补充项全部完成后必须按以下顺序执行：",
  "1. 只调用一次 finejob.prepare_job_hunt_refresh_analysis(run_id)。该工具会返回本 Run 允许分析的统一任务清单，并在 prepare 阶段完成代码确定性事实和旧任务状态同步。",
  "2. 在当前 Codex CLI 会话内，按 prepare 返回的 conversation_items/job_evaluation_items 的 context_arguments 调用 finejob.get_job_hunt_refresh_analysis_item_context 读取单个 item 详情；这是按需读取资料，不是第二次 AI 调用。",
  "3. 如果 prepare 或单个 item context 返回 blocker，例如 analysis_manifest_too_large 或 analysis_item_context_too_large，停止并输出 blocker，不要拆这个 item 的上下文、不要重试 prepare、不要继续伪生成。",
  "4. 基于同一个 Codex CLI 会话中读取到的 item context，一次性完成本次勾选的 AI 结果；输出必须按 session_id/job_id 独立，不得把不同聊天、岗位或 JD 的事实交叉使用。",
  "5. 调用 finejob.save_job_hunt_refresh_analysis(run_id, analysis_result) 保存同一次分析得到的完整结果；结果体积过大时可分批保存，中间批传 final_batch=false，最后一批传 final_batch=true，但不得重新 prepare，不得把投递建议/回复草稿/跟进建议拆成多次 AI 分析。",
  "6. 如需核对分析保存/跳过明细，调用 finejob.list_job_hunt_refresh_analysis_items(run_id, item_type)；不要用 finejob.list_job_hunt_refresh_items 查询 conversation 或 job_evaluation 分析明细。",
  "7. 保存完成后调用 finejob.complete_job_hunt_refresh_run(run_id) 汇总。",
  analysisPrompt(target)
].join(" ");

const refreshPrompt = (target: FineJobJobHuntRefreshRun) => {
  const chatRemaining = remaining(target, "chat_messages");
  const jobRemaining = remaining(target, "related_jobs");
  const dataRefreshCompleted = chatRemaining === 0 && jobRemaining === 0;
  const analysisEnabled = hasAnalysisOutput(target);
  const base = [
    "使用 $finejob 执行 FineJob Job Hunt Refresh Run。",
    `任务：${JSON.stringify({ workflow: "job_hunt_refresh_v1", run_id: target.id })}`,
    "先调用 finejob.get_job_hunt_refresh_run 读取持久化配置，并以工具返回的最新 Run 为准。",
    "聊天消息同步由后端 BossChatBatchManager 执行；只有 scope.counts.extra_jobs 大于 0 时才逐项补采额外岗位。"
  ];
  if (dataRefreshCompleted) {
    return [
      ...base,
      `当前没有待执行的数据补充项：聊天消息剩余 ${chatRemaining}，关联岗位/JD 剩余 ${jobRemaining}。`,
      "不要等待不存在的 item，不要调用 finejob.refresh_job_hunt_chat_batch，不要调用 finejob.refresh_job_hunt_related_job。",
      analysisEnabled
        ? unifiedAnalysisPrompt(target)
        : "现在直接调用 finejob.complete_job_hunt_refresh_run 完成最终汇总。",
      "完成后输出 complete 工具返回的摘要。不得修改 selected_since_time，不得扩大处理范围，不重新采集聊天、不补历史、不重新采集岗位，不执行自动发送。"
    ].join(" ");
  }
  return [
    ...base,
    chatRemaining > 0
      ? `还有 ${chatRemaining} 个聊天消息 item 待处理；调用 finejob.refresh_job_hunt_chat_batch。任务返回非终态时，用 finejob.get_operation_status 读取状态，结束后再次调用 finejob.refresh_job_hunt_chat_batch 完成持久化；重复到没有未完成或可重试聊天项。`
      : "聊天消息 item 已完成或无需执行；不要调用 finejob.refresh_job_hunt_chat_batch。",
    jobRemaining > 0
      ? `还有 ${jobRemaining} 个关联岗位/JD item 待处理；调用 finejob.refresh_job_hunt_related_job 处理相关 item。聊天批量覆盖的岗位不再逐个调用岗位刷新。岗位补采只处理 extra_jobs；调用 list_job_hunt_refresh_items(item_type='related_job') 时，FineJob Service 会过滤或跳过聊天批量已覆盖的历史岗位 item。岗位详情任务返回非终态时，用 finejob.get_operation_status 读取状态，结束后再次调用同一岗位 item 工具完成持久化。`
      : "关联岗位/JD item 已完成或无需执行；不要调用 finejob.refresh_job_hunt_related_job。",
    analysisEnabled
      ? "单项失败后继续其他 item；没有待处理 item 后执行统一分析流程，再调用 complete 汇总。"
      : "单项失败后继续其他 item；没有待处理 item 后调用 finejob.complete_job_hunt_refresh_run。",
    analysisEnabled ? unifiedAnalysisPrompt(target) : "",
    "不得修改 selected_since_time，不得扩大处理范围，不重新采集聊天、不补历史、不重新采集岗位，不执行自动发送。"
  ].join(" ");
};

const submitRunToCodex = async (target: FineJobJobHuntRefreshRun) => {
  const bridge = getCodexBridge();
  if (!codexReady.value || !bridge?.submitCodexPrompt || !codexStore.runId) {
    throw new Error("Codex 未就绪，请先在 Codex 工作台选择模型并启动会话。");
  }
  await store.attachCodexSession(target.id, codexStore.runId);
  const submitted = await bridge.submitCodexPrompt(refreshPrompt(target));
  if (!submitted) throw new Error("Codex 未接收任务，可在就绪后使用原 run_id 重新提交。");
  await store.markPromptSubmitted(target.id);
};

const start = async () => {
  submittingCodex.value = true;
  try {
    if (!codexReady.value) {
      throw new Error("Codex 未就绪，请先在 Codex 工作台选择模型并启动会话。");
    }
    if (store.scope && Date.now() - new Date(store.scope.scope_generated_at).getTime() > 30 * 60 * 1_000) {
      ElMessage.warning("当前 Scope 已生成超过 30 分钟，本次仍按页面显示的固定范围执行。");
    }
    const created = await store.createRun();
    await submitRunToCodex(created);
    ElMessage.success("Refresh Run 已创建并交给 Codex 执行");
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : "任务启动失败");
  } finally {
    submittingCodex.value = false;
  }
};

const resume = async () => {
  if (!run.value) return;
  submittingCodex.value = true;
  try {
    await submitRunToCodex(run.value);
    ElMessage.success("已使用原 run_id 继续未完成项目");
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : "任务继续失败");
  } finally {
    submittingCodex.value = false;
  }
};

const cancel = async () => {
  if (!run.value) return;
  try {
    await store.cancelRun(run.value.id);
    ElMessage.success("Refresh Run 已取消，Scope 和执行记录已保留");
  } catch (value) {
    ElMessage.error(value instanceof Error ? value.message : "任务取消失败");
  }
};

const selectRun = async (runId: string) => {
  try {
    await store.selectRun(runId);
  } catch {
    ElMessage.error(store.error ?? "任务读取失败");
  }
};

const openCodex = () => router.push({ name: "fine-job-codex" });
const openActionableChats = () => router.push({
  name: "fine-job-chat",
  query: { attention: "actionable" }
});

onMounted(async () => {
  try {
    await Promise.all([store.load(), codexStore.load()]);
    selectedDate.value = new Date(store.selectedSinceTime);
  } catch {
    ElMessage.error(store.error ?? "求职数据更新页面加载失败");
  }
});

onBeforeUnmount(() => store.stopProgressReading());
</script>

<template>
  <section class="refresh-page">
    <header class="page-heading">
      <div>
        <p class="app-shell__eyebrow">Job Hunt Refresh</p>
        <h3>求职数据更新</h3>
        <p class="secondary-text">按时间范围刷新聊天与关联岗位，执行过程由持久化 Run 记录。</p>
      </div>
      <el-button @click="openCodex">查看 Codex 工作台</el-button>
    </header>

    <el-alert v-if="store.error" :title="store.error" type="error" :closable="false" />

    <section class="surface-card setup-card">
      <div class="section-title">
        <div>
          <h3>从此时间开始更新</h3>
          <p class="secondary-text">
            本地已同步最新聊天：{{ formatDateTime(store.context?.latest_local_message_at) }} · 时区：Asia/Shanghai
          </p>
        </div>
      </div>

      <div class="range-row">
        <el-button :disabled="!store.context?.last_successful_completed_at" @click="chooseRange('last')">
          上次成功更新后
        </el-button>
        <el-button @click="chooseRange('day')">最近 24 小时</el-button>
        <el-button @click="chooseRange('days3')">最近 3 天</el-button>
        <el-button @click="chooseRange('days7')">最近 7 天</el-button>
        <el-date-picker
          v-model="selectedDate"
          type="datetime"
          placeholder="选择自定义时间"
          :clearable="false"
          @change="updateCustomTime"
        />
      </div>

      <div class="workflow-grid">
        <el-checkbox :model-value="true" disabled>聊天列表范围在获取范围时确定</el-checkbox>
        <el-checkbox v-model="store.workflowOptions.refresh_chat_messages">更新聊天消息</el-checkbox>
        <el-checkbox v-model="store.workflowOptions.refresh_related_jobs">采集/刷新关联岗位与 JD</el-checkbox>
        <el-checkbox v-model="store.workflowOptions.analyze_conversations">识别聊天事实并更新求职进展</el-checkbox>
        <el-checkbox v-model="store.workflowOptions.generate_missing_suggestions">生成缺失投递建议</el-checkbox>
        <el-checkbox v-model="store.workflowOptions.generate_reply_drafts">生成回复草稿</el-checkbox>
        <el-checkbox v-model="store.workflowOptions.generate_followup_recommendations">生成跟进建议</el-checkbox>
      </div>

      <div class="source-mode-row">
        <span>聊天列表来源</span>
        <el-radio-group v-model="store.sourceMode" @change="store.invalidateScope()">
          <el-radio-button value="auto">智能选择（默认）</el-radio-button>
          <el-radio-button value="local">使用本地聊天列表</el-radio-button>
          <el-radio-button value="refresh">先刷新 BOSS 聊天列表</el-radio-button>
        </el-radio-group>
        <span class="secondary-text">
          本地聊天列表最后同步：{{ formatDateTime(store.context?.chat_list_synced_at) }}
        </span>
      </div>

      <div class="card-actions">
        <el-button
          type="primary"
          :loading="store.discovering"
          :disabled="!selectedDate"
          data-testid="scope-discovery-button"
          @click="discoverScope"
        >
          获取更新范围
        </el-button>
      </div>
    </section>

    <section v-if="store.scope" class="surface-card preview-card">
      <div class="section-title">
        <div>
          <h3>本次更新范围</h3>
          <p class="secondary-text">
            Scope 生成时间：{{ formatDateTime(store.scope.scope_generated_at) }}
          </p>
          <p class="secondary-text">
            本次范围来源：{{ store.scope.scope_source === "refresh" ? "BOSS 最新列表" : "本地聊天列表" }} ·
            列表同步时间：{{ formatDateTime(store.scope.chat_list_synced_at) }}
          </p>
          <p
            v-if="store.scope.scope_source === 'local' && typeof store.scope.friend_list_result.age_minutes === 'number'"
            class="secondary-text"
          >
            聊天列表 {{ store.scope.friend_list_result.age_minutes }} 分钟前已同步，本次直接使用本地数据。
          </p>
        </div>
        <el-tag type="success">已确定</el-tag>
      </div>
      <el-alert
        v-if="scopeIsOld"
        title="该范围生成已超过 30 分钟；开始更新仍会使用页面显示的固定 Scope。如需平台最新范围，请重新获取。"
        type="warning"
        :closable="false"
      />
      <div class="metric-grid">
        <article><span>时间范围内聊天</span><strong>{{ store.scope.counts.sessions_in_scope }}</strong></article>
        <article><span>待同步聊天</span><strong>{{ store.scope.counts.sessions_to_sync }}</strong></article>
        <article><span>聊天更新涉及岗位</span><strong>{{ store.scope.counts.chat_update_jobs }}</strong></article>
        <article><span>额外需要补采岗位</span><strong>{{ store.scope.counts.extra_jobs }}</strong></article>
        <article><span>去重后实际更新岗位</span><strong>{{ store.scope.counts.jobs_to_update }}</strong></article>
        <article><span>缺失 JD</span><strong>{{ store.scope.counts.jobs_missing_jd }}</strong></article>
        <article><span>缺少投递建议</span><strong>{{ store.scope.counts.jobs_missing_evaluation }}</strong></article>
        <article><span>新增消息</span><strong class="metric-pending">更新完成后统计</strong></article>
      </div>
      <div class="execution-summary">
        <span>{{ formatDateTime(store.scope.selected_since_time) }} → Scope 生成时间</span>
        <span>✓ 聊天列表范围已确定</span>
        <span>{{ store.workflowOptions.refresh_chat_messages ? "✓" : "○" }} 更新聊天消息</span>
        <span>{{ store.workflowOptions.refresh_related_jobs ? "✓" : "○" }} 采集关联岗位</span>
        <span>{{ store.workflowOptions.analyze_conversations ? "✓" : "○" }} 沟通分析</span>
        <span>{{ store.workflowOptions.generate_missing_suggestions ? "✓" : "○" }} 缺失建议</span>
        <span>{{ store.workflowOptions.generate_reply_drafts ? "✓" : "○" }} 回复草稿</span>
        <span>{{ store.workflowOptions.generate_followup_recommendations ? "✓" : "○" }} 跟进建议</span>
      </div>
      <section class="codex-readiness">
        <div>
          <strong>Codex 执行器</strong>
          <span>状态：{{ codexStatusLabel }}</span>
          <span>会话：{{ codexStore.runId || "无" }}</span>
        </div>
        <p v-if="!codexReady" class="secondary-text">
          Codex 未就绪，请先选择模型并启动 Codex 会话。
        </p>
        <el-button v-if="!codexReady" @click="openCodex">前往 Codex 工作台</el-button>
      </section>
      <el-button
        type="primary"
        :disabled="!canStart"
        :loading="store.starting || submittingCodex"
        data-testid="start-refresh-button"
        @click="start"
      >
        开始更新
      </el-button>
    </section>

    <section v-if="run" class="surface-card run-card">
      <div class="section-title">
        <div>
          <h3>当前任务</h3>
          <p class="secondary-text">
            {{ run.id }} · Scope：{{ run.scope_id }} · 生成于 {{ formatDateTime(run.scope_generated_at) }}
          </p>
        </div>
        <div class="card-actions">
          <el-tag :type="statusType(run.status)">{{ statusLabel(run.status) }}</el-tag>
          <el-button
            v-if="canResubmit"
            :loading="submittingCodex"
            data-testid="resume-refresh-button"
            @click="resume"
          >重新提交到 Codex</el-button>
          <el-button
            v-if="canCancel"
            type="danger"
            plain
            data-testid="cancel-refresh-button"
            @click="cancel"
          >取消任务</el-button>
        </div>
      </div>

      <p class="current-step">{{ stepLabel(run.current_step) }}</p>
      <div class="progress-list">
        <div>
          <span>范围发现 / 聊天列表</span>
          <strong>{{ statusLabel(run.progress.chat_list.status) }}</strong>
        </div>
        <div>
          <span>聊天消息</span>
          <strong>{{ run.progress.chat_messages.completed }} / {{ run.progress.chat_messages.total }}</strong>
        </div>
        <div>
          <span>关联岗位与 JD</span>
          <strong>{{ run.progress.related_jobs.completed }} / {{ run.progress.related_jobs.total }}</strong>
        </div>
        <div v-if="hasAnalysisOutput(run)">
          <span>分析 / 建议</span>
          <strong>{{ analysisProgress(run).completed }} / {{ analysisProgress(run).total }}</strong>
        </div>
      </div>

      <div v-if="run.status === 'completed' || run.status === 'completed_with_errors'" class="result-grid">
        <article>
          <h4>聊天</h4>
          <p>{{ run.summary.sessions_total ?? 0 }} 个会话</p>
          <p>{{ run.summary.sessions_succeeded ?? 0 }} 个成功</p>
          <p>新增 {{ run.summary.new_messages ?? 0 }} 条消息</p>
          <p>{{ run.summary.sessions_failed ?? 0 }} 个失败</p>
        </article>
        <article>
          <h4>岗位</h4>
          <p>{{ run.scope.counts.related_jobs }} 个关联岗位</p>
          <p>{{ run.summary.related_jobs_total ?? 0 }} 个采集/刷新任务</p>
          <p>{{ run.summary.jobs_created ?? 0 }} 个新增</p>
          <p>{{ run.summary.jobs_refreshed ?? 0 }} 个刷新</p>
          <p>{{ run.summary.unresolved_jobs ?? 0 }} 个无法关联</p>
        </article>
        <article v-if="hasAnalysisOutput(run)">
          <h4>分析</h4>
          <p>{{ run.summary.conversations_analyzed ?? 0 }} 个会话已分析</p>
          <p>{{ run.summary.conversations_skipped ?? 0 }} 个会话降级或跳过</p>
          <p>{{ run.summary.activities_written ?? 0 }} 条进度记录</p>
          <p>{{ run.summary.missing_suggestions_generated ?? 0 }} / {{ run.summary.missing_suggestions_total ?? 0 }} 个缺失建议已生成</p>
          <p>{{ run.summary.reply_drafts_generated ?? 0 }} 条草稿</p>
        </article>
        <article v-if="hasAnalysisOutput(run)" class="progress-result">
          <h4>求职进展</h4>
          <p>更新进展：{{ run.summary.progress_updates ?? 0 }}</p>
          <p>等招聘方回复：{{ run.summary.waiting_for_recruiter ?? 0 }}</p>
          <p>等我回复：{{ run.summary.waiting_for_candidate ?? 0 }}</p>
          <p>建议跟进：{{ run.summary.followup_recommended ?? 0 }}</p>
          <p>简历已查看：{{ run.summary.resume_viewed ?? 0 }}</p>
          <p>用人部门评估：{{ run.summary.under_review ?? 0 }}</p>
          <p>新识别拒绝：{{ run.summary.rejections_detected ?? 0 }}</p>
          <p>岗位关闭：{{ run.summary.jobs_closed ?? 0 }}</p>
          <el-button type="primary" plain @click="openActionableChats">查看需要处理的岗位</el-button>
        </article>
      </div>
    </section>

    <section class="surface-card recent-card">
      <div class="section-title"><h3>最近任务结果</h3></div>
      <el-table :data="store.recentRuns" empty-text="尚无求职数据更新任务">
        <el-table-column label="创建时间" min-width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="范围起点" min-width="170">
          <template #default="{ row }">{{ formatDateTime(row.selected_since_time) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="聊天" width="100" prop="processed_sessions" />
        <el-table-column label="岗位" width="100" prop="processed_jobs" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }"><el-button link @click="selectRun(row.id)">查看</el-button></template>
        </el-table-column>
      </el-table>
    </section>
  </section>
</template>

<style scoped>
.refresh-page { display: grid; gap: 18px; }
.setup-card, .preview-card, .run-card, .recent-card { display: grid; gap: 18px; }
.section-title { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.section-title h3, .section-title h4, .section-title p { margin: 0; }
.range-row, .card-actions, .execution-summary, .source-mode-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.workflow-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.metric-grid article, .result-grid article { display: grid; gap: 6px; padding: 16px; border-radius: 12px; background: var(--el-fill-color-light); }
.metric-grid span { color: var(--el-text-color-secondary); }
.metric-grid strong { font-size: 28px; }
.metric-grid .metric-pending { font-size: 16px; }
.current-step { margin: 0; color: var(--el-color-primary); }
.codex-readiness { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; padding: 14px; border-radius: 12px; background: var(--el-fill-color-light); }
.codex-readiness > div { display: flex; flex-wrap: wrap; gap: 12px; }
.codex-readiness p { margin: 0; }
.progress-list { display: grid; gap: 10px; }
.progress-list > div { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.result-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
.result-grid h4, .result-grid p { margin: 0; }
@media (max-width: 720px) { .section-title { flex-direction: column; } }
</style>
