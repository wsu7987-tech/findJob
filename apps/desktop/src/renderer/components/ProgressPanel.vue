<script setup lang="ts">
import type { UiRunSnapshot } from "@/types";
import { mapRunStageLabel } from "@/services/contract";

defineProps<{
  snapshot: UiRunSnapshot | null;
  streamMode: "idle" | "sse" | "polling";
}>();
</script>

<template>
  <div v-if="snapshot" class="progress-panel">
    <div class="progress-panel__header">
      <div>
        <p class="panel-eyebrow">运行状态</p>
        <h3>{{ mapRunStageLabel(snapshot.stage) }}</h3>
      </div>
      <el-tag round :type="streamMode === 'polling' ? 'warning' : 'success'">
        {{
          streamMode === "sse"
            ? "实时推送"
            : streamMode === "polling"
              ? "轮询兜底"
              : "静态快照"
        }}
      </el-tag>
    </div>

    <el-progress
      :percentage="snapshot.progressPercent"
      :stroke-width="16"
      :show-text="false"
      status="success"
    />

    <div class="progress-grid">
      <div>
        <span>智能执行器</span>
        <strong>{{ snapshot.executorType === "codex-cli" ? "Codex CLI" : "LLM" }}</strong>
      </div>
      <div>
        <span>模型 / 推理强度</span>
        <strong>{{ snapshot.modelName ?? "默认" }} / {{ snapshot.reasoningEffort ?? "默认" }}</strong>
      </div>
      <div>
        <span>总数</span>
        <strong>{{ snapshot.totalItems }}</strong>
      </div>
      <div>
        <span>已处理</span>
        <strong>{{ snapshot.totalProcessed }}</strong>
      </div>
      <div>
        <span>当前条目</span>
        <strong>{{ snapshot.currentItemLabel ?? "等待中…" }}</strong>
      </div>
      <div>
        <span>更新时间</span>
        <strong>{{ snapshot.updatedAt }}</strong>
      </div>
    </div>
  </div>
</template>
