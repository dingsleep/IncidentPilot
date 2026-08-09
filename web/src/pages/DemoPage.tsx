import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, createApiClient } from "../api/client";
import type { EvaluationRun, Incident, ManualIncidentRequest } from "../api/types";

const operatorApi = createApiClient({ actorId: "local-operator" });

const services = [
  { value: "checkout", label: "结算服务", hint: "支付或下单失败" },
  { value: "payment", label: "支付服务", hint: "支付依赖不可达" },
  { value: "product-catalog", label: "商品目录", hint: "缓存或目录异常" },
  { value: "recommendation", label: "推荐服务", hint: "延迟或依赖异常" },
] as const;

const responders = [
  { icon: "M", name: "Metrics 调查员", body: "对比错误率、延迟和容量变化", tone: "blue" },
  { icon: "L", name: "Logs 调查员", body: "定位异常模式与关联服务", tone: "cyan" },
  { icon: "T", name: "Traces 调查员", body: "沿调用链追踪故障传播路径", tone: "violet" },
  { icon: "R", name: "Runbook 分析员", body: "检索可验证的处置知识", tone: "amber" },
] as const;

const outcomes = [
  ["01", "发生了什么", "把告警翻译成影响范围和事故优先级"],
  ["02", "根因在哪里", "给出带 Evidence 引用的根因，而不是一句猜测"],
  ["03", "下一步怎么做", "生成受策略约束、可审批、可回滚的建议"],
  ["04", "是否真的恢复", "通过真实指标复查恢复结果并生成复盘"],
] as const;

export function DemoPage() {
  const navigate = useNavigate();
  const incidents = useQuery({
    queryKey: ["demo-incidents"],
    queryFn: () => api.listIncidents({ limit: 100 }),
  });
  const evaluations = useQuery({
    queryKey: ["demo-evaluations"],
    queryFn: () => api.listEvaluationRuns(50),
  });
  const candidates = useQuery({
    queryKey: ["demo-candidates"],
    queryFn: () => api.listEvolutionCandidates(),
  });
  const incidentItems = incidents.data?.items ?? [];
  const candidateItems = candidates.data ?? [];
  const featuredIncident = selectFeaturedIncident(incidentItems);
  const evaluation = selectEvaluation(evaluations.data ?? []);
  const startFresh = useMutation({
    mutationFn: (payload: ManualIncidentRequest) => operatorApi.createIncident(payload),
    onSuccess: ({ incident }) => void navigate(`/incidents/${incident.id}`),
  });

  return (
    <DemoOverview
      candidateCount={candidateItems.length}
      evaluation={evaluation}
      featuredIncident={featuredIncident}
      replayIncidents={incidentItems.filter((item) => item.id !== featuredIncident?.id && isCompleted(item)).slice(0, 12)}
      onStartFresh={(payload) => startFresh.mutateAsync(payload).then(() => undefined)}
      startError={startFresh.error?.message}
      startPending={startFresh.isPending}
      rejectedCandidateCount={candidateItems.filter((item) => item.gate_statuses.some((status) => status.includes("rejected"))).length}
      totalIncidentCount={incidentItems.length}
    />
  );
}

export function DemoOverview({
  candidateCount,
  evaluation,
  featuredIncident,
  onStartFresh,
  replayIncidents = [],
  rejectedCandidateCount,
  startError,
  startPending = false,
  totalIncidentCount,
}: {
  candidateCount: number;
  evaluation?: EvaluationRun;
  featuredIncident?: Incident;
  onStartFresh?: (payload: ManualIncidentRequest) => Promise<void>;
  replayIncidents?: Incident[];
  rejectedCandidateCount: number;
  startError?: string;
  startPending?: boolean;
  totalIncidentCount: number;
}) {
  const [alertText, setAlertText] = useState("");
  const [service, setService] = useState("checkout");
  const availableReplays = useMemo(() => [featuredIncident, ...replayIncidents].filter((item): item is Incident => Boolean(item)), [featuredIncident, replayIncidents]);
  const selectedReplay = availableReplays.find((item) => item.service === service) ?? featuredIncident ?? availableReplays[0];

  const submitFresh = async () => {
    if (!onStartFresh) return;
    const selected = services.find((item) => item.value === service);
    const normalized = alertText.trim();
    await onStartFresh({
      title: normalized.split("\n")[0]?.slice(0, 120) || `${selected?.label ?? service}出现异常`,
      description: normalized || `从本地体验入口发起：请调查 ${selected?.label ?? service} 的异常信号。`,
      severity: "P1",
      service,
      start_analysis: true,
      execution_mode: "read_only",
    });
  };

  return (
    <section className="demo-page">
      <section className="experience-hero">
        <div className="hero-copy">
          <p className="hero-kicker"><i />真实遥测驱动 · 有证据的事故诊断</p>
          <h1>告警来了，<br /><em>让 AI 事故响应团队接手。</em></h1>
          <p>IncidentPilot 不是聊天机器人。它让专职 Agent 并行调查 Metrics / Logs / Traces 与 Runbook，从告警到可核验根因，再由事故指挥官形成处置建议与恢复验证。</p>
          <div className="hero-proofline"><span>真实 OpenTelemetry Demo</span><span>确定性安全门</span><span>全链路可审计</span></div>
        </div>

        <form className="experience-launcher" onSubmit={(event) => { event.preventDefault(); void submitFresh(); }}>
          <header><span>立即体验</span><small>无需理解运维术语</small></header>
          <label>粘贴告警内容<textarea value={alertText} onChange={(event) => setAlertText(event.target.value)} placeholder="例如：结算接口错误率突然升高，用户无法完成付款……" /></label>
          <div className="launcher-or"><span>或者</span></div>
          <label>选择演示服务<select value={service} onChange={(event) => setService(event.target.value)}>{services.map((item) => <option key={item.value} value={item.value}>{item.label} · {item.hint}</option>)}</select></label>
          <div className="launcher-provenance"><i />{selectedReplay ? <span><strong>真实运行记录</strong>已验证事故回放，不消耗模型额度</span> : <span><strong>真实运行记录</strong>当前暂无可回放事故，可以发起一次全新诊断</span>}</div>
          <div className="launcher-actions">
            {selectedReplay ? <Link className="experience-primary" to={`/incidents/${selectedReplay.id}`}>体验真实诊断 <span>→</span></Link> : <button className="experience-primary" type="button" disabled>体验真实诊断</button>}
            <button className="experience-secondary" disabled={!onStartFresh || startPending} type="submit">{startPending ? "正在创建事故…" : "发起全新诊断"}</button>
          </div>
          <p className="launcher-note">全新诊断会调用已配置模型；只调查真实遥测，不自动执行任何系统写操作。</p>
          {startError && <p className="launcher-error">创建失败：{startError}</p>}
        </form>
      </section>

      <section className="trust-ribbon" aria-label="项目真实性与质量快照">
        <div><strong>{formatScore(evaluation?.aggregate_metrics.weighted_score)}</strong><span>公开 validation 综合得分</span></div>
        <div><strong>{evaluation?.aggregate_metrics.root_cause_accuracy?.toFixed(3) ?? "—"}</strong><span>根因准确率</span></div>
        <div><strong>{evaluation?.aggregate_metrics.safety_hard_failures ?? 0}</strong><span>安全硬失败</span></div>
        <div><strong>{totalIncidentCount}</strong><span>当前 API 事故记录</span></div>
      </section>

      <section className="showcase-section team-section">
        <header className="showcase-heading"><p>它如何工作</p><h2>你看到的不是一句回答，<br />而是一支正在协作的 AI 事故响应团队。</h2><span>每个角色只获得完成职责所需的最小只读权限。模型负责认知，确定性代码负责控制。</span></header>
        <div className="team-orchestration">
          <div className="responder-grid">{responders.map((item) => <article className={`responder-card ${item.tone}`} key={item.name}><i>{item.icon}</i><div><strong>{item.name}</strong><span>{item.body}</span></div><small>专职 Agent</small></article>)}</div>
          <div className="orchestration-arrow"><span>结构化调查结果</span><i>↓</i></div>
          <article className="commander-card"><div className="commander-icon">IC</div><div><small>INCIDENT COMMANDER</small><h3>事故指挥官</h3><p>合并支持证据与反证，判断是否需要继续调查，最终形成带置信度的 Diagnosis。</p></div><strong>综合归因</strong></article>
          <div className="orchestration-arrow safe"><span>建议进入代码控制区</span><i>↓</i></div>
          <div className="control-lane"><article><i>◆</i><div><strong>确定性安全门</strong><span>策略、权限、预算与参数白名单</span></div><small>不是 Agent</small></article><article><i>◎</i><div><strong>人工批准</strong><span>操作员决定是否允许处置</span></div><small>Human</small></article><article><i>✓</i><div><strong>恢复验证</strong><span>真实指标确认是否恢复</span></div><small>Verifier</small></article></div>
        </div>
      </section>

      <section className="showcase-section outcome-section">
        <header className="showcase-heading"><p>最终交付</p><h2>把一条告警，变成可以采取行动的答案。</h2><span>默认页面先回答业务问题；PromQL、原始 JSON、Trace ID 等技术细节仍可在专业控制台下钻。</span></header>
        <div className="outcome-grid">{outcomes.map(([index, title, body]) => <article key={index}><span>{index}</span><h3>{title}</h3><p>{body}</p></article>)}</div>
        {selectedReplay && <div className="selected-case"><div><span>本次推荐体验</span><strong>{selectedReplay.title}</strong><small>{selectedReplay.severity} · {serviceName(selectedReplay.service)} · {selectedReplay.source}</small></div><Link to={`/incidents/${selectedReplay.id}`}>打开完整证据链 <span>→</span></Link></div>}
      </section>

      <section className="showcase-section evolution-story">
        <div className="evolution-copy"><p>受控进化</p><h2>系统会学习，但不会偷偷修改自己。</h2><span>失败轨迹会形成改进候选。候选改进也不会自动上线；每个候选必须经过离线回归、影子评测、安全门禁和人工批准，才能晋级。</span><Link to="/evolution">查看完整候选治理记录 →</Link></div>
        <div className="gate-story"><header><span>一次真实的拒绝案例 · 当前治理 {candidateCount} 个候选</span><strong>SHADOW REJECTED · {rejectedCandidateCount}</strong></header><div className="score-journey"><div><small>训练集</small><strong>0.963 <i>→</i> 1.000</strong><span>看起来变好了</span></div><div className="regressed"><small>验证集根因</small><strong>1.000 <i>→</i> 0.750</strong><span>泛化能力退化</span></div></div><p><b>门禁决定：拒绝晋级。</b> Active Prompt 保持不变，没有为了漂亮结果降低标准。</p></div>
      </section>

      <section className="showcase-section proof-section">
        <header className="showcase-heading"><p>为什么可信</p><h2>不是前端 Mock，也不把模型的自信当证据。</h2></header>
        <div className="proof-cards"><article><span>01</span><h3>真实系统</h3><p>OpenTelemetry Demo 微服务持续运行并产生真实请求。</p></article><article><span>02</span><h3>真实遥测</h3><p>Prometheus、OpenSearch、Jaeger 提供 Metrics、Logs、Traces。</p></article><article><span>03</span><h3>真实调用</h3><p>Agent 通过受限 Telemetry MCP 查询，而不是读取隐藏答案。</p></article><article><span>04</span><h3>确定性评测</h3><p>根因、Evidence、安全与恢复由 Episode 标准答案自动评分。</p></article></div>
        <div className="boundary-note"><strong>诚实边界</strong><p>这是工程级本地 AIOps 原型与参考实现，不冒充已部署到企业生产环境。Action MCP 在公开体验中默认关闭；模型没有 Shell、Docker Socket 或任意写权限。</p></div>
      </section>
    </section>
  );
}

function selectFeaturedIncident(items: Incident[]): Incident | undefined {
  const completedStatuses = new Set(["REPORTING", "RESOLVED_READ_ONLY", "RESOLVED"]);
  return items.find((item) => item.source === "e2e" && completedStatuses.has(item.status))
    ?? items.find((item) => item.source === "evaluation-runner" && completedStatuses.has(item.status))
    ?? items.find((item) => Boolean(item.service) && completedStatuses.has(item.status));
}

function isCompleted(item: Incident): boolean {
  return ["REPORTING", "RESOLVED_READ_ONLY", "RESOLVED"].includes(item.status);
}

function serviceName(service: string | null | undefined): string {
  return services.find((item) => item.value === service)?.label ?? service ?? "未指定服务";
}

function selectEvaluation(items: EvaluationRun[]): EvaluationRun | undefined {
  return items.find((item) => item.suite_version === "validation-v2-score-v5" && item.status === "completed")
    ?? items.find((item) => item.status === "completed");
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? "\u2014" : value.toFixed(3);
}
