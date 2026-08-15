<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { emptyIntent, useFineJobIntentStore } from "@/stores/fineJobIntent";
import type { FineJobIntent } from "@/types";

const intentStore = useFineJobIntentStore();
const form = ref<FineJobIntent>(emptyIntent());

const ready = computed(
  () =>
    Boolean(form.value.target_title.trim()) &&
    form.value.cities.length > 0 &&
    form.value.keywords.length > 0
);

onMounted(async () => {
  const intent = await intentStore.load();
  form.value = intent ? cloneIntent(intent) : emptyIntent();
});

watch(
  () => intentStore.intent,
  (intent) => {
    if (intent) {
      form.value = cloneIntent(intent);
    }
  }
);

const saveIntent = async () => {
  try {
    await intentStore.save(normalizeIntent(form.value));
    ElMessage.success("期望岗位已保存");
  } catch {
    ElMessage.error(intentStore.error ?? "期望岗位保存失败");
  }
};

const normalizeIntent = (intent: FineJobIntent): FineJobIntent => ({
  ...intent,
  target_title: intent.target_title.trim(),
  cities: normalizeList(intent.cities),
  keywords: normalizeList(intent.keywords),
  expanded_keywords: normalizeList(intent.expanded_keywords),
  excluded_keywords: normalizeList(intent.excluded_keywords),
  notes: intent.notes.trim()
});

const normalizeList = (values: string[]) => {
  const seen = new Set<string>();
  return values
    .map((value) => value.trim())
    .filter((value) => {
      if (!value || seen.has(value)) {
        return false;
      }
      seen.add(value);
      return true;
    });
};

const cloneIntent = (intent: FineJobIntent): FineJobIntent => ({
  ...emptyIntent(),
  ...intent,
  cities: [...intent.cities],
  keywords: [...intent.keywords],
  expanded_keywords: [...intent.expanded_keywords],
  excluded_keywords: [...intent.excluded_keywords]
});
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Job Intent</p>
        <h1>期望岗位</h1>
        <p class="secondary-text">
          这里决定系统搜索什么岗位、用哪些关键词打招呼，以及哪些岗位需要跳过。
        </p>
      </div>
      <div class="card-actions">
        <el-tag :type="ready ? 'success' : 'warning'">
          {{ ready ? "已完成" : "未完成" }}
        </el-tag>
        <el-button type="primary" :loading="intentStore.saving" @click="saveIntent">
          保存意向
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="intentStore.error"
      type="error"
      title="期望岗位操作失败"
      :description="intentStore.error"
      show-icon
    />

    <section v-loading="intentStore.loading" class="page-panel intent-form-panel">
      <div class="panel-title-row">
        <div>
          <p class="panel-eyebrow">Required</p>
          <h2>开始投递前必须填写</h2>
        </div>
        <span class="secondary-text">目标岗位、城市、核心关键词会影响搜索和筛选。</span>
      </div>

      <el-form label-position="top" class="intent-form">
        <div class="form-grid">
          <el-form-item label="目标岗位">
            <el-input v-model="form.target_title" placeholder="例如：大模型应用开发" />
          </el-form-item>

          <el-form-item label="城市">
            <el-select
              v-model="form.cities"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="例如：上海、杭州、远程"
            >
              <el-option label="北京" value="北京" />
              <el-option label="上海" value="上海" />
              <el-option label="杭州" value="杭州" />
              <el-option label="深圳" value="深圳" />
              <el-option label="广州" value="广州" />
              <el-option label="远程" value="远程" />
            </el-select>
          </el-form-item>

          <el-form-item label="工作模式">
            <el-select v-model="form.work_mode">
              <el-option label="不限" value="any" />
              <el-option label="现场办公" value="onsite" />
              <el-option label="混合办公" value="hybrid" />
              <el-option label="远程" value="remote" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item label="核心关键词">
          <el-select
            v-model="form.keywords"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="例如：大模型应用、AI Agent、RAG"
          />
        </el-form-item>

        <el-form-item label="扩展关键词">
          <el-select
            v-model="form.expanded_keywords"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="例如：Agent 开发、LangGraph、LLM 应用开发"
          />
        </el-form-item>

        <el-form-item label="排除关键词">
          <el-select
            v-model="form.excluded_keywords"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="例如：销售、客服、讲师、培训、外包、实习"
          />
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="最低薪资 K">
            <el-input-number v-model="form.salary_min" :min="0" :max="300" controls-position="right" />
          </el-form-item>
          <el-form-item label="最高薪资 K">
            <el-input-number v-model="form.salary_max" :min="0" :max="300" controls-position="right" />
          </el-form-item>
        </div>

        <el-form-item label="备注">
          <el-input
            v-model="form.notes"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
            placeholder="例如：优先产品型团队，不考虑纯外包，不接受长期出差。"
          />
        </el-form-item>
      </el-form>
    </section>
  </section>
</template>
