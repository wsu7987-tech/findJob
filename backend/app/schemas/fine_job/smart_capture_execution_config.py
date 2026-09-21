from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def execution_config_validation_errors(payload: Mapping[str, Any]) -> list[str]:
    """返回共享执行配置的条件校验错误，供请求模型和服务层复用。"""
    errors: list[str] = []
    delivery_enabled = bool(payload.get("delivery_target_enabled", False))
    if delivery_enabled:
        if not str(payload.get("recommendation_strategy_id") or "").strip():
            errors.append("开启投递目标时必须填写建议投递策略。")
        if not str(payload.get("codex_model") or "").strip():
            errors.append("开启投递目标时必须填写 Codex 模型。")
        if not str(payload.get("codex_reasoning_effort") or "").strip():
            errors.append("开启投递目标时必须填写 Codex 推理强度。")
        recommend_target = payload.get("recommend_target") or payload.get("target_count")
        if recommend_target is None:
            errors.append("开启投递目标时必须填写 Recommend 目标。")
        else:
            candidate_target = int(payload.get("candidate_target_count") or 15)
            if candidate_target < int(recommend_target):
                errors.append("候选池目标不能小于本轮推荐岗位目标。")

    min_depth = int(payload.get("min_depth") or payload.get("pages") or 1)
    max_depth = int(payload.get("max_depth") or 20)
    if max_depth < min_depth:
        errors.append("最大搜索深度不能小于最低探索深度。")
    return errors
