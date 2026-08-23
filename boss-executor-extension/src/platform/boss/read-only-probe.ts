import type {
  BossJobDetailSubset,
  BossJobIdentity,
  BossJobItemSubset,
  BossPageKind,
  BossReadOnlySnapshot
} from "./types";

const PAGE_CONTAINER_SELECTORS = [
  "#wrap .page-job-wrapper",
  ".job-recommend-main",
  ".page-jobs-main"
] as const;

type ProbeInput = {
  pathname: string;
  loggedIn: boolean;
  pageVue: unknown;
  standaloneJobInfo?: unknown;
  standaloneDetailEvidence?: StandaloneDetailEvidence;
  observedAt?: number;
};

type StandaloneDetailEvidence = {
  state: "ready" | "waiting" | "mismatch";
  bossName: string;
  contacted: boolean | null;
  lid: string;
  reason: string;
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
    // 保存页面和参考项目尚未证明 user_id 的业务语义，后续动作必须继续阻断。
    bossIdentifierVerified: false
  };
};

const readJobIdFromDetailPath = (pathname: string): string => {
  const matched = pathname.match(/\/job_detail\/([^/]+?)\.html(?:\/|$)/);
  return matched?.[1] ?? "";
};

const communicationStateFromButton = (button: Element): boolean | null | "conflict" => {
  const text = button.textContent?.replace(/\s+/g, " ").trim() ?? "";
  const textState = text.includes("继续沟通")
    ? true
    : text.includes("立即沟通")
      ? false
      : null;
  const friendValue = button.getAttribute("data-isfriend");
  const attributeState = friendValue === "true" ? true : friendValue === "false" ? false : null;
  if (textState !== null && attributeState !== null && textState !== attributeState) {
    return "conflict";
  }
  return textState ?? attributeState;
};

const readButtonTarget = (button: Element): { jobId: string; securityId: string } | null => {
  const dataUrl = button.getAttribute("data-url");
  if (!dataUrl) return null;
  try {
    const url = new URL(dataUrl, "https://www.zhipin.com");
    return {
      jobId: url.searchParams.get("jobId") ?? "",
      securityId: url.searchParams.get("securityId") ?? ""
    };
  } catch {
    return null;
  }
};

export const readStandaloneDetailDomEvidence = (
  job: BossJobIdentity,
  root: ParentNode = document
): StandaloneDetailEvidence => {
  const bossNames = Array.from(
    root.querySelectorAll(".job-detail .job-boss-info h2.name")
  )
    .map((element) => element.textContent?.replace(/\s+/g, " ").trim() ?? "")
    .filter(Boolean);
  const uniqueBossNames = [...new Set(bossNames)];
  if (uniqueBossNames.length === 0) {
    return {
      state: "waiting",
      bossName: "",
      contacted: null,
      lid: "",
      reason: "等待详情页 HR 姓名"
    };
  }
  if (uniqueBossNames.length !== 1) {
    return {
      state: "mismatch",
      bossName: "",
      contacted: null,
      lid: "",
      reason: "详情页出现多个不同 HR 姓名，禁止执行"
    };
  }
  const bossName = uniqueBossNames[0] ?? "";

  const buttons = Array.from(
    root.querySelectorAll(".job-op a.btn-startchat")
  );
  if (buttons.length === 0) {
    return {
      state: "waiting",
      bossName,
      contacted: null,
      lid: "",
      reason: "等待详情页沟通按钮状态"
    };
  }

  const states = new Set<boolean>();
  const lids = new Set<string>();
  for (const button of buttons) {
    const state = communicationStateFromButton(button);
    const target = readButtonTarget(button);
    if (
      state === "conflict" ||
      state === null ||
      !target?.jobId ||
      !target.securityId ||
      target.jobId !== job.encryptJobId ||
      target.securityId !== job.securityId
    ) {
      return {
        state: "mismatch",
        bossName: "",
        contacted: null,
        lid: "",
        reason: "详情页沟通按钮与当前岗位身份不一致，禁止执行"
      };
    }
    states.add(state);
    const lid = button.getAttribute("lid") ?? "";
    if (lid) lids.add(lid);
  }

  if (states.size !== 1 || lids.size > 1) {
    return {
      state: "mismatch",
      bossName: "",
      contacted: null,
      lid: "",
      reason: "详情页重复沟通按钮状态不一致，禁止执行"
    };
  }

  return {
    state: "ready",
    bossName,
    contacted: states.values().next().value ?? null,
    lid: [...lids][0] ?? "",
    reason: "详情页 HR 与沟通状态只读识别完成"
  };
};

export const detectBossPageKind = (pathname: string): BossPageKind => {
  if (pathname.includes("/web/geek/job-recommend")) return "recommend";
  if (pathname.includes("/web/geek/jobs")) return "search";
  if (pathname.includes("/job_detail/") || pathname.includes("/web/geek/job-detail")) {
    return "detail";
  }
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
    contact: typeof item.contact === "boolean" ? item.contact : null
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
      jobName: readString(jobInfo, "jobName")
    },
    bossInfo: {
      name: readString(bossInfo, "name"),
      title: readString(bossInfo, "title")
    },
    relationInfo: {
      beFriend:
        typeof relationInfo?.beFriend === "boolean" ? relationInfo.beFriend : null
    }
  };
};

const createIdentity = (
  detail: BossJobDetailSubset,
  item: BossJobItemSubset | null
): BossJobIdentity => ({
  encryptJobId: detail.jobInfo.encryptId,
  securityId: detail.securityId || item?.securityId || "",
  encryptBossId: item?.encryptBossId || detail.jobInfo.encryptUserId,
  jobName: detail.jobInfo.jobName || item?.jobName || "",
  bossName: detail.bossInfo.name || item?.bossName || "",
  bossTitle: detail.bossInfo.title || item?.bossTitle || "",
  lid: detail.lid || item?.lid || "",
  contacted: item ? item.contact : detail.relationInfo.beFriend,
  identitySource: "vue-list-detail",
  bossIdentifierVerified: true
});

export const extractBossReadOnlySnapshot = ({
  pathname,
  loggedIn,
  pageVue,
  standaloneJobInfo,
  standaloneDetailEvidence,
  observedAt = Date.now()
}: ProbeInput): BossReadOnlySnapshot => {
  const pageKind = detectBossPageKind(pathname);
  const base = {
    component: "boss-read-only-probe" as const,
    readOnly: true as const,
    pathname,
    pageKind,
    loggedIn,
    observedAt
  };

  if (pageKind === "other") {
    return {
      ...base,
      state: "unsupported",
      jobCount: 0,
      job: null,
      reason: "当前页面不是已支持的 BOSS 岗位页面"
    };
  }

  if (pageKind === "detail") {
    const standaloneJob = readStandaloneJobIdentity(standaloneJobInfo);
    if (standaloneJob) {
      const pathJobId = readJobIdFromDetailPath(pathname);
      if (!pathJobId || pathJobId !== standaloneJob.encryptJobId) {
        return {
          ...base,
          state: "mismatch",
          jobCount: 0,
          job: null,
          reason: "详情页 URL 与 _jobInfo 岗位 ID 不匹配，禁止执行"
        };
      }

      if (!standaloneDetailEvidence || standaloneDetailEvidence.state === "waiting") {
        return {
          ...base,
          state: "waiting",
          jobCount: 1,
          job: null,
          reason: standaloneDetailEvidence?.reason ?? "等待详情页 HR 与沟通状态"
        };
      }
      if (standaloneDetailEvidence.state === "mismatch") {
        return {
          ...base,
          state: "mismatch",
          jobCount: 1,
          job: null,
          reason: standaloneDetailEvidence.reason
        };
      }

      const verifiedJob = {
        ...standaloneJob,
        bossName: standaloneDetailEvidence.bossName,
        contacted: standaloneDetailEvidence.contacted,
        lid: standaloneDetailEvidence.lid
      };

      return {
        ...base,
        state: "ready",
        jobCount: 1,
        job: verifiedJob,
        reason: "详情页岗位、HR 与沟通状态已识别；_jobInfo.user_id 语义仍待验证"
      };
    }
  }

  const pageRecord = asRecord(pageVue);
  if (!pageRecord) {
    return {
      ...base,
      state: "waiting",
      jobCount: 0,
      job: null,
      reason:
        pageKind === "detail"
          ? "等待详情页 _jobInfo 或 BOSS Vue 岗位容器"
          : "等待 BOSS Vue 岗位容器"
    };
  }

  const rawList = unwrap(pageRecord.jobList);
  const items = Array.isArray(rawList)
    ? rawList.map(readJobItem).filter((item): item is BossJobItemSubset => item !== null)
    : [];
  const detail = readJobDetail(pageRecord.jobDetail);
  if (!detail) {
    return {
      ...base,
      state: "waiting",
      jobCount: items.length,
      job: null,
      reason: "等待当前岗位详情"
    };
  }

  if (pageKind !== "detail" && items.length === 0) {
    return {
      ...base,
      state: "waiting",
      jobCount: 0,
      job: null,
      reason: "等待当前页面岗位列表，禁止仅凭详情执行"
    };
  }

  const matchedItem =
    items.find((item) => item.lid !== "" && item.lid === detail.lid) ??
    items.find((item) => item.encryptJobId === detail.jobInfo.encryptId) ??
    null;

  if (pageKind !== "detail" && !matchedItem) {
    return {
      ...base,
      state: "mismatch",
      jobCount: items.length,
      job: null,
      reason: "岗位详情与当前岗位列表不匹配，禁止执行"
    };
  }

  const job = createIdentity(detail, matchedItem);
  if (!job.encryptJobId || !job.securityId || !job.encryptBossId || !job.jobName) {
    return {
      ...base,
      state: "unavailable",
      jobCount: items.length,
      job: null,
      reason: "岗位身份字段不完整，禁止执行"
    };
  }

  return {
    ...base,
    state: "ready",
    jobCount: items.length,
    job,
    reason: loggedIn ? "岗位身份只读识别完成" : "岗位已识别，但登录状态未确认"
  };
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
  if (readString(page, "encryptUserId").length > 0) return true;
  const userInfo = asRecord((window as unknown as { _userInfo?: unknown })._userInfo);
  return userInfo?.isLogin === true;
};

export const readBossPageSnapshot = (): BossReadOnlySnapshot =>
  (() => {
    const standaloneJobInfo = (window as unknown as { _jobInfo?: unknown })._jobInfo;
    const standaloneJob = readStandaloneJobIdentity(standaloneJobInfo);
    return extractBossReadOnlySnapshot({
      pathname: window.location.pathname,
      loggedIn: detectLoggedIn(),
      pageVue: findPageVue(),
      standaloneJobInfo,
      ...(standaloneJob
        ? { standaloneDetailEvidence: readStandaloneDetailDomEvidence(standaloneJob) }
        : {})
    });
  })();

export const snapshotFingerprint = (snapshot: BossReadOnlySnapshot): string =>
  JSON.stringify({
    pathname: snapshot.pathname,
    pageKind: snapshot.pageKind,
    state: snapshot.state,
    loggedIn: snapshot.loggedIn,
    jobCount: snapshot.jobCount,
    job: snapshot.job,
    reason: snapshot.reason
  });
