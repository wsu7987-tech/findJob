<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

const props = defineProps<{
  option: EChartsOption;
  height?: number;
  ariaLabel?: string;
}>();

const root = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

const render = () => {
  if (!root.value) {
    return;
  }

  chart ??= echarts.init(root.value);
  chart.setOption(props.option);
  chart.resize();
};

onMounted(() => {
  render();
  window.addEventListener("resize", render);
});

watch(
  () => props.option,
  () => {
    render();
  },
  { deep: true }
);

onBeforeUnmount(() => {
  window.removeEventListener("resize", render);
  chart?.dispose();
});
</script>

<template>
  <div
    ref="root"
    class="chart-surface"
    role="img"
    :aria-label="ariaLabel ?? '数据图表'"
    :style="{ height: `${height ?? 260}px` }"
  ></div>
</template>
