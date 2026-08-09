import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiProblemError, api } from "../api/client";
import type { DiagnosisView, HypothesisView, IncidentStatus, TimelineEvent, WorkbenchReport } from "../api/types";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { HypothesisPanel } from "../components/HypothesisPanel";
import type { TopologyLink, TopologyNode } from "../components/ServiceTopology";
import { Timeline, type TimelineEntry } from "../components/Timeline";
import { useIncidentEvents, type IncidentSseEvent } from "../hooks/useIncidentEvents";

const ServiceTopology = lazy(() => import("../components/ServiceTopology").then((module) => ({ default: module.ServiceTopology })));

export function IncidentWorkbenchPage() {
  const { incidentId } = useParams();
  const queryClient = useQueryClient();
  const [liveEvents, setLiveEvents] = useState<TimelineEntry[]>([]);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string>();
  const incident = useQuery({ queryKey: ["incident", incidentId], queryFn: () => api.getIncident(incidentId ?? ""), enabled: Boolean(incidentId) });
  const timeline = useQuery({ queryKey: ["timeline", incidentId], queryFn: () => api.listTimeline(incidentId ?? ""), enabled: Boolean(incidentId) });
  const evidence = useQuery({ queryKey: ["evidence", incidentId], queryFn: () => api.listEvidence(incidentId ?? ""), enabled: Boolean(incidentId) });
  const evidenceDetail = useQuery({
    queryKey: ["evidence-detail", incidentId, selectedEvidenceId],
    queryFn: () => api.getEvidence(incidentId ?? "", selectedEvidenceId ?? ""),
    enabled: Boolean(incidentId && selectedEvidenceId),
  });
  const connection = useIncidentEvents(incidentId, (event) => {
    setLiveEvents((current) => mergeEntries(current, [sseEntry(event)]));
    for (const key of ["incident", "timeline", "evidence"]) {
      void queryClient.invalidateQueries({ queryKey: [key, incidentId] });
    }
  });
  const events = useMemo(
    () => mergeEntries((timeline.data ?? []).map(restEntry), liveEvents),
    [liveEvents, timeline.data],
  );
  const report = extractReport(events);
  const topology = buildTopology(incident.data?.service, report?.diagnosis, report);
  const proposalId = actionProposalId(events);
  const proposal = useQuery({
    queryKey: ["action-proposal", incidentId, proposalId],
    queryFn: () => api.getActionProposal(incidentId ?? "", proposalId ?? ""),
    enabled: Boolean(incidentId && proposalId),
  });

  if (!incidentId) return <ErrorState message="事故 ID 缺失" />;
  if (incident.isPending) return <div className="workbench-loading">正在装载事故上下文…</div>;
  if (incident.isError) return <ErrorState message={formatError(incident.error)} />;

  return (
    <section className="workbench-page">
      <header className="incident-header">
        <div><Link to="/incidents">← 返回事故队列</Link><p className="eyebrow">INCIDENT / {incident.data.id}</p><h1>{friendlyIncidentTitle(incident.data.title, incident.data.service)}</h1>{friendlyIncidentTitle(incident.data.title, incident.data.service) !== incident.data.title && <small className="incident-original-title">原始告警：{incident.data.title}</small>}</div>
        <div className="incident-facts">
          <span><small>{"\u4e25\u91cd\u5ea6"}</small><strong className={`severity ${incident.data.severity.toLowerCase()}`}>{incident.data.severity}</strong></span>
          <span><small>{"\u72b6\u6001"}</small><strong>{statusLabel(incident.data.status)}</strong></span>
          <span><small>{"\u670d\u52a1"}</small><strong>{friendlyService(incident.data.service)}</strong></span>
          <span><small>{"\u5f00\u59cb\u65f6\u95f4"}</small><strong>{formatDate(incident.data.created_at)}</strong></span>
        </div>
        <span className={`connection ${connection}`}><i />{connectionLabel(connection)}</span>
      </header>

      <DiagnosisExperience incidentStatus={incident.data.status} report={report} />

      {proposal.data && incident.data.status === "WAITING_APPROVAL" && <ApprovalPanel proposal={proposal.data} onDecision={async (decision, reason) => {
        await api.decideApproval(incidentId, proposal.data.id, { decision, reason: reason || "Operator decision recorded." });
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["incident", incidentId] }),
          queryClient.invalidateQueries({ queryKey: ["timeline", incidentId] }),
          queryClient.invalidateQueries({ queryKey: ["action-proposal", incidentId, proposal.data.id] }),
        ]);
      }} />}

      <details className="technical-workbench">
        <summary><span><strong>查看专业调查详情</strong><small>时间线、服务拓扑、候选假设、原始 Evidence 与结构化报告</small></span><i>+</i></summary>
        <div className="technical-workbench-body">
          <div className="workbench-grid">
            <section className="workbench-panel timeline-panel"><PanelTitle index="01" title="调查轨迹" meta={`${events.length} \u4e2a\u4e8b\u4ef6`} />{timeline.isError ? <ErrorState message={formatError(timeline.error)} /> : <Timeline events={events} />}</section>
            <section className="workbench-panel topology-panel"><PanelTitle index="02" title="服务拓扑" meta={`${topology.nodes.length} \u4e2a\u8282\u70b9`} /><Suspense fallback={<p className="empty-note">正在装载拓扑渲染器…</p>}><ServiceTopology nodes={topology.nodes} links={topology.links} /></Suspense></section>
            <section className="workbench-panel hypothesis-panel"><PanelTitle index="03" title="诊断与假设" meta={report?.diagnosis ? "\u5df2\u786e\u8ba4" : "\u8c03\u67e5\u4e2d"} /><HypothesisPanel diagnosis={report?.diagnosis} hypotheses={report?.hypotheses ?? []} /></section>
          </div>

          <section className="evidence-section">
            <PanelTitle index="04" title="Evidence 证据库" meta={`${evidence.data?.length ?? 0} \u6761`} />
            {evidence.isError && <ErrorState message={formatError(evidence.error)} />}
            <div className="evidence-grid">{evidence.data?.map((item) => (
              <button type="button" key={item.id} onClick={() => setSelectedEvidenceId(item.id)}>
                <span>{item.kind}</span><strong>{item.summary}</strong><small>{item.source_system} · {formatDate(item.collected_at)}</small>
              </button>
            ))}</div>
          </section>

          <section className="report-section">
            <PanelTitle index="05" title="原始结构化报告" meta={report ? "\u5df2\u751f\u6210" : "\u7b49\u5f85\u751f\u6210"} />
            {report?.diagnosis ? <div className="report-body"><p>{report.diagnosis.root_cause_summary}</p><dl><div><dt>客户影响</dt><dd>{report.diagnosis.customer_impact}</dd></div><div><dt>证据引用</dt><dd>{report.diagnosis.evidence_ids.join(", ")}</dd></div></dl></div> : <p className="empty-note">报告将在调查完成后由确定性 renderer 生成。</p>}
          </section>
        </div>
      </details>

      {selectedEvidenceId && <EvidenceDrawer evidence={evidenceDetail.data} loading={evidenceDetail.isPending} onClose={() => setSelectedEvidenceId(undefined)} />}
    </section>
  );
}

const investigatorRoles = [
  { key: "metrics", icon: "M", name: "Metrics 调查员", task: "错误率、延迟与容量" },
  { key: "logs", icon: "L", name: "Logs 调查员", task: "异常模式与错误上下文" },
  { key: "traces", icon: "T", name: "Traces 调查员", task: "调用链与故障传播" },
  { key: "runbook", icon: "R", name: "Runbook 分析员", task: "处置知识与验证标准" },
] as const;

export function DiagnosisExperience({ incidentStatus, report }: { incidentStatus: IncidentStatus; report?: WorkbenchReport }) {
  const diagnosis = report?.diagnosis;
  const completed = Boolean(diagnosis);
  const reports = report?.reports ?? [];

  return (
    <section className="diagnosis-experience">
      <header className="experience-status">
        <div><span className={completed ? "done" : "working"}><i />{completed ? "AI 团队已完成诊断" : "AI 团队正在调查"}</span><h2>{completed ? "我们找到了最可能的根因" : "正在把告警变成有证据的结论"}</h2></div>
        <div className="experience-status-facts"><span><small>事故状态</small><strong>{statusLabel(incidentStatus)}</strong></span><span><small>调查方式</small><strong>多 Agent 并行</strong></span><span><small>数据来源</small><strong>真实遥测</strong></span></div>
      </header>

      {diagnosis ? (
        <div className="plain-diagnosis">
          <article className="root-cause-card"><span>根因定位</span><h3>{friendlyService(diagnosis.root_cause_service)}</h3><small>{diagnosis.root_cause_service}</small><p>系统将根因定位在{friendlyService(diagnosis.root_cause_service)}，故障类型为{categoryLabel(diagnosis.root_cause_category)}。</p></article>
          <article><span>判断可信度</span><strong>{Math.round(diagnosis.confidence * 100)}%</strong><p>{diagnosis.evidence_ids.length} 条证据交叉支持</p></article>
          <article><span>影响范围</span><h3>{friendlyService(diagnosis.symptom_service)}</h3><p>该服务的用户请求可能失败或响应变慢，需要优先处理。</p></article>
          <article><span>处置边界</span><h3>{actionBoundary(incidentStatus)}</h3><p>模型只生成建议；策略、批准和执行权由确定性代码与操作员掌握。</p></article>
        </div>
      ) : <div className="diagnosis-pending"><i /><div><strong>调查任务已进入受控状态图</strong><p>页面会通过 SSE 接收进度。刷新后仍会从持久化 checkpoint 继续，不会丢失事故上下文。</p></div></div>}

      <div className="live-team-heading"><div><span>AI 事故响应团队</span><h3>{completed ? "本次调查的协作记录" : "当前协作状态"}</h3></div><small>只展示结构化结论与工具事实，不展示私有思维链</small></div>
      <div className="live-team-grid">
        {investigatorRoles.map((role) => {
          const actual = reports.find((item) => item.report.investigator.toLowerCase().includes(role.key));
          return <article className={`live-agent ${actual ? "complete" : "idle"}`} key={role.key}><div className="live-agent-icon">{role.icon}</div><div><strong>{role.name}</strong><span>{role.task}</span>{actual && <small>调查范围：{actual.report.scope_services.map(friendlyService).join("、")}</small>}</div><b>{actual ? "已产出结果" : completed ? "本次未触发" : "等待调度"}</b></article>;
        })}
        <article className={`live-agent commander ${diagnosis ? "complete" : "working"}`}><div className="live-agent-icon">IC</div><div><strong>事故指挥官</strong><span>合并证据、反证与候选假设</span></div><b>{diagnosis ? "已形成诊断" : "等待调查结果"}</b></article>
        <article className="live-agent deterministic"><div className="live-agent-icon">◆</div><div><strong>确定性安全门</strong><span>策略、权限、参数与人工批准</span></div><b>{actionBoundary(incidentStatus)}</b></article>
      </div>
    </section>
  );
}

function PanelTitle({ index, title, meta }: { index: string; title: string; meta: string }) {
  return <header className="panel-title"><span>{index}</span><h2>{title}</h2><small>{meta}</small></header>;
}

function ErrorState({ message }: { message: string }) {
  return <div className="error-panel"><strong>数据通道不可用</strong><p>{message}</p></div>;
}

function formatError(error: Error): string {
  return error instanceof ApiProblemError ? `${error.message} · ${error.correlationId}` : error.message;
}

function connectionLabel(value: string): string {
  const labels: Record<string, string> = {
    live: "\u5b9e\u65f6\u8fde\u63a5",
    connecting: "\u8fde\u63a5\u4e2d",
    reconnecting: "\u91cd\u8fde\u4e2d",
    closed: "\u5df2\u65ad\u5f00",
  };
  return labels[value] ?? value.toUpperCase();
}

function restEntry(event: TimelineEvent): TimelineEntry {
  return { id: event.id, eventType: event.event_type, actorId: event.actor_id, actorType: event.actor_type, createdAt: event.created_at, payload: event.payload };
}

function sseEntry(event: IncidentSseEvent): TimelineEntry {
  const actor = record(event.data.actor);
  const auditId = stringValue(event.data.audit_event_id) ?? event.id;
  return {
    id: auditId,
    eventType: event.eventType,
    actorId: stringValue(actor?.id) ?? "system",
    actorType: stringValue(actor?.type) ?? "system",
    createdAt: stringValue(event.data.created_at) ?? new Date().toISOString(),
    payload: record(event.data.payload) ?? {},
  };
}

function mergeEntries(left: TimelineEntry[], right: TimelineEntry[]): TimelineEntry[] {
  return [...new Map([...left, ...right].map((event) => [event.id, event])).values()];
}

function extractReport(events: TimelineEntry[]): WorkbenchReport | undefined {
  for (const event of [...events].reverse()) {
    const value = record(event.payload.report);
    if (!value || typeof value.incident_id !== "string") continue;
    const diagnosis = diagnosisView(value.diagnosis);
    const hypotheses = Array.isArray(value.hypotheses) ? value.hypotheses.flatMap((item) => {
      const hypothesis = hypothesisView(item);
      return hypothesis ? [hypothesis] : [];
    }) : [];
    return { incident_id: value.incident_id, status: stringValue(value.status) as WorkbenchReport["status"], diagnosis, hypotheses, reports: reportScopes(value.reports) };
  }
  return undefined;
}

function actionProposalId(events: TimelineEntry[]): string | undefined {
  for (const event of [...events].reverse()) {
    if (event.eventType !== "ACTION_PROPOSAL_CREATED") continue;
    const proposalId = stringValue(record(event.payload)?.proposal_id);
    if (proposalId) return proposalId;
  }
  return undefined;
}

function diagnosisView(value: unknown): DiagnosisView | undefined {
  const item = record(value);
  if (!item || !requiredStrings(item, ["symptom_service", "root_cause_service", "root_cause_category", "root_cause_summary", "customer_impact"]) || typeof item.confidence !== "number") return undefined;
  return {
    symptom_service: item.symptom_service as string,
    root_cause_service: item.root_cause_service as string,
    dependency_service: stringValue(item.dependency_service),
    root_cause_category: item.root_cause_category as string,
    root_cause_summary: item.root_cause_summary as string,
    confidence: item.confidence,
    evidence_ids: stringArray(item.evidence_ids),
    customer_impact: item.customer_impact as string,
    diagnosis_limits: stringArray(item.diagnosis_limits),
  };
}

function hypothesisView(value: unknown): HypothesisView | undefined {
  const item = record(value);
  if (!item || !requiredStrings(item, ["id", "root_cause_service", "failure_mode"]) || typeof item.confidence !== "number") return undefined;
  return { id: item.id as string, root_cause_service: item.root_cause_service as string, failure_mode: item.failure_mode as string, confidence: item.confidence, supporting_evidence_ids: stringArray(item.supporting_evidence_ids), contradicting_evidence_ids: stringArray(item.contradicting_evidence_ids), missing_evidence: stringArray(item.missing_evidence) };
}

function reportScopes(value: unknown): WorkbenchReport["reports"] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const item = record(entry); const report = record(item?.report);
    if (!item || !report || typeof item.wave !== "number") return [];
    return [{ wave: item.wave, report: { investigator: stringValue(report.investigator) ?? "unknown", scope_services: stringArray(report.scope_services) } }];
  });
}

function buildTopology(service: string | null | undefined, diagnosis: DiagnosisView | undefined, report: WorkbenchReport | undefined): { nodes: TopologyNode[]; links: TopologyLink[] } {
  const nodes = new Map<string, TopologyNode>();
  const add = (name: string | null | undefined, role: TopologyNode["role"]) => { if (name) nodes.set(name, { name, role }); };
  add(service, "symptom");
  for (const wave of report?.reports ?? []) for (const name of wave.report.scope_services) if (!nodes.has(name)) add(name, "observed");
  add(diagnosis?.dependency_service, "dependency");
  add(diagnosis?.root_cause_service, "root");
  const links: TopologyLink[] = [];
  if (diagnosis?.symptom_service && diagnosis.root_cause_service !== diagnosis.symptom_service) links.push({ source: diagnosis.symptom_service, target: diagnosis.root_cause_service });
  if (diagnosis?.dependency_service && diagnosis.dependency_service !== diagnosis.root_cause_service) links.push({ source: diagnosis.root_cause_service, target: diagnosis.dependency_service });
  return { nodes: [...nodes.values()], links };
}

function record(value: unknown): Record<string, unknown> | undefined { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
function stringValue(value: unknown): string | undefined { return typeof value === "string" ? value : undefined; }
function stringArray(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function requiredStrings(value: Record<string, unknown>, keys: string[]): boolean { return keys.every((key) => typeof value[key] === "string"); }
function friendlyService(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    checkout: "结算服务",
    payment: "支付服务",
    "product-catalog": "商品目录服务",
    recommendation: "推荐服务",
    cart: "购物车服务",
    frontend: "用户前端",
    shipping: "配送服务",
    currency: "汇率服务",
  };
  return value ? labels[value] ?? value : "未指定服务";
}
function categoryLabel(value: string): string {
  const labels: Record<string, string> = {
    application_failure: "应用错误",
    dependency_failure: "依赖不可用",
    cache_failure: "缓存异常",
    latency: "响应延迟",
    resource_exhaustion: "资源耗尽",
  };
  return labels[value] ?? value;
}
function friendlyIncidentTitle(title: string, service: string | null | undefined): string {
  const known: Record<string, string> = {
    "Checkout failures": "结算请求持续失败",
    "Payment failures": "支付请求持续失败",
    "Product catalog failures": "商品目录服务异常",
  };
  if (known[title]) return known[title];
  return /[\u4e00-\u9fff]/.test(title) ? title : `${friendlyService(service)}出现异常`;
}
function statusLabel(value: IncidentStatus): string {
  const labels: Partial<Record<IncidentStatus, string>> = {
    RECEIVED: "已接收",
    TRIAGING: "正在分诊",
    INVESTIGATING: "正在调查",
    SYNTHESIZING: "正在综合证据",
    DIAGNOSED: "诊断完成",
    PLANNING: "正在规划处置",
    WAITING_APPROVAL: "等待人工批准",
    AUTHORIZING: "正在授权",
    EXECUTING: "正在执行",
    VERIFYING: "正在验证恢复",
    RESOLVED: "已恢复",
    RESOLVED_READ_ONLY: "只读诊断完成",
    NEEDS_HUMAN: "需要人工介入",
    REPORTING: "报告已生成",
  };
  return labels[value] ?? value;
}
function actionBoundary(value: IncidentStatus): string {
  if (value === "WAITING_APPROVAL") return "等待人工批准";
  if (["AUTHORIZING", "EXECUTING", "VERIFYING", "RESOLVED"].includes(value)) return value === "RESOLVED" ? "已验证恢复" : "受控处置中";
  return "本次未进入写操作";
}
function formatDate(value: string): string { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
