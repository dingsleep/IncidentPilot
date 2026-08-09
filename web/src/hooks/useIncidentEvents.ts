import { useEffect, useRef, useState } from "react";

export interface IncidentSseEvent {
  id: string;
  eventType: string;
  data: Record<string, unknown>;
}

interface SubscriptionOptions {
  incidentId: string;
  onEvent: (event: IncidentSseEvent) => void;
  signal: AbortSignal;
  actorId?: string;
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  retryDelayMs?: number;
  onStateChange?: (state: ConnectionState) => void;
  onError?: (error: unknown) => void;
}

export type ConnectionState = "connecting" | "live" | "reconnecting" | "stopped";

export async function subscribeIncidentEvents(options: SubscriptionOptions): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const retryDelayMs = options.retryDelayMs ?? 1_000;
  const seen = new Set<string>();
  let lastEventId: string | undefined;
  let firstAttempt = true;

  while (!options.signal.aborted) {
    options.onStateChange?.(firstAttempt ? "connecting" : "reconnecting");
    const headers = new Headers({
      Accept: "text/event-stream",
      "X-IncidentPilot-Actor": options.actorId ?? "local-viewer",
    });
    if (lastEventId) headers.set("Last-Event-ID", lastEventId);
    try {
      const response = await fetchImpl(
        `${baseUrl}/incidents/${encodeURIComponent(options.incidentId)}/events`,
        { headers, signal: options.signal },
      );
      if (!response.ok || response.body === null) {
        throw new Error(`SSE request failed with status ${response.status}`);
      }
      options.onStateChange?.("live");
      await readEventStream(response.body, options.signal, (event) => {
        if (seen.has(event.id)) return;
        seen.add(event.id);
        if (seen.size > 500) seen.delete(seen.values().next().value as string);
        lastEventId = event.id;
        options.onEvent(event);
      });
    } catch (error) {
      if (options.signal.aborted) break;
      options.onError?.(error);
    }
    firstAttempt = false;
    await wait(retryDelayMs, options.signal);
  }
  options.onStateChange?.("stopped");
}

export function useIncidentEvents(
  incidentId: string | undefined,
  onEvent: (event: IncidentSseEvent) => void,
): ConnectionState {
  const callback = useRef(onEvent);
  const [state, setState] = useState<ConnectionState>("connecting");
  callback.current = onEvent;

  useEffect(() => {
    if (!incidentId) return;
    const controller = new AbortController();
    void subscribeIncidentEvents({
      incidentId,
      signal: controller.signal,
      onEvent: (event) => callback.current(event),
      onStateChange: setState,
    });
    return () => controller.abort();
  }, [incidentId]);
  return state;
}

async function readEventStream(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
  onEvent: (event: IncidentSseEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseEvent(block);
        if (event) onEvent(event);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseEvent(block: string): IncidentSseEvent | undefined {
  if (!block || block.startsWith(":")) return undefined;
  let id = "";
  let eventType = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).trimStart();
    if (field === "id") id = value;
    if (field === "event") eventType = value;
    if (field === "data") data.push(value);
  }
  if (!id || data.length === 0) return undefined;
  const parsed: unknown = JSON.parse(data.join("\n"));
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return undefined;
  return { id, eventType, data: parsed as Record<string, unknown> };
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (milliseconds <= 0 || signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}
