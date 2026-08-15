<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";

import { formatDateTime } from "@/services/format";
import { useFineJobDeliveryRunsStore } from "@/stores/fineJobDeliveryRuns";

const route = useRoute();
const runsStore = useFineJobDeliveryRunsStore();

const selectedRun = computed(() => runsStore.selectedRun ?? runsStore.latestRun);
const statusType = computed(() => {
  if (selectedRun.value?.status === "completed") {
    return "success";
  }
  if (selectedRun.value?.status === "failed") {
    return "danger";
  }
  return "warning";
});

const openJob = (url: string) => {
  if (!url) {
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
};

onMounted(async () => {
  await runsStore.load();
  const runId = typeof route.query.runId === "string" ? route.query.runId : selectedRun.value?.id;
  if (runId) {
    await runsStore.loadRunDetail(runId);
  }
});
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Run Status</p>
        <h1>投递任务状态</h1>
        <p class="secondary-text">
          当前仍是 dry-run：可以真实采集 BOSS 搜索结果并生成候选岗位，但不会发送打招呼、投简历或回复 HR。
        </p>
      </div>
      <div class="card-actions">
        <el-button :loading="runsStore.loading" @click="runsStore.load()">刷新</el-button>
        <el-button disabled>暂停任务</el-button>
      </div>
    </div>

    <el-alert
      v-if="runsStore.error"
      type="error"
      title="投递任务操作失败"
      :description="runsStore.error"
      show-icon
    />

    <el-alert
      v-if="selectedRun?.error_message"
      type="warning"
      title="任务已暂停或异常"
      :description="selectedRun.error_message"
      show-icon
      :closable="false"
    />

    <div class="metric-grid">
      <article class="metric-card">
        <span>已采集</span>
        <strong>{{ selectedRun?.searched_count ?? 0 }}</strong>
        <p>候选岗位数量</p>
      </article>
      <article class="metric-card">
        <span>已跳过</span>
        <strong>{{ selectedRun?.skipped_count ?? 0 }}</strong>
        <p>命中排除词或低匹配</p>
      </article>
      <article class="metric-card">
        <span>已打招呼</span>
        <strong>{{ selectedRun?.greeted_count ?? 0 }}</strong>
        <p>当前版本固定为 0</p>
      </article>
      <article class="metric-card">
        <span>异常</span>
        <strong>{{ selectedRun?.error_count ?? 0 }}</strong>
        <p>登录、验证码、风险或页面变化</p>
      </article>
    </div>

    <section class="page-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Latest Run</p>
          <h2>最近任务</h2>
        </div>
        <el-tag v-if="selectedRun" :type="statusType">{{ selectedRun.status }}</el-tag>
      </div>

      <el-empty v-if="!selectedRun" description="还没有投递任务" />
      <div v-else class="meta-list">
        <div><span>任务 ID</span><strong>{{ selectedRun.id }}</strong></div>
        <div><span>模式</span><strong>{{ selectedRun.mode }}</strong></div>
        <div><span>阶段</span><strong>{{ selectedRun.stage }}</strong></div>
        <div><span>开始时间</span><strong>{{ formatDateTime(selectedRun.started_at) }}</strong></div>
      </div>
    </section>

    <section class="table-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Candidates</p>
          <h2>候选岗位</h2>
        </div>
      </div>
      <el-table :data="runsStore.candidates" empty-text="暂无候选岗位">
        <el-table-column prop="city" label="城市" width="90" />
        <el-table-column prop="keyword" label="关键词" width="140" />
        <el-table-column label="岗位" min-width="220">
          <template #default="{ row }">
            <el-button v-if="row.job_url" link type="primary" @click="openJob(row.job_url)">
              {{ row.job_title }}
            </el-button>
            <span v-else>{{ row.job_title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="company_name" label="公司" width="150" />
        <el-table-column prop="salary_text" label="薪资" width="120" />
        <el-table-column label="条件" min-width="180">
          <template #default="{ row }">
            {{ [row.location_text, row.experience_text, row.education_text].filter(Boolean).join(" / ") || "-" }}
          </template>
        </el-table-column>
        <el-table-column label="匹配分" width="100">
          <template #default="{ row }">{{ row.match_score ?? "-" }}</template>
        </el-table-column>
        <el-table-column prop="decision" label="决策" width="130" />
        <el-table-column prop="reason" label="原因" min-width="240" />
      </el-table>
    </section>

    <section class="table-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Action Logs</p>
          <h2>动作日志</h2>
        </div>
      </div>
      <el-table :data="runsStore.logs" empty-text="暂无日志">
        <el-table-column label="时间" width="190">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="level" label="级别" width="100" />
        <el-table-column prop="action_type" label="动作" width="180" />
        <el-table-column prop="message" label="说明" min-width="280" />
      </el-table>
    </section>
  </section>
</template>
