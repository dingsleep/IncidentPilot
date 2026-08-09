import type { DiagnosisView, HypothesisView } from "../api/types";

export function HypothesisPanel({
  diagnosis,
  hypotheses,
}: {
  diagnosis?: DiagnosisView;
  hypotheses: HypothesisView[];
}) {
  return (
    <div className="hypothesis-stack">
      {diagnosis && (
        <article className="diagnosis-card">
          <p className="eyebrow">{"\u5df2\u786e\u8ba4\u8bca\u65ad / DIAGNOSIS"}</p>
          <div className="confidence"><strong>{Math.round(diagnosis.confidence * 100)}</strong><span>%</span></div>
          <h3>{diagnosis.root_cause_service}</h3>
          <p>{diagnosis.root_cause_summary}</p>
          <dl>
            <div><dt>症状服务</dt><dd>{diagnosis.symptom_service}</dd></div>
            <div><dt>类别</dt><dd>{diagnosis.root_cause_category}</dd></div>
            <div><dt>Evidence</dt><dd>{diagnosis.evidence_ids.length}</dd></div>
          </dl>
        </article>
      )}
      {hypotheses.length === 0 && !diagnosis && (
        <p className="empty-note">尚未形成候选假设。</p>
      )}
      {[...hypotheses].sort((a, b) => b.confidence - a.confidence).map((hypothesis, index) => (
        <article className="hypothesis-card" key={hypothesis.id}>
          <header><span>H{index + 1}</span><strong>{Math.round(hypothesis.confidence * 100)}%</strong></header>
          <h4>{hypothesis.root_cause_service}</h4>
          <p>{hypothesis.failure_mode}</p>
          <div className="evidence-counts">
            <span>支持 {hypothesis.supporting_evidence_ids.length}</span>
            <span>反证 {hypothesis.contradicting_evidence_ids?.length ?? 0}</span>
          </div>
        </article>
      ))}
    </div>
  );
}
