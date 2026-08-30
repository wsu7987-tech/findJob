from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.profile_v3 import (
    AIDerivedResumePreviewRequest,
    AIDerivedResumePreviewResponse,
    ContextDraftUpdate,
    ContextHeadEnvelope,
    ContextSaveRequest,
    ContextTaskResolutionRequest,
    ContextTaskResolutionResponse,
    ContextView,
    IssueAnswerCreate,
    IssueChangeSetUpdate,
    IssueStatusUpdate,
    ProfileIssueEnvelope,
    ProfileIssueListEnvelope,
    QAAnswerPreviewRequest,
    QAAnswerPreviewResponse,
    QARevisionListEnvelope,
    QATemplateEnvelope,
    QATemplateListEnvelope,
    QATemplatePayload,
    ResumeDeleteImpactResponse,
    ResumeDeleteRequest,
    ResumeDeleteResult,
    ResumeLinksUpdate,
)
from backend.app.schemas.fine_job.profiles import ProfileFactEnvelope, ProfileQuestionEnvelope
from backend.app.services.fine_job import profile_store, profile_v3


router = APIRouter(prefix="/fine-job/profiles", tags=["fine-job-profiles-v3"])


@router.post(
    "/{profile_id}/resume-versions/ai-derived-preview",
    response_model=AIDerivedResumePreviewResponse,
)
def create_ai_derived_resume_preview(
    profile_id: str,
    payload: AIDerivedResumePreviewRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> AIDerivedResumePreviewResponse:
    return AIDerivedResumePreviewResponse(
        **profile_v3.preview_ai_derived_resume(db, config, profile_id, payload)
    )


@router.get(
    "/{profile_id}/questions/{question_id}/revisions",
    response_model=QARevisionListEnvelope,
)
def list_profile_question_revisions(
    profile_id: str,
    question_id: str,
    db: Database = Depends(get_database),
) -> QARevisionListEnvelope:
    question = profile_store.get_question(db, question_id)
    if question["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_QUESTION_NOT_FOUND", "QA 不存在。")
    return QARevisionListEnvelope(
        revisions=profile_store.list_question_revisions(db, question_id)
    )


@router.post(
    "/{profile_id}/questions/{question_id}/ai-answer-preview",
    response_model=QAAnswerPreviewResponse,
)
def create_profile_question_ai_answer_preview(
    profile_id: str,
    question_id: str,
    payload: QAAnswerPreviewRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> QAAnswerPreviewResponse:
    return QAAnswerPreviewResponse(
        **profile_v3.preview_qa_answer(
            db, config, profile_id, question_id, payload
        )
    )


@router.put("/{profile_id}/facts/{fact_id}/resume-links", response_model=ProfileFactEnvelope)
def replace_fact_resume_links(
    profile_id: str,
    fact_id: str,
    payload: ResumeLinksUpdate,
    db: Database = Depends(get_database),
) -> ProfileFactEnvelope:
    return ProfileFactEnvelope(
        fact=profile_v3.update_fact_resume_links(db, profile_id, fact_id, payload)
    )


@router.put("/{profile_id}/qa/{question_id}/resume-links", response_model=ProfileQuestionEnvelope)
def replace_question_resume_links(
    profile_id: str,
    question_id: str,
    payload: ResumeLinksUpdate,
    db: Database = Depends(get_database),
) -> ProfileQuestionEnvelope:
    return ProfileQuestionEnvelope(
        question=profile_v3.update_question_resume_links(db, profile_id, question_id, payload)
    )


@router.get("/{profile_id}/qa-templates", response_model=QATemplateListEnvelope)
def list_profile_qa_templates(
    profile_id: str,
    db: Database = Depends(get_database),
) -> QATemplateListEnvelope:
    return QATemplateListEnvelope(templates=profile_v3.list_qa_templates(db, profile_id))


@router.post(
    "/{profile_id}/qa-templates",
    response_model=QATemplateEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def create_profile_qa_template(
    profile_id: str,
    payload: QATemplatePayload,
    db: Database = Depends(get_database),
) -> QATemplateEnvelope:
    return QATemplateEnvelope(
        template=profile_v3.create_qa_template(db, profile_id, payload)
    )


@router.patch("/{profile_id}/qa-templates/{template_id}", response_model=QATemplateEnvelope)
def update_profile_qa_template(
    profile_id: str,
    template_id: str,
    payload: QATemplatePayload,
    db: Database = Depends(get_database),
) -> QATemplateEnvelope:
    template = profile_v3.get_qa_template(db, template_id)
    if template["profile_id"] != profile_id:
        raise AppError(404, "QA_TEMPLATE_NOT_FOUND", "QA 模板不存在。")
    return QATemplateEnvelope(
        template=profile_v3.update_qa_template(db, template_id, payload)
    )


@router.delete(
    "/{profile_id}/qa-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_profile_qa_template(
    profile_id: str,
    template_id: str,
    db: Database = Depends(get_database),
) -> Response:
    template = profile_v3.get_qa_template(db, template_id)
    if template["profile_id"] != profile_id:
        raise AppError(404, "QA_TEMPLATE_NOT_FOUND", "QA 模板不存在。")
    profile_v3.delete_qa_template(db, template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}/issues", response_model=ProfileIssueListEnvelope)
def list_profile_issues(
    profile_id: str,
    issue_status: str | None = Query(default=None, alias="status"),
    db: Database = Depends(get_database),
) -> ProfileIssueListEnvelope:
    return ProfileIssueListEnvelope(
        issues=profile_v3.list_issues(db, profile_id, status=issue_status)
    )


@router.get("/{profile_id}/issues/{issue_id}", response_model=ProfileIssueEnvelope)
def get_profile_issue(
    profile_id: str,
    issue_id: str,
    db: Database = Depends(get_database),
) -> ProfileIssueEnvelope:
    issue = profile_v3.get_issue(db, issue_id)
    if issue["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_ISSUE_NOT_FOUND", "待处理事项不存在。")
    return ProfileIssueEnvelope(issue=issue)


@router.post("/{profile_id}/issues/{issue_id}/answers", response_model=ProfileIssueEnvelope)
def answer_profile_issue(
    profile_id: str,
    issue_id: str,
    payload: IssueAnswerCreate,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ProfileIssueEnvelope:
    issue = profile_v3.get_issue(db, issue_id)
    if issue["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_ISSUE_NOT_FOUND", "待处理事项不存在。")
    return ProfileIssueEnvelope(
        issue=profile_v3.answer_and_organize_issue(
            db, config, issue_id, payload.answer_text
        )
    )


@router.patch("/{profile_id}/issues/{issue_id}/change-set", response_model=ProfileIssueEnvelope)
def update_profile_issue_change_set(
    profile_id: str,
    issue_id: str,
    payload: IssueChangeSetUpdate,
    db: Database = Depends(get_database),
) -> ProfileIssueEnvelope:
    issue = profile_v3.get_issue(db, issue_id)
    if issue["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_ISSUE_NOT_FOUND", "待处理事项不存在。")
    drafts = [item for item in issue["change_sets"] if item["status"] == "draft"]
    if not drafts:
        raise AppError(404, "ISSUE_CHANGE_SET_NOT_FOUND", "待应用变更不存在。")
    return ProfileIssueEnvelope(
        issue=profile_v3.update_issue_change_set(db, str(drafts[0]["id"]), payload)
    )


@router.post("/{profile_id}/issues/{issue_id}/apply", response_model=ProfileIssueEnvelope)
def apply_profile_issue(
    profile_id: str,
    issue_id: str,
    db: Database = Depends(get_database),
) -> ProfileIssueEnvelope:
    issue = profile_v3.get_issue(db, issue_id)
    if issue["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_ISSUE_NOT_FOUND", "待处理事项不存在。")
    drafts = [item for item in issue["change_sets"] if item["status"] == "draft"]
    if not drafts:
        raise AppError(404, "ISSUE_CHANGE_SET_NOT_FOUND", "待应用变更不存在。")
    return ProfileIssueEnvelope(
        issue=profile_v3.apply_issue_change_set(db, str(drafts[0]["id"]))
    )


@router.post("/{profile_id}/issues/{issue_id}/status", response_model=ProfileIssueEnvelope)
def set_profile_issue_status(
    profile_id: str,
    issue_id: str,
    payload: IssueStatusUpdate,
    db: Database = Depends(get_database),
) -> ProfileIssueEnvelope:
    issue = profile_v3.get_issue(db, issue_id)
    if issue["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_ISSUE_NOT_FOUND", "待处理事项不存在。")
    return ProfileIssueEnvelope(
        issue=profile_v3.update_issue_status(db, issue_id, payload.status)
    )


@router.get(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}",
    response_model=ContextHeadEnvelope,
)
def get_resume_context(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    return ContextHeadEnvelope(
        context=profile_v3.get_context_head(db, profile_id, resume_version_id, view)
    )


@router.post(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/drafts",
    response_model=ContextHeadEnvelope,
)
def create_resume_context_draft(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    payload: ContextSaveRequest | None = None,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    if payload is None:
        context = profile_v3.generate_context_draft(
            db, profile_id, resume_version_id, view
        )
    else:
        context = profile_v3.update_context_draft(
            db, profile_id, resume_version_id, view, payload.content
        )
    return ContextHeadEnvelope(context=context)


@router.post(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/regenerate",
    response_model=ContextHeadEnvelope,
)
def regenerate_resume_context(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    return ContextHeadEnvelope(
        context=profile_v3.generate_context_draft(
            db, profile_id, resume_version_id, view
        )
    )


@router.patch(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/drafts/{revision_id}",
    response_model=ContextHeadEnvelope,
)
def update_resume_context_draft(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    revision_id: str,
    payload: ContextDraftUpdate,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    context = profile_v3.get_context_head(
        db, profile_id, resume_version_id, view, include_history=False
    )
    if not context["draft_revision"] or context["draft_revision"]["id"] != revision_id:
        raise AppError(404, "CONTEXT_DRAFT_NOT_FOUND", "上下文草稿不存在。")
    return ContextHeadEnvelope(
        context=profile_v3.update_context_draft(
            db, profile_id, resume_version_id, view, payload.content
        )
    )


@router.post(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/drafts/{revision_id}/save",
    response_model=ContextHeadEnvelope,
)
def save_resume_context_draft(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    revision_id: str,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    context = profile_v3.get_context_head(
        db, profile_id, resume_version_id, view, include_history=False
    )
    draft = context["draft_revision"]
    if not draft or draft["id"] != revision_id:
        raise AppError(404, "CONTEXT_DRAFT_NOT_FOUND", "上下文草稿不存在。")
    return ContextHeadEnvelope(
        context=profile_v3.save_context(
            db, profile_id, resume_version_id, view, str(draft["content"])
        )
    )


@router.delete(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/drafts/{revision_id}",
    response_model=ContextHeadEnvelope,
)
def delete_resume_context_draft(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    revision_id: str,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    return ContextHeadEnvelope(
        context=profile_v3.delete_context_draft(
            db, profile_id, resume_version_id, view, revision_id
        )
    )


@router.post(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/revisions/{revision_id}/restore",
    response_model=ContextHeadEnvelope,
)
def restore_resume_context_revision(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    revision_id: str,
    db: Database = Depends(get_database),
) -> ContextHeadEnvelope:
    return ContextHeadEnvelope(
        context=profile_v3.restore_context_revision(
            db, profile_id, resume_version_id, view, revision_id
        )
    )


@router.post(
    "/{profile_id}/resume-versions/{resume_version_id}/contexts/{view}/resolve-task",
    response_model=ContextTaskResolutionResponse,
)
def resolve_resume_task_context(
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    payload: ContextTaskResolutionRequest,
    db: Database = Depends(get_database),
) -> ContextTaskResolutionResponse:
    return ContextTaskResolutionResponse(
        **profile_v3.resolve_task_context(
            db, profile_id, resume_version_id, view, payload.stale_action
        )
    )


@router.get(
    "/{profile_id}/resume-versions/{resume_version_id}/delete-impact",
    response_model=ResumeDeleteImpactResponse,
)
def get_resume_delete_impact(
    profile_id: str,
    resume_version_id: str,
    db: Database = Depends(get_database),
) -> ResumeDeleteImpactResponse:
    resume = profile_v3._require_resume(db, profile_id, resume_version_id)  # noqa: SLF001
    del resume
    return ResumeDeleteImpactResponse(
        **profile_v3.resume_delete_impact(db, resume_version_id)
    )


@router.delete(
    "/{profile_id}/resume-versions/{resume_version_id}",
    response_model=ResumeDeleteResult,
)
def delete_profile_resume_version_v3(
    profile_id: str,
    resume_version_id: str,
    payload: ResumeDeleteRequest,
    db: Database = Depends(get_database),
) -> ResumeDeleteResult:
    profile_v3._require_resume(db, profile_id, resume_version_id)  # noqa: SLF001
    return ResumeDeleteResult(
        **profile_v3.delete_resume_version(db, resume_version_id, payload)
    )
