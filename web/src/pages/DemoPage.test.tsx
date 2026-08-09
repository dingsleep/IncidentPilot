import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { EvaluationRun, Incident } from "../api/types";
import { DemoOverview } from "./DemoPage";

describe("DemoOverview", () => {
  it("explains the real investigation path and the read-only action boundary", () => {
    const incident = {
      id: "inc-demo",
      tenant_id: "local",
      source: "e2e",
      external_id: "inc-demo",
      status: "REPORTING",
      severity: "P1",
      title: "Checkout failures",
      service: "checkout",
      created_at: "2026-08-08T06:00:00Z",
      updated_at: "2026-08-08T06:04:00Z",
    } satisfies Incident;
    const evaluation = {
      id: "eval-demo",
      suite_version: "validation-v2-score-v5",
      candidate_version: "candidate-v1",
      status: "completed",
      aggregate_metrics: {
        mode: "multi",
        case_count: 4,
        weighted_score: 1,
        root_cause_accuracy: 1,
        evidence_fidelity: 1,
        safety_hard_failures: 0,
        total_cost_microusd: 900,
        total_duration_ms: 1000,
        total_tool_calls: 12,
      },
    } satisfies EvaluationRun;
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <DemoOverview
          candidateCount={1}
          evaluation={evaluation}
          featuredIncident={incident}
          rejectedCandidateCount={1}
          totalIncidentCount={18}
        />
      </MemoryRouter>,
    );

    expect(html).toContain("\u4ece\u544a\u8b66\u5230");
    expect(html).toContain("\u53ef\u6838\u9a8c\u6839\u56e0");
    expect(html).toContain("Metrics / Logs / Traces");
    expect(html).toContain("\u771f\u5b9e\u9065\u6d4b");
    expect(html).toContain("Action MCP");
    expect(html).toContain("\u9ed8\u8ba4\u5173\u95ed");
    expect(html).toContain("/incidents/inc-demo");
    expect(html).toContain("\u5019\u9009\u6539\u8fdb\u4e5f\u4e0d\u4f1a\u81ea\u52a8\u4e0a\u7ebf");
  });

  it("lets a non-technical visitor understand and start the product experience", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <DemoOverview
          candidateCount={3}
          rejectedCandidateCount={2}
          totalIncidentCount={18}
        />
      </MemoryRouter>,
    );

    expect(html).toContain("让 AI 事故响应团队接手");
    expect(html).toContain("粘贴告警内容");
    expect(html).toContain("选择演示服务");
    expect(html).toContain("体验真实诊断");
    expect(html).toContain("发起全新诊断");
    expect(html).toContain("AI 事故响应团队");
    expect(html).toContain("Metrics 调查员");
    expect(html).toContain("Logs 调查员");
    expect(html).toContain("Traces 调查员");
    expect(html).toContain("事故指挥官");
    expect(html).toContain("确定性安全门");
    expect(html).toContain("受控进化");
    expect(html).toContain("真实运行记录");
    expect(html).toContain("textarea");
    expect(html).toContain("select");
  });
});
