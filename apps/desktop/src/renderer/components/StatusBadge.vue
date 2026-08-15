<script setup lang="ts">
import { computed } from "vue";

import { mapPoolStatus } from "@/services/contract";

const props = defineProps<{
  status: string;
  run?: boolean;
}>();

const tagType = computed(() => {
  if (props.status === "completed" || props.status === "succeeded") {
    return "success";
  }
  if (props.status === "running" || props.status === "pending") {
    return "warning";
  }
  if (props.status === "failed" || props.status === "cancelled") {
    return "danger";
  }
  return "info";
});

const label = computed(() => {
  if (props.run) {
    const runMap: Record<string, string> = {
      pending: "待启动",
      running: "进行中",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消"
    };

    return runMap[props.status] ?? props.status;
  }

  return mapPoolStatus(props.status);
});
</script>

<template>
  <el-tag effect="dark" round :type="tagType">{{ label }}</el-tag>
</template>
