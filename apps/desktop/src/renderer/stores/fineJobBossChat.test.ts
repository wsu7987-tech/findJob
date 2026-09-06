import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { useFineJobBossChatStore } from "./fineJobBossChat";


const runtime = {
  id: "boss",
  listen_enabled: true,
  generation_enabled: false,
  send_enabled: true,
  trigger_mode: "manual",
  interval_minutes: 0,
  leader_epoch: 3,
  leader_tab_id: "tab-a",
  leader_lease_expires_at: "2099-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z"
} as const;

const session = {
  id: "session-1",
  platform: "boss",
  account_uid: "100",
  peer_uid: "200",
  encrypt_peer_uid: "enc-peer",
  security_id: "security",
  job_id: null,
  encrypt_job_id: "enc-job",
  job_title: "Python 开发",
  peer_name: "王经理",
  company_name: "示例科技",
  status: "active",
  session_version: 1,
  latest_message_id: "message-1",
  latest_inbound_message_id: "message-1",
  last_message_at: "2026-08-24T00:00:00Z",
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z"
} as const;

const replyTask = {
  id: "reply-1",
  session_id: "session-1",
  trigger_source: "manual",
  status: "awaiting_review",
  based_on_message_id: "message-1",
  based_on_session_version: 1,
  context: {},
  draft_text: "AI 草稿",
  final_text: "AI 草稿",
  generation_model: "stub-chat-model",
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z"
} as const;

const detail = () => ({
  session,
  messages: [],
  reply_tasks: [replyTask],
  send_actions: []
});

describe("fineJobBossChat store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    vi.spyOn(api, "getFineJobChatRuntime").mockResolvedValue({ runtime } as never);
    vi.spyOn(api, "listFineJobChatSessions").mockResolvedValue({ sessions: [session] } as never);
    vi.spyOn(api, "getFineJobChatSession").mockResolvedValue(detail() as never);
    vi.spyOn(api, "getFineJobChatBatchSummary").mockResolvedValue({
      pending_chat_count: 0,
      pending_job_count: 0,
      queued_chat_count: 0,
      batch_limit: 100
    } as never);
  });

  it("加载运行状态、会话列表和选中会话详情", async () => {
    const store = useFineJobBossChatStore();
    await store.load();

    expect(store.runtime?.leader_epoch).toBe(3);
    expect(store.selectedSessionId).toBeNull();
    expect(store.detail).toBeNull();
  });

  it("确认发送时携带草稿依据消息和会话版本", async () => {
    vi.spyOn(api, "editFineJobChatReply").mockResolvedValue({ reply_task: replyTask } as never);
    const confirmSpy = vi.spyOn(api, "confirmFineJobChatReply").mockResolvedValue({
      action: { id: "send-1", status: "queued" }
    } as never);
    const store = useFineJobBossChatStore();
    await store.load();
    await store.loadDetail("session-1");
    await store.confirm("人工编辑后的回复");

    expect(confirmSpy).toHaveBeenCalledWith("reply-1", {
      final_text: "人工编辑后的回复",
      based_on_message_id: "message-1",
      based_on_session_version: 1
    });
  });

  it("紧急设置可一次关闭监听、生成和发送权限", async () => {
    const updateSpy = vi.spyOn(api, "updateFineJobChatRuntime").mockResolvedValue({
      runtime: { ...runtime, listen_enabled: false, generation_enabled: false, send_enabled: false }
    } as never);
    const store = useFineJobBossChatStore();
    await store.updateRuntime({ listen_enabled: false, generation_enabled: false, send_enabled: false });

    expect(updateSpy).toHaveBeenCalledWith({
      listen_enabled: false,
      generation_enabled: false,
      send_enabled: false
    });
    expect(store.runtime?.send_enabled).toBe(false);
  });

  it("会话搜索和状态筛选通过服务端参数加载", async () => {
    const listSpy = vi.mocked(api.listFineJobChatSessions);
    const store = useFineJobBossChatStore();
    store.searchQuery = "王经理";
    store.statusFilter = "active";
    store.accountFilter = "100";

    await store.loadList();

    expect(listSpy).toHaveBeenLastCalledWith({
      q: "王经理",
      status: "active",
      account_uid: "100",
      limit: 50,
      offset: 0
    });
  });

  it("已有本地消息且没有更新时不重复请求历史接口", async () => {
    vi.mocked(api.getFineJobChatSession).mockResolvedValue({
      ...detail(),
      messages: [{ id: "message-1" }],
      message_count: 1,
      session: { ...session, message_update_required: false }
    } as never);
    const historySpy = vi.spyOn(api, "refreshFineJobChatHistory");
    const store = useFineJobBossChatStore();
    await store.load();
    await store.loadDetail("session-1");

    const result = await store.refreshHistory();

    expect(historySpy).not.toHaveBeenCalled();
    expect(result.inserted_count).toBe(0);
  });

  it("获取更多消息后刷新当前会话", async () => {
    const moreSpy = vi.spyOn(api, "loadMoreFineJobChatHistory").mockResolvedValue({
      session_id: "session-1",
      fetched_count: 20,
      inserted_count: 18,
      message_update_required: false,
      has_more: false
    } as never);
    const store = useFineJobBossChatStore();
    await store.load();
    await store.loadDetail("session-1");

    const result = await store.loadMoreHistory();

    expect(moreSpy).toHaveBeenCalledWith("session-1");
    expect(result.inserted_count).toBe(18);
  });
});
