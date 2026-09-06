from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class JobPipelineSnapshot(BaseModel):
    job_id: str
    company_id: str | None = None
    stage: str
    stage_source: str
    stage_event_id: str
    stage_updated_at: str
    waiting_on: str = "unknown"
    waiting_since_at: str | None = None
    contact_origin: str = "unknown"
    rejection_reason_source: str = "unknown"
    rejection_reason_category: str = "unknown"
    rejection_reason_summary: str = ""
    projection_version: int
    created_at: str
    updated_at: str


class JobLegacyApplication(BaseModel):
    id: str
    job_id: str
    company_id: str | None = None
    status: str | None = None
    source: str
    source_action_id: str | None = None
    evidence_level: str
    applied_at: str
    note: str
    created_at: str
    updated_at: str


class JobActivityEvent(BaseModel):
    id: str
    job_id: str
    company_id: str | None = None
    chat_session_id: str | None = None
    event_type: str
    occurred_at: str
    source: str
    source_ref_type: str
    source_ref_id: str
    confidence: float
    evidence_level: str
    payload: dict[str, Any]
    created_at: str


class ExecutionEvidence(BaseModel):
    id: str
    action_ref_type: str
    action_ref_id: str
    evidence_type: str
    source: str
    source_ref_type: str
    source_ref_id: str
    observed_at: str
    confidence: float
    evidence_level: str
    payload: dict[str, Any]
    created_at: str


class ExecutionReconciliation(BaseModel):
    id: str
    action_ref_type: str
    action_ref_id: str
    previous_status: str
    new_status: str
    reconciled_at: str
    reconciliation_reason: str
    evidence_id: str
    evidence_level: str
    created_at: str


class JobExecutionSummary(BaseModel):
    action_ref_type: str
    action_ref_id: str
    action_type: str
    dedupe_identity: str
    session_id: str | None = None
    raw_status: str
    canonical_status: str
    canonical_reason: str
    canonical_updated_at: str | None = None
    status_code: str
    error_message: str
    executor_id: str
    leader_tab_id: str
    execution_epoch: int
    attempt_count: int
    created_at: str
    started_at: str | None = None
    dispatch_started_at: str | None = None
    completed_at: str | None = None
    evidence: list[ExecutionEvidence]
    reconciliations: list[ExecutionReconciliation]


class JobProgressView(BaseModel):
    job_id: str
    session_id: str | None = None
    stage: str
    stage_updated_at: str
    waiting_on: str
    waiting_since_at: str | None = None
    contact_origin: str
    latest_activity: dict[str, Any] | None = None
    followup: dict[str, Any]
    outcome: dict[str, Any]
    primary_action: dict[str, Any] | None = None
    analysis_updated_at: str | None = None


class JobJourneyResponse(BaseModel):
    job_id: str
    pipeline: JobPipelineSnapshot | None = None
    legacy_application: JobLegacyApplication | None = None
    progress: JobProgressView | None = None
    activities: list[JobActivityEvent]
    executions: list[JobExecutionSummary]
