export interface TimelineEntry {
  id: string;
  eventType: string;
  actorType: string;
  actorId: string;
  createdAt: string;
  payload: Record<string, unknown>;
}

const labels: Record<string, string> = {
  "incident.status_changed": "事故状态更新",
  "agent.started": "调查 Agent 启动",
  "tool.started": "工具调用开始",
  "tool.completed": "工具调用完成",
  "evidence.created": "证据已固化",
  "hypothesis.updated": "候选假设更新",
  "diagnosis.created": "诊断已形成",
  "approval.requested": "等待人工审批",
  "action.completed": "处置动作完成",
  "verification.completed": "恢复验证完成",
  "incident.completed": "只读调查完成",
  "run.failed": "调查运行失败",
  GRAPH_COMPLETED: "只读调查完成",
};

export function Timeline({ events }: { events: TimelineEntry[] }) {
  const ordered = [...events].sort((left, right) =>
    left.createdAt.localeCompare(right.createdAt),
  );
  if (ordered.length === 0) {
    return <p className="empty-note">尚无调查事件，等待 Worker 接手。</p>;
  }
  return (
    <ol className="timeline">
      {ordered.map((event) => {
        const metadata = auditMetadata(event.payload);
        return (
          <li key={event.id}>
            <span className="timeline-rail" aria-hidden="true" />
            <div className="timeline-heading">
              <strong>{labels[event.eventType] ?? "事故状态更新"}</strong>
              <time dateTime={event.createdAt}>{formatTime(event.createdAt)}</time>
            </div>
            <p>{event.actorId}<span> / {event.actorType}</span></p>
            {metadata.length > 0 && (
              <dl>{metadata.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function auditMetadata(payload: Record<string, unknown>): [string, string][] {
  const diagnosis = record(payload.diagnosis);
  const values: [string, unknown][] = [
    ["status", payload.status],
    ["agent", payload.agent_name],
    ["tool", payload.tool_name],
    ["evidence", payload.evidence_id],
    ["root cause", payload.root_cause_service ?? diagnosis?.root_cause_service],
    ["confidence", payload.confidence ?? diagnosis?.confidence],
    ["duration", duration(payload.duration_ms)],
  ];
  return values.flatMap(([key, value]) => {
    if (typeof value === "string" || typeof value === "number") return [[key, String(value)]];
    return [];
  });
}

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function duration(value: unknown): string | undefined {
  return typeof value === "number" ? `${Math.round(value)} ms` : undefined;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(date);
}
