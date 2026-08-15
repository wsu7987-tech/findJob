<script setup lang="ts">
defineProps<{
  title: string;
  detail: string;
  type?: "warning" | "error" | "info" | "success";
}>();
</script>

<template>
  <el-alert
    :type="type ?? 'warning'"
    :title="title"
    :description="detail"
    show-icon
    :closable="false"
  />
</template>
