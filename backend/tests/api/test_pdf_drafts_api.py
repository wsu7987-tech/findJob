from __future__ import annotations


class _FakeParseService:
    def parse_file(
        self,
        *,
        file_path,
        parser_name,
        knowledge_item_id=None,
        cancel_check=None,
        on_page=None,
    ):
        from pathlib import Path

        from backend.app.services.pdf_parse.types import PdfParsePage, PdfParseResult

        del knowledge_item_id
        name = Path(file_path).name
        if parser_name == "rapid_ocr":
            if callable(cancel_check) and cancel_check():
                raise AssertionError("cancelled too early")
            if callable(on_page):
                on_page(PdfParsePage(page_number=1, content_type="text", content=f"ocr:{name}"), 1)
            return PdfParseResult(
                parser_name="rapid_ocr",
                raw_text=f"ocr:{name}",
                markdown_text=None,
                preview_text=f"ocr:{name}",
                page_count=1,
                char_count=len(f"ocr:{name}"),
                quality_score=0.73,
                warnings=["ocr"],
                is_ocr=True,
                preview_pages=[
                    PdfParsePage(page_number=1, content_type="text", content=f"ocr:{name}")
                ],
            )
        if callable(on_page):
            on_page(PdfParsePage(page_number=1, content_type="markdown", content=f"# {name}"), 2)
            on_page(
                PdfParsePage(page_number=2, content_type="markdown", content=f"## tail {name}"),
                2,
            )
        return PdfParseResult(
            parser_name="pymupdf4llm_markdown",
            raw_text=f"markdown:{name}",
            markdown_text=f"# {name}",
            preview_text=f"# {name}",
            page_count=2,
            char_count=len(f"markdown:{name}"),
            quality_score=0.93,
            warnings=[],
            is_ocr=False,
            preview_pages=[
                PdfParsePage(page_number=1, content_type="markdown", content=f"# {name}"),
                PdfParsePage(page_number=2, content_type="markdown", content=f"## tail {name}"),
            ],
        )


def test_create_pdf_draft_returns_draft_payload(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    response = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    )

    assert response.status_code == 202
    payload = response.json()
    body = payload["draft"]
    assert payload["job"]["draft_id"] == body["id"]
    assert body["parse_results"][0]["status"] in {"running", "saved"}


def test_reparse_pdf_draft_returns_preview_result(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    response = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/reparse",
        json={"parser_name": "rapid_ocr"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job"]["draft_id"] == created["id"]
    assert payload["job"]["parser_name"] == "rapid_ocr"

    job_id = payload["job"]["id"]
    job = configured_client.get(
        f"/api/pdf/drafts/{created['id']}/jobs/{job_id}"
    )
    assert job.status_code == 200
    assert job.json()["job"]["status"] in {"running", "completed"}

    refreshed = configured_client.get(f"/api/pdf/drafts/{created['id']}").json()["draft"]
    parse_result_id = refreshed["latest_preview_result_id"]
    assert parse_result_id is not None
    page = configured_client.get(
        f"/api/pdf/drafts/{created['id']}/parse-results/{parse_result_id}/pages/1"
    )
    assert page.status_code == 200
    assert page.json()["page"]["page_number"] == 1


def test_save_pdf_draft_parse_result_promotes_saved_version(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/reparse",
        json={"parser_name": "rapid_ocr"},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]

    response = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/parse-results/{preview_id}/save"
    )

    assert response.status_code == 200
    body = response.json()["draft"]
    assert body["saved_parse_result_id"] == preview_id


def test_pdf_preview_page_rehydrates_requested_page_when_partial_cache_missing(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    refreshed = configured_client.get(f"/api/pdf/drafts/{created['id']}").json()["draft"]
    parse_result_id = refreshed["latest_preview_result_id"]
    draft = configured_client.app.state.pdf_draft_store.get_draft(created["id"])
    assert draft is not None
    for item in draft.parse_results:
        if item.id == parse_result_id:
            item.preview_pages = [
                page for page in item.preview_pages if page.page_number == 1
            ]
            break

    response = configured_client.get(
        f"/api/pdf/drafts/{created['id']}/parse-results/{parse_result_id}/pages/2"
    )

    assert response.status_code == 200
    assert response.json()["page"]["page_number"] == 2


def test_pdf_preview_page_rehydrates_missing_pages_instead_of_404(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    refreshed = configured_client.get(f"/api/pdf/drafts/{created['id']}").json()["draft"]
    parse_result_id = refreshed["latest_preview_result_id"]
    draft = configured_client.app.state.pdf_draft_store.get_draft(created["id"])
    assert draft is not None
    for item in draft.parse_results:
        if item.id == parse_result_id:
            item.preview_pages = []
            break

    response = configured_client.get(
        f"/api/pdf/drafts/{created['id']}/parse-results/{parse_result_id}/pages/2"
    )

    assert response.status_code == 200
    assert response.json()["page"]["page_number"] == 2


def test_pdf_preview_page_returns_500_when_requested_page_is_out_of_range(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    refreshed = configured_client.get(f"/api/pdf/drafts/{created['id']}").json()["draft"]
    parse_result_id = refreshed["latest_preview_result_id"]

    response = configured_client.get(
        f"/api/pdf/drafts/{created['id']}/parse-results/{parse_result_id}/pages/4"
    )

    assert response.status_code == 500


def test_commit_pdf_draft_returns_pool_item(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/reparse",
        json={"parser_name": "rapid_ocr"},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]
    configured_client.post(f"/api/pdf/drafts/{created['id']}/parse-results/{preview_id}/save")

    response = configured_client.post(f"/api/pdf/drafts/{created['id']}/commit")

    assert response.status_code == 201
    assert response.json()["item"]["source_type"] == "pdf"


def test_commit_pdf_draft_prefers_structured_markdown_for_canonical_content(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    response = configured_client.post(f"/api/pdf/drafts/{created['id']}/commit")

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT raw_content
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["raw_content"] == "# draft.pdf"


def test_commit_pdf_draft_persists_category_and_tags(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    response = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/commit",
        json={"category": "engineering", "tags": ["pdf", "rag"]},
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT capture_category, capture_tags_json
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["capture_category"] == "engineering"
    assert row["capture_tags_json"] == '["pdf", "rag"]'


def test_commit_pdf_draft_uses_cleaned_text_override(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    response = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/commit",
        json={"cleaned_text": "cleaned pdf body", "cleaning_level": "enhanced"},
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT raw_content, cleaning_level
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert "cleaned pdf body" in row["raw_content"]
    assert "markdown:draft.pdf" not in row["raw_content"]
    assert row["cleaning_level"] == "enhanced"


def test_cancel_pdf_draft_reparse_sets_cancel_flag(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    job = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/reparse",
        json={"parser_name": "rapid_ocr"},
    ).json()["job"]

    response = configured_client.post(
        f"/api/pdf/drafts/{created['id']}/jobs/{job['id']}/cancel"
    )

    assert response.status_code == 202
    assert response.json()["job"]["cancel_requested"] is True


def test_list_pdf_reparse_jobs_uses_static_jobs_route(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "draft.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.routers.pdf_drafts.build_default_pdf_parse_service",
        lambda config: _FakeParseService(),
    )

    created = configured_client.post(
        "/api/pdf/drafts",
        json={"file_path": str(pdf_path), "title": "Draft"},
    ).json()["draft"]

    configured_client.post(
        f"/api/pdf/drafts/{created['id']}/reparse",
        json={"parser_name": "rapid_ocr"},
    )

    response = configured_client.get("/api/pdf/drafts/jobs")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["jobs"], list)
    assert len(body["jobs"]) >= 1
