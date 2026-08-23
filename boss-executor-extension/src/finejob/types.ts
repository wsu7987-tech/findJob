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
  currentAction: FineJobQueueAction | null;
  lastResult: string;
};

export type MainWorldCommand = {
  type: "BOSS_DEFAULT_GREETING";
  actionId: string;
  executionEpoch: number;
  encryptJobId: string;
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
  control(command: "allow" | "pause" | "resume" | "emergency_stop"): Promise<void>;
  returnToReview(actionId: string): Promise<void>;
};

export type SnapshotReport = Pick<
  BossReadOnlySnapshot,
  "state" | "loggedIn" | "pageKind" | "job" | "reason"
>;
