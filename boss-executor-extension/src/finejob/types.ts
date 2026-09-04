export type ExecutorQueueState = "running" | "paused" | "risk_paused";

export type FineJobQueueAction = {
  id: string;
  job_id: string;
  review_item_id: string;
  action_type: string;
  task_type: "BOSS_DEFAULT_GREETING" | "TEST_DELAY";
  status: string;
  execution_state: string;
  execution_epoch: number;
  job_title: string;
  company_name: string;
  encrypt_job_id: string;
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
  runtime_phase?: "idle" | "task_cooldown";
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
};

export type ChatSendCommand = {
  type: "BOSS_CHAT_SEND";
  targetTabId: string;
  leaderEpoch: number;
  action: FineJobChatSendAction;
};

export type MainWorldCommand = DefaultGreetingCommand | ChatSendCommand | BossPageProbeCommand;

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
