import type { BossReadOnlySnapshot } from "../platform/boss/types";

export type ExecutorPermissionState =
  | "not_authorized"
  | "allowed"
  | "paused"
  | "risk_paused";
export type ExecutorQueueState = "running" | "paused" | "emergency_stopped" | "risk_paused";

export type FineJobQueueAction = {
  id: string;
  job_id: string;
  review_item_id: string;
  action_type: "BOSS_DEFAULT_GREETING";
  status: string;
  execution_state: string;
  execution_epoch: number;
  queue_position: number;
  page_open_attempts: number;
  page_deadline_at?: string | null;
  request_accepted_at?: string | null;
  verification_state: "not_required" | "waiting_refresh" | "refreshing" | "waiting_snapshot" | "page_confirmed" | "manual_confirmed" | "pending" | "chat_confirmed";
  verification_method: "none" | "page_refresh" | "manual" | "chat";
  verification_delay_seconds?: number | null;
  verification_due_at?: string | null;
  verification_started_at?: string | null;
  verification_completed_at?: string | null;
  verification_attempts: number;
  job_title: string;
  company_name: string;
  encrypt_job_id: string;
  last_status_code?: string | null;
  last_error?: string | null;
};

export type FineJobExecutorInstance = {
  id: string;
  plugin_version: string;
  protocol_version: string;
  permission_state: ExecutorPermissionState;
  queue_state: ExecutorQueueState;
  risk_state: string;
  browser_connected: boolean;
  current_action_id?: string | null;
  current_epoch?: number | null;
  cooldown_seconds?: number | null;
  next_eligible_at?: string | null;
  last_heartbeat_at?: string | null;
};

export type ExecutorRuntimeState = {
  connected: boolean;
  paired: boolean;
  detail: string;
  executor: FineJobExecutorInstance | null;
  queue: FineJobQueueAction[];
  failedQueue: FineJobQueueAction[];
  currentAction: FineJobQueueAction | null;
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
  actionId: string;
  executionEpoch: number;
  encryptJobId: string;
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

export type MainWorldCommand = DefaultGreetingCommand | ChatSendCommand;

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
  actionId: string;
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
  control(command: "allow" | "pause" | "resume" | "emergency_stop"): Promise<void>;
  returnToReview(actionId: string): Promise<void>;
  retryFailedAction(actionId: string): Promise<void>;
  cancelFailedAction(actionId: string): Promise<void>;
  retryAllFailed(): Promise<void>;
  cancelAllFailed(): Promise<void>;
};

export type SnapshotReport = Pick<
  BossReadOnlySnapshot,
  "state" | "loggedIn" | "pageKind" | "job" | "reason"
>;
