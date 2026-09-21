import { describe, expect, it } from "vitest";

import {
  createDefaultSmartCaptureExecutionConfig,
  toSmartCaptureRequest,
  validateSmartCaptureExecutionConfig
} from "./smartCaptureExecutionConfig";

describe("Smart Capture execution config", () => {
  it("keeps OFF valid without Codex or recommendation fields", () => {
    const config = createDefaultSmartCaptureExecutionConfig("boss_capture");
    config.filter_strategy_id = "filter-1";
    config.allowed_search_keywords = ["Python"];
    config.allowed_cities = ["上海"];

    const validation = validateSmartCaptureExecutionConfig(config);
    const request = toSmartCaptureRequest(config);

    expect(validation.isValid).toBe(true);
    expect(validation.missingFields).toEqual([]);
    expect(request.recommendation_strategy_id).toBeUndefined();
    expect(request.codex_model).toBeUndefined();
  });

  it("requires ON delivery fields and numeric relationships", () => {
    const config = createDefaultSmartCaptureExecutionConfig("task_cockpit");
    config.filter_strategy_id = "filter-1";
    config.allowed_search_keywords = ["Python"];
    config.allowed_cities = ["上海"];
    config.candidate_target_count = 2;
    config.recommend_target = 3;

    const validation = validateSmartCaptureExecutionConfig(config);

    expect(validation.isValid).toBe(false);
    expect(validation.missingFields).toEqual(expect.arrayContaining([
      "recommendation_strategy_id",
      "codex_model"
    ]));
    expect(validation.errors.candidate_target_count).toContain("不能小于");
  });

  it("flattens the complete form model without dropping fields", () => {
    const config = createDefaultSmartCaptureExecutionConfig("task_cockpit");
    config.filter_strategy_id = "filter-1";
    config.allowed_search_keywords = ["Python"];
    config.allowed_cities = ["上海"];
    config.recommendation_strategy_id = "recommendation-1";
    config.codex_model = "gpt-5.6-luna";
    config.filters = { experience: "104" };
    config.review_target_enabled = true;
    config.review_target = 2;

    const request = toSmartCaptureRequest(config);

    expect(request).toMatchObject({
      filter_strategy_id: "filter-1",
      filters: { experience: "104" },
      recommendation_strategy_id: "recommendation-1",
      review_target: 2,
      codex_model: "gpt-5.6-luna",
      context_soft_budget_characters: 12000,
      min_depth: 5,
      max_depth: 20
    });
  });
});
