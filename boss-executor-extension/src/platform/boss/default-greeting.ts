import type { MainWorldCommand, MainWorldExecutionResult } from "../../finejob/types";
import { readBossPageSnapshot } from "./read-only-probe";

type BossCookieApi = { get(name: string): string | undefined };

const readBossToken = (): string => {
  const cookieApi = (window as unknown as { Cookie?: BossCookieApi }).Cookie;
  return cookieApi?.get("bst") ?? "";
};

/**
 * 参数与请求入口适配自MIT许可的boss-helper sendPublishReq。
 * FineJob版本只发送一次，不自动重试、不确认平台限额、不发送自定义文本。
 */
export const executeDefaultGreeting = async (
  command: MainWorldCommand
): Promise<MainWorldExecutionResult> => {
  const snapshot = readBossPageSnapshot();
  if (
    snapshot.state !== "ready" ||
    !snapshot.loggedIn ||
    snapshot.job?.encryptJobId !== command.encryptJobId ||
    snapshot.job.contacted !== false
  ) {
    return {
      actionId: command.actionId,
      executionEpoch: command.executionEpoch,
      outcome: "failed",
      contacted: snapshot.job?.contacted ?? null,
      statusCode: "PRE_DISPATCH_PAGE_MISMATCH",
      message: "真实请求前页面身份或沟通状态发生变化",
      evidence: { pageState: snapshot.state, pageKind: snapshot.pageKind }
    };
  }

  const token = readBossToken();
  if (!token) {
    return {
      actionId: command.actionId,
      executionEpoch: command.executionEpoch,
      outcome: "failed",
      contacted: false,
      statusCode: "BOSS_TOKEN_MISSING",
      message: "无法读取BOSS登录令牌",
      evidence: {}
    };
  }

  const url = new URL("https://www.zhipin.com/wapi/zpgeek/friend/add.json");
  url.searchParams.set("securityId", snapshot.job.securityId);
  url.searchParams.set("jobId", command.encryptJobId);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { Zp_token: token }
    });
    const body = await response.json() as {
      code?: number;
      message?: string;
      zpData?: { bizData?: { chatRemindDialog?: { content?: string } } };
    };
    if (!response.ok || body.code !== 0) {
      const message = String(body.zpData?.bizData?.chatRemindDialog?.content || body.message || `HTTP ${response.status}`);
      const statusCode = message.includes("频繁")
        ? "BOSS_RATE_LIMIT"
        : message.includes("沟通") ? "BOSS_GREETING_LIMIT" : "BOSS_REQUEST_REJECTED";
      return {
        actionId: command.actionId,
        executionEpoch: command.executionEpoch,
        outcome: "failed",
        contacted: false,
        statusCode,
        message,
        evidence: { responseCode: body.code ?? null, httpStatus: response.status }
      };
    }

    return {
      actionId: command.actionId,
      executionEpoch: command.executionEpoch,
      outcome: "accepted",
      contacted: null,
      statusCode: "BOSS_REQUEST_ACCEPTED",
      message: "平台已受理建立沟通请求，等待状态验证",
      evidence: { responseCode: body.code, httpStatus: response.status }
    };
  } catch (error) {
    return {
      actionId: command.actionId,
      executionEpoch: command.executionEpoch,
      outcome: "unknown",
      contacted: null,
      statusCode: "BOSS_REQUEST_NETWORK_UNKNOWN",
      message: `请求结果未知：${(error as Error).message}`,
      evidence: {}
    };
  }
};
