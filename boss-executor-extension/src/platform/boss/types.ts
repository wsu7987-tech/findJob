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

export type BossPageIdentity = {
  component: "boss-page-identity";
  pathname: string;
  pageKind: BossPageKind;
  state: BossProbeState;
  loggedIn: boolean;
  job: BossJobIdentity | null;
  reason: string;
};

export const isBossPageIdentity = (value: unknown): value is BossPageIdentity => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<BossPageIdentity>;
  if (
    candidate.component !== "boss-page-identity" ||
    typeof candidate.pathname !== "string" ||
    !["search", "recommend", "detail", "other"].includes(candidate.pageKind ?? "") ||
    !["ready", "waiting", "unsupported", "mismatch", "unavailable"].includes(candidate.state ?? "") ||
    typeof candidate.loggedIn !== "boolean" ||
    typeof candidate.reason !== "string"
  ) return false;

  if (candidate.state !== "ready") return candidate.job === null;
  if (!candidate.job || typeof candidate.job !== "object") return false;
  const job = candidate.job as Partial<BossJobIdentity>;
  return (
    typeof job.encryptJobId === "string" && job.encryptJobId.length > 0 &&
    typeof job.securityId === "string" && job.securityId.length > 0 &&
    typeof job.encryptBossId === "string" && job.encryptBossId.length > 0 &&
    typeof job.jobName === "string" && job.jobName.length > 0 &&
    typeof job.bossName === "string" && typeof job.bossTitle === "string" &&
    typeof job.lid === "string" &&
    (typeof job.contacted === "boolean" || job.contacted === null) &&
    ["vue-list-detail", "standalone-job-info"].includes(job.identitySource ?? "") &&
    typeof job.bossIdentifierVerified === "boolean"
  );
};
