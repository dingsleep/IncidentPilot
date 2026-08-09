import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiProblemError, api } from "../api/client";
import type { Incident, IncidentStatus, Severity } from "../api/types";

const statusLabels: Record<IncidentStatus, string> = {
  RECEIVED: "已接收", TRIAGING: "分诊中", INVESTIGATING: "调查中", SYNTHESIZING: "综合中",
  DIAGNOSED: "已诊断", PLANNING: "规划中", WAITING_APPROVAL: "等待审批", AUTHORIZING: "授权中",
  EXECUTING: "执行中", VERIFYING: "验证中", ROLLING_BACK: "回滚中", RESOLVED: "已恢复",
  RESOLVED_READ_ONLY: "只读解决", NEEDS_HUMAN: "需要人工", POLICY_REJECTED: "策略拒绝",
  ACTION_FAILED: "动作失败", REJECTED: "已拒绝", REPORTING: "报告完成",
};

export function IncidentListPage() {
  const [service, setService] = useState("");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [includeSynthetic, setIncludeSynthetic] = useState(false);
  const incidents = useInfiniteQuery({
    queryKey: ["incidents", service, severity],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.listIncidents({
      cursor: pageParam,
      limit: 20,
      service: service || undefined,
      severity: severity || undefined,
    }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const allItems = incidents.data?.pages.flatMap((page) => page.items) ?? [];
  const items = includeSynthetic ? allItems : allItems.filter(isPublicIncident);

  return (
    <section className="incident-list-page">
      <header className="page-heading">
        <div><p className="eyebrow">{"\u5b9e\u65f6\u4e8b\u6545 / LIVE API"}</p><h1>事故队列</h1></div>
        <div className="queue-count"><strong>{items.length.toString().padStart(2, "0")}</strong><span>{"\u5f53\u524d\u663e\u793a"}</span></div>
      </header>
      <div className="filter-strip">
        <label>服务<input value={service} onChange={(event) => setService(event.target.value)} placeholder="checkout" /></label>
        <label>严重度<select value={severity} onChange={(event) => setSeverity(event.target.value as Severity | "")}><option value="">全部</option>{(["P1", "P2", "P3", "P4"] as const).map((value) => <option key={value}>{value}</option>)}</select></label>
        <button className="synthetic-toggle" type="button" aria-pressed={includeSynthetic} onClick={() => setIncludeSynthetic((value) => !value)}>{includeSynthetic ? "\u9690\u85cf\u6d4b\u8bd5\u8bb0\u5f55" : "\u663e\u793a\u6d4b\u8bd5\u8bb0\u5f55"}</button>
        <span>{"\u53ea\u8bfb\u8bca\u65ad"}</span>
      </div>
      {incidents.isPending && <ListSkeleton />}
      {incidents.isError && <ErrorPanel error={incidents.error} />}
      {!incidents.isPending && !incidents.isError && items.length === 0 && (
        <div className="empty-state"><span>00</span><h2>当前筛选下没有事故</h2><p>新告警到达后会出现在这里。</p></div>
      )}
      {items.length > 0 && (
        <div className="incident-table" aria-label="事故列表">
          <div className="incident-row table-head"><span>事故 / 服务</span><span>严重度</span><span>状态</span><span>持续时间</span></div>
          {items.map((incident) => {
            const status = incidentStatusView(incident);
            return <Link className={`incident-row${status.stale ? " needs-attention" : ""}`} key={incident.id} to={`/incidents/${incident.id}`}>
              <span><strong>{incident.title}</strong><small>{friendlyServiceName(incident.service)} · {incident.id.slice(0, 14)}</small></span>
              <span><i className={`severity ${incident.severity.toLowerCase()}`}>{incident.severity}</i></span>
              <span><i className="status-dot" />{status.label}{status.stale && <small className="status-note">长时间未处理</small>}</span>
              <span className="mono">{durationSince(incident.created_at)}</span>
            </Link>;
          })}
        </div>
      )}
      {incidents.hasNextPage && <button className="load-more" type="button" disabled={incidents.isFetchingNextPage} onClick={() => void incidents.fetchNextPage()}>{incidents.isFetchingNextPage ? "加载中…" : "加载下一页"}</button>}
    </section>
  );
}

function ListSkeleton() {
  return <div className="list-skeleton" aria-label="正在加载事故"><span /><span /><span /></div>;
}

function ErrorPanel({ error }: { error: Error }) {
  const correlation = error instanceof ApiProblemError ? error.correlationId : undefined;
  return <div className="error-panel"><strong>事故列表读取失败</strong><p>{error.message}</p>{correlation && <small>correlation: {correlation}</small>}</div>;
}

function durationSince(value: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).valueOf()) / 1_000));
  if (seconds < 60) return `${seconds} \u79d2`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)} \u5206`;
  return `${Math.floor(seconds / 3_600)} \u65f6 ${Math.floor(seconds % 3_600 / 60)} \u5206`;
}

export function isPublicIncident(incident: Incident): boolean {
  if (["prometheus-alertmanager", "e2e", "evaluation-runner"].includes(incident.source)) {
    return true;
  }
  const fixturePattern = /test|fixture|recorder|resilience|workflow|job recovery|local evidence|evidence persistence|audit concurrency/i;
  return !fixturePattern.test(incident.source) && !fixturePattern.test(incident.title);
}

export function friendlyServiceName(service?: string | null): string {
  return ({ checkout: "结算服务", payment: "支付服务", cart: "购物车服务", recommendation: "推荐服务", frontend: "用户前端" } as Record<string, string>)[service ?? ""] ?? service ?? "未限定服务";
}

export function incidentStatusView(incident: Incident, now = Date.now()): { label: string; stale: boolean } {
  const stale = incident.status === "WAITING_APPROVAL" && now - new Date(incident.updated_at).valueOf() >= 15 * 60 * 1_000;
  return { label: stale ? "待人工复核" : statusLabels[incident.status], stale };
}
