import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, createApiClient } from "../api/client";
import type { ActionProposalView, DiagnosisView, Evidence, Incident, TimelineEvent, WorkbenchReport } from "../api/types";
import { useIncidentEvents, type IncidentSseEvent } from "../hooks/useIncidentEvents";

const operatorApi = createApiClient({ actorId: "local-operator" });

export interface LiveEvent {
  id: string; eventType: string; actorId: string; actorType: string;
  createdAt: string; payload: Record<string, unknown>;
}

const agents = [
  { id: "metrics_investigator", sig: "M", name: "指标调查 Agent", source: "Prometheus", role: "错误率、延迟与容量" },
  { id: "logs_investigator", sig: "L", name: "日志调查 Agent", source: "OpenSearch", role: "异常模式与错误上下文" },
  { id: "traces_investigator", sig: "T", name: "调用链调查 Agent", source: "Jaeger", role: "依赖路径与故障传播" },
  { id: "runbook_analyst", sig: "R", name: "手册分析 Agent", source: "Runbook", role: "恢复步骤与验证方法" },
] as const;

export function LiveIncidentPage() {
  const { incidentId = "" } = useParams();
  const queryClient = useQueryClient();
  const [live, setLive] = useState<LiveEvent[]>([]);
  const [drawer, setDrawer] = useState(false);
  const incident = useQuery({ queryKey: ["incident", incidentId], queryFn: () => api.getIncident(incidentId), enabled: Boolean(incidentId), refetchInterval: 3000 });
  const timeline = useQuery({ queryKey: ["timeline", incidentId], queryFn: () => api.listTimeline(incidentId), enabled: Boolean(incidentId) });
  const evidence = useQuery({ queryKey: ["evidence", incidentId], queryFn: () => api.listEvidence(incidentId), enabled: Boolean(incidentId) });
  const connection = useIncidentEvents(incidentId, (event) => {
    setLive((current) => mergeEvents(current, [fromSse(event)]));
    void queryClient.invalidateQueries({ queryKey: ["incident", incidentId] });
    void queryClient.invalidateQueries({ queryKey: ["evidence", incidentId] });
  });
  const events = useMemo(() => mergeEvents((timeline.data ?? []).map(fromRest), live), [timeline.data, live]);
  const report = reportFrom(events);
  const proposal = useQuery({ queryKey: ["proposal", incidentId], queryFn: () => api.getCurrentActionProposal(incidentId), enabled: Boolean(incidentId), refetchInterval: 2500 });
  const proposalId = proposal.data?.id;
  const blockers = useQuery({ queryKey: ["waiting-approval-blocker", incidentId], queryFn: () => api.listIncidents({ status: "WAITING_APPROVAL", limit: 10 }), enabled: incident.data?.status === "RECEIVED" && events.length === 0, refetchInterval: 3000 });
  const blockedByIncident = blockers.data?.items.find((item) => item.id !== incidentId);
  const latestMessage = [...events].reverse().find((event) => typeof event.payload.message === "string");
  const completed = ["RESOLVED", "RESOLVED_READ_ONLY", "NEEDS_HUMAN", "REJECTED", "POLICY_REJECTED", "ACTION_FAILED"].includes(incident.data?.status ?? "");
  const elapsed = useElapsed(incident.data?.created_at, completed ? incident.data?.updated_at : undefined);

  if (incident.isPending) return <div className="page-loading">正在接入真实诊断运行…</div>;
  if (!incident.data) return <div className="page-error">找不到这次诊断运行。</div>;

  return <main className="live-page compact-command-page">
    <header className="live-header">
      <div className="incident-identity"><Link to="/">← 返回</Link><span className="run-kicker">RUN / {incident.data.id.slice(-8)}</span><h1>{friendlyTitle(incident.data.title, incident.data.service)}</h1></div>
      <p className="header-activity"><i className={completed ? "done" : ""} />{latestMessage ? String(latestMessage.payload.message) : "正在等待后端事件"}</p>
      <div className="run-health"><span className={`live-signal ${connection}`}><i />{connection === "live" ? "实时" : "连接中"}</span><span><small>用时</small><strong>{elapsed}</strong></span><span><small>阶段</small><strong>{blockedByIncident ? "等待上一事故审批" : statusText(incident.data.status)}</strong></span><button className="detail-trigger" type="button" onClick={() => setDrawer(true)}>证据与专业详情</button></div>
    </header>
    <section className="command-surface">
      <div className="workflow-column"><section className="run-banner"><span className={completed ? "complete" : "working"}>{completed ? "本次运行已结束" : "AI 团队正在真实运行"}</span><p>蓝色为 Agent，绿色为真实遥测，橙色为确定性代码控制。</p><div className="truth-proof"><i />真实模型 <i />真实工具 <i />可审计 Evidence</div></section><LiveTeamBoard events={events} completed={completed} /></div>
      <ResultFlow
        diagnosis={report?.diagnosis}
        evidence={evidence.data ?? []}
        completed={completed}
        incidentStatus={incident.data.status}
        proposal={proposal.data ?? undefined}
        blockedByIncident={blockedByIncident}
        events={events}
        onOpenDetails={() => setDrawer(true)}
        onDecision={async (decision) => {
          if (!proposalId) return;
          const label = decision === "approve" ? "批准并执行这项受限动作" : "拒绝这项处置建议";
          if (!window.confirm(`确认${label}？`)) return;
          await operatorApi.decideApproval(incidentId, proposalId, { decision, reason: decision === "approve" ? "用户在事故指挥台批准" : "用户在事故指挥台拒绝" });
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["incident", incidentId] }),
            queryClient.invalidateQueries({ queryKey: ["timeline", incidentId] }),
            queryClient.invalidateQueries({ queryKey: ["proposal", incidentId] }),
          ]);
        }}
      />
    </section>
    {drawer && <TechnicalDrawer events={events} evidence={evidence.data ?? []} diagnosis={report?.diagnosis} proposal={proposal.data ?? undefined} incidentStatus={incident.data.status} onClose={() => setDrawer(false)} />}
  </main>;
}

export function LiveTeamBoard({ events, completed }: { events: LiveEvent[]; completed: boolean }) {
  const triage = agentState(events, "triage", "分诊 Agent", "读取告警并划定服务范围");
  const investigatorStates = agents.map((agent) => ({ ...agent, state: terminalState(agentState(events, agent.id, agent.name, agent.role), completed) }));
  const commander = agentState(events, "incident_commander", "事故指挥 Agent", "等待各调查角色返回结构化结果");
  const safety = stageState(events, "safety", "确定性安全门", "检查权限、策略与执行边界");
  const evolution = stageState(events, "evolution", "受控进化", "等待本次轨迹进入离线样本池");
  const focus = currentFocus(triage, investigatorStates.map((item) => item.state), commander, safety, evolution, completed);
  const completedInvestigators = investigatorStates.filter((item) => item.state.status === "completed").length;
  const activeInvestigators = investigatorStates.some((item) => item.state.status === "running");
  return <section className="team-board">
    <header><div><span>实时协作看板</span><h2>{completed ? "本次 AI 团队的调查轨迹" : "AI 团队正在工作"}</h2></div><p><b className="legend ai" />AI Agent <b className="legend data" />真实数据 <b className="legend gate" />确定性代码</p></header>
    <div className={`operation-focus ${completed ? "complete" : "active"}`}><div><span>当前焦点</span><strong>{focus.title}</strong></div><p>{focus.message}</p><div className="focus-progress"><span>{focus.progress}</span><i><b style={{ width: `${focus.percent}%` }} /></i></div></div>
    <div className="flow-stack">
      <AgentNode state={triage} kind="ai" sig="A1" focused={!completed && triage.status === "running"} />
      <FlowWire transmitting={activeInvestigators} complete={triage.status === "completed"} />
      <div className={`parallel-zone ${activeInvestigators ? "active" : completedInvestigators === 4 ? "complete" : ""}`}><div className="parallel-label"><span>并行调查</span><small>{completedInvestigators} / 4 已返回 · 结构化发现实时送往指挥 Agent</small></div><div className="agent-grid">{investigatorStates.map((agent) => <AgentNode key={agent.id} kind="data" sig={agent.sig} state={agent.state} source={agent.source} focused={!completed && agent.state.status === "running"} />)}</div></div>
      <FlowWire transmitting={commander.status === "running"} complete={commander.status === "completed"} />
      <AgentNode state={commander} kind="ai" sig="IC" wide focused={!completed && commander.status === "running"} />
      <FlowWire transmitting={["running", "waiting"].includes(safety.status)} complete={safety.status === "completed"} />
      <div className="final-gates"><AgentNode state={safety} kind="gate" sig="◆" focused={!completed && ["running", "waiting"].includes(safety.status)} /><AgentNode state={evolution} kind="evolution" sig="↗" focused={!completed && evolution.status === "running"} /></div>
    </div>
  </section>;
}

type NodeStatus = "pending" | "running" | "completed" | "waiting" | "failed" | "skipped";
interface NodeState { name: string; defaultMessage: string; message: string; status: NodeStatus; at?: string }

function AgentNode({ state, kind, sig, source, wide = false, focused = false }: { state: NodeState; kind: string; sig: string; source?: string; wide?: boolean; focused?: boolean }) {
  return <article className={`agent-node ${kind} ${state.status} ${wide ? "wide" : ""} ${focused ? "focused" : ""}`}><div className="agent-avatar"><span>{sig}</span>{state.status === "running" && <i />}</div><div className="agent-copy"><div><strong>{state.name}</strong>{source && <small>{source}</small>}</div><p>{state.message || state.defaultMessage}</p></div><span className="node-status">{nodeStatusText(state.status)}</span>{focused && <div className="activity-wave" aria-hidden="true"><i /><i /><i /></div>}</article>;
}

function FlowWire({ transmitting, complete }: { transmitting: boolean; complete: boolean }) { return <div className={`flow-wire ${transmitting ? "transmitting" : complete ? "complete" : ""}`}><i /><i /><i /></div>; }

function currentFocus(triage: NodeState, investigators: NodeState[], commander: NodeState, safety: NodeState, evolution: NodeState, completed: boolean) {
  const returned = investigators.filter((item) => item.status === "completed").length;
  if (completed) return { title: "运行闭环已完成", message: "结论、Evidence、处置与恢复记录已经固化，可在右侧和专业详情中复核。", progress: "8 / 8", percent: 100 };
  if (triage.status === "running" || (triage.status === "pending" && returned === 0)) return { title: "事故理解", message: triage.message, progress: "1 / 8", percent: 12.5 };
  if (investigators.some((item) => item.status === "running") || returned < 4) return { title: "四路并行调查", message: `${returned} / 4 已返回；Metrics、Logs、Traces 与 Runbook 正在独立取证。`, progress: `${1 + returned} / 8`, percent: 12.5 + returned * 12.5 };
  if (commander.status !== "completed") return { title: "交叉验证与综合判断", message: commander.message, progress: "6 / 8", percent: 75 };
  if (["running", "waiting"].includes(safety.status)) return { title: "确定性安全控制", message: safety.message, progress: "7 / 8", percent: 87.5 };
  if (evolution.status === "running") return { title: "恢复复盘与受控进化", message: evolution.message, progress: "8 / 8", percent: 96 };
  return { title: "形成最终结论", message: "后端正在固化审计记录与恢复状态。", progress: "7 / 8", percent: 87.5 };
}

export function ResultFlow({ diagnosis, evidence, completed, incidentStatus, proposal, blockedByIncident, events, onOpenDetails, onDecision }: {
  diagnosis?: DiagnosisView;
  evidence: Evidence[];
  completed: boolean;
  incidentStatus: string;
  proposal?: ActionProposalView;
  blockedByIncident?: Incident;
  events: LiveEvent[];
  onOpenDetails: () => void;
  onDecision: (decision: "approve" | "reject") => Promise<void>;
}) {
  const [busy, setBusy] = useState<"approve" | "reject">();
  const verification = [...events].reverse().find((event) => event.eventType === "verification.completed" && objectValue(event.payload.details)?.remediation_action !== false);
  async function decide(value: "approve" | "reject") { setBusy(value); try { await onDecision(value); } finally { setBusy(undefined); } }
  return <aside className="outcome-panel" aria-label="诊断结论与处置">
    <header><div><span>诊断结论与处置</span><h2>{diagnosis ? "结论已经形成" : completed ? "转人工复核" : "正在汇聚证据"}</h2></div><button type="button" onClick={onOpenDetails}>专业详情 ↗</button></header>
    {!diagnosis ? blockedByIncident ? <div className="outcome-pending queue-blocker"><span>演示环境正在串行保护真实故障</span><strong>等待上一条事故完成审批</strong><p>“{friendlyTitle(blockedByIncident.title, blockedByIncident.service)}”仍在等待人工决定。处理完成后，本次运行会自动继续。</p><Link to={`/incidents/${blockedByIncident.id}`}>前往审批上一条事故 →</Link></div> : <div className="outcome-pending"><div className="search-orbit"><i /><i /><i /></div><strong>Agent 结果会在这里原地出现</strong><p>左侧继续展示协作过程；一旦根因满足 Evidence 门槛，右侧无需下滑就会切换成正式结论。</p></div> : <>
      <section className="root-summary"><div><small>最可能根因</small><b>{Math.round(diagnosis.confidence * 100)}% 置信度</b></div><h3>{friendlyService(diagnosis.root_cause_service)}</h3><p>{diagnosis.root_cause_summary}</p><span>影响：{diagnosis.customer_impact || `${friendlyService(diagnosis.symptom_service)}请求可能失败或变慢`}</span></section>
      <section className="evidence-summary"><div><strong>Evidence</strong><button type="button" onClick={onOpenDetails}>查看全部 {evidence.length} 条</button></div>{evidence.slice(0, 2).map((item) => <article key={item.id}><i className={item.kind} /><p>{friendlyEvidenceSummary(item.summary)}</p><small>{item.source_system}</small></article>)}</section>
      <section className="action-summary"><div><strong>建议动作</strong><span className={proposal ? "ready" : "readonly"}>{proposal ? riskText(proposal.proposal.risk) : "无可执行动作"}</span></div>{proposal ? <><p>{proposal.proposal.expected_effect}</p><dl><div><dt>动作</dt><dd>{proposal.proposal.action.action_type === "rollback_change" ? "回滚已验证的配置变更" : "重启服务"}</dd></div><div><dt>目标</dt><dd>{friendlyService(proposal.proposal.action.target_service)}</dd></div><div><dt>控制</dt><dd>签名审批 · 单次 nonce · 幂等执行</dd></div></dl></> : <p>没有通过服务端策略门的 allowlist 动作时，系统保持只读，不让模型自行修改环境。</p>}</section>
      {incidentStatus === "RESOLVED_READ_ONLY" && !proposal && <section className="readonly-terminal"><div><span>本次只读诊断已完成</span><strong>这里不是卡住，也不需要继续审批</strong><p>系统已经给出根因与 Evidence，但没有生成符合 allowlist 和策略门的写操作，因此安全结束。</p></div><nav><Link to="/demo">发起新的真实诊断</Link><Link to="/incidents">查看事故记录</Link></nav></section>}
      {incidentStatus === "WAITING_APPROVAL" && proposal && <section className="approval-decision"><span>等待你的决定</span><strong>确定性安全门要求人工批准</strong><p>这是中风险写操作。批准后才会向独立 Action MCP 签发一次性授权。</p><div><button disabled={Boolean(busy)} type="button" onClick={() => void decide("reject")}>{busy === "reject" ? "提交中…" : "拒绝"}</button><button disabled={Boolean(busy)} type="button" onClick={() => void decide("approve")}>{busy === "approve" ? "授权中…" : "批准并执行"}</button></div></section>}
      {!["WAITING_APPROVAL", "RESOLVED_READ_ONLY", "DIAGNOSED", "PLANNING"].includes(incidentStatus) && proposal && <section className={`execution-state ${incidentStatus.toLowerCase()}`}><strong>{statusText(incidentStatus)}</strong><p>{incidentStatus === "RESOLVED" ? "真实恢复指标与 Prometheus SLO 已通过，处置闭环完成。" : "审批、执行和恢复验证状态来自后端审计事件。"}</p></section>}
      {verification && incidentStatus !== "RESOLVED" && <section className="verification-summary"><span>恢复验证</span><strong>{objectValue(verification.payload.details)?.recovered === true ? "Prometheus SLO 已通过" : "环境恢复记录已固化"}</strong></section>}
      <section className="closure-summary">
        {completed && <div><span>事故复盘已固化</span><strong>完整轨迹可审计</strong></div>}
        <div><span>受控进化</span><strong>已进入离线样本池</strong></div>
      </section>
    </>}
  </aside>;
}

function TechnicalDrawer({ events, evidence, diagnosis, proposal, incidentStatus, onClose }: { events: LiveEvent[]; evidence: Evidence[]; diagnosis?: DiagnosisView; proposal?: ActionProposalView; incidentStatus: string; onClose: () => void }) {
  const [tab, setTab] = useState<"overview" | "evidence" | "safety" | "audit">("overview");
  const toolEvents = events.filter((event) => event.eventType.includes("tool") || event.payload.stage === "investigation");
  const executionId = findDetail(events, "execution_id");
  const approvalId = findDetail(events, "approval_id");
  const verification = [...events].reverse().find((event) => event.eventType === "verification.completed");
  return <div className="drawer-backdrop" onMouseDown={onClose}><aside className="technical-drawer investigation-drawer" onMouseDown={(event) => event.stopPropagation()}>
    <header><div><span>专业调查详情</span><h2>运行证据与工程控制</h2></div><button type="button" onClick={onClose}>关闭</button></header>
    <nav className="drawer-tabs">{[["overview", "运行概览"], ["evidence", `Evidence ${evidence.length}`], ["safety", "安全处置"], ["audit", `审计事件 ${events.length}`]].map(([value, label]) => <button className={tab === value ? "active" : ""} type="button" onClick={() => setTab(value as typeof tab)} key={value}>{label}</button>)}</nav>
    {tab === "overview" && <section className="drawer-overview">
      <div className="professional-summary"><article><small>当前状态</small><strong>{statusText(incidentStatus)}</strong><span>后端状态机</span></article><article><small>根因置信度</small><strong>{diagnosis ? `${Math.round(diagnosis.confidence * 100)}%` : "—"}</strong><span>Evidence 约束</span></article><article><small>调查/工具事件</small><strong>{toolEvents.length}</strong><span>可审计调用</span></article><article><small>Evidence</small><strong>{evidence.length}</strong><span>Metrics / Logs / Traces</span></article></div>
      <div className="architecture-boundaries"><h3>不是自由群聊，而是可恢复的有界状态图</h3><div>{["类型化共享状态", "专职无状态子 Agent", "Checkpoint 断点恢复", "只读调查工具最小授权", "Read / Action MCP 凭据隔离", "Policy Gate 不由模型决定"].map((item) => <span key={item}>✓ {item}</span>)}</div></div>
      <div className="agent-ledger"><h3>调查角色与数据边界</h3>{agents.map((agent) => { const state = agentState(events, agent.id, agent.name, agent.role); return <article key={agent.id}><span>{agent.sig}</span><div><strong>{agent.name}</strong><small>{agent.source} · 只读</small></div><p>{state.message}</p><b>{nodeStatusText(state.status)}</b></article>; })}</div>
    </section>}
    {tab === "evidence" && <section><div className="section-intro"><h3>Evidence 索引</h3><p>每条结论必须引用真实工具产出的 Evidence；这里不展示私有思维链。</p></div><div className="drawer-evidence">{evidence.map((item) => <article key={item.id}><span>{item.kind}</span><strong>{friendlyEvidenceSummary(item.summary)}</strong><small>{item.source_system} · {item.id}</small><code>{compactJson(item.query)}</code></article>)}</div></section>}
    {tab === "safety" && <section className="safety-ledger"><div className="section-intro"><h3>写操作控制链</h3><p>建议由模型生成，权限、审批、幂等、执行与验证全部由确定性代码控制。</p></div><div className="control-chain">{["处置建议", "Policy Gate", "风险分级", "人工/自动审批", "一次性授权", "Action MCP", "恢复验证"].map((item, index) => <span className={index < (incidentStatus === "RESOLVED" ? 7 : proposal ? 4 : 2) ? "complete" : ""} key={item}>{item}{index < 6 && <i>→</i>}</span>)}</div><div className="safety-facts"><p><span>Proposal ID</span><code>{proposal?.id ?? "尚未生成"}</code></p><p><span>风险等级</span><strong>{proposal ? riskText(proposal.proposal.risk) : "只读调查"}</strong></p><p><span>Idempotency Key</span><code>{proposal?.proposal.idempotency_key ?? "—"}</code></p><p><span>Approval ID</span><code>{approvalId ?? "—"}</code></p><p><span>Execution ID</span><code>{executionId ?? "—"}</code></p><p><span>恢复验证</span><strong>{verification ? String(verification.payload.message ?? "已记录") : "等待执行"}</strong></p></div></section>}
    {tab === "audit" && <section><div className="section-intro"><h3>不可变事件时间线</h3><p>仅记录结构化结论、工具调用和状态变化。</p></div><ol>{events.map((event) => <li key={event.id}><time>{new Date(event.createdAt).toLocaleTimeString("zh-CN")}</time><div><strong>{String(event.payload.message ?? event.eventType)}</strong><small>{event.eventType} · {event.actorId}</small>{objectValue(event.payload.details) && <code>{compactJson(event.payload.details)}</code>}</div></li>)}</ol></section>}
  </aside></div>;
}

function agentState(events: LiveEvent[], id: string, name: string, defaultMessage: string): NodeState {
  const relevant = events.filter((event) => event.payload.agent === id);
  const last = relevant.at(-1);
  if (!last) return { name, defaultMessage, message: defaultMessage, status: "pending" };
  const raw = String(last.payload.status ?? (last.eventType === "agent.completed" ? "completed" : "running"));
  return { name, defaultMessage, message: String(last.payload.message ?? defaultMessage), status: normalizeStatus(raw), at: last.createdAt };
}

function stageState(events: LiveEvent[], stage: string, name: string, defaultMessage: string): NodeState {
  const last = events.filter((event) => event.payload.stage === stage).at(-1);
  if (!last) return { name, defaultMessage, message: defaultMessage, status: "pending" };
  return { name, defaultMessage, message: String(last.payload.message ?? defaultMessage), status: normalizeStatus(String(last.payload.status ?? "running")), at: last.createdAt };
}
function normalizeStatus(value: string): NodeStatus { return (["running", "completed", "waiting", "failed"].includes(value) ? value : "pending") as NodeStatus; }
function nodeStatusText(value: NodeStatus): string { return { pending: "等待调度", running: "运行中", completed: "已完成", waiting: "等待决策", failed: "运行失败", skipped: "本次未调用" }[value]; }
function terminalState(state: NodeState, completed: boolean): NodeState { return completed && state.status === "pending" ? { ...state, message: "本次诊断未调用该角色", status: "skipped" } : state; }
function fromRest(event: TimelineEvent): LiveEvent { return { id: event.id, eventType: event.event_type, actorId: event.actor_id, actorType: event.actor_type, createdAt: event.created_at, payload: event.payload }; }
function fromSse(event: IncidentSseEvent): LiveEvent { const actor = objectValue(event.data.actor); return { id: String(event.data.audit_event_id ?? event.id), eventType: event.eventType, actorId: String(actor?.id ?? "system"), actorType: String(actor?.type ?? "system"), createdAt: String(event.data.created_at ?? new Date().toISOString()), payload: objectValue(event.data.payload) ?? {} }; }
function mergeEvents(left: LiveEvent[], right: LiveEvent[]) { return [...new Map([...left, ...right].map((item) => [item.id, item])).values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt)); }
function reportFrom(events: LiveEvent[]): WorkbenchReport | undefined { for (const event of [...events].reverse()) { const report = objectValue(event.payload.report); if (!report) continue; return { incident_id: String(report.incident_id ?? ""), status: report.status as WorkbenchReport["status"], diagnosis: diagnosisFrom(report.diagnosis), hypotheses: [], reports: [] }; } return undefined; }
function diagnosisFrom(value: unknown): DiagnosisView | undefined { const item = objectValue(value); if (!item || typeof item.root_cause_service !== "string" || typeof item.symptom_service !== "string" || typeof item.confidence !== "number") return undefined; return { symptom_service: item.symptom_service, root_cause_service: item.root_cause_service, dependency_service: typeof item.dependency_service === "string" ? item.dependency_service : undefined, root_cause_category: String(item.root_cause_category ?? "unknown"), root_cause_summary: String(item.root_cause_summary ?? ""), confidence: item.confidence, evidence_ids: Array.isArray(item.evidence_ids) ? item.evidence_ids.filter((x): x is string => typeof x === "string") : [], customer_impact: String(item.customer_impact ?? "") }; }
function objectValue(value: unknown): Record<string, unknown> | undefined { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
function findDetail(events: LiveEvent[], key: string) { for (const event of [...events].reverse()) { const details = objectValue(event.payload.details); if (typeof details?.[key] === "string") return details[key]; if (typeof event.payload[key] === "string") return event.payload[key] as string; } return undefined; }
function compactJson(value: unknown) { const text = JSON.stringify(value); return text.length > 260 ? `${text.slice(0, 257)}…` : text; }
function riskText(value: string) { return ({ read_only: "只读", low: "低风险", medium: "中风险 · 需审批", high: "高风险 · 需审批" } as Record<string, string>)[value] ?? value; }
function useElapsed(start?: string, end?: string) { const [now, setNow] = useState(Date.now()); useEffect(() => { if (end) return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, [end]); if (!start) return "00:00"; const seconds = Math.max(0, Math.floor(((end ? new Date(end).getTime() : now) - new Date(start).getTime()) / 1000)); return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`; }
function friendlyService(value?: string | null) { return ({ checkout: "结算服务", payment: "支付服务", cart: "购物车服务", recommendation: "推荐服务", frontend: "用户前端" } as Record<string, string>)[value ?? ""] ?? value ?? "未知服务"; }
function friendlyTitle(title: string, service?: string | null) { return /[\u4e00-\u9fff]/.test(title) ? title : `${friendlyService(service)}出现异常`; }
function friendlyEvidenceSummary(value: string) { const count = value.match(/contains (\d+)/)?.[1]; if (/^Metric evidence/.test(value)) return `指标查询返回 ${count ?? "若干"} 个聚合结果`; if (/^Trace evidence/.test(value)) return `调用链查询返回 ${count ?? "若干"} 条 Trace`; if (/^Log evidence/.test(value)) return `日志查询返回 ${count ?? "若干"} 条记录`; if (/^Runbook evidence/.test(value)) return `运行手册检索返回 ${count ?? "若干"} 个匹配片段`; return value; }
function statusText(value: string) { return ({ RECEIVED: "准备环境", TRIAGING: "事故理解", INVESTIGATING: "并行调查", SYNTHESIZING: "交叉验证", DIAGNOSED: "形成诊断", PLANNING: "生成处置方案", WAITING_APPROVAL: "等待批准", AUTHORIZING: "校验授权", EXECUTING: "执行处置", VERIFYING: "验证恢复", REPORTING: "生成复盘", RESOLVED_READ_ONLY: "诊断完成", NEEDS_HUMAN: "需要人工复核", REJECTED: "已拒绝执行", POLICY_REJECTED: "安全门已拒绝", ACTION_FAILED: "处置执行失败", RESOLVED: "已验证恢复" } as Record<string, string>)[value] ?? value; }
