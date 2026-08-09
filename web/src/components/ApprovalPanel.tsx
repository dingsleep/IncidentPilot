import { useState } from "react";

import type { ActionProposalView } from "../api/types";

type Decision = "approve" | "reject";

export function ApprovalPanel({
  proposal,
  onDecision,
}: {
  proposal: ActionProposalView;
  onDecision: (decision: Decision, reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<Decision>();
  const [error, setError] = useState<string>();
  const action = proposal.proposal.action;

  async function decide(decision: Decision) {
    setBusy(decision);
    setError(undefined);
    try {
      await onDecision(decision, reason.trim());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "审批请求未完成。");
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <section className="approval-panel" aria-label="处置审批">
      <header className="approval-heading">
        <div><span className="approval-index">06</span><p className="eyebrow">HUMAN AUTHORIZATION REQUIRED</p><h2>处置审批</h2></div>
        <strong className={`proposal-status ${proposal.status.toLowerCase()}`}>{proposal.status}</strong>
      </header>
      <div className="approval-action">
        <small>固定动作 · 参数不可编辑</small>
        <strong>{action.action_type === "restart_service" ? "RESTART SERVICE" : "ROLLBACK CHANGE"}</strong>
        <dl>
          <div><dt>目标服务</dt><dd>{action.target_service}</dd></div>
          {action.action_type === "restart_service" ? <div><dt>Grace period</dt><dd>{action.grace_period_seconds}s</dd></div> : <div><dt>Change ID</dt><dd>{action.change_id}</dd></div>}
          <div><dt>风险</dt><dd>{proposal.proposal.risk}</dd></div>
        </dl>
      </div>
      <div className="approval-details">
        <DetailList label="Evidence" values={proposal.proposal.diagnosis_evidence_ids} />
        <DetailList label="验证检查" values={proposal.proposal.verification_checks.map((check) => `${check.metric} ${check.comparator} ${String(check.threshold)} · ${check.observation_seconds}s`)} />
        <DetailList label="补偿语义" values={[`${proposal.proposal.compensation_plan.mode} / ${proposal.proposal.compensation_plan.trigger}`, proposal.proposal.compensation_plan.reason]} />
      </div>
      <label className="approval-reason">审批备注（可选）<textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} placeholder="记录批准或拒绝依据" /></label>
      {error && <p className="approval-error" role="alert">{error}</p>}
      <div className="approval-actions">
        <button type="button" className="reject-action" disabled={Boolean(busy)} onClick={() => void decide("reject")}>{busy === "reject" ? "提交中…" : "拒绝"}</button>
        <button type="button" className="approve-action" disabled={Boolean(busy)} onClick={() => void decide("approve")}>{busy === "approve" ? "提交中…" : "批准受限动作"}</button>
      </div>
    </section>
  );
}

function DetailList({ label, values }: { label: string; values: string[] }) {
  return <div><small>{label}</small><ul>{values.map((value) => <li key={value}>{value}</li>)}</ul></div>;
}
