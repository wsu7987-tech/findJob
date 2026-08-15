<script setup lang="ts">
defineProps<{
  label: string;
  value: string | number;
  note?: string;
}>();
</script>

<template>
  <article class="metric-card">
    <p>{{ label }}</p>
    <strong>{{ value }}</strong>
    <span v-if="note">{{ note }}</span>
  </article>
</template>
