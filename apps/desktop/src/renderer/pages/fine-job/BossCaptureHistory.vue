<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { formatDateTime } from "@/services/format";
import { useFineJobBossCaptureStore } from "@/stores/fineJobBossCapture";
import { useFineJobBossHistoryStore } from "@/stores/fineJobBossHistory";
import type { FineJobBossHistoryJob, FineJobBossHistorySortField } from "@/types";

const captureStore = useFineJobBossCaptureStore();
const historyStore = useFineJobBossHistoryStore();
const selectedJob = ref<FineJobBossHistoryJob | null>(null);
const detailDrawerOpen = ref(false);
const dateRange = ref<string[]>([]);
const filters = reactive({
  query: "",
  city: "",
  companyScale: "",
  companyIndustry: "",
  companyStage: "",
  detailStatus: "",
  repeatStatus: "all" as "all" | "first_seen" | "repeated",
  sortBy: "last_collected_at" as FineJobBossHistorySortField,
  sortOrder: "desc" as "asc" | "desc"
});

const companyScaleOptions = [
  "0-20人",
  "20-99人",
  "100-499人",
  "500-999人",
  "1000-9999人",
  "10000人以上"
];
const companyStageOptions = ["未融资", "不需要融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市"];
const companyIndustryOptions = ["人工智能", "互联网", "计算机软件", "电子商务", "云计算", "游戏", "移动互联网"];

const queryPayload = computed(() => ({
  query: filters.query.trim(),
  city: filters.city,
  company_scale: filters.companyScale,
  company_industry: filters.companyIndustry,
  company_stage: filters.companyStage,
  detail_status: filters.detailStatus,
  repeat_status: filters.repeatStatus,
  collected_from: dateRange.value[0] ? `${dateRange.value[0]}T00:00:00Z` : "",
  collected_to: dateRange.value[1] ? `${dateRange.value[1]}T23:59:59Z` : "",
  sort_by: filters.sortBy,
  sort_order: filters.sortOrder,
  page: historyStore.page,
  page_size: historyStore.pageSize
}));
const detailTaskRunning = computed(
  () => historyStore.detailTask?.status === "queued" || historyStore.detailTask?.status === "running"
);
const detailProgressPercentage = computed(() => {
  const task = historyStore.detailTask;
  if (!task?.progress_total) return 0;
  return Math.min(100, Math.round((task.progress_current / task.progress_total) * 100));
});

const loadHistory = async () => {
  try {
    await historyStore.load(queryPayload.value);
  } catch {
    ElMessage.error(historyStore.error ?? "历史采集记录加载失败");
  }
};

const search = async () => {
  historyStore.page = 1;
  await loadHistory();
};

const reset = async () => {
  Object.assign(filters, {
    query: "",
    city: "",
    companyScale: "",
    companyIndustry: "",
    companyStage: "",
    detailStatus: "",
    repeatStatus: "all"
  });
  dateRange.value = [];
  historyStore.page = 1;
  await loadHistory();
};

const handlePageChange = async (value: number) => {
  historyStore.page = value;
  await loadHistory();
};

const handlePageSizeChange = async (value: number) => {
  historyStore.pageSize = value;
  historyStore.page = 1;
  await loadHistory();
};

const handleSortChange = async ({
  prop,
  order
}: {
  prop: string;
  order: "ascending" | "descending" | null;
}) => {
  const fields: Record<string, FineJobBossHistorySortField> = {
    title: "title",
    boss_name: "company_name",
    first_collected_at: "first_collected_at",
    last_collected_at: "last_collected_at",
    collect_count: "collect_count"
  };
  filters.sortBy = order && fields[prop] ? fields[prop] : "last_collected_at";
  filters.sortOrder = order === "ascending" ? "asc" : "desc";
  historyStore.page = 1;
  await loadHistory();
};

const captureHistoryDetails = async (job: FineJobBossHistoryJob) => {
  if (!captureStore.status?.running) {
    ElMessage.warning("请先在平台登录或岗位采集页面打开 BOSS 专用浏览器");
    return;
  }
  try {
    await historyStore.captureDetails(job.id);
    ElMessage.success(`${detailActionLabel(job)}任务已启动`);
  } catch {
    ElMessage.error(historyStore.error ?? "启动岗位详情采集失败");
  }
};

const detailActionLabel = (job: FineJobBossHistoryJob) =>
  job.detail_status === "completed" || Boolean(job.detail) ? "重新采集详情" : "采集详情";

const openDetail = (job: FineJobBossHistoryJob) => {
  selectedJob.value = job;
  detailDrawerOpen.value = true;
};

const detailStatusLabel = (status?: string) => ({
  completed: "已采详情",
  failed: "详情失败",
  queued: "等待采集",
  collecting: "采集中",
  not_collected: "未采详情"
}[status || "not_collected"] || "未采详情");

const detailStatusType = (status?: string) => {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "queued" || status === "collecting") return "warning";
  return "info";
};

onMounted(async () => {
  await Promise.all([captureStore.loadCities(), captureStore.loadStatus(), loadHistory()]);
});

onBeforeUnmount(() => historyStore.stopDetailPolling());

watch(
  () => historyStore.detailTask?.status,
  async (status) => {
    if (status === "completed") {
      const selectedId = selectedJob.value?.id;
      await loadHistory();
      if (selectedId) {
        selectedJob.value = historyStore.items.find((item) => item.id === selectedId) ?? null;
      }
      ElMessage.success("岗位详情采集完成");
      historyStore.clearDetailTask();
    } else if (status === "failed") {
      ElMessage.error(historyStore.detailTask?.error_message || "岗位详情采集失败");
    }
  }
);
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">BOSS Capture History</p>
        <h1>历史采集</h1>
        <p class="secondary-text">按岗位去重保存；重复采集会更新最后采集时间和采集次数。</p>
      </div>
      <el-tag type="info">共 {{ historyStore.total }} 个岗位</el-tag>
    </div>

    <el-alert
      v-if="historyStore.error"
      type="error"
      title="历史采集操作失败"
      :description="historyStore.error"
      show-icon
    />

    <section class="page-panel history-filter-panel">
      <el-form label-position="top">
        <div class="history-filter-grid">
          <el-form-item label="岗位 / 公司 / 技能">
            <el-input v-model="filters.query" clearable placeholder="输入关键词" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="城市">
            <el-select v-model="filters.city" filterable clearable placeholder="全部城市">
              <el-option v-for="city in captureStore.cities" :key="city.code" :label="city.name" :value="city.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="公司规模">
            <el-select v-model="filters.companyScale" filterable clearable allow-create placeholder="全部规模">
              <el-option v-for="scale in companyScaleOptions" :key="scale" :label="scale" :value="scale" />
            </el-select>
          </el-form-item>
          <el-form-item label="公司行业">
            <el-select v-model="filters.companyIndustry" filterable clearable allow-create placeholder="全部行业">
              <el-option v-for="item in companyIndustryOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="融资阶段">
            <el-select v-model="filters.companyStage" filterable clearable allow-create placeholder="全部阶段">
              <el-option v-for="item in companyStageOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="详情状态">
            <el-select v-model="filters.detailStatus" clearable placeholder="全部状态">
              <el-option label="已采详情" value="completed" />
              <el-option label="未采详情" value="not_collected" />
              <el-option label="详情失败" value="failed" />
            </el-select>
          </el-form-item>
          <el-form-item label="采集次数">
            <el-select v-model="filters.repeatStatus">
              <el-option label="全部岗位" value="all" />
              <el-option label="仅首次采集" value="first_seen" />
              <el-option label="重复采集过" value="repeated" />
            </el-select>
          </el-form-item>
          <el-form-item label="最后采集日期">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              range-separator="至"
              clearable
            />
          </el-form-item>
        </div>
      </el-form>
      <div class="history-filter-actions">
        <el-button type="primary" :loading="historyStore.loading" @click="search">查询</el-button>
        <el-button :disabled="historyStore.loading" @click="reset">重置</el-button>
      </div>
    </section>

    <section
      v-if="historyStore.detailTask && historyStore.detailTask.status !== 'completed'"
      class="page-panel history-detail-progress"
    >
      <div class="panel-title-row">
        <div><p class="panel-eyebrow">Detail Progress</p><h2>岗位详情采集</h2></div>
        <el-tag :type="historyStore.detailTask.status === 'failed' ? 'danger' : 'warning'">
          {{ historyStore.detailTask.status === "failed" ? "失败" : "进行中" }}
        </el-tag>
      </div>
      <el-progress
        :percentage="detailProgressPercentage"
        :status="historyStore.detailTask.status === 'failed' ? 'exception' : undefined"
      />
      <p>{{ historyStore.detailTask.message }}</p>
    </section>

    <section class="page-panel">
      <el-table
        v-loading="historyStore.loading"
        :data="historyStore.items"
        row-key="id"
        max-height="600"
        :default-sort="{ prop: 'last_collected_at', order: 'descending' }"
        empty-text="暂无历史采集岗位"
        @sort-change="handleSortChange"
        @row-click="openDetail"
      >
        <el-table-column label="记录" width="90">
          <template #default="{ row }">
            <el-tag :type="row.collect_count > 1 ? 'warning' : 'success'" size="small">
              {{ row.collect_count > 1 ? "重复" : "首次" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="岗位" min-width="180" show-overflow-tooltip sortable="custom" />
        <el-table-column prop="boss_name" label="公司" min-width="150" show-overflow-tooltip sortable="custom" />
        <el-table-column prop="company_scale" label="公司规模" width="120" />
        <el-table-column prop="company_industry" label="行业" width="120" show-overflow-tooltip />
        <el-table-column prop="company_stage" label="融资阶段" width="110" />
        <el-table-column prop="salary" label="薪资" width="110" />
        <el-table-column prop="location" label="地点" min-width="130" show-overflow-tooltip />
        <el-table-column prop="experience" label="经验" width="100" />
        <el-table-column prop="degree" label="学历" width="90" />
        <el-table-column prop="first_collected_at" label="首次采集" width="190" sortable="custom">
          <template #default="{ row }">{{ formatDateTime(row.first_collected_at) }}</template>
        </el-table-column>
        <el-table-column prop="last_collected_at" label="最后采集" width="190" sortable="custom">
          <template #default="{ row }">{{ formatDateTime(row.last_collected_at) }}</template>
        </el-table-column>
        <el-table-column prop="collect_count" label="次数" width="100" align="center" sortable="custom" />
        <el-table-column label="详情" width="110">
          <template #default="{ row }">
            <el-tag :type="detailStatusType(row.detail_status)" size="small">
              {{ detailStatusLabel(row.detail_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row)">查看详情</el-button>
            <el-button
              link
              type="success"
              :loading="historyStore.detailJobId === row.id"
              :disabled="detailTaskRunning && historyStore.detailJobId !== row.id"
              @click.stop="captureHistoryDetails(row)"
            >
              {{ detailActionLabel(row) }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="history-pagination">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next, jumper"
          :total="historyStore.total"
          :current-page="historyStore.page"
          :page-size="historyStore.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="handlePageChange"
          @size-change="handlePageSizeChange"
        />
      </div>
    </section>

    <el-drawer v-model="detailDrawerOpen" size="48%" :title="selectedJob?.title || '岗位详情'">
      <template v-if="selectedJob">
        <div class="history-detail-heading">
          <h2>{{ selectedJob.title }}</h2>
          <p>{{ selectedJob.boss_name }} · {{ selectedJob.company_scale || "规模未知" }} · {{ selectedJob.location }}</p>
          <div class="history-detail-tags">
            <el-tag>{{ selectedJob.salary || "薪资未知" }}</el-tag>
            <el-tag type="info">已采集 {{ selectedJob.collect_count }} 次</el-tag>
            <el-tag :type="detailStatusType(selectedJob.detail_status)">{{ detailStatusLabel(selectedJob.detail_status) }}</el-tag>
          </div>
        </div>
        <el-divider />
        <h3>采集记录</h3>
        <p>首次采集：{{ formatDateTime(selectedJob.first_collected_at) }}</p>
        <p>最后采集：{{ formatDateTime(selectedJob.last_collected_at) }}</p>
        <p v-if="selectedJob.detail_collected_at">详情采集：{{ formatDateTime(selectedJob.detail_collected_at) }}</p>
        <el-divider />
        <h3>技能与标签</h3>
        <p>{{ selectedJob.skills || selectedJob.job_labels || selectedJob.tags || "暂无标签" }}</p>
        <p>行业：{{ selectedJob.company_industry || "未知" }}；融资阶段：{{ selectedJob.company_stage || "未知" }}</p>
        <p>福利：{{ selectedJob.welfare || "暂无" }}</p>
        <p>招聘者活跃：{{ selectedJob.boss_active_status || "未获取" }}</p>
        <el-divider />
        <h3>职位描述</h3>
        <div v-if="selectedJob.detail?.jd" class="job-description">{{ selectedJob.detail.jd }}</div>
        <p v-else class="secondary-text">该岗位尚未采集完整详情。</p>
        <el-button
          type="primary"
          :loading="historyStore.detailJobId === selectedJob.id"
          :disabled="detailTaskRunning && historyStore.detailJobId !== selectedJob.id"
          @click="captureHistoryDetails(selectedJob)"
        >
          {{ detailActionLabel(selectedJob) }}
        </el-button>
        <el-divider />
        <el-link v-if="selectedJob.job_link" :href="selectedJob.job_link" target="_blank" type="primary">
          打开 BOSS 原始岗位页面
        </el-link>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.history-filter-panel {
  display: grid;
  gap: 12px;
}

.history-detail-progress {
  display: grid;
  gap: 12px;
}

.history-filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 4px 16px;
}

.history-filter-actions,
.history-detail-tags {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.history-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 18px;
}

.history-detail-heading h2 {
  margin-bottom: 6px;
}

.job-description {
  line-height: 1.8;
  white-space: pre-wrap;
}
</style>
