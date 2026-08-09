import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalPanel } from "./ApprovalPanel";

describe("ApprovalPanel", () => {
  it("shows fixed action, evidence, compensation and verification without editable parameters", () => {
    const html = renderToStaticMarkup(<ApprovalPanel proposal={{
      id: "proposal-1", status: "PENDING_APPROVAL", policy: { allowed: true }, proposal: {
        action: { action_type: "restart_service", target_service: "checkout", grace_period_seconds: 30 }, risk: "low", diagnosis_evidence_ids: ["ev-1", "ev-2"], expected_effect: "Recover checkout.", idempotency_key: "restart-1",
        compensation_plan: { mode: "not_applicable", trigger: "none", reason: "No config is changed." },
        verification_checks: [{ service: "checkout", metric: "error_ratio", query_template_id: "service_error_ratio", comparator: "lt", threshold: 0.05, observation_seconds: 30 }],
      },
    }} onDecision={async () => {}} />);

    expect(html).toContain("checkout");
    expect(html).toContain("ev-1");
    expect(html).toContain("not_applicable / none");
    expect(html).toContain("error_ratio lt 0.05");
    expect(html).toContain("批准受限动作");
    expect(html).toContain("拒绝");
    expect(html).not.toContain("<input");
  });
});
