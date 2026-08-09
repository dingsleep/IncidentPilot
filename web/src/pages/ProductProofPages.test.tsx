import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EvaluationRun, EvolutionCandidate } from "../api/types";
import { EffectDashboard } from "./EffectPage";
import { EvolutionDecisionBoard } from "./LearningPage";

const candidateVersion = "p1-4d19782f3126:qwen3.7-flash:json_output:q-f4a05b7141c0:t-telemetry-v9:s-v15-a1-t8-m1";

function run(seed: number, cost: number): EvaluationRun {
  return {
    id: `eval-multi-seed-${seed}`,
    suite_version: "validation-v2-score-v5",
    candidate_version: candidateVersion,
    status: "completed",
    aggregate_metrics: {
      mode: "multi", case_count: 4, weighted_score: 1, root_cause_accuracy: 1,
      evidence_fidelity: 1, safety_hard_failures: 0, total_cost_microusd: cost,
      total_duration_ms: 110_000, total_tool_calls: 12,
    },
  };
}

describe("EffectDashboard", () => {
  it("makes the three-seed public validation evidence visible without presenting it as private holdout", () => {
    const html = renderToStaticMarkup(<EffectDashboard runs={[run(64, 2758), run(71, 2680), run(79, 3011)]} cases={[]} />);

    expect(html).toContain("三次独立 seed");
    expect(html).toContain("seed 64");
    expect(html).toContain("seed 71");
    expect(html).toContain("seed 79");
    expect(html).toContain("公开 validation");
    expect(html).toContain("不是私有 holdout");
    expect(html).toContain("0.679");
    expect(html).toContain("8,449 µUSD");
    expect(html).toContain("12 次独立场景运行");
  });
});

describe("EvolutionDecisionBoard", () => {
  it("shows the real rejected candidate, diff and regression evidence inline", () => {
    const candidate: EvolutionCandidate = {
      id: "candidate-f871693e17e3", kind: "prompt", base_version: "v1",
      target_failure_label: "wrong_synthesis", target_component: "incident_commander",
      generator_model: "deterministic-m9.3", digest: "f871693e17e307d5", status: "candidate",
      diff: "--- v1\n+++ candidate:prompt\n+Verify wrong synthesis before completing the response.",
      gate_statuses: ["shadow_rejected"], rejection_reasons: [],
      gate_records: [{
        status: "shadow_rejected", human_rejection_reason: null,
        decision: { comparison: { strategy: "json_output", worst_seed: 41,
          train: { active_run: "train-active", candidate_run: "train-candidate", active: aggregate(.9625, 1, 2913), candidate: aggregate(1, 1, 3124) },
          validation: { active_run: "val-active", candidate_run: "val-candidate", active: aggregate(1, 1, 2998), candidate: aggregate(.9125, .75, 2614) },
        } },
      }],
    };
    const html = renderToStaticMarkup(<EvolutionDecisionBoard candidates={[candidate]} />);

    expect(html).toContain("线上 Active 保持不变");
    expect(html).toContain("Prompt Diff");
    expect(html).toContain("Verify wrong synthesis");
    expect(html).toContain("验证集根因准确率");
    expect(html).toContain("1.000 → 0.750");
    expect(html).toContain("拒绝不是失败");
    expect(html).not.toContain("打开 Prompt diff");
  });
});

function aggregate(score: number, root: number, cost: number) {
  return { weighted_score: score, root_cause_accuracy: root, evidence_fidelity: 1, safety_hard_failures: 0, total_cost_microusd: cost };
}
