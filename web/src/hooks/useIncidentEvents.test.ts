import { describe, expect, it, vi } from "vitest";

import { subscribeIncidentEvents } from "./useIncidentEvents";

const encoder = new TextEncoder();

function eventStream(chunks: string[]): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("subscribeIncidentEvents", () => {
  it("reconnects with Last-Event-ID and ignores a replayed event", async () => {
    const abort = new AbortController();
    const requests: Headers[] = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(new Headers(init?.headers));
      if (requests.length === 1) {
        return eventStream([
          "id: event-1\nevent: tool.completed\nda",
          'ta: {"schema_version":1,"payload":{"tool_name":"query_metrics"}}\n\n',
        ]);
      }
      return eventStream([
        'id: event-1\nevent: tool.completed\ndata: {"schema_version":1,"payload":{}}\n\n',
        'id: event-2\nevent: diagnosis.created\ndata: {"schema_version":1,"payload":{"root_cause_service":"payment"}}\n\n',
      ]);
    });
    const seen: string[] = [];

    await subscribeIncidentEvents({
      actorId: "local-viewer",
      fetchImpl: fetchImpl as typeof fetch,
      incidentId: "inc-1",
      retryDelayMs: 0,
      signal: abort.signal,
      onEvent(event) {
        seen.push(event.id);
        if (event.id === "event-2") abort.abort();
      },
    });

    expect(seen).toEqual(["event-1", "event-2"]);
    expect(requests).toHaveLength(2);
    expect(requests[0].get("X-IncidentPilot-Actor")).toBe("local-viewer");
    expect(requests[1].get("Last-Event-ID")).toBe("event-1");
  });
});
