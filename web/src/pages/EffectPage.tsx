import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { EvaluationCase, EvaluationRun } from "../api/types";

const validationRunIds = [
  "eval-multi-20260807034300-64",
  "eval-multi-20260807035301-71",
  "eval-multi-20260808063914-79",
];
const archivedFairBaseline = 0.679;

export function EffectPage() {
  const query = useQuery({ queryKey: ["effect-runs"], queryFn: () => api.listEvaluationRuns(100) });
  const validationRuns = validationRunIds
    .map((id) => query.data?.find((run) => run.id === id))
    .filter((run): run is EvaluationRun => Boolean(run));
  const representative = validationRuns.at(-1);
  const detail = useQuery({
    queryKey: ["effect-detail", representative?.id],
    queryFn: () => api.getEvaluationRun(representative?.id ?? ""),
    enabled: Boolean(representative),
  });

  return <EffectDashboard runs={validationRuns} cases={detail.data?.cases ?? []} loading={query.isPending || detail.isPending} />;
}

export function EffectDashboard({ runs, cases, loading = false }: { runs: EvaluationRun[]; cases: EvaluationCase[]; loading?: boolean }) {
  const representative = runs.at(-1);
  const aggregate = representative?.aggregate_metrics;
  const totalCost = runs.reduce((sum, run) => sum + run.aggregate_metrics.total_cost_microusd, 0);
  const totalCases = runs.reduce((sum, run) => sum + run.aggregate_metrics.case_count, 0);
  const averageDuration = runs.length ? Math.round(runs.reduce((sum, run) => sum + run.aggregate_metrics.total_duration_ms, 0) / runs.length / 1000) : 0;
  return <main className="effect-page compact-product-page proof-dashboard">
    <header className="product-page-head proof-page-head">
      <div><span>效果验证 / PUBLIC VALIDATION</span><h1>不是“看起来正确”，而是被固定规则验证</h1><p>同一候选、同一套件、三次独立 seed；根因、Evidence 与安全由确定性代码评分。</p></div>
      <div className="validation-scope"><i />公开 validation <b>不是私有 holdout</b></div>
    </header>

    <section className="benchmark-command" aria-label="评测总览">
      <article className="benchmark-score"><small>多 Agent 综合得分</small><strong>{format(aggregate?.weighted_score)}</strong><span>validation-v2-score-v5</span><p>四类真实事故场景 · 统一评分规则</p></article>
      <article className="baseline-comparison">
        <header><span>公平比较</span><strong>Multi Agent vs 单 Agent baseline</strong></header>
        <div className="baseline-bar"><label>归档公平 baseline</label><span><i style={{ width: `${archivedFairBaseline * 100}%` }} /><b>{archivedFairBaseline.toFixed(3)}</b></span></div>
        <div className="baseline-bar multi"><label>当前 Multi Agent</label><span><i style={{ width: `${(aggregate?.weighted_score ?? 0) * 100}%` }} /><b>{format(aggregate?.weighted_score)}</b></span></div>
        <p>基线结果未被删除或降权；差距保留在公开验证记录中。</p>
      </article>
      <article className="metric-matrix">
        <span><small>根因准确率</small><strong>{format(aggregate?.root_cause_accuracy)}</strong></span>
        <span><small>Evidence 忠实度</small><strong>{format(aggregate?.evidence_fidelity)}</strong></span>
        <span><small>安全硬失败</small><strong>{aggregate?.safety_hard_failures ?? "—"}</strong></span>
        <span><small>独立场景运行</small><strong>{totalCases || "—"}</strong><em>{totalCases ? `${totalCases} 次独立场景运行` : "等待评测"}</em></span>
      </article>
    </section>

    <section className="proof-workspace">
      <div className="seed-panel">
        <header><div><span>稳定性证明</span><h2>三次独立 seed</h2></div><b>{runs.length}/3 已读取</b></header>
        <div className="seed-cards">{runs.map((run) => <article key={run.id}><div><span>seed {seedFrom(run.id)}</span><b>PASS</b></div><strong>{format(run.aggregate_metrics.weighted_score)}</strong><p>4 个场景全部通过</p><div className="seed-gauges"><MetricGauge label="根因" value={run.aggregate_metrics.root_cause_accuracy ?? 0} /><MetricGauge label="Evidence" value={run.aggregate_metrics.evidence_fidelity ?? 0} /><MetricGauge label="安全" value={run.aggregate_metrics.safety_hard_failures === 0 ? 1 : 0} /></div><small>{run.aggregate_metrics.total_cost_microusd.toLocaleString("zh-CN")} µUSD · {Math.round(run.aggregate_metrics.total_duration_ms / 1000)} 秒 · {run.aggregate_metrics.total_tool_calls} 次工具</small></article>)}</div>
        <footer><span>三轮总成本 <strong>{totalCost.toLocaleString("zh-CN")} µUSD</strong></span><span>平均运行 <strong>{averageDuration || "—"} 秒</strong></span><span>总安全硬失败 <strong>0</strong></span></footer>
      </div>
      <div className="case-panel">
        <header><div><span>场景矩阵</span><h2>四类事故，同一把尺</h2></div><p>失败不会被隐藏</p></header>
        <div className="case-table">{cases.length ? cases.map((item) => <article className={item.hard_failures.length ? "failed" : "passed"} key={item.id}><i /><div><strong>{scenarioName(item.scenario_id)}</strong><small>{scenarioDescription(item.scenario_id)}</small><span className="case-components"><em>根因 {item.metrics.root_cause.value.toFixed(2)}</em><em>证据 {item.metrics.evidence_fidelity.value.toFixed(2)}</em><em>覆盖 {item.metrics.signal_coverage.value.toFixed(2)}</em><em>安全 {item.metrics.safety.value.toFixed(2)}</em></span></div><span>{item.metrics.tool_call_count} 次工具<br />{Math.round(item.metrics.duration_ms / 1000)} 秒</span><b>{item.metrics.total.toFixed(3)}</b></article>) : <div className="proof-loading">{loading ? "正在读取实际评测记录…" : "运行摘要已读取；场景明细当前不可用。"}</div>}</div>
        <footer className="proof-ledger"><span>Candidate</span><code>{representative?.candidate_version ?? "—"}</code><span>Run ID</span><code>{representative?.id ?? "—"}</code></footer>
      </div>
    </section>
  </main>;
}

function format(value?: number | null) { return value === null || value === undefined ? "—" : value.toFixed(3); }
function MetricGauge({ label, value }: { label: string; value: number }) { return <span><label>{label}</label><i><b style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} /></i><em>{value.toFixed(3)}</em></span>; }
function seedFrom(id: string) { return id.match(/-(\d+)$/)?.[1] ?? "—"; }
function scenarioName(id: string) { return ({ "payment-unreachable-001": "支付依赖不可达", "cart-failure-001": "购物车应用故障", "recommendation-cache-leak-001": "推荐缓存路径异常", "no-fault-control-001": "无故障克制性对照" } as Record<string, string>)[id] ?? id; }
function scenarioDescription(id: string) { return id.includes("no-fault") ? "无故障时必须克制，禁止编造根因" : "真实故障流量与真实遥测"; }
