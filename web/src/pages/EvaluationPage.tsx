import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiProblemError, api } from "../api/client";
import type { CaseScore, EvaluationRun } from "../api/types";

const components: Array<[keyof CaseScore, string]> = [
  ["root_cause", "根因"],
  ["root_cause_category", "类别"],
  ["evidence_fidelity", "证据"],
  ["signal_coverage", "覆盖"],
  ["tool_process", "工具"],
  ["safety", "安全"],
  ["recovery", "恢复"],
  ["efficiency", "效率"],
];

export function EvaluationPage() {
  const runs = useQuery({
    queryKey: ["evaluation-runs"],
    queryFn: () => api.listEvaluationRuns(),
  });
  const [selectedId, setSelectedId] = useState<string>();
  const items = runs.data ?? [];

  useEffect(() => {
    if (!selectedId && items[0]) setSelectedId(items[0].id);
  }, [items, selectedId]);

  const selected = useQuery({
    queryKey: ["evaluation-run", selectedId],
    queryFn: () => api.getEvaluationRun(selectedId ?? ""),
    enabled: Boolean(selectedId),
  });

  return (
    <section className="evaluation-page">
      <header className="evaluation-heading">
        <div>
          <p className="eyebrow">{"M6 / \u786e\u5b9a\u6027\u8bc4\u6d4b"}</p>
          <h1>评测记录</h1>
        </div>
        <div className="eval-counter">
          <strong>{items.length.toString().padStart(2, "0")}</strong>
          <span>{"\u5df2\u8bb0\u5f55"}<br />{"\u6279\u6b21"}</span>
        </div>
      </header>

      {runs.isPending && <div className="workbench-loading">{"\u6b63\u5728\u8bfb\u53d6\u8bc4\u6d4b\u8bb0\u5f55\u2026"}</div>}
      {runs.isError && <EvaluationError error={runs.error} />}
      {!runs.isPending && !runs.isError && items.length === 0 && (
        <div className="evaluation-empty">
          <span>EVAL / 00</span><h2>尚无评测记录</h2>
          <p>运行 baseline 或 multi validation 后，确定性分数会显示在这里。</p>
        </div>
      )}

      {items.length > 0 && (
        <div className="evaluation-layout">
          <aside className="run-ledger" aria-label="评测运行列表">
            <div className="ledger-label">{"\u8fd0\u884c\u8bb0\u5f55"}</div>
            {items.map((run) => (
              <RunButton
                key={run.id}
                run={run}
                selected={run.id === selectedId}
                onSelect={setSelectedId}
              />
            ))}
          </aside>
          <main className="evaluation-detail">
            {selected.isPending && <div className="workbench-loading">{"\u6b63\u5728\u8bfb\u53d6\u4e8b\u5b9e\u2026"}</div>}
            {selected.isError && <EvaluationError error={selected.error} />}
            {selected.data && <RunDetail run={selected.data} />}
          </main>
        </div>
      )}
    </section>
  );
}

function RunButton({ run, selected, onSelect }: {
  run: EvaluationRun;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const score = run.aggregate_metrics.weighted_score;
  return (
    <button
      className={selected ? "run-entry selected" : "run-entry"}
      type="button"
      onClick={() => onSelect(run.id)}
    >
      <span><i className={`mode-mark ${run.aggregate_metrics.mode ?? "unknown"}`} />{run.aggregate_metrics.mode ?? "pending"}</span>
      <strong>{score === null || score === undefined ? "—" : score.toFixed(3)}</strong>
      <small>{run.candidate_version}<br />{run.id}</small>
    </button>
  );
}

function RunDetail({ run }: { run: Awaited<ReturnType<typeof api.getEvaluationRun>> }) {
  const aggregate = run.aggregate_metrics;
  return (
    <>
      <header className="run-detail-head">
        <div>
          <p className="eyebrow">{aggregate.mode?.toUpperCase() ?? "PENDING"} / {run.status.toUpperCase()}</p>
          <h2>{run.candidate_version}</h2>
          <small>{run.suite_version} · {run.id}</small>
        </div>
        <div className="primary-score"><span>{"\u52a0\u6743\u603b\u5206"}</span><strong>{formatScore(aggregate.weighted_score)}</strong></div>
      </header>
      <div className="metric-rack">
        <Metric label={"\u6839\u56e0\u51c6\u786e\u7387"} value={aggregate.root_cause_accuracy} />
        <Metric label={"EVIDENCE \u5fe0\u5b9e\u5ea6"} value={aggregate.evidence_fidelity} />
        <Metric label={"\u5b89\u5168\u786c\u5931\u8d25"} value={aggregate.safety_hard_failures} integer />
        <Metric label={"\u5de5\u5177\u8c03\u7528"} value={aggregate.total_tool_calls} integer />
        <Metric label={"\u603b\u8017\u65f6"} value={aggregate.total_duration_ms} integer suffix=" ms" />
        <Metric label={"\u6a21\u578b\u6210\u672c"} value={aggregate.total_cost_microusd} integer suffix=" µUSD" />
      </div>
      <div className="case-matrix">
        <div className="case-row case-head">
          <span>{"\u573a\u666f"}</span><span>{"\u603b\u5206"}</span>
          {components.map(([, label]) => <span key={label}>{label}</span>)}
          <span>{"\u4e8b\u5b9e\u6458\u8981"}</span>
        </div>
        {run.cases.map((item) => (
          <div className={item.hard_failures.length ? "case-row failed" : "case-row"} key={item.id}>
            <span><strong>{item.scenario_id}</strong><small>seed {item.metrics.seed} · {item.metrics.tool_call_count} calls</small></span>
            <span className="score-cell">{item.metrics.total.toFixed(3)}</span>
            {components.map(([key]) => {
              const component = item.metrics[key];
              return typeof component === "object" && component !== null && "value" in component
                ? <span className="component-bar" key={key}><i style={{ width: `${component.value * 100}%` }} /><b>{component.value.toFixed(2)}</b></span>
                : null;
            })}
            <span className="facts-cell" title={item.metrics.facts_digest}>{item.metrics.facts_digest.slice(0, 8)}</span>
            {item.hard_failures.length > 0 && (
              <div className="failure-strip">
                <strong>{"\u786c\u5931\u8d25"}</strong>{item.hard_failures.join(" · ")}
                {item.metrics.trajectory_uri && <a href={item.metrics.trajectory_uri}>轨迹 ↗</a>}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

function Metric({ label, value, integer = false, suffix = "" }: {
  label: string;
  value: number | null | undefined;
  integer?: boolean;
  suffix?: string;
}) {
  return <div><span>{label}</span><strong>{value === null || value === undefined ? "—" : `${integer ? value : value.toFixed(3)}${suffix}`}</strong></div>;
}

function EvaluationError({ error }: { error: Error }) {
  const correlation = error instanceof ApiProblemError ? error.correlationId : undefined;
  return <div className="error-panel"><strong>评测记录读取失败</strong><p>{error.message}</p>{correlation && <small>correlation: {correlation}</small>}</div>;
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toFixed(3);
}
