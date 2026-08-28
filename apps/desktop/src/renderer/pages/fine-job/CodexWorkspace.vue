<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";

import CodexTerminal from "@/components/CodexTerminal.vue";
import { getCodexBridge } from "@/services/desktop-bridge";
import { useFineJobCodexStore } from "@/stores/fineJobCodex";
import type { FineJobCodexPermissions } from "@/types";

const store = useFineJobCodexStore();
const terminal = ref<{
  copyAll: () => Promise<boolean>;
  copySelection: () => Promise<boolean>;
  focus: () => void;
  paste: () => Promise<boolean>;
} | null>(null);
const terminalSize = ref({ cols: 120, rows: 36 });
const savingPermissions = ref(false);
const copyMessage = ref("");

const labels: Record<string, string> = {
  send_greeting: "发送打招呼",
  send_chat_reply: "发送代聊回复",
  send_contact_info: "发送联系方式",
  send_commitment_reply: "发送承诺性回复",
  send_interview_decision: "发送面试决定",
  start_greeting_batch: "启动批量打招呼",
  resume_external_executor: "恢复外部执行器",
  submit_application: "提交投递",
  change_automation_policy: "修改自动化策略"
};

const isRunning = computed(() => store.status === "running" || store.status === "starting");

const start = async (resume = false) => {
  await store.start(terminalSize.value.cols, terminalSize.value.rows, resume);
  await nextTick();
  terminal.value?.focus();
};

const stop = () => getCodexBridge()?.stopCodex?.();
const interrupt = () => getCodexBridge()?.interruptCodex?.();

const showClipboardMessage = (successMessage: string, failureMessage: string, success: boolean) => {
  copyMessage.value = success ? successMessage : failureMessage;
  globalThis.setTimeout(() => {
    copyMessage.value = "";
  }, 1800);
};

const copySelection = async () => {
  showClipboardMessage("已复制", "请先选择要复制的内容", Boolean(await terminal.value?.copySelection()));
};

const copyAll = async () => {
  showClipboardMessage("已复制", "当前没有可复制的会话内容", Boolean(await terminal.value?.copyAll()));
};

const paste = async () => {
  showClipboardMessage("已粘贴", "剪贴板没有可粘贴的文本", Boolean(await terminal.value?.paste()));
};

const savePermissions = async () => {
  if (!store.permissions) return;
  savingPermissions.value = true;
  try {
    await store.savePermissions({
      ...store.permissions,
      permissions: { ...store.permissions.permissions }
    } as FineJobCodexPermissions);
  } finally {
    savingPermissions.value = false;
  }
};

onMounted(() => void store.load());
</script>

<template>
  <section class="codex-workspace">
    <header class="page-heading">
      <div>
        <p class="app-shell__eyebrow">Codex × FineJob</p>
        <h3>业务协作工作台</h3>
        <p class="secondary-text">Codex 通过本机 MCP 调用岗位、简历、打招呼与代聊能力。</p>
      </div>
      <div class="card-actions">
        <el-tag :type="isRunning ? 'success' : 'info'">{{ store.status }}</el-tag>
        <el-button :disabled="isRunning" type="primary" @click="start(false)">新建会话</el-button>
        <el-button :disabled="isRunning" @click="start(true)">恢复最近会话</el-button>
        <el-button :disabled="!isRunning" @click="interrupt">中断</el-button>
        <el-button :disabled="!isRunning" @click="stop">结束</el-button>
      </div>
    </header>

    <el-alert v-if="store.error" :title="store.error" type="error" :closable="false" />
    <el-alert
      v-if="store.statusMessage"
      :title="store.statusMessage"
      :type="store.status === 'failed' ? 'error' : 'info'"
      show-icon
    />

    <div class="surface-card terminal-card">
      <div class="terminal-toolbar">
        <span class="secondary-text">拖动选择文本后可按 Ctrl/Cmd+C 复制</span>
        <div class="card-actions">
          <span v-if="copyMessage" class="secondary-text">{{ copyMessage }}</span>
          <el-button :disabled="!terminal" @click="paste">粘贴</el-button>
          <el-button :disabled="!terminal" @click="copySelection">复制选中内容</el-button>
          <el-button :disabled="!terminal" @click="copyAll">复制全部会话</el-button>
        </div>
      </div>
      <CodexTerminal ref="terminal" @ready="terminalSize = $event" />
    </div>

    <div class="codex-columns">
      <section class="surface-card">
        <div class="page-heading">
          <div>
            <h3>敏感操作预授权</h3>
            <p class="secondary-text">总开关与操作开关同时启用后，匹配分类的动作可进入业务队列。</p>
          </div>
          <el-switch
            v-if="store.permissions"
            v-model="store.permissions.enabled"
            active-text="启用"
            @change="savePermissions"
          />
        </div>
        <div v-if="store.permissions" class="permission-list">
          <label v-for="(_, key) in store.permissions.permissions" :key="key">
            <span>{{ labels[key] ?? key }}</span>
            <el-switch
              v-model="store.permissions.permissions[key]"
              :disabled="!store.permissions.supported[key]"
              @change="savePermissions"
            />
          </label>
        </div>
      </section>

      <section class="surface-card">
        <div class="page-heading">
          <div>
            <h3>待确认卡片</h3>
            <p class="secondary-text">当前共 {{ store.pendingCount }} 项。</p>
          </div>
          <el-button :loading="store.loading" @click="store.load">刷新</el-button>
        </div>
        <el-empty
          v-if="store.pending.greetings.length + store.pending.chat_replies.length === 0"
          description="暂无待确认内容"
        />
        <div v-else class="pending-list">
          <article v-for="item in store.pending.greetings" :key="item.id" class="pending-card">
            <strong>打招呼预览</strong>
            <p>{{ item.final_message || item.draft_message }}</p>
            <div class="card-actions">
              <el-button type="primary" @click="store.decide('greeting_preview', item, 'approve')">确认</el-button>
              <el-button @click="store.decide('greeting_preview', item, 'reject')">拒绝</el-button>
            </div>
          </article>
          <article v-for="item in store.pending.chat_replies" :key="item.id" class="pending-card">
            <strong>代聊回复</strong>
            <p>{{ item.final_text }}</p>
            <div class="card-actions">
              <el-button type="primary" @click="store.decide('chat_reply', item, 'approve')">确认</el-button>
              <el-button @click="store.decide('chat_reply', item, 'reject')">拒绝</el-button>
            </div>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.codex-workspace,
.permission-list,
.pending-list {
  display: grid;
  gap: 14px;
}

.terminal-card {
  padding: 10px;
}

.terminal-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 2px 10px;
}

.codex-columns {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(380px, 1.1fr);
  gap: 14px;
}

.permission-list label,
.pending-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
}

.pending-card {
  align-items: flex-start;
  flex-direction: column;
}

.pending-card p {
  white-space: pre-wrap;
}

@media (max-width: 1100px) {
  .codex-columns { grid-template-columns: 1fr; }
}
</style>
