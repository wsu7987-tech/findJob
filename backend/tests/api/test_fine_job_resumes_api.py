from __future__ import annotations

import json

from backend.app.utils import utc_now


def test_extract_and_save_resume_facts(configured_client, sqlite_connection) -> None:
    now = utc_now()
    sqlite_connection.execute(
        """
        INSERT INTO fj_resumes (
          id, name, file_path, file_hash, parser_name, raw_text,
          markdown_text, preview_text, page_count, char_count,
          quality_score, is_ocr, warnings_json, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "resume-1",
            "resume",
            "D:/resume.pdf",
            "hash-1",
            "auto",
            "张三\n手机号 13800138000\n邮箱 zhangsan@example.com\n教育经历\n本科 软件工程",
            None,
            "张三",
            1,
            64,
            0.8,
            0,
            json.dumps([]),
            "parsed",
            now,
            now,
        ),
    )
    sqlite_connection.commit()

    extract_response = configured_client.post("/api/fine-job/resumes/resume-1/facts/extract")

    assert extract_response.status_code == 200
    facts = extract_response.json()["facts"]
    assert any(fact["fact_key"] == "手机号" for fact in facts)
    assert any(fact["fact_key"] == "邮箱" for fact in facts)

    save_response = configured_client.put(
        "/api/fine-job/resumes/resume-1/facts",
        json={
            "facts": [
                {
                    "fact_type": "basic",
                    "fact_key": "姓名",
                    "fact_value": "张三",
                    "confidence": 1,
                    "user_confirmed": True,
                    "sensitive": False,
                }
            ]
        },
    )

    assert save_response.status_code == 200
    saved = save_response.json()["facts"]
    assert saved == [
        {
            "id": saved[0]["id"],
            "resume_id": "resume-1",
            "fact_type": "basic",
            "fact_key": "姓名",
            "fact_value": "张三",
            "confidence": 1.0,
            "source_text": None,
            "user_confirmed": True,
            "sensitive": False,
            "created_at": saved[0]["created_at"],
            "updated_at": saved[0]["updated_at"],
        }
    ]
