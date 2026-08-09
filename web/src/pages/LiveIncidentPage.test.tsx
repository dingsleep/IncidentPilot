import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { LiveTeamBoard, ResultFlow } from "./LiveIncidentPage";

describe("LiveTeamBoard", () => {
  it("renders actual event state instead of an invented animation", () => {
    const html = renderToStaticMarkup(
      <LiveTeamBoard events={[{
        id: "audit-1",
        eventType: "agent.completed",
        actorId: "graph-worker",
        actorType: "worker",
        createdAt: "2026-08-08T06:00:01Z",
        payload: {
          stage: "investigation",
          status: "completed",
          agent: "logs_investigator",
          message: "发现支付连接异常",
        },
      }]} completed={false} />,
    );

    expect(html).toContain("AI 团队正在工作");
    expect(html).toContain("日志调查 Agent");
    expect(html).toContain("发现支付连接异常");
    expect(html).toContain("事故指挥 Agent");
    expect(html).toContain("确定性安全门");
    expect(html).toContain("parallel-zone");
    expect(html).toContain("当前焦点");
    expect(html).toContain("四路并行调查");
    expect(html).toContain("1 / 4 已返回");
    expect(html).toContain("operation-focus");
  });

  it("treats a read-only diagnosis as a terminal result with clear next actions", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ResultFlow
      diagnosis={{
        symptom_service: "checkout", root_cause_service: "payment",
        root_cause_category: "dependency_unreachable", root_cause_summary: "支付依赖不可达",
        confidence: .92, evidence_ids: [], customer_impact: "结算失败",
      }}
      evidence={[]}
      completed
      incidentStatus="RESOLVED_READ_ONLY"
      events={[]}
      onOpenDetails={() => undefined}
      onDecision={async () => undefined}
    /></MemoryRouter>);

    expect(html).toContain("本次只读诊断已完成");
    expect(html).toContain("不是卡住");
    expect(html).toContain("发起新的真实诊断");
    expect(html).toContain("查看事故记录");
    expect(html).toContain("事故复盘已固化");
    expect(html).toContain('href="/demo"');
  });

  it("links a queued run to the real incident waiting for approval", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ResultFlow
      evidence={[]}
      completed={false}
      incidentStatus="RECEIVED"
      events={[]}
      blockedByIncident={{
        id: "inc-blocker", tenant_id: "local", source: "manual", external_id: "inc-blocker",
        status: "WAITING_APPROVAL", severity: "P1", title: "购物车操作失败", service: "cart",
        created_at: "2026-08-09T04:46:40Z", updated_at: "2026-08-09T04:47:32Z",
      }}
      onOpenDetails={() => undefined}
      onDecision={async () => undefined}
    /></MemoryRouter>);

    expect(html).toContain("等待上一条事故完成审批");
    expect(html).toContain("购物车操作失败");
    expect(html).toContain('href="/incidents/inc-blocker"');
  });
});
