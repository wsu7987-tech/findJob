from __future__ import annotations


def test_get_active_pdf_parse_result_returns_current_saved_result(
    configured_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.pdf_parse.get_active_parse_result",
        lambda db, knowledge_item_id: {
            "id": "parse-1",
            "parser_name": "pymupdf4llm_markdown",
            "status": "saved",
            "preview_text": "# Title\n\nBody",
        },
    )

    response = configured_client.get("/api/pdf/items/ki-1/parse-result")

    assert response.status_code == 200
    assert response.json()["parse_result"]["status"] == "saved"


def test_reparse_pdf_creates_preview_only(configured_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.routers.pdf_parse.create_pdf_parse_preview",
        lambda db, config, knowledge_item_id, parser_name: {
            "id": "parse-preview-1",
            "parser_name": parser_name,
            "status": "preview",
            "preview_text": "preview text",
            "warning": "当前解析结果未保存前不会生效",
        },
    )

    response = configured_client.post(
        "/api/pdf/items/ki-1/reparse",
        json={"parser_name": "rapid_ocr"},
    )

    assert response.status_code == 202
    assert response.json()["parse_result"]["status"] == "preview"


def test_save_pdf_parse_result_promotes_preview(configured_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.routers.pdf_parse.save_pdf_parse_result",
        lambda db, config, knowledge_item_id, parse_result_id: {
            "id": parse_result_id,
            "parser_name": "rapid_ocr",
            "status": "saved",
            "preview_text": "saved text",
        },
    )

    response = configured_client.post(
        "/api/pdf/items/ki-1/parse-results/parse-preview-1/save"
    )

    assert response.status_code == 200
    assert response.json()["parse_result"]["status"] == "saved"
