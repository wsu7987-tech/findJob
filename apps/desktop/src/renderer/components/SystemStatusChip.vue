<script setup lang="ts">
import { computed } from "vue";

import type { CapabilityStatusState } from "@/stores/config";

const props = defineProps<{
  label: string;
  state: CapabilityStatusState;
}>();

const statusLabel = computed(() => {
  const map: Record<CapabilityStatusState["status"], string> = {
    unknown: "未检测",
    checking: "检测中",
    ready: "可用",
    failed: "失败",
    invalid: "待配置"
  };

  return map[props.state.status];
});
</script>

<template>
  <div
    class="system-status-chip"
    :class="`system-status-chip--${state.status}`"
    :title="state.detail"
  >
    <span class="system-status-chip__label">{{ label }}</span>
    <strong>{{ statusLabel }}</strong>
    <span class="system-status-chip__detail">{{ state.detail }}</span>
  </div>
</template>
