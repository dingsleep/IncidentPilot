import type { EvolutionCandidate } from "../api/types";

type GateRecord = EvolutionCandidate["gate_records"][number];

type Aggregate = {
  weighted_score: number;
  root_cause_accuracy: number;
  evidence_fidelity: number;
  safety_hard_failures: number;
  total_cost_microusd: number;
};

type SplitComparison = {
  active_run: string;
  candidate_run: string;
  active: Aggregate;
  candidate: Aggregate;
};

type ShadowComparison = {
  strategy: string;
  worst_seed: number;
  train: SplitComparison;
  validation: SplitComparison;
};

export function CandidateGateLedger({ records }: { records: GateRecord[] }) {
  return (
    <section className="candidate-gate-ledger" aria-label={"\u5019\u9009 Gate \u8bb0\u5f55"}>
      <header>
        <span>{"GATE \u51b3\u7b56\u8bb0\u5f55"}</span>
        <strong>{records.length.toString().padStart(2, "0")}</strong>
      </header>
      {records.length === 0 && <p>{"\u5c1a\u65e0\u5f71\u5b50\u8bc4\u6d4b\u8bb0\u5f55\uff0cActive Prompt \u4fdd\u6301\u4e0d\u53d8\u3002"}</p>}
      {records.map((record, index) => (
        <article className="candidate-gate-record" key={`${record.status}-${index}`}>
          <div className="candidate-gate-status">
            <strong>{record.status}</strong>
            {record.human_rejection_reason && <em>{record.human_rejection_reason}</em>}
          </div>
          {comparisonFrom(record.decision) ? (
            <ComparisonPanel comparison={comparisonFrom(record.decision)!} />
          ) : (
            <p>{stringValue(record.decision.reason) ?? JSON.stringify(record.decision)}</p>
          )}
        </article>
      ))}
    </section>
  );
}

function ComparisonPanel({ comparison }: { comparison: ShadowComparison }) {
  return (
    <div className="shadow-comparison">
      <p>
        {comparison.strategy.toUpperCase()} · {"\u6700\u5dee SEED"} {comparison.worst_seed}
      </p>
      <ComparisonTable label={"TRAIN / \u8bad\u7ec3\u96c6"} comparison={comparison.train} />
      <ComparisonTable label={"VALIDATION / \u9a8c\u8bc1\u96c6"} comparison={comparison.validation} />
    </div>
  );
}

function ComparisonTable({ label, comparison }: { label: string; comparison: SplitComparison }) {
  return (
    <div className="comparison-table">
      <header>
        <strong>{label}</strong>
        <small>{comparison.active_run} → {comparison.candidate_run}</small>
      </header>
      <div className="comparison-row comparison-head">
        <span>{"\u6307\u6807"}</span><span>{"\u5f53\u524d\u7248"}</span><span>{"\u5019\u9009\u7248"}</span><span>{"\u53d8\u5316"}</span>
      </div>
      <MetricRow
        label={"\u52a0\u6743\u603b\u5206"}
        active={comparison.active.weighted_score}
        candidate={comparison.candidate.weighted_score}
      />
      <MetricRow
        label={"\u6839\u56e0\u51c6\u786e\u7387"}
        active={comparison.active.root_cause_accuracy}
        candidate={comparison.candidate.root_cause_accuracy}
      />
      <MetricRow
        label={"Evidence \u5fe0\u5b9e\u5ea6"}
        active={comparison.active.evidence_fidelity}
        candidate={comparison.candidate.evidence_fidelity}
      />
      <MetricRow
        label={"\u5b89\u5168\u786c\u5931\u8d25"}
        active={comparison.active.safety_hard_failures}
        candidate={comparison.candidate.safety_hard_failures}
        integer
      />
      <MetricRow
        label="COST µUSD"
        active={comparison.active.total_cost_microusd}
        candidate={comparison.candidate.total_cost_microusd}
        integer
      />
    </div>
  );
}

function MetricRow({ label, active, candidate, integer = false }: {
  label: string;
  active: number;
  candidate: number;
  integer?: boolean;
}) {
  const delta = candidate - active;
  const format = (value: number) => integer ? value.toString() : value.toFixed(3);
  return (
    <div className={delta < 0 ? "comparison-row regressed" : "comparison-row"}>
      <span>{label}</span><span>{format(active)}</span><span>{format(candidate)}</span>
      <span>{delta > 0 ? "+" : ""}{format(delta)}</span>
    </div>
  );
}

function comparisonFrom(value: Record<string, unknown>): ShadowComparison | null {
  const comparison = recordValue(value.comparison);
  if (!comparison) return null;
  const strategy = stringValue(comparison.strategy);
  const worstSeed = numberValue(comparison.worst_seed);
  const train = splitComparison(comparison.train);
  const validation = splitComparison(comparison.validation);
  return strategy && worstSeed !== null && train && validation
    ? { strategy, worst_seed: worstSeed, train, validation }
    : null;
}

function splitComparison(value: unknown): SplitComparison | null {
  const split = recordValue(value);
  if (!split) return null;
  const activeRun = stringValue(split.active_run);
  const candidateRun = stringValue(split.candidate_run);
  const active = aggregate(split.active);
  const candidate = aggregate(split.candidate);
  return activeRun && candidateRun && active && candidate
    ? { active_run: activeRun, candidate_run: candidateRun, active, candidate }
    : null;
}

function aggregate(value: unknown): Aggregate | null {
  const metrics = recordValue(value);
  if (!metrics) return null;
  const weightedScore = numberValue(metrics.weighted_score);
  const rootCauseAccuracy = numberValue(metrics.root_cause_accuracy);
  const evidenceFidelity = numberValue(metrics.evidence_fidelity);
  const safetyHardFailures = numberValue(metrics.safety_hard_failures);
  const totalCost = numberValue(metrics.total_cost_microusd);
  return weightedScore !== null &&
    rootCauseAccuracy !== null &&
    evidenceFidelity !== null &&
    safetyHardFailures !== null &&
    totalCost !== null
    ? {
      weighted_score: weightedScore,
      root_cause_accuracy: rootCauseAccuracy,
      evidence_fidelity: evidenceFidelity,
      safety_hard_failures: safetyHardFailures,
      total_cost_microusd: totalCost,
    }
    : null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
