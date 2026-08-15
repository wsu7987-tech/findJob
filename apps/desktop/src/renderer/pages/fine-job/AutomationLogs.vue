<script setup lang="ts">
import { onMounted } from "vue";

import { formatDateTime } from "@/services/format";
import { useFineJobDeliveryRunsStore } from "@/stores/fineJobDeliveryRuns";

const runsStore = useFineJobDeliveryRunsStore();

onMounted(() => {
  void runsStore.loadRecentLogs();
});
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Audit Trail</p>
        <h1>动作日志</h1>
        <p class="secondary-text">记录投递任务、AI 建议、用户确认、页面动作、失败原因和策略检查结果。</p>
      </div>
      <el-button :loading="runsStore.loading" @click="runsStore.loadRecentLogs()">刷新</el-button>
    </div>

    <el-alert
      v-if="runsStore.error"
      type="error"
      title="动作日志加载失败"
      :description="runsStore.error"
      show-icon
    />

    <section class="table-panel">
      <el-table v-loading="runsStore.loading" :data="runsStore.logs" empty-text="暂无动作日志">
        <el-table-column label="时间" width="190">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="100">
          <template #default="{ row }">
            <el-tag
              :type="row.level === 'error' ? 'danger' : row.level === 'warning' ? 'warning' : 'info'"
            >
              {{ row.level }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="action_type" label="动作" width="180" />
        <el-table-column prop="message" label="说明" min-width="320" />
        <el-table-column prop="run_id" label="任务" width="180" />
      </el-table>
    </section>
  </section>
</template>
