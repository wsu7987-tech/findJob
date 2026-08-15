<script setup lang="ts">
import { computed } from "vue";

const DEFAULT_CATEGORIES: string[] = ["engineering", "research", "product", "general"];
const DEFAULT_TAGS: string[] = [
  "backend",
  "frontend",
  "database",
  "api",
  "rag",
  "workflow",
  "ocr",
  "summary",
  "research",
  "product"
];

const props = withDefaults(
  defineProps<{
    category: string;
    tags: string[];
    suggestLoading?: boolean;
    testIdPrefix?: string;
  }>(),
  {
    suggestLoading: false,
    testIdPrefix: "metadata"
  }
);

const emit = defineEmits<{
  "update:category": [value: string];
  "update:tags": [value: string[]];
  suggest: [];
}>();

const labels = {
  heading: "\u5206\u7c7b\u4e0e\u6807\u7b7e",
  description: "\u53ef\u4ee5\u624b\u52a8\u9009\u62e9\uff0c\u4e5f\u53ef\u4ee5\u5148\u751f\u6210\u5efa\u8bae\u3002",
  category: "\u5206\u7c7b",
  tags: "\u6807\u7b7e",
  suggest: "\u5efa\u8bae\u5206\u7c7b\u4e0e\u6807\u7b7e",
  categoryPlaceholder: "可选：输入分类…",
  tagsPlaceholder: "可选：选择或输入多个标签…"
} as const;

const normalizeTags = (values: unknown[]) => {
  const uniqueTags: string[] = [];
  for (const item of values) {
    const normalized = String(item).trim();
    if (normalized && !uniqueTags.includes(normalized)) {
      uniqueTags.push(normalized);
    }
  }
  return uniqueTags;
};

const categoryOptions = computed(() => {
  const options = [...DEFAULT_CATEGORIES];
  if (props.category && !options.includes(props.category)) {
    options.unshift(props.category);
  }
  return options;
});

const tagOptions = computed(() => {
  const options = [...DEFAULT_TAGS];
  for (const tag of props.tags) {
    if (!options.includes(tag)) {
      options.unshift(tag);
    }
  }
  return options;
});

const handleCategoryChange = (value: string | null) => {
  emit("update:category", (value ?? "").trim());
};

const handleTagsChange = (value: unknown) => {
  const nextValues = Array.isArray(value) ? value : [];
  emit("update:tags", normalizeTags(nextValues));
};
</script>

<template>
  <div class="pool-metadata-fields">
    <div class="pool-metadata-fields__header">
      <div class="pool-metadata-fields__title-block">
        <strong>{{ labels.heading }}</strong>
        <p class="pool-metadata-fields__description">{{ labels.description }}</p>
      </div>
      <el-button
        class="pool-metadata-fields__suggest"
        type="primary"
        plain
        size="small"
        :loading="suggestLoading"
        :data-testid="`${testIdPrefix}-suggest`"
        @click="emit('suggest')"
      >
        {{ labels.suggest }}
      </el-button>
    </div>

    <div class="pool-metadata-fields__grid">
      <el-form-item :label="labels.category">
        <el-select
          :data-testid="`${testIdPrefix}-category`"
          :aria-label="labels.category"
          :model-value="category || null"
          filterable
          allow-create
          default-first-option
          clearable
          :placeholder="labels.categoryPlaceholder"
          @update:model-value="handleCategoryChange"
        >
          <el-option
            v-for="option in categoryOptions"
            :key="option"
            :label="option"
            :value="option"
          />
        </el-select>
      </el-form-item>

      <el-form-item :label="labels.tags">
        <el-select
          :data-testid="`${testIdPrefix}-tags`"
          :aria-label="labels.tags"
          :model-value="tags"
          multiple
          filterable
          allow-create
          default-first-option
          :placeholder="labels.tagsPlaceholder"
          @update:model-value="handleTagsChange"
        >
          <el-option v-for="option in tagOptions" :key="option" :label="option" :value="option" />
        </el-select>
      </el-form-item>
    </div>
  </div>
</template>

<style scoped>
.pool-metadata-fields {
  display: grid;
  gap: 12px;
  padding: 14px 16px 16px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.94));
}

.pool-metadata-fields__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.pool-metadata-fields__title-block {
  display: grid;
  gap: 4px;
}

.pool-metadata-fields__title-block strong {
  font-size: 14px;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.pool-metadata-fields__description {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.pool-metadata-fields__suggest {
  flex-shrink: 0;
}

.pool-metadata-fields__grid {
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(180px, 0.9fr) minmax(0, 1.1fr);
}

.pool-metadata-fields :deep(.el-form-item) {
  margin-bottom: 0;
}

.pool-metadata-fields :deep(.el-form-item__label) {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.pool-metadata-fields :deep(.el-select) {
  width: 100%;
}

@media (max-width: 720px) {
  .pool-metadata-fields__header {
    flex-direction: column;
  }

  .pool-metadata-fields__grid {
    grid-template-columns: 1fr;
  }
}
</style>
