import { describe, expect, it } from "vitest";

import type { Incident } from "../api/types";
import { friendlyServiceName, incidentStatusView, isPublicIncident } from "./IncidentListPage";

function incident(source: string, title: string): Incident {
  return {
    id: `inc-${source}`,
    tenant_id: "local",
    source,
    external_id: `inc-${source}`,
    status: "RECEIVED",
    severity: "P2",
    title,
    service: "checkout",
    created_at: "2026-08-08T06:00:00Z",
    updated_at: "2026-08-08T06:00:00Z",
  };
}

describe("isPublicIncident", () => {
  it("keeps reproducible demo episodes while hiding internal test fixtures", () => {
    expect(isPublicIncident(incident("prometheus-alertmanager", "Checkout is failing"))).toBe(true);
    expect(isPublicIncident(incident("e2e", "Checkout failures"))).toBe(true);
    expect(isPublicIncident(incident("evaluation-runner", "Cart operations are failing"))).toBe(true);
    expect(isPublicIncident(incident("workflow-store-test", "Workflow store test"))).toBe(false);
    expect(isPublicIncident(incident("manual", "SSE integration test"))).toBe(false);
  });

  it("translates service names and marks long-waiting approvals for human review", () => {
    const waiting = { ...incident("manual", "Cart operations are failing"), status: "WAITING_APPROVAL" as const };

    expect(friendlyServiceName("recommendation")).toBe("推荐服务");
    expect(incidentStatusView(waiting, new Date("2026-08-08T06:16:00Z").valueOf())).toEqual({
      label: "待人工复核",
      stale: true,
    });
  });
});
