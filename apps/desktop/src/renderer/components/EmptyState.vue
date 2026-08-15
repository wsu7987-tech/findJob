<script setup lang="ts">
defineProps<{
  title: string;
  description: string;
  actionText?: string;
}>();

const emit = defineEmits<{
  (event: "action"): void;
}>();
</script>

<template>
  <div class="empty-state">
    <p class="empty-state__eyebrow">暂无数据</p>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <el-button v-if="actionText" type="primary" plain @click="emit('action')">
      {{ actionText }}
    </el-button>
  </div>
</template>
