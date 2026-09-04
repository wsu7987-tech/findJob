import type {
  BossJobDetailSubset,
  BossJobIdentity,
  BossJobItemSubset,
  BossPageIdentity,
  BossPageKind,
} from "./types";

const PAGE_CONTAINER_SELECTORS = [
  "#wrap .page-job-wrapper",
  ".job-recommend-main",
  ".page-jobs-main",
] as const;

type IdentityInput = {
  pathname: string;
  loggedIn: boolean;
  pageVue: unknown;
  standaloneJobInfo?: unknown;
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;

const unwrap = (value: unknown): unknown => {
  const record = asRecord(value);
  return record && "value" in record ? record.value : value;
};

const readString = (record: Record<string, unknown> | null, key: string): string => {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
};

const readStandaloneJobIdentity = (value: unknown): BossJobIdentity | null => {
  const jobInfo = asRecord(value);
  if (!jobInfo) return null;
  const encryptJobId = readString(jobInfo, "job_id");
  const securityId = readString(jobInfo, "securityId");
  const encryptBossId = readString(jobInfo, "user_id");
  const jobName = readString(jobInfo, "job_name");
  if (!encryptJobId || !securityId || !encryptBossId || !jobName) return null;
  return {
    encryptJobId,
    securityId,
    encryptBossId,
    jobName,
    bossName: "",
    bossTitle: "",
    lid: "",
    contacted: null,
    identitySource: "standalone-job-info",
    bossIdentifierVerified: false,
  };
};

const readJobIdFromDetailPath = (pathname: string): string =>
  pathname.match(/\/job_detail\/([^/]+?)\.html(?:\/|$)/)?.[1] ?? "";

const communicationStateFromButton = (button: Element): boolean | null | "conflict" => {
  const text = button.textContent?.replace(/\s+/g, " ").trim() ?? "";
  const textState = text.includes("继续沟通") ? true : text.includes("立即沟通") ? false : null;
  const friendValue = button.getAttribute("data-isfriend");
  const attributeState = friendValue === "true" ? true : friendValue === "false" ? false : null;
  if (textState !== null && attributeState !== null && textState !== attributeState) return "conflict";
  return textState ?? attributeState;
};

const readButtonTarget = (button: Element): { jobId: string; securityId: string } | null => {
  const dataUrl = button.getAttribute("data-url");
  if (!dataUrl) return null;
  try {
    const url = new URL(dataUrl, "https://www.zhipin.com");
    return { jobId: url.searchParams.get("jobId") ?? "", securityId: url.searchParams.get("securityId") ?? "" };
  } catch {
    return null;
  }
};

const readDetailContact = (job: BossJobIdentity): boolean | null | "mismatch" => {
  const buttons = Array.from(document.querySelectorAll(".job-op a.btn-startchat"));
  if (buttons.length === 0) return null;
  const states = new Set<boolean>();
  for (const button of buttons) {
    const state = communicationStateFromButton(button);
    const target = readButtonTarget(button);
    if (
      state === "conflict" || state === null ||
      !target?.jobId || !target.securityId ||
      target.jobId !== job.encryptJobId || target.securityId !== job.securityId
    ) return "mismatch";
    states.add(state);
  }
  return states.size === 1 ? states.values().next().value ?? null : "mismatch";
};

export const detectBossPageKind = (pathname: string): BossPageKind => {
  if (pathname.includes("/web/geek/job-recommend")) return "recommend";
  if (pathname.includes("/web/geek/jobs")) return "search";
  if (pathname.includes("/job_detail/") || pathname.includes("/web/geek/job-detail")) return "detail";
  return "other";
};

const readJobItem = (value: unknown): BossJobItemSubset | null => {
  const item = asRecord(value);
  if (!item) return null;
  const encryptJobId = readString(item, "encryptJobId");
  const securityId = readString(item, "securityId");
  if (!encryptJobId || !securityId) return null;
  return {
    encryptJobId,
    securityId,
    encryptBossId: readString(item, "encryptBossId"),
    bossName: readString(item, "bossName"),
    bossTitle: readString(item, "bossTitle"),
    jobName: readString(item, "jobName"),
    lid: readString(item, "lid"),
    contact: typeof item.contact === "boolean" ? item.contact : null,
  };
};

const readJobDetail = (value: unknown): BossJobDetailSubset | null => {
  const detail = asRecord(unwrap(value));
  const jobInfo = asRecord(detail?.jobInfo);
  const bossInfo = asRecord(detail?.bossInfo);
  const relationInfo = asRecord(detail?.relationInfo);
  const encryptId = readString(jobInfo, "encryptId");
  const securityId = readString(detail, "securityId");
  if (!detail || !jobInfo || !bossInfo || !encryptId || !securityId) return null;
  return {
    securityId,
    lid: readString(detail, "lid"),
    jobInfo: {
      encryptId,
      encryptUserId: readString(jobInfo, "encryptUserId"),
      jobName: readString(jobInfo, "jobName"),
    },
    bossInfo: { name: readString(bossInfo, "name"), title: readString(bossInfo, "title") },
    relationInfo: { beFriend: typeof relationInfo?.beFriend === "boolean" ? relationInfo.beFriend : null },
  };
};

const createIdentity = (detail: BossJobDetailSubset, item: BossJobItemSubset | null): BossJobIdentity => ({
  encryptJobId: detail.jobInfo.encryptId,
  securityId: detail.securityId || item?.securityId || "",
  encryptBossId: item?.encryptBossId || detail.jobInfo.encryptUserId,
  jobName: detail.jobInfo.jobName || item?.jobName || "",
  bossName: detail.bossInfo.name || item?.bossName || "",
  bossTitle: detail.bossInfo.title || item?.bossTitle || "",
  lid: detail.lid || item?.lid || "",
  contacted: item ? item.contact : detail.relationInfo.beFriend,
  identitySource: "vue-list-detail",
  bossIdentifierVerified: true,
});

export const extractBossPageIdentity = ({
  pathname,
  loggedIn,
  pageVue,
  standaloneJobInfo,
}: IdentityInput): BossPageIdentity => {
  const pageKind = detectBossPageKind(pathname);
  const base = { component: "boss-page-identity" as const, pathname, pageKind, loggedIn };
  if (pageKind === "other") {
    return { ...base, state: "unsupported", job: null, reason: "当前页面不是已支持的 BOSS 岗位页面" };
  }

  const standaloneJob = readStandaloneJobIdentity(standaloneJobInfo);
  if (pageKind === "detail" && standaloneJob) {
    const pathJobId = readJobIdFromDetailPath(pathname);
    if (!pathJobId || pathJobId !== standaloneJob.encryptJobId) {
      return { ...base, state: "mismatch", job: null, reason: "详情页 URL 与岗位 ID 不匹配" };
    }
    const contacted = readDetailContact(standaloneJob);
    if (contacted === "mismatch") {
      return { ...base, state: "mismatch", job: null, reason: "详情页沟通按钮与岗位身份不一致" };
    }
    return {
      ...base,
      state: "ready",
      job: { ...standaloneJob, contacted },
      reason: "详情页岗位身份已识别",
    };
  }

  const pageRecord = asRecord(pageVue);
  if (!pageRecord) return { ...base, state: "waiting", job: null, reason: "等待 BOSS 岗位容器" };
  const rawList = unwrap(pageRecord.jobList);
  const items = Array.isArray(rawList)
    ? rawList.map(readJobItem).filter((item): item is BossJobItemSubset => item !== null)
    : [];
  const detail = readJobDetail(pageRecord.jobDetail);
  if (!detail) return { ...base, state: "waiting", job: null, reason: "等待当前岗位详情" };
  if (pageKind !== "detail" && items.length === 0) {
    return { ...base, state: "waiting", job: null, reason: "等待当前页面岗位列表" };
  }
  const matchedItem = items.find((item) => item.lid && item.lid === detail.lid)
    ?? items.find((item) => item.encryptJobId === detail.jobInfo.encryptId)
    ?? null;
  if (pageKind !== "detail" && !matchedItem) {
    return { ...base, state: "mismatch", job: null, reason: "岗位详情与当前列表不匹配" };
  }
  const job = createIdentity(detail, matchedItem);
  if (!job.encryptJobId || !job.securityId || !job.encryptBossId || !job.jobName) {
    return { ...base, state: "unavailable", job: null, reason: "岗位身份字段不完整" };
  }
  return { ...base, state: "ready", job, reason: loggedIn ? "岗位身份已识别" : "岗位已识别，但登录状态未确认" };
};

const findPageVue = (): unknown => {
  for (const selector of PAGE_CONTAINER_SELECTORS) {
    const element = document.querySelector(selector) as (Element & { __vue__?: unknown }) | null;
    if (element?.__vue__) return element.__vue__;
  }
  return null;
};

const detectLoggedIn = (): boolean => {
  const page = asRecord((window as unknown as { _PAGE?: unknown })._PAGE);
  if (readString(page, "encryptUserId")) return true;
  const userInfo = asRecord((window as unknown as { _userInfo?: unknown })._userInfo);
  return userInfo?.isLogin === true;
};

export const readBossPageIdentity = (): BossPageIdentity => extractBossPageIdentity({
  pathname: window.location.pathname,
  loggedIn: detectLoggedIn(),
  pageVue: findPageVue(),
  standaloneJobInfo: (window as unknown as { _jobInfo?: unknown })._jobInfo,
});
