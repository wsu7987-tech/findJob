<script setup lang="ts">
import type { ComponentState, FrameworkStatus } from "../executor/framework-mode";
import type { BossJobIdentity, BossPageKind, BossProbeState } from "../platform/boss/types";

defineProps<{
  status: FrameworkStatus;
}>();

const label = (state: ComponentState) =>
  ({ checking: "检查中", ready: "正常", error: "异常" })[state];

const probeLabel = (state: BossProbeState) =>
  ({
    ready: "正常",
    waiting: "等待",
    unsupported: "不支持",
    mismatch: "错配",
    unavailable: "不可执行"
  })[state];

const pageLabel = (kind?: BossPageKind) =>
  kind
    ? ({ search: "搜索", recommend: "推荐", detail: "详情", other: "其他" })[kind]
    : "等待识别";

const suffix = (value?: string) => (value ? `…${value.slice(-8)}` : "未识别");

const contactedLabel = (value?: boolean | null) =>
  value === true ? "是" : value === false ? "否" : "未知";

const identitySourceLabel = (job?: BossJobIdentity | null) => {
  if (!job) return "未识别";
  return job.identitySource === "standalone-job-info"
    ? "详情页 _jobInfo"
    : "Vue 列表与详情";
};

const hrLabel = (job?: BossJobIdentity | null) => {
  if (!job) return "未识别";
  const displayName = `${job.bossName} ${job.bossTitle}`.trim();
  return displayName || `标识 ${suffix(job.encryptBossId)}（待验证）`;
};
</script>

<template>
  <section class="finejob-panel" aria-label="FineJob BOSS 执行器状态">
    <header>FineJob BOSS 执行器</header>
    <p class="success">框架已加载</p>
    <dl>
      <div>
        <dt>Background</dt>
        <dd :data-state="status.background">{{ label(status.background) }}</dd>
      </div>
      <div>
        <dt>Content</dt>
        <dd :data-state="status.content">{{ label(status.content) }}</dd>
      </div>
      <div>
        <dt>Main World</dt>
        <dd :data-state="status.mainWorld">{{ label(status.mainWorld) }}</dd>
      </div>
      <div>
        <dt>模式</dt>
        <dd>只读框架</dd>
      </div>
      <div>
        <dt>FineJob</dt>
        <dd>尚未连接</dd>
      </div>
      <div>
        <dt>真实动作</dt>
        <dd data-state="error">已禁用</dd>
      </div>
    </dl>
    <section class="probe">
      <p class="probe-title">岗位只读识别</p>
      <dl>
        <div>
          <dt>岗位识别</dt>
          <dd :data-state="status.bossProbe">{{ probeLabel(status.bossProbe) }}</dd>
        </div>
        <div>
          <dt>页面类型</dt>
          <dd>{{ pageLabel(status.bossSnapshot?.pageKind) }}</dd>
        </div>
        <div>
          <dt>登录状态</dt>
          <dd>{{ status.bossSnapshot?.loggedIn ? "正常" : "未识别" }}</dd>
        </div>
        <div>
          <dt>岗位</dt>
          <dd>{{ status.bossSnapshot?.job?.jobName || "未识别" }}</dd>
        </div>
        <div>
          <dt>身份来源</dt>
          <dd>{{ identitySourceLabel(status.bossSnapshot?.job) }}</dd>
        </div>
        <div>
          <dt>HR</dt>
          <dd>{{ hrLabel(status.bossSnapshot?.job) }}</dd>
        </div>
        <div>
          <dt>岗位 ID</dt>
          <dd>{{ status.bossSnapshot?.job?.encryptJobId }}</dd>
        </div>
        <div>
          <dt>已沟通</dt>
          <dd>{{ contactedLabel(status.bossSnapshot?.job?.contacted) }}</dd>
        </div>
      </dl>
      <p class="detail">{{ status.bossSnapshot?.reason || "等待岗位页面数据" }}</p>
    </section>
    <p class="page">页面：{{ status.page || "等待识别" }}</p>
    <p v-if="status.detail" class="detail">{{ status.detail }}</p>
  </section>
</template>
