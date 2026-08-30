from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.profiles import (
    AnalysisItemDecision,
    AnalysisItemListEnvelope,
    AnalysisItemUpdate,
    AnalysisItemsApplyRequest,
    AnswerVariantEnvelope,
    AnswerVariantListEnvelope,
    AnswerVariantPayload,
    AnswerVariantUpdate,
    CandidateProfileCreate,
    CandidateProfileEnvelope,
    CandidateProfileListEnvelope,
    CandidateProfileUpdate,
    FactEvidenceEnvelope,
    FactEvidenceListEnvelope,
    FactEvidencePayload,
    JobAnswerAnalysisCreate,
    MigrationPreviewResponse,
    MigrationApplyRequest,
    MigrationApplyResponse,
    ProfileAnalysisRunCreate,
    ProfileAnalysisRunEnvelope,
    ProfileContextEnvelope,
    ProfileFactEnvelope,
    ProfileFactListEnvelope,
    ProfileFactPayload,
    ProfileFactUpdate,
    ProfileQuestionEnvelope,
    ProfileQuestionListEnvelope,
    ProfileQuestionPayload,
    ProfileQuestionUpdate,
    ProfileSourceCreateFile,
    ProfileSourceCreateText,
    ProfileSourceEnvelope,
    ProfileSourceListEnvelope,
    ProfileSourceUpdate,
    ResumeVersionEnvelope,
    ResumeVersionListEnvelope,
    ResumeVersionPayload,
    ResumeVersionUpdate,
    SearchCampaignEnvelope,
    SearchCampaignListEnvelope,
    SearchCampaignPayload,
    SearchCampaignUpdate,
    SearchQueriesReplaceRequest,
)
from backend.app.schemas.fine_job.resume_analysis_v2 import (
    DerivedResumeImportRequest,
    ResumeAnalysisIssueListEnvelope,
    ResumeAnalysisIssueResponse,
    ResumeAnalysisIssueStatusUpdate,
    ResumeAnalysisRunCreate,
    ResumeAnalysisRunEnvelope,
    ResumeEditableContentUpdate,
    ResumeFamilyEnvelope,
    ResumeFamilyImportRequest,
    ResumeFamilyListEnvelope,
    ResumeNormalizedMarkdownUpdate,
    ResumeSearchKeywordListEnvelope,
    ResumeSearchKeywordsReplace,
    ResumeStrategyListEnvelope,
    ResumeStrategyResponse,
    ResumeStrategyUpdate,
)
from backend.app.services.fine_job import (
    profile_analysis,
    profile_context,
    profile_store,
    resume_analysis_v2,
)


router = APIRouter(prefix="/fine-job/profiles", tags=["fine-job-profiles"])


@router.get("", response_model=CandidateProfileListEnvelope)
def list_candidate_profiles(db: Database = Depends(get_database)) -> CandidateProfileListEnvelope:
    return CandidateProfileListEnvelope(profiles=profile_store.list_profiles(db))


@router.post("", response_model=CandidateProfileEnvelope, status_code=status.HTTP_201_CREATED)
def create_candidate_profile(
    payload: CandidateProfileCreate,
    db: Database = Depends(get_database),
) -> CandidateProfileEnvelope:
    return CandidateProfileEnvelope(profile=profile_store.create_profile(db, payload))


@router.get("/migration-preview", response_model=MigrationPreviewResponse)
def preview_legacy_profile_migration(
    db: Database = Depends(get_database),
) -> MigrationPreviewResponse:
    return MigrationPreviewResponse(**profile_store.migration_preview(db))


@router.post("/migration-apply", response_model=MigrationApplyResponse)
def apply_legacy_profile_migration(
    payload: MigrationApplyRequest,
    db: Database = Depends(get_database),
) -> MigrationApplyResponse:
    return MigrationApplyResponse(**profile_store.apply_legacy_migration(db, payload.profile_id))


@router.get("/{profile_id}", response_model=CandidateProfileEnvelope)
def get_candidate_profile(
    profile_id: str,
    db: Database = Depends(get_database),
) -> CandidateProfileEnvelope:
    return CandidateProfileEnvelope(profile=profile_store.get_profile(db, profile_id))


@router.put("/{profile_id}", response_model=CandidateProfileEnvelope)
def update_candidate_profile(
    profile_id: str,
    payload: CandidateProfileUpdate,
    db: Database = Depends(get_database),
) -> CandidateProfileEnvelope:
    return CandidateProfileEnvelope(profile=profile_store.update_profile(db, profile_id, payload))


@router.get("/{profile_id}/sources", response_model=ProfileSourceListEnvelope)
def list_profile_sources(
    profile_id: str,
    db: Database = Depends(get_database),
) -> ProfileSourceListEnvelope:
    return ProfileSourceListEnvelope(sources=profile_store.list_sources(db, profile_id))


@router.post("/{profile_id}/sources/text", response_model=ProfileSourceEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_text_source(
    profile_id: str,
    payload: ProfileSourceCreateText,
    db: Database = Depends(get_database),
) -> ProfileSourceEnvelope:
    return ProfileSourceEnvelope(source=profile_store.create_text_source(db, profile_id, payload))


@router.post("/{profile_id}/sources/file", response_model=ProfileSourceEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_file_source(
    profile_id: str,
    payload: ProfileSourceCreateFile,
    db: Database = Depends(get_database),
) -> ProfileSourceEnvelope:
    return ProfileSourceEnvelope(source=profile_store.create_file_source(db, profile_id, payload))


@router.put("/sources/{source_id}", response_model=ProfileSourceEnvelope)
def update_profile_source(
    source_id: str,
    payload: ProfileSourceUpdate,
    db: Database = Depends(get_database),
) -> ProfileSourceEnvelope:
    return ProfileSourceEnvelope(source=profile_store.update_source(db, source_id, payload))


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_source(source_id: str, db: Database = Depends(get_database)) -> Response:
    profile_store.delete_source(db, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sources/{source_id}/clean", response_model=ProfileSourceEnvelope)
def clean_profile_source(
    source_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ProfileSourceEnvelope:
    return ProfileSourceEnvelope(source=profile_analysis.clean_profile_source(db, config, source_id))


@router.get("/{profile_id}/resume-families", response_model=ResumeFamilyListEnvelope)
def list_profile_resume_families(
    profile_id: str,
    db: Database = Depends(get_database),
) -> ResumeFamilyListEnvelope:
    return ResumeFamilyListEnvelope(
        resume_families=resume_analysis_v2.list_resume_families(db, profile_id)
    )


@router.post(
    "/{profile_id}/resume-families/from-pdf",
    response_model=ResumeFamilyEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def import_profile_resume_family(
    profile_id: str,
    payload: ResumeFamilyImportRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ResumeFamilyEnvelope:
    return ResumeFamilyEnvelope(
        resume_family=resume_analysis_v2.import_pdf_resume(
            db, config, profile_id, payload
        )
    )


@router.post(
    "/{profile_id}/resume-families/{resume_family_id}/derived-from-pdf",
    response_model=ResumeVersionEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def import_profile_derived_resume(
    profile_id: str,
    resume_family_id: str,
    payload: DerivedResumeImportRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ResumeVersionEnvelope:
    return ResumeVersionEnvelope(
        resume_version=resume_analysis_v2.import_derived_pdf_resume(
            db, config, profile_id, resume_family_id, payload
        )
    )


@router.get("/resume-families/{resume_family_id}", response_model=ResumeFamilyEnvelope)
def get_profile_resume_family(
    resume_family_id: str,
    db: Database = Depends(get_database),
) -> ResumeFamilyEnvelope:
    return ResumeFamilyEnvelope(
        resume_family=resume_analysis_v2.get_resume_family(db, resume_family_id)
    )


@router.put("/sources/{source_id}/editable-content", response_model=ProfileSourceEnvelope)
def update_profile_source_editable_content(
    source_id: str,
    payload: ResumeEditableContentUpdate,
    db: Database = Depends(get_database),
) -> ProfileSourceEnvelope:
    return ProfileSourceEnvelope(
        source=resume_analysis_v2.update_editable_content(db, source_id, payload)
    )


@router.put("/sources/{source_id}/normalized-markdown", response_model=ProfileSourceEnvelope)
def update_profile_source_normalized_markdown(
    source_id: str,
    payload: ResumeNormalizedMarkdownUpdate,
    db: Database = Depends(get_database),
) -> ProfileSourceEnvelope:
    return ProfileSourceEnvelope(
        source=resume_analysis_v2.update_normalized_markdown(db, source_id, payload)
    )


@router.post(
    "/{profile_id}/resume-families/{resume_family_id}/analysis-runs",
    response_model=ResumeAnalysisRunEnvelope,
)
def start_resume_analysis_run(
    profile_id: str,
    resume_family_id: str,
    payload: ResumeAnalysisRunCreate,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ResumeAnalysisRunEnvelope:
    return ResumeAnalysisRunEnvelope(
        analysis_run=resume_analysis_v2.start_analysis_run(
            db, config, profile_id, resume_family_id, payload
        )
    )


@router.get(
    "/resume-families/{resume_family_id}/analysis-runs/latest",
    response_model=ResumeAnalysisRunEnvelope,
)
def get_latest_resume_analysis_run(
    resume_family_id: str,
    db: Database = Depends(get_database),
) -> ResumeAnalysisRunEnvelope:
    return ResumeAnalysisRunEnvelope(
        analysis_run=resume_analysis_v2.get_latest_analysis_run(db, resume_family_id)
    )


@router.get("/resume-analysis-runs/{run_id}", response_model=ResumeAnalysisRunEnvelope)
def get_resume_analysis_run(
    run_id: str,
    db: Database = Depends(get_database),
) -> ResumeAnalysisRunEnvelope:
    return ResumeAnalysisRunEnvelope(
        analysis_run=resume_analysis_v2.get_analysis_run(db, run_id)
    )


@router.post(
    "/resume-analysis-runs/{run_id}/cancel",
    response_model=ResumeAnalysisRunEnvelope,
)
def cancel_resume_analysis_run(
    run_id: str,
    db: Database = Depends(get_database),
) -> ResumeAnalysisRunEnvelope:
    return ResumeAnalysisRunEnvelope(
        analysis_run=resume_analysis_v2.cancel_analysis_run(db, run_id)
    )


@router.post(
    "/resume-analysis-runs/{run_id}/retry",
    response_model=ResumeAnalysisRunEnvelope,
)
def retry_resume_analysis_run(
    run_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ResumeAnalysisRunEnvelope:
    return ResumeAnalysisRunEnvelope(
        analysis_run=resume_analysis_v2.retry_analysis_run(db, config, run_id)
    )


@router.get(
    "/resume-families/{resume_family_id}/issues",
    response_model=ResumeAnalysisIssueListEnvelope,
)
def list_resume_analysis_issues(
    resume_family_id: str,
    db: Database = Depends(get_database),
) -> ResumeAnalysisIssueListEnvelope:
    return ResumeAnalysisIssueListEnvelope(
        issues=resume_analysis_v2.list_issues(db, resume_family_id)
    )


@router.put(
    "/resume-analysis-issues/{issue_id}/status",
    response_model=ResumeAnalysisIssueResponse,
)
def update_resume_analysis_issue_status(
    issue_id: str,
    payload: ResumeAnalysisIssueStatusUpdate,
    db: Database = Depends(get_database),
) -> ResumeAnalysisIssueResponse:
    return ResumeAnalysisIssueResponse(
        **resume_analysis_v2.update_issue_status(db, issue_id, payload.status)
    )


@router.get(
    "/resume-families/{resume_family_id}/strategies",
    response_model=ResumeStrategyListEnvelope,
)
def list_resume_family_strategies(
    resume_family_id: str,
    db: Database = Depends(get_database),
) -> ResumeStrategyListEnvelope:
    return ResumeStrategyListEnvelope(
        strategies=resume_analysis_v2.list_strategies(db, resume_family_id)
    )


@router.put(
    "/resume-strategies/{strategy_id}",
    response_model=ResumeStrategyResponse,
)
def update_resume_strategy(
    strategy_id: str,
    payload: ResumeStrategyUpdate,
    db: Database = Depends(get_database),
) -> ResumeStrategyResponse:
    return ResumeStrategyResponse(
        **resume_analysis_v2.update_strategy(
            db,
            strategy_id,
            name=payload.name,
            content=payload.content,
            expected_version=payload.expected_version,
        )
    )


@router.get(
    "/resume-families/{resume_family_id}/search-keywords",
    response_model=ResumeSearchKeywordListEnvelope,
)
def list_resume_family_search_keywords(
    resume_family_id: str,
    db: Database = Depends(get_database),
) -> ResumeSearchKeywordListEnvelope:
    return ResumeSearchKeywordListEnvelope(
        keywords=resume_analysis_v2.list_search_keywords(db, resume_family_id)
    )


@router.put(
    "/resume-families/{resume_family_id}/search-keywords",
    response_model=ResumeSearchKeywordListEnvelope,
)
def replace_resume_family_search_keywords(
    resume_family_id: str,
    payload: ResumeSearchKeywordsReplace,
    db: Database = Depends(get_database),
) -> ResumeSearchKeywordListEnvelope:
    return ResumeSearchKeywordListEnvelope(
        keywords=resume_analysis_v2.replace_search_keywords(
            db, resume_family_id, payload
        )
    )


@router.post("/{profile_id}/analysis-runs", response_model=ProfileAnalysisRunEnvelope)
def create_profile_analysis_run(
    profile_id: str,
    payload: ProfileAnalysisRunCreate,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(
        analysis_run=profile_analysis.run_profile_analysis(db, config, profile_id, payload.source_ids)
    )


@router.get("/{profile_id}/analysis-runs/latest", response_model=ProfileAnalysisRunEnvelope)
def get_latest_profile_analysis_run(
    profile_id: str,
    db: Database = Depends(get_database),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(analysis_run=profile_store.get_latest_analysis_run(db, profile_id))


@router.post("/{profile_id}/analysis-runs/async", response_model=ProfileAnalysisRunEnvelope)
def start_profile_analysis_run(
    profile_id: str,
    payload: ProfileAnalysisRunCreate,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ProfileAnalysisRunEnvelope:
    # 桌面端先取得任务 ID，再按任务状态展示识别和 AI 分析进度。
    return ProfileAnalysisRunEnvelope(
        analysis_run=profile_analysis.start_profile_analysis(db, config, profile_id, payload.source_ids)
    )


@router.post("/{profile_id}/jobs/{job_id}/answer-analysis", response_model=ProfileAnalysisRunEnvelope)
def create_job_answer_analysis_run(
    profile_id: str,
    job_id: str,
    payload: JobAnswerAnalysisCreate,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(
        analysis_run=profile_analysis.run_job_answer_analysis(
            db,
            config,
            profile_id,
            job_id,
            payload.question_keys,
        )
    )


@router.get("/analysis-runs/{run_id}", response_model=ProfileAnalysisRunEnvelope)
def get_profile_analysis_run(
    run_id: str,
    db: Database = Depends(get_database),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(analysis_run=profile_store.get_analysis_run(db, run_id))


@router.post("/analysis-runs/{run_id}/cancel", response_model=ProfileAnalysisRunEnvelope)
def cancel_profile_analysis_run(
    run_id: str,
    db: Database = Depends(get_database),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(analysis_run=profile_analysis.cancel_analysis_run(db, run_id))


@router.post("/analysis-runs/{run_id}/auto-apply-facts", response_model=ProfileAnalysisRunEnvelope)
def auto_apply_profile_analysis_facts(
    run_id: str,
    db: Database = Depends(get_database),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(analysis_run=profile_analysis.auto_apply_analysis_facts(db, run_id))


@router.post("/analysis-runs/{run_id}/retry", response_model=ProfileAnalysisRunEnvelope)
def retry_profile_analysis_run(
    run_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ProfileAnalysisRunEnvelope:
    return ProfileAnalysisRunEnvelope(analysis_run=profile_analysis.retry_profile_analysis(db, config, run_id))


@router.get("/analysis-runs/{run_id}/items", response_model=AnalysisItemListEnvelope)
def list_profile_analysis_items(
    run_id: str,
    db: Database = Depends(get_database),
) -> AnalysisItemListEnvelope:
    return AnalysisItemListEnvelope(items=profile_store.list_analysis_items(db, run_id))


@router.put("/analysis-items/{item_id}", response_model=dict)
def update_profile_analysis_item(
    item_id: str,
    payload: AnalysisItemUpdate,
    db: Database = Depends(get_database),
) -> dict[str, object]:
    return {"item": profile_analysis.update_analysis_item(
        db,
        item_id,
        payload=payload.payload,
        expected_status=payload.expected_status,
    )}


@router.post("/analysis-items/{item_id}/{decision}", response_model=dict)
def decide_profile_analysis_item(
    item_id: str,
    decision: Literal["accepted", "rejected", "deferred"],
    payload: AnalysisItemDecision,
    db: Database = Depends(get_database),
) -> dict[str, object]:
    return {"item": profile_analysis.decide_analysis_item(
        db,
        item_id,
        expected_status=payload.expected_status,
        decision=decision,
        decision_note=payload.decision_note,
    )}


@router.post("/analysis-runs/{run_id}/apply", response_model=AnalysisItemListEnvelope)
def apply_profile_analysis_items(
    run_id: str,
    payload: AnalysisItemsApplyRequest,
    db: Database = Depends(get_database),
) -> AnalysisItemListEnvelope:
    return AnalysisItemListEnvelope(
        items=profile_analysis.apply_analysis_items(
            db,
            run_id,
            payload.item_ids,
            payload.expected_versions.model_dump(),
        )
    )


@router.get("/{profile_id}/facts", response_model=ProfileFactListEnvelope)
def list_profile_facts(
    profile_id: str,
    db: Database = Depends(get_database),
) -> ProfileFactListEnvelope:
    facts, version = profile_store.list_facts(db, profile_id)
    return ProfileFactListEnvelope(facts=facts, facts_version=version)


@router.post("/{profile_id}/facts", response_model=ProfileFactEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_fact(
    profile_id: str,
    payload: ProfileFactPayload,
    db: Database = Depends(get_database),
) -> ProfileFactEnvelope:
    return ProfileFactEnvelope(fact=profile_store.create_fact(db, profile_id, payload))


@router.put("/facts/{fact_id}", response_model=ProfileFactEnvelope)
def update_profile_fact(
    fact_id: str,
    payload: ProfileFactUpdate,
    db: Database = Depends(get_database),
) -> ProfileFactEnvelope:
    return ProfileFactEnvelope(fact=profile_store.update_fact(db, fact_id, payload))


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_fact(fact_id: str, db: Database = Depends(get_database)) -> Response:
    profile_store.delete_fact(db, fact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/facts/{fact_id}/evidence", response_model=FactEvidenceListEnvelope)
def list_profile_fact_evidence(
    fact_id: str,
    db: Database = Depends(get_database),
) -> FactEvidenceListEnvelope:
    return FactEvidenceListEnvelope(evidence=profile_store.list_evidence(db, fact_id))


@router.post("/facts/{fact_id}/evidence", response_model=FactEvidenceEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_fact_evidence(
    fact_id: str,
    payload: FactEvidencePayload,
    db: Database = Depends(get_database),
) -> FactEvidenceEnvelope:
    return FactEvidenceEnvelope(evidence=profile_store.create_evidence(db, fact_id, payload))


@router.delete("/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_fact_evidence(
    evidence_id: str,
    db: Database = Depends(get_database),
) -> Response:
    profile_store.delete_evidence(db, evidence_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}/questions", response_model=ProfileQuestionListEnvelope)
def list_profile_questions(
    profile_id: str,
    db: Database = Depends(get_database),
) -> ProfileQuestionListEnvelope:
    questions, version = profile_store.list_questions(db, profile_id)
    return ProfileQuestionListEnvelope(questions=questions, questions_version=version)


@router.post("/{profile_id}/questions", response_model=ProfileQuestionEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_question(
    profile_id: str,
    payload: ProfileQuestionPayload,
    db: Database = Depends(get_database),
) -> ProfileQuestionEnvelope:
    return ProfileQuestionEnvelope(question=profile_store.create_question(db, profile_id, payload))


@router.put("/questions/{question_id}", response_model=ProfileQuestionEnvelope)
def update_profile_question(
    question_id: str,
    payload: ProfileQuestionUpdate,
    db: Database = Depends(get_database),
) -> ProfileQuestionEnvelope:
    return ProfileQuestionEnvelope(question=profile_store.update_question(db, question_id, payload))


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_question(question_id: str, db: Database = Depends(get_database)) -> Response:
    profile_store.delete_question(db, question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/questions/{question_id}/answers", response_model=AnswerVariantListEnvelope)
def list_profile_answer_variants(
    question_id: str,
    db: Database = Depends(get_database),
) -> AnswerVariantListEnvelope:
    return AnswerVariantListEnvelope(answer_variants=profile_store.list_answer_variants(db, question_id))


@router.post("/questions/{question_id}/answers", response_model=AnswerVariantEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_answer_variant(
    question_id: str,
    payload: AnswerVariantPayload,
    db: Database = Depends(get_database),
) -> AnswerVariantEnvelope:
    return AnswerVariantEnvelope(answer_variant=profile_store.create_answer_variant(db, question_id, payload))


@router.put("/answers/{variant_id}", response_model=AnswerVariantEnvelope)
def update_profile_answer_variant(
    variant_id: str,
    payload: AnswerVariantUpdate,
    db: Database = Depends(get_database),
) -> AnswerVariantEnvelope:
    return AnswerVariantEnvelope(answer_variant=profile_store.update_answer_variant(db, variant_id, payload))


@router.post("/answers/{variant_id}/confirm", response_model=AnswerVariantEnvelope)
def confirm_profile_answer_variant(
    variant_id: str,
    db: Database = Depends(get_database),
) -> AnswerVariantEnvelope:
    return AnswerVariantEnvelope(answer_variant=profile_store.confirm_answer_variant(db, variant_id))


@router.delete("/answers/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_answer_variant(variant_id: str, db: Database = Depends(get_database)) -> Response:
    profile_store.delete_answer_variant(db, variant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}/resume-versions", response_model=ResumeVersionListEnvelope)
def list_profile_resume_versions(
    profile_id: str,
    db: Database = Depends(get_database),
) -> ResumeVersionListEnvelope:
    return ResumeVersionListEnvelope(resume_versions=profile_store.list_resume_versions(db, profile_id))


@router.post("/{profile_id}/resume-versions", response_model=ResumeVersionEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_resume_version(
    profile_id: str,
    payload: ResumeVersionPayload,
    db: Database = Depends(get_database),
) -> ResumeVersionEnvelope:
    return ResumeVersionEnvelope(resume_version=profile_store.create_resume_version(db, profile_id, payload))


@router.put("/resume-versions/{resume_version_id}", response_model=ResumeVersionEnvelope)
def update_profile_resume_version(
    resume_version_id: str,
    payload: ResumeVersionUpdate,
    db: Database = Depends(get_database),
) -> ResumeVersionEnvelope:
    return ResumeVersionEnvelope(resume_version=profile_store.update_resume_version(db, resume_version_id, payload))


@router.post("/resume-versions/{resume_version_id}/confirm", response_model=ResumeVersionEnvelope)
def confirm_profile_resume_version(
    resume_version_id: str,
    db: Database = Depends(get_database),
) -> ResumeVersionEnvelope:
    return ResumeVersionEnvelope(resume_version=profile_store.confirm_resume_version(db, resume_version_id))


@router.post("/resume-versions/{resume_version_id}/set-as-base", response_model=ResumeVersionEnvelope)
def set_profile_resume_version_as_base(
    resume_version_id: str,
    db: Database = Depends(get_database),
) -> ResumeVersionEnvelope:
    return ResumeVersionEnvelope(
        resume_version=profile_store.set_resume_version_as_base(db, resume_version_id)
    )


@router.delete("/resume-versions/{resume_version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_resume_version(
    resume_version_id: str,
    db: Database = Depends(get_database),
) -> Response:
    profile_store.delete_resume_version(db, resume_version_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}/campaigns", response_model=SearchCampaignListEnvelope)
def list_profile_campaigns(
    profile_id: str,
    db: Database = Depends(get_database),
) -> SearchCampaignListEnvelope:
    return SearchCampaignListEnvelope(campaigns=profile_store.list_campaigns(db, profile_id))


@router.post("/{profile_id}/campaigns", response_model=SearchCampaignEnvelope, status_code=status.HTTP_201_CREATED)
def create_profile_campaign(
    profile_id: str,
    payload: SearchCampaignPayload,
    db: Database = Depends(get_database),
) -> SearchCampaignEnvelope:
    return SearchCampaignEnvelope(campaign=profile_store.create_campaign(db, profile_id, payload))


@router.put("/campaigns/{campaign_id}", response_model=SearchCampaignEnvelope)
def update_profile_campaign(
    campaign_id: str,
    payload: SearchCampaignUpdate,
    db: Database = Depends(get_database),
) -> SearchCampaignEnvelope:
    return SearchCampaignEnvelope(campaign=profile_store.update_campaign(db, campaign_id, payload))


@router.put("/campaigns/{campaign_id}/queries", response_model=SearchCampaignEnvelope)
def replace_profile_campaign_queries(
    campaign_id: str,
    payload: SearchQueriesReplaceRequest,
    db: Database = Depends(get_database),
) -> SearchCampaignEnvelope:
    return SearchCampaignEnvelope(campaign=profile_store.replace_search_queries(db, campaign_id, payload))


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_campaign(campaign_id: str, db: Database = Depends(get_database)) -> Response:
    profile_store.delete_campaign(db, campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{profile_id}/context", response_model=ProfileContextEnvelope)
def read_profile_context(
    profile_id: str,
    view: Literal["full", "search", "evaluation", "chat"] = Query(default="full"),
    job_id: str | None = Query(default=None),
    role_family: str | None = Query(default=None),
    resume_family_id: str | None = Query(default=None),
    db: Database = Depends(get_database),
) -> ProfileContextEnvelope:
    return ProfileContextEnvelope(
        context=profile_context.get_profile_context(
            db,
            profile_id,
            view=view,
            job_id=job_id,
            role_family=role_family,
            resume_family_id=resume_family_id,
        )
    )
