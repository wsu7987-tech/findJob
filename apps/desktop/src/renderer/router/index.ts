import { createRouter, createWebHashHistory } from "vue-router";

import AutomationLogsPage from "@/pages/fine-job/AutomationLogs.vue";
import BossCapturePage from "@/pages/fine-job/BossCapture.vue";
import BossCaptureHistoryPage from "@/pages/fine-job/BossCaptureHistory.vue";
import DeliveryReadinessPage from "@/pages/fine-job/DeliveryReadiness.vue";
import DeliveryRunStatusPage from "@/pages/fine-job/DeliveryRunStatus.vue";
import StrategyManagementPage from "@/pages/fine-job/StrategyManagement.vue";
import PlatformLoginPage from "@/pages/fine-job/PlatformLogin.vue";
import ResumeProfilePage from "@/pages/fine-job/ResumeProfile.vue";
import ReviewQueuePage from "@/pages/fine-job/ReviewQueue.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: "/",
      redirect: "/fine-job"
    },
    {
      path: "/fine-job",
      name: "fine-job-dashboard",
      component: DeliveryReadinessPage,
      meta: { title: "投递准备" }
    },
    {
      path: "/fine-job/resumes",
      name: "fine-job-resumes",
      component: ResumeProfilePage,
      meta: { title: "简历资料" }
    },
    {
      path: "/fine-job/intent",
      redirect: "/fine-job/strategy"
    },
    {
      path: "/fine-job/platform",
      name: "fine-job-platform",
      component: PlatformLoginPage,
      meta: { title: "平台登录" }
    },
    {
      path: "/fine-job/capture",
      name: "fine-job-capture",
      component: BossCapturePage,
      meta: { title: "岗位采集" }
    },
    {
      path: "/fine-job/capture-history",
      name: "fine-job-capture-history",
      component: BossCaptureHistoryPage,
      meta: { title: "历史采集" }
    },
    {
      path: "/fine-job/strategy",
      name: "fine-job-strategy",
      component: StrategyManagementPage,
      meta: { title: "策略管理" }
    },
    {
      path: "/fine-job/runs",
      name: "fine-job-runs",
      component: DeliveryRunStatusPage,
      meta: { title: "运行状态" }
    },
    {
      path: "/fine-job/review",
      name: "fine-job-review",
      component: ReviewQueuePage,
      meta: { title: "待确认" }
    },
    {
      path: "/fine-job/logs",
      name: "fine-job-logs",
      component: AutomationLogsPage,
      meta: { title: "动作日志" }
    }
  ]
});
