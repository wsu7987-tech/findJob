import { createRouter, createWebHashHistory } from "vue-router";

import AutomationLogsPage from "@/pages/fine-job/AutomationLogs.vue";
import DeliveryReadinessPage from "@/pages/fine-job/DeliveryReadiness.vue";
import DeliveryRunStatusPage from "@/pages/fine-job/DeliveryRunStatus.vue";
import DeliveryStrategyPage from "@/pages/fine-job/DeliveryStrategy.vue";
import JobIntentPage from "@/pages/fine-job/JobIntent.vue";
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
      name: "fine-job-intent",
      component: JobIntentPage,
      meta: { title: "期望岗位" }
    },
    {
      path: "/fine-job/platform",
      name: "fine-job-platform",
      component: PlatformLoginPage,
      meta: { title: "平台登录" }
    },
    {
      path: "/fine-job/strategy",
      name: "fine-job-strategy",
      component: DeliveryStrategyPage,
      meta: { title: "投递策略" }
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
