export type BossPageKind = "search" | "recommend" | "detail" | "other";

export type BossProbeState = "ready" | "waiting" | "unsupported" | "mismatch" | "unavailable";

export type BossJobItemSubset = {
  securityId: string;
  encryptBossId: string;
  bossName: string;
  bossTitle: string;
  encryptJobId: string;
  jobName: string;
  lid: string;
  contact: boolean | null;
};

export type BossJobDetailSubset = {
  securityId: string;
  lid: string;
  jobInfo: {
    encryptId: string;
    encryptUserId: string;
    jobName: string;
  };
  bossInfo: {
    name: string;
    title: string;
  };
  relationInfo: {
    beFriend: boolean | null;
  };
};

export type BossJobIdentity = {
  encryptJobId: string;
  securityId: string;
  encryptBossId: string;
  jobName: string;
  bossName: string;
  bossTitle: string;
  lid: string;
  contacted: boolean | null;
  identitySource: "vue-list-detail" | "standalone-job-info";
  bossIdentifierVerified: boolean;
};

export type BossReadOnlySnapshot = {
  component: "boss-read-only-probe";
  readOnly: true;
  pathname: string;
  pageKind: BossPageKind;
  state: BossProbeState;
  loggedIn: boolean;
  jobCount: number;
  job: BossJobIdentity | null;
  reason: string;
  observedAt: number;
};

export const isBossReadOnlySnapshot = (value: unknown): value is BossReadOnlySnapshot => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<BossReadOnlySnapshot>;
  if (
    candidate.component !== "boss-read-only-probe" ||
    candidate.readOnly !== true ||
    typeof candidate.pathname !== "string" ||
    !["search", "recommend", "detail", "other"].includes(candidate.pageKind ?? "") ||
    !["ready", "waiting", "unsupported", "mismatch", "unavailable"].includes(
      candidate.state ?? ""
    ) ||
    typeof candidate.loggedIn !== "boolean" ||
    typeof candidate.jobCount !== "number" ||
    typeof candidate.reason !== "string" ||
    typeof candidate.observedAt !== "number"
  ) {
    return false;
  }

  // 失败或等待状态不能夹带可被后续动作误用的岗位身份。
  if (candidate.state !== "ready") return candidate.job === null;
  if (!candidate.job || typeof candidate.job !== "object") return false;
  const job = candidate.job as Partial<BossJobIdentity>;
  return (
    typeof job.encryptJobId === "string" &&
    job.encryptJobId.length > 0 &&
    typeof job.securityId === "string" &&
    job.securityId.length > 0 &&
    typeof job.encryptBossId === "string" &&
    job.encryptBossId.length > 0 &&
    typeof job.jobName === "string" &&
    job.jobName.length > 0 &&
    typeof job.bossName === "string" &&
    typeof job.bossTitle === "string" &&
    typeof job.lid === "string" &&
    (typeof job.contacted === "boolean" || job.contacted === null) &&
    ["vue-list-detail", "standalone-job-info"].includes(job.identitySource ?? "") &&
    typeof job.bossIdentifierVerified === "boolean"
  );
};
