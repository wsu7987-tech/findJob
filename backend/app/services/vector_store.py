from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.errors import AppError
from backend.app.services.ai import RelatedContextItem

try:
    from qdrant_client import QdrantClient, models
except ImportError:  # pragma: no cover - exercised in runtime verification instead
    QdrantClient = None
    models = None


@dataclass(slots=True)
class VectorRecord:
    snapshot_id: str
    knowledge_item_id: str
    title: str
    final_category: str
    summary_text: str


class BaseJsonQdrantStore:
    def __init__(
        self,
        *,
        config: AppConfig,
        collection_prefix: str,
        provider_name: str,
        model_name: str,
        version_tag: str | None = None,
    ) -> None:
        self.collection_name = _collection_name(
            collection_prefix,
            provider_name,
            model_name,
            version_tag=version_tag,
        )
        self.storage_path = config.qdrant_path / f"{self.collection_name}.json"
        self.client = None
        if QdrantClient is not None and models is not None:
            try:
                self.client = QdrantClient(path=str(config.qdrant_path))
            except Exception:
                self.client = None

    def search_payloads(
        self,
        vector: list[float],
        *,
        limit: int = 5,
        filters: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        if not vector:
            return []

        if self.client is not None and self._collection_exists():
            try:
                results = self._search_points(vector=vector, limit=limit, filters=filters)
            except Exception:
                results = None
            if results is not None:
                items: list[dict[str, object]] = []
                for point in results:
                    payload = point.payload or {}
                    item = dict(payload)
                    item["id"] = str(point.id)
                    item["score"] = float(getattr(point, "score", 0.0) or 0.0)
                    items.append(item)
                return items

        stored_records = self._read_fallback_records()
        scored_items = []
        for item in stored_records:
            item_vector = item.get("vector")
            if not isinstance(item_vector, list):
                continue
            if not _matches_payload_filters(item, filters):
                continue
            score = _cosine_similarity(vector, [float(value) for value in item_vector])
            payload = {key: value for key, value in item.items() if key != "vector"}
            payload["score"] = score
            scored_items.append(payload)
        scored_items.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
        return scored_items[:limit]

    def _search_points(
        self,
        *,
        vector: list[float],
        limit: int,
        filters: dict[str, object] | None = None,
    ):
        if self.client is None:
            return None
        query_filter = _build_qdrant_filter(filters)
        if hasattr(self.client, "search"):
            kwargs = {
                "collection_name": self.collection_name,
                "query_vector": vector,
                "limit": limit,
                "with_payload": True,
            }
            if query_filter is not None:
                kwargs["query_filter"] = query_filter
            return self.client.search(**kwargs)
        if hasattr(self.client, "query_points"):
            kwargs = {
                "collection_name": self.collection_name,
                "query": vector,
                "limit": limit,
                "with_payload": True,
            }
            if query_filter is not None:
                kwargs["query_filter"] = query_filter
            response = self.client.query_points(**kwargs)
            return getattr(response, "points", None)
        raise AttributeError("The configured Qdrant client does not support search or query_points.")

    def upsert_payload(
        self,
        *,
        point_id: str,
        vector: list[float],
        payload: dict[str, object],
    ) -> None:
        if not vector:
            raise AppError(
                status_code=500,
                error_category="EMBEDDING_FAILED",
                error_message="Cannot upsert an empty embedding vector.",
            )

        if self.client is not None:
            try:
                self._ensure_collection(vector_size=len(vector))
                self.client.upsert(
                    collection_name=self.collection_name,
                    wait=True,
                    points=[
                        models.PointStruct(
                            id=point_id,
                            vector=vector,
                            payload=payload,
                        )
                    ],
                )
                return
            except Exception:
                self.client = None

        self._upsert_fallback_payload(point_id=point_id, vector=vector, payload=payload)

    def delete_payloads_by_field(self, *, field_name: str, field_value: object) -> None:
        if self.client is not None and self._collection_exists():
            try:
                self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key=field_name,
                                    match=models.MatchValue(value=field_value),
                                )
                            ]
                        )
                    ),
                    wait=True,
                )
                return
            except Exception:
                self.client = None

        records = self._read_fallback_records()
        filtered = [item for item in records if item.get(field_name) != field_value]
        self._write_fallback_records(filtered)

    def reset(self) -> None:
        if self.client is not None and self._collection_exists():
            try:
                self.client.delete_collection(collection_name=self.collection_name)
            except Exception:
                self.client = None
        if self.storage_path.exists():
            self.storage_path.unlink(missing_ok=True)

    def _ensure_collection(self, *, vector_size: int) -> None:
        if self.client is None:
            return
        if self._collection_exists():
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    def _collection_exists(self) -> bool:
        if self.client is None:
            return False
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            return False
        return True

    def _read_fallback_records(self) -> list[dict[str, object]]:
        if not self.storage_path.exists():
            return []
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError(
                status_code=500,
                error_category="RETRIEVAL_FAILED",
                error_message=f"Failed to read local vector index: {exc}",
            ) from exc
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _write_fallback_records(self, records: list[dict[str, object]]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.storage_path.write_text(
                json.dumps(records, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise AppError(
                status_code=500,
                error_category="RETRIEVAL_FAILED",
                error_message=f"Failed to persist local vector index: {exc}",
            ) from exc

    def _upsert_fallback_payload(
        self,
        *,
        point_id: str,
        vector: list[float],
        payload: dict[str, object],
    ) -> None:
        records = self._read_fallback_records()
        stored_payload = dict(payload)
        stored_payload["id"] = point_id
        stored_payload["vector"] = vector
        filtered = [item for item in records if str(item.get("id")) != point_id]
        filtered.append(stored_payload)
        self._write_fallback_records(filtered)


class SummaryVectorStore(BaseJsonQdrantStore):
    def __init__(
        self,
        *,
        config: AppConfig,
        provider_name: str,
        model_name: str,
        version_tag: str | None = None,
    ) -> None:
        super().__init__(
            config=config,
            collection_prefix="summary",
            provider_name=provider_name,
            model_name=model_name,
            version_tag=version_tag,
        )

    def search_related(self, vector: list[float], *, limit: int = 5) -> list[RelatedContextItem]:
        payloads = self.search_payloads(vector, limit=limit)
        items: list[RelatedContextItem] = []
        for item in payloads:
            snapshot_id = str(item.get("snapshot_id") or item.get("id") or "")
            items.append(
                RelatedContextItem(
                    snapshot_id=snapshot_id,
                    knowledge_item_id=_as_optional_string(item.get("knowledge_item_id")),
                    title=_as_optional_string(item.get("title")) or snapshot_id,
                    final_category=_as_optional_string(item.get("final_category")),
                    summary_text=_as_optional_string(item.get("summary_text")) or "",
                    score=float(item.get("score", 0.0) or 0.0),
                )
            )
        return items

    def upsert_snapshot(self, *, vector: list[float], record: VectorRecord) -> None:
        self.upsert_payload(
            point_id=record.snapshot_id,
            vector=vector,
            payload={
                "snapshot_id": record.snapshot_id,
                "knowledge_item_id": record.knowledge_item_id,
                "title": record.title,
                "final_category": record.final_category,
                "summary_text": record.summary_text,
            },
        )


def _collection_name(
    collection_prefix: str,
    provider_name: str,
    model_name: str,
    *,
    version_tag: str | None = None,
) -> str:
    prefix_slug = _slug(collection_prefix or "vector")
    provider_slug = _slug(provider_name or "embedding")
    model_slug = _slug(model_name or "default")
    base_name = f"{prefix_slug}_{provider_slug}_{model_slug}"
    if not version_tag or version_tag == "legacy":
        return base_name
    return f"{base_name}_v_{_slug(version_tag)}"


def _slug(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in value.lower())
    return normalized.strip("_") or "default"


def _as_optional_string(value: object) -> str | None:
    if value is None:
        return None
    string_value = str(value).strip()
    return string_value or None


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    numerator = sum(l_value * r_value for l_value, r_value in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _matches_payload_filters(payload: dict[str, object], filters: dict[str, object] | None) -> bool:
    if not filters:
        return True
    source_types = filters.get("source_types")
    if source_types and payload.get("source_type") not in source_types:
        return False
    knowledge_item_ids = filters.get("knowledge_item_ids")
    if knowledge_item_ids and payload.get("knowledge_item_id") not in knowledge_item_ids:
        return False
    category = filters.get("category")
    if category and payload.get("category") != category:
        return False
    created_at_from = filters.get("created_at_from")
    if created_at_from and not _timestamp_on_or_after(payload.get("created_at"), created_at_from):
        return False
    created_at_to = filters.get("created_at_to")
    if created_at_to and not _timestamp_on_or_before(payload.get("created_at"), created_at_to):
        return False
    user_tags = filters.get("user_tags")
    if user_tags and not _payload_list_overlaps(payload.get("user_tags"), user_tags):
        return False
    ai_tags = filters.get("ai_tags")
    if ai_tags and not _payload_list_overlaps(payload.get("ai_tags"), ai_tags):
        return False
    return True


def _payload_list_overlaps(value: object, expected: object) -> bool:
    if not isinstance(expected, list) or not expected:
        return True
    if not isinstance(value, list):
        return False
    actual = {str(item) for item in value}
    return any(str(item) in actual for item in expected)


def _build_qdrant_filter(filters: dict[str, object] | None):
    if not filters or models is None:
        return None
    must_conditions = []

    source_types = filters.get("source_types")
    if isinstance(source_types, list) and source_types:
        must_conditions.append(
            models.FieldCondition(
                key="source_type",
                match=models.MatchAny(any=[str(item) for item in source_types]),
            )
        )
    knowledge_item_ids = filters.get("knowledge_item_ids")
    if isinstance(knowledge_item_ids, list) and knowledge_item_ids:
        must_conditions.append(
            models.FieldCondition(
                key="knowledge_item_id",
                match=models.MatchAny(any=[str(item) for item in knowledge_item_ids]),
            )
        )
    category = filters.get("category")
    if category:
        must_conditions.append(
            models.FieldCondition(
                key="category",
                match=models.MatchValue(value=str(category)),
            )
        )
    created_at_from = _parse_datetime(filters.get("created_at_from"))
    created_at_to = _parse_datetime(filters.get("created_at_to"))
    if created_at_from is not None or created_at_to is not None:
        must_conditions.append(
            models.FieldCondition(
                key="created_at",
                range=models.DatetimeRange(gte=created_at_from, lte=created_at_to),
            )
        )
    user_tags = filters.get("user_tags")
    if isinstance(user_tags, list) and user_tags:
        must_conditions.append(
            models.FieldCondition(
                key="user_tags",
                match=models.MatchAny(any=[str(item) for item in user_tags]),
            )
        )
    ai_tags = filters.get("ai_tags")
    if isinstance(ai_tags, list) and ai_tags:
        must_conditions.append(
            models.FieldCondition(
                key="ai_tags",
                match=models.MatchAny(any=[str(item) for item in ai_tags]),
            )
        )

    if not must_conditions:
        return None
    return models.Filter(must=must_conditions)


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_on_or_after(value: object, boundary: object) -> bool:
    parsed_value = _parse_datetime(value)
    parsed_boundary = _parse_datetime(boundary)
    if parsed_value is None or parsed_boundary is None:
        return str(value or "") >= str(boundary or "")
    return parsed_value >= parsed_boundary


def _timestamp_on_or_before(value: object, boundary: object) -> bool:
    parsed_value = _parse_datetime(value)
    parsed_boundary = _parse_datetime(boundary)
    if parsed_value is None or parsed_boundary is None:
        return str(value or "") <= str(boundary or "")
    return parsed_value <= parsed_boundary
