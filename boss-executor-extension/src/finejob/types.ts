export type ExecutorQueueState = "running" | "paused" | "risk_paused";

export type FineJobQueueAction = {
  id: string;
  job_id: string;
  review_item_id: string;
  action_type: string;
  task_type: "BOSS_DEFAULT_GREETING" | "BOSS_CHAT_RESUME" | "BOSS_CHAT_MESSAGE" | "TEST_DELAY";
  task_source?: "greeting" | "chat" | "test";
  status: string;
  execution_state: string;
  execution_epoch: number;
  job_title: string;
  company_name: string;
  encrypt_job_id: string;
  account_uid?: string;
  page_type?: "job" | "chat";
  chat_page_url?: string;
  test_task_type?: "greeting" | "resume" | "chat" | "";
  last_status_code?: string | null;
  last_error?: string | null;
  close_page_after_completion: boolean;
  delay_seconds: number;
};

export type FineJobExecutorInstance = {
  id: string;
  plugin_version: string;
  protocol_version: string;
  queue_state: ExecutorQueueState;
  risk_state: string;
  browser_connected: boolean;
  last_heartbeat_at?: string | null;
  task_cooldown_max_seconds: number;
  page_load_wait_max_seconds: number;
  runtime_phase?: "idle" | "task_cooldown" | "page_opening" | "page_matching";
  runtime_detail?: string;
  runtime_until_at?: string | null;
};

export type ExecutorRuntimeState = {
  connected: boolean;
  paired: boolean;
  detail: string;
  executor: FineJobExecutorInstance | null;
  queue: FineJobQueueAction[];
  lastResult: string;
  chat?: BossChatCoordinatorStatus;
};

export type BossChatCoordinatorStatus = {
  listenEnabled: boolean;
  runtimeKnown: boolean;
  eventOutboxCount: number;
  eventOutboxBytes: number;
  eventOutboxBlocked: boolean;
  resultOutboxCount: number;
  lastSuccessfulFlushAt: string;
  lastError: string;
};

export type DefaultGreetingCommand = {
  type: "BOSS_DEFAULT_GREETING";
  taskId: string;
  executionEpoch: number;
  encryptJobId: string;
  targetTabId?: string;
};

export type BossPageProbeCommand = {
  type: "BOSS_PAGE_PROBE";
};

export type BossChatPageProbeCommand = {
  type: "BOSS_CHAT_PAGE_PROBE";
};

export type ChatObservedMessage = {
  eventId: string;
  accountUid: string;
  platformMessageId: string;
  direction: "inbound" | "outbound";
  messageType: "text" | "image" | "system" | "unknown";
  content: string;
  senderUid: string;
  receiverUid: string;
  clientMid: string;
  peerUid: string;
  encryptPeerUid: string;
  securityId: string;
  encryptJobId: string;
  jobTitle: string;
  peerName: string;
  companyName: string;
  sentAt: string;
  observedAt: string;
  source: "websocket" | "manual" | "assistant";
  /** 帧由本机 send hook 或远端 message listener 提供，决定其可用证据等级。 */
  frameOrigin: "local_send" | "remote_message";
  evidenceSource: "local_transport_write" | "remote_outbound_echo" | "remote_message" | "message_sync";
  serverMid: string;
  rawMeta: Record<string, unknown>;
};

export type ChatIdentity = {
  accountUid: string;
  loggedIn: boolean;
  pathname: string;
  observedAt: number;
};

export type ChatTabHeartbeat = ChatIdentity & {
  tabId: string;
  visible: boolean;
};

export type FineJobChatSendAction = {
  id: string;
  session_id: string;
  status: string;
  text: string;
  execution_epoch: number;
  account_uid: string;
  peer_uid: string;
  encrypt_peer_uid: string;
  security_id: string;
  encrypt_job_id: string;
  client_mid: string;
  operation_kind?: "text" | "resume_list" | "resume";
  encrypt_resume_id?: string;
  resume_filename?: string;
};

export type ChatSendCommand = {
  type: "BOSS_CHAT_SEND";
  targetTabId: string;
  leaderEpoch: number;
  action: FineJobChatSendAction;
};

export type MainWorldCommand = DefaultGreetingCommand | ChatSendCommand | BossPageProbeCommand | BossChatPageProbeCommand;

export type ChatSendExecutionResult = {
  actionId: string;
  executionEpoch: number;
  outcome: "accepted" | "failed" | "unknown";
  platformMessageId: string;
  clientMid: string;
  statusCode: string;
  message: string;
  evidence: Record<string, unknown>;
};

export type ChatSendOptions = {
  dryRun?: boolean;
  /** MAIN World 在真正 publish 前读取当前后端运行时开关。 */
  isSendEnabled?: () => Promise<boolean>;
  /** 同一聊天标签页中 BOSS 页面接口实际使用的 zp_token。 */
  getLiveZpToken?: () => Promise<string>;
  /** BOSS 已确认简历交换请求成功时同步插件状态栏。 */
  onResumeSendSucceeded?: () => Promise<void>;
  /** 简历发送任一步失败时同步插件状态栏。 */
  onResumeSendFailed?: (reason: string) => Promise<void>;
};

export type MainWorldExecutionResult = {
  taskId: string;
  executionEpoch: number;
  outcome: "accepted" | "succeeded" | "failed" | "unknown";
  contacted: boolean | null;
  statusCode: string;
  message: string;
  evidence: Record<string, unknown>;
};

export type ExecutorPanelController = {
  pair(code: string): Promise<void>;
  testHeartbeat(): Promise<void>;
  disconnect(): Promise<void>;
  control(command: "start" | "pause"): Promise<void>;
};
