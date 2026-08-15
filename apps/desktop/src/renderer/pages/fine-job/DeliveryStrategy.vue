<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import {
  emptyDeliveryStrategy,
  useFineJobDeliveryStrategyStore
} from "@/stores/fineJobDeliveryStrategy";
import type { FineJobDeliveryStrategy } from "@/types";

const strategyStore = useFineJobDeliveryStrategyStore();
const form = ref<FineJobDeliveryStrategy>(emptyDeliveryStrategy());

const ready = computed(() => Boolean(strategyStore.strategy?.ready));

onMounted(async () => {
  const strategy = await strategyStore.load();
  form.value = cloneStrategy(strategy ?? emptyDeliveryStrategy());
});

watch(
  () => strategyStore.strategy,
  (strategy) => {
    if (strategy) {
      form.value = cloneStrategy(strategy);
    }
  },
  { deep: true }
);

const saveStrategy = async () => {
  try {
    const saved = await strategyStore.save(normalizeStrategy(form.value));
    if (saved) {
      form.value = cloneStrategy(saved);
    }
    ElMessage.success("投递策略已确认");
  } catch {
    ElMessage.error(strategyStore.error ?? "投递策略保存失败");
  }
};

const normalizeStrategy = (strategy: FineJobDeliveryStrategy): FineJobDeliveryStrategy => ({
  ...strategy,
  daily_greeting_limit: Math.max(1, Math.trunc(strategy.daily_greeting_limit)),
  hourly_greeting_limit: Math.max(1, Math.trunc(strategy.hourly_greeting_limit)),
  min_match_score: Math.min(1, Math.max(0, Number(strategy.min_match_score))),
  notes: strategy.notes.trim()
});

const cloneStrategy = (strategy: FineJobDeliveryStrategy): FineJobDeliveryStrategy => ({
  ...emptyDeliveryStrategy(),
  ...strategy
});
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Automation Policy</p>
        <h1>投递策略</h1>
        <p class="secondary-text">
          明确哪些动作可以自动执行，哪些必须进入待确认。第一版默认保守，避免误投和过度触发风控。
        </p>
      </div>
      <div class="card-actions">
        <el-tag :type="ready ? 'success' : 'warning'">
          {{ ready ? "已确认" : "未确认" }}
        </el-tag>
        <el-button type="primary" :loading="strategyStore.saving" @click="saveStrategy">
          确认策略
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="strategyStore.error"
      type="error"
      title="投递策略操作失败"
      :description="strategyStore.error"
      show-icon
    />

    <section v-loading="strategyStore.loading" class="page-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Required</p>
          <h2>自动化边界</h2>
        </div>
        <span class="secondary-text">建议先用保守策略跑通流程，再逐步放开。</span>
      </div>

      <el-form label-position="top" class="intent-form">
        <div class="form-grid">
          <el-form-item label="自动化等级">
            <el-select v-model="form.automation_level">
              <el-option label="辅助模式：只生成建议，不自动打招呼" value="assist" />
              <el-option label="半自动：可批量确认后执行" value="semi_auto" />
              <el-option label="自动打招呼：高匹配岗位自动打招呼" value="auto_greeting" />
            </el-select>
          </el-form-item>

          <el-form-item label="允许自动打招呼">
            <el-switch v-model="form.auto_greeting_enabled" />
          </el-form-item>

          <el-form-item label="遇到风险立即暂停">
            <el-switch v-model="form.pause_on_risk" />
          </el-form-item>
        </div>

        <div class="form-grid">
          <el-form-item label="每日打招呼上限">
            <el-input-number v-model="form.daily_greeting_limit" :min="1" :max="500" />
          </el-form-item>

          <el-form-item label="每小时打招呼上限">
            <el-input-number v-model="form.hourly_greeting_limit" :min="1" :max="100" />
          </el-form-item>

          <el-form-item label="最低匹配分">
            <el-slider v-model="form.min_match_score" :min="0" :max="1" :step="0.01" show-input />
          </el-form-item>
        </div>

        <div class="form-grid">
          <el-form-item label="投递简历">
            <el-select v-model="form.resume_submit_mode">
              <el-option label="必须人工确认" value="manual" />
              <el-option label="收到 HR 邀请且匹配时自动投递" value="auto_on_invite" />
            </el-select>
          </el-form-item>

          <el-form-item label="交换联系方式">
            <el-select v-model="form.contact_share_mode">
              <el-option label="必须人工确认" value="manual" />
              <el-option label="匹配通过后自动发送" value="auto_after_match" />
            </el-select>
          </el-form-item>

          <el-form-item label="面试时间">
            <el-select v-model="form.interview_accept_mode">
              <el-option label="必须人工确认" value="manual" />
              <el-option label="命中可选时间段时自动接受" value="auto_in_selected_slots" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item label="只接受线上面试">
          <el-switch v-model="form.only_online_interview" />
        </el-form-item>

        <el-form-item label="备注">
          <el-input
            v-model="form.notes"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
            placeholder="例如：刚开始每天最多 20 个；投简历、联系方式、线下面试都必须人工确认。"
          />
        </el-form-item>
      </el-form>
    </section>
  </section>
</template>
