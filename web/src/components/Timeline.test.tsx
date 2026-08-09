import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Timeline, type TimelineEntry } from "./Timeline";

describe("Timeline", () => {
  it("renders auditable metadata without exposing thought-like payload fields", () => {
    const events: TimelineEntry[] = [
      {
        id: "evt-2",
        eventType: "diagnosis.created",
        actorId: "graph-worker",
        actorType: "worker",
        createdAt: "2026-07-17T08:01:00Z",
        payload: {
          confidence: 0.91,
          root_cause_service: "payment",
          reasoning_content: "private reasoning must never render",
        },
      },
      {
        id: "evt-1",
        eventType: "tool.completed",
        actorId: "traces-agent",
        actorType: "agent",
        createdAt: "2026-07-17T08:00:00Z",
        payload: {
          duration_ms: 84,
          thought: "hidden chain of thought",
          tool_name: "search_traces",
        },
      },
    ];

    const html = renderToStaticMarkup(<Timeline events={events} />);

    expect(html).toContain("search_traces");
    expect(html).toContain("payment");
    expect(html.indexOf("search_traces")).toBeLessThan(html.indexOf("payment"));
    expect(html).not.toContain("hidden chain of thought");
    expect(html).not.toContain("private reasoning");
    expect(html).not.toContain("Thought");
  });
});
