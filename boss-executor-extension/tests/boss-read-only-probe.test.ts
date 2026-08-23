import { describe, expect, it } from "vitest";

import {
  detectBossPageKind,
  extractBossReadOnlySnapshot,
  readStandaloneDetailDomEvidence,
  snapshotFingerprint
} from "../src/platform/boss/read-only-probe";
import { isBossReadOnlySnapshot } from "../src/platform/boss/types";

const jobItem = {
  securityId: "security-list-1",
  encryptBossId: "boss-encrypted-1",
  bossName: "王经理",
  bossTitle: "招聘经理",
  encryptJobId: "job-encrypted-1",
  jobName: "前端工程师",
  lid: "lid-1",
  contact: false
};

const jobDetail = {
  securityId: "security-detail-1",
  lid: "lid-1",
  jobInfo: {
    encryptId: "job-encrypted-1",
    encryptUserId: "boss-encrypted-1",
    jobName: "前端工程师"
  },
  bossInfo: {
    name: "王经理",
    title: "招聘经理"
  },
  relationInfo: {
    beFriend: false
  }
};

describe("BOSS 岗位只读识别", () => {
  it("识别支持的页面类型", () => {
    expect(detectBossPageKind("/web/geek/jobs")).toBe("search");
    expect(detectBossPageKind("/web/geek/job-recommend")).toBe("recommend");
    expect(detectBossPageKind("/job_detail/abc.html")).toBe("detail");
    expect(detectBossPageKind("/web/geek/chat")).toBe("other");
  });

  it("用 lid 对齐岗位列表和当前详情", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/web/geek/jobs",
      loggedIn: true,
      pageVue: {
        jobList: [jobItem],
        jobDetail
      },
      observedAt: 1
    });

    expect(snapshot.state).toBe("ready");
    expect(snapshot.job).toMatchObject({
      encryptJobId: "job-encrypted-1",
      encryptBossId: "boss-encrypted-1",
      jobName: "前端工程师",
      contacted: false,
      identitySource: "vue-list-detail",
      bossIdentifierVerified: true
    });
    expect(isBossReadOnlySnapshot(snapshot)).toBe(true);
  });

  it("详情与当前列表不匹配时失败关闭", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/web/geek/jobs",
      loggedIn: true,
      pageVue: {
        jobList: [{ ...jobItem, lid: "other-lid", encryptJobId: "other-job" }],
        jobDetail
      }
    });

    expect(snapshot.state).toBe("mismatch");
    expect(snapshot.job).toBeNull();
    expect(snapshot.reason).toContain("禁止执行");
  });

  it("搜索页缺少岗位列表时保持等待并失败关闭", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/web/geek/jobs",
      loggedIn: true,
      pageVue: { jobDetail }
    });

    expect(snapshot.state).toBe("waiting");
    expect(snapshot.job).toBeNull();
    expect(snapshot.reason).toContain("禁止仅凭详情执行");
  });

  it("独立岗位详情页允许直接读取详情身份", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/job_detail/job-encrypted-1.html",
      loggedIn: true,
      pageVue: {
        jobDetail
      }
    });

    expect(snapshot.state).toBe("ready");
    expect(snapshot.job?.encryptJobId).toBe("job-encrypted-1");
  });

  it("从独立详情页 _jobInfo 读取岗位并标记 HR 标识待验证", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/job_detail/job-encrypted-1.html",
      loggedIn: true,
      pageVue: null,
      standaloneJobInfo: {
        job_id: "job-encrypted-1",
        securityId: "security-detail-1",
        user_id: "boss-inferred-1",
        job_name: "前端工程师"
      },
      standaloneDetailEvidence: {
        state: "ready",
        bossName: "王经理",
        contacted: false,
        lid: "lid-detail-1",
        reason: "详情页 HR 与沟通状态只读识别完成"
      }
    });

    expect(snapshot.state).toBe("ready");
    expect(snapshot.job).toMatchObject({
      encryptJobId: "job-encrypted-1",
      encryptBossId: "boss-inferred-1",
      bossName: "王经理",
      contacted: false,
      identitySource: "standalone-job-info",
      bossIdentifierVerified: false
    });
    expect(snapshot.reason).toContain("user_id 语义仍待验证");
  });

  it("详情页 URL 与 _jobInfo 岗位 ID 不一致时失败关闭", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/job_detail/url-job.html",
      loggedIn: true,
      pageVue: null,
      standaloneJobInfo: {
        job_id: "other-job",
        securityId: "security-detail-1",
        user_id: "boss-inferred-1",
        job_name: "前端工程师"
      }
    });

    expect(snapshot.state).toBe("mismatch");
    expect(snapshot.job).toBeNull();
    expect(snapshot.reason).toContain("禁止执行");
  });

  it("详情页立即沟通映射为未沟通，并读取唯一 HR 姓名", () => {
    document.body.innerHTML = `
      <div class="job-detail">
        <div class="job-boss-info"><h2 class="name">王晓丽<i></i></h2></div>
        <div class="job-op"><a class="btn btn-startchat" data-isfriend="false" lid="lid-1"
          data-url="/wapi/zpgeek/friend/add.json?securityId=security-detail-1&jobId=job-encrypted-1">
          立即沟通
        </a></div>
      </div>
    `;

    const evidence = readStandaloneDetailDomEvidence({
      encryptJobId: "job-encrypted-1",
      securityId: "security-detail-1",
      encryptBossId: "boss-inferred-1",
      jobName: "前端工程师",
      bossName: "",
      bossTitle: "",
      lid: "",
      contacted: null,
      identitySource: "standalone-job-info",
      bossIdentifierVerified: false
    });

    expect(evidence).toMatchObject({
      state: "ready",
      bossName: "王晓丽",
      contacted: false,
      lid: "lid-1"
    });
  });

  it("详情页继续沟通映射为已沟通", () => {
    document.body.innerHTML = `
      <div class="job-detail">
        <div class="job-boss-info"><h2 class="name">王晓丽</h2></div>
        <div class="job-op"><a class="btn btn-startchat" data-isfriend="true"
          data-url="/wapi/zpgeek/friend/add.json?securityId=security-detail-1&jobId=job-encrypted-1">
          继续沟通
        </a></div>
      </div>
    `;

    const evidence = readStandaloneDetailDomEvidence({
      encryptJobId: "job-encrypted-1",
      securityId: "security-detail-1",
      encryptBossId: "boss-inferred-1",
      jobName: "前端工程师",
      bossName: "",
      bossTitle: "",
      lid: "",
      contacted: null,
      identitySource: "standalone-job-info",
      bossIdentifierVerified: false
    });

    expect(evidence.state).toBe("ready");
    expect(evidence.contacted).toBe(true);
  });

  it("沟通按钮文本与 data-isfriend 冲突时失败关闭", () => {
    document.body.innerHTML = `
      <div class="job-detail">
        <div class="job-boss-info"><h2 class="name">王晓丽</h2></div>
        <div class="job-op"><a class="btn btn-startchat" data-isfriend="true"
          data-url="/wapi/zpgeek/friend/add.json?securityId=security-detail-1&jobId=job-encrypted-1">
          立即沟通
        </a></div>
      </div>
    `;

    const evidence = readStandaloneDetailDomEvidence({
      encryptJobId: "job-encrypted-1",
      securityId: "security-detail-1",
      encryptBossId: "boss-inferred-1",
      jobName: "前端工程师",
      bossName: "",
      bossTitle: "",
      lid: "",
      contacted: null,
      identitySource: "standalone-job-info",
      bossIdentifierVerified: false
    });

    expect(evidence.state).toBe("mismatch");
    expect(evidence.reason).toContain("禁止执行");
  });

  it("源数据没有沟通字段时保留未知状态", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/job_detail/job-encrypted-1.html",
      loggedIn: true,
      pageVue: {
        jobDetail: {
          ...jobDetail,
          relationInfo: {}
        }
      }
    });

    expect(snapshot.state).toBe("ready");
    expect(snapshot.job?.contacted).toBeNull();
  });

  it("必需的 HR 标识缺失时返回不可执行", () => {
    const snapshot = extractBossReadOnlySnapshot({
      pathname: "/job_detail/job-encrypted-1.html",
      loggedIn: true,
      pageVue: {
        jobDetail: {
          ...jobDetail,
          jobInfo: {
            ...jobDetail.jobInfo,
            encryptUserId: ""
          }
        }
      }
    });

    expect(snapshot.state).toBe("unavailable");
    expect(snapshot.job).toBeNull();
  });

  it("时间变化不会造成重复上报指纹变化", () => {
    const input = {
      pathname: "/web/geek/jobs",
      loggedIn: true,
      pageVue: { jobList: [jobItem], jobDetail }
    };
    const first = extractBossReadOnlySnapshot({ ...input, observedAt: 1 });
    const second = extractBossReadOnlySnapshot({ ...input, observedAt: 2 });
    expect(snapshotFingerprint(first)).toBe(snapshotFingerprint(second));
  });
});
