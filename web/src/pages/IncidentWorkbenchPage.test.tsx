import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { WorkbenchReport } from "../api/types";
import { DiagnosisExperience } from "./IncidentWorkbenchPage";

describe("DiagnosisExperience", () => {
  it("explains the diagnosis and agent collaboration before technical details", () => {
    const report = {
      incident_id: "inc-real",
      diagnosis: {
        symptom_service: "checkout",
        root_cause_service: "payment",
        root_cause_category: "application_failure",
        root_cause_summary: "Payment calls failed after the dependency became unreachable.",
        confidence: 0.94,
        evidence_ids: ["ev-metric", "ev-log", "ev-trace"],
        customer_impact: "Checkout requests failed.",
      },
      reports: [
        { wave: 1, report: { investigator: "metrics", scope_services: ["checkout", "payment"] } },
        { wave: 1, report: { investigator: "logs", scope_services: ["payment"] } },
        { wave: 1, report: { investigator: "traces", scope_services: ["checkout", "payment"] } },
      ],
    } satisfies WorkbenchReport;

    const html = renderToStaticMarkup(<DiagnosisExperience incidentStatus="RESOLVED_READ_ONLY" report={report} />);

    expect(html).toContain("AI 团队已完成诊断");
    expect(html).toContain("根因定位");
    expect(html).toContain("支付服务");
    expect(html).toContain("94%");
    expect(html).toContain("3 条证据交叉支持");
    expect(html).toContain("Metrics 调查员");
    expect(html).toContain("Logs 调查员");
    expect(html).toContain("Traces 调查员");
    expect(html).toContain("事故指挥官");
    expect(html).toContain("确定性安全门");
    expect(html).toContain("本次未进入写操作");
  });
});
