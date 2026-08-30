from __future__ import annotations

from typing import Any

from backend.app.services.fine_job.profile_analysis import profile_analysis_output_schema


def _walk_schema(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_schema(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_schema(value)


def test_profile_analysis_schema_is_strict_and_requires_every_object_field() -> None:
    schema = profile_analysis_output_schema()

    for node in _walk_schema(schema):
        if node.get("type") == "object" and "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        assert "default" not in node

    fact_schema = schema["$defs"]["AnalysisFactOutput"]["properties"]["value"]
    assert fact_schema.get("anyOf")
    assert {item["type"] for item in fact_schema["anyOf"]} >= {
        "string",
        "integer",
        "number",
        "boolean",
        "array",
    }
