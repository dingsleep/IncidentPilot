import { describe, expect, it, vi } from "vitest";

import { ApiProblemError, createApiClient } from "./client";

describe("API client", () => {
  it("sends local identity and correlation headers and preserves pagination", async () => {
    const page = {
      items: [],
      next_cursor: "next-page",
    };
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(page), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = createApiClient({
      actorId: "local-operator",
      baseUrl: "http://localhost:8200/api/v1",
      correlationId: () => "corr-123",
      fetchImpl,
    });

    await expect(
      client.listIncidents({ cursor: "cursor-1", limit: 20, severity: "P1" }),
    ).resolves.toEqual(page);

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(
      "http://localhost:8200/api/v1/incidents?cursor=cursor-1&limit=20&severity=P1",
    );
    expect(new Headers(init?.headers).get("X-IncidentPilot-Actor")).toBe("local-operator");
    expect(new Headers(init?.headers).get("X-Correlation-ID")).toBe("corr-123");
  });

  it("normalizes Problem Details and exposes the correlation id", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "urn:incidentpilot:not-found",
          title: "Incident not found",
          status: 404,
          detail: "No incident exists for the supplied id.",
        }),
        {
          status: 404,
          headers: {
            "Content-Type": "application/problem+json",
            "X-Correlation-ID": "server-corr",
          },
        },
      ),
    );
    const client = createApiClient({ fetchImpl, correlationId: () => "client-corr" });

    const error = await client.getIncident("missing").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiProblemError);
    expect(error).toMatchObject({
      correlationId: "server-corr",
      problem: {
        status: 404,
        title: "Incident not found",
        type: "urn:incidentpilot:not-found",
      },
    });
  });

  it("loads evaluation run summaries and a selected run", async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify([])))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "run-1", cases: [] })));
    const client = createApiClient({ fetchImpl });

    await client.listEvaluationRuns(12);
    await client.getEvaluationRun("run-1");

    expect(fetchImpl.mock.calls[0][0]).toBe("/api/v1/evaluations/runs?limit=12");
    expect(fetchImpl.mock.calls[1][0]).toBe("/api/v1/evaluations/runs/run-1");
  });

  it("creates a fresh read-only diagnosis with the local operator identity", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ incident: { id: "inc-new" }, job_id: "job-new" })),
    );
    const client = createApiClient({ actorId: "local-operator", fetchImpl });

    await client.createIncident({
      title: "结算服务出现异常",
      description: "错误率升高",
      severity: "P1",
      service: "checkout",
    });

    expect(fetchImpl.mock.calls[0][0]).toBe("/api/v1/incidents");
    expect(fetchImpl.mock.calls[0][1]?.method).toBe("POST");
    expect(new Headers(fetchImpl.mock.calls[0][1]?.headers).get("X-IncidentPilot-Actor")).toBe("local-operator");
  });

  it("loads the current proposal without deriving its id from redacted audit events", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ id: "proposal-real", status: "PENDING_APPROVAL" })),
    );
    const client = createApiClient({ fetchImpl });

    await client.getCurrentActionProposal("inc-1");

    expect(fetchImpl.mock.calls[0][0]).toBe("/api/v1/incidents/inc-1/proposals/current");
  });
});
