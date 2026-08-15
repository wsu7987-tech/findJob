<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import {
  CircleCheckFilled,
  Clock,
  Connection,
  Lock,
  Operation,
  Promotion,
  Suitcase
} from "@element-plus/icons-vue";

import { useConfigStore } from "@/stores/config";
import { useFineJobDeliveryRunsStore } from "@/stores/fineJobDeliveryRuns";
import { useFineJobDeliveryStrategyStore } from "@/stores/fineJobDeliveryStrategy";
import { useFineJobIntentStore } from "@/stores/fineJobIntent";
import { useFineJobPlatformSessionsStore } from "@/stores/fineJobPlatformSessions";
import { useFineJobResumesStore } from "@/stores/fineJobResumes";

type ReadinessState = "ready" | "missing" | "optional";

const emit = defineEmits<{
  (event: "open-settings"): void;
}>();

const configStore = useConfigStore();
const runsStore = useFineJobDeliveryRunsStore();
const strategyStore = useFineJobDeliveryStrategyStore();
const intentStore = useFineJobIntentStore();
const platformStore = useFineJobPlatformSessionsStore();
const resumesStore = useFineJobResumesStore();
const router = useRouter();

const llmReady = computed(() => configStore.llmStatus.status === "ready");

onMounted(() => {
  void strategyStore.load();
  void intentStore.load();
  void platformStore.load();
  void resumesStore.load();
});

const requiredItems = computed(() => [
  {
    key: "llm",
    title: "LLM 配置",
    description: llmReady.value
      ? "模型连通性正常，可用于岗位匹配、打招呼和回复草稿。"
      : "需要先配置并测试 LLM。Embedding 可选，不阻塞开始投递。",
    state: llmReady.value ? "ready" : "missing",
    action: llmReady.value ? "已就绪" : "去配置",
    routeName: null,
    icon: Connection
  },
  {
    key: "intent",
    title: "期望岗位",
    description: intentStore.ready
      ? "已设置目标岗位、城市和搜索关键词。"
      : "需要设置目标岗位、城市和至少一个搜索关键词。",
    state: intentStore.ready ? "ready" : "missing",
    action: intentStore.ready ? "查看意向" : "补充意向",
    routeName: "fine-job-intent",
    icon: Suitcase
  },
  {
    key: "platform",
    title: "平台登录",
    description: platformStore.bossReady
      ? "BOSS 登录态已标记可用，后续自动化会复用该浏览器会话。"
      : "需要完成 BOSS 可见浏览器登录态确认。",
    state: platformStore.bossReady ? "ready" : "missing",
    action: platformStore.bossReady ? "查看登录态" : "去登录",
    routeName: "fine-job-platform",
    icon: Lock
  },
  {
    key: "strategy",
    title: "投递策略",
    description: strategyStore.ready
      ? "已确认自动化等级、限频和高风险动作确认规则。"
      : "需要确认自动化等级、每日上限、是否自动打招呼和投递确认规则。",
    state: strategyStore.ready ? "ready" : "missing",
    action: strategyStore.ready ? "查看策略" : "确认策略",
    routeName: "fine-job-strategy",
    icon: Operation
  }
] satisfies Array<{
  key: string;
  title: string;
  description: string;
  state: ReadinessState;
  action: string;
  routeName: string | null;
  icon: unknown;
}>);

const optionalItems = computed(() => [
  {
    title: "简历资料",
    description: resumesStore.hasResume
      ? "已上传简历，可用于 JD 匹配、投递判断和回复草稿增强。"
      : "可稍后上传并确认信息；打招呼最小闭环不依赖本地简历解析。",
    state: resumesStore.hasResume ? "ready" : ("optional" as const),
    action: resumesStore.hasResume ? "查看简历" : "上传简历",
    routeName: "fine-job-resumes"
  },
  {
    title: "Embedding 语义记忆",
    description: "可提升相似岗位、历史决策和对话检索效果；第一版不配置也能开始投递。",
    state: "optional" as const,
    action: "去配置",
    routeName: null
  },
  {
    title: "回答模板",
    description: "可提前准备离职原因、通勤、薪资、到岗时间等常见回复。",
    state: "optional" as const,
    action: "稍后配置",
    routeName: null
  }
]);

const missingItems = computed(() => requiredItems.value.filter((item) => item.state === "missing"));
const readyCount = computed(() => requiredItems.value.length - missingItems.value.length);
const canStart = computed(() => missingItems.value.length === 0);

const statusCopy = computed(() => {
  if (canStart.value) {
    return {
      label: "准备完成",
      title: "可以开始投递",
      description: "关键资料已经补齐。启动后系统会按策略搜索岗位、匹配 JD、生成打招呼，并把高风险动作放入待确认。"
    };
  }

  return {
    label: "未就绪",
    title: `还缺 ${missingItems.value.length} 项，暂不能开始投递`,
    description: "先补齐关键前置条件。系统不会在 LLM、岗位意向、平台登录或投递策略缺失时启动自动化。"
  };
});

const handleItemAction = async (item: (typeof requiredItems.value)[number]) => {
  if (item.key === "llm") {
    emit("open-settings");
    return;
  }
  if (item.routeName) {
    await router.push({ name: item.routeName });
  }
};

const startDryRun = async () => {
  if (!canStart.value) {
    return;
  }
  const run = await runsStore.createDryRun();
  await router.push({ name: "fine-job-runs", query: { runId: run.id } });
};
</script>

<template>
  <section class="page-stack fine-job-page readiness-page">
    <div class="readiness-hero" :class="{ 'readiness-hero--ready': canStart }">
      <div>
        <p class="panel-eyebrow">Delivery Readiness</p>
        <h1>{{ statusCopy.title }}</h1>
        <p>{{ statusCopy.description }}</p>
      </div>

      <div class="readiness-hero__action">
        <el-tag size="large" :type="canStart ? 'success' : 'warning'">{{ statusCopy.label }}</el-tag>
        <el-button
          type="primary"
          size="large"
          :disabled="!canStart"
          :loading="runsStore.creating"
          :icon="Promotion"
          @click="startDryRun"
        >
          开始投递
        </el-button>
      </div>
    </div>

    <div class="readiness-summary">
      <article class="metric-card">
        <span>准备进度</span>
        <strong>{{ readyCount }}/{{ requiredItems.length }}</strong>
        <p>必须项完成数量</p>
      </article>
      <article class="metric-card">
        <span>运行状态</span>
        <strong>未启动</strong>
        <p>补齐准备项后可启动</p>
      </article>
      <article class="metric-card">
        <span>待确认</span>
        <strong>0</strong>
        <p>发送、投递、联系方式等动作会进入这里</p>
      </article>
      <article class="metric-card">
        <span>今日投递</span>
        <strong>0</strong>
        <p>等待启动首个任务</p>
      </article>
    </div>

    <div class="readiness-layout">
      <section class="page-panel">
        <div class="panel-title-row">
          <div>
            <p class="panel-eyebrow">Required</p>
            <h2>开始投递前必须补齐</h2>
          </div>
          <span class="secondary-text">缺失项会阻止“开始投递”</span>
        </div>

        <div class="readiness-list">
          <article
            v-for="item in requiredItems"
            :key="item.key"
            class="readiness-item"
            :class="`readiness-item--${item.state}`"
          >
            <div class="readiness-item__icon">
              <component :is="item.state === 'ready' ? CircleCheckFilled : item.icon" />
            </div>
            <div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.description }}</p>
            </div>
            <el-button
              :type="item.state === 'ready' ? 'success' : 'primary'"
              plain
              @click="handleItemAction(item)"
            >
              {{ item.action }}
            </el-button>
          </article>
        </div>
      </section>

      <aside class="page-panel">
        <p class="panel-eyebrow">Optional</p>
        <h2>可选增强，不阻塞启动</h2>
        <div class="optional-list">
          <article v-for="item in optionalItems" :key="item.title" class="optional-item">
            <component :is="item.state === 'ready' ? CircleCheckFilled : Clock" />
            <div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.description }}</p>
            </div>
            <el-button
              v-if="item.routeName"
              size="small"
              plain
              @click="router.push({ name: item.routeName })"
            >
              {{ item.action }}
            </el-button>
          </article>
        </div>
      </aside>
    </div>
  </section>
</template>
