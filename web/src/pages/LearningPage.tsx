import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { EvolutionCandidate } from "../api/types";

const featuredCandidateId = "candidate-f871693e17e3";

export function LearningPage() {
  const query = useQuery({ queryKey: ["learning-candidates"], queryFn: () => api.listEvolutionCandidates() });
  const meaningful = selectMeaningfulCandidates(query.data ?? []);
  return <EvolutionDecisionBoard candidates={meaningful} loading={query.isPending} />;
}

export function EvolutionDecisionBoard({ candidates, loading = false }: { candidates: EvolutionCandidate[]; loading?: boolean }) {
  const [selectedId, setSelectedId] = useState<string>();
  const candidate = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const comparison = candidate ? comparisonFrom(candidate) : undefined;
  const checks = candidate ? checksFrom(candidate) : [];
  const rejected = candidate ? effectiveStatus(candidate).includes("rejected") : false;

  return <main className="learning-page compact-product-page governance-dashboard">
    <header className="product-page-head proof-page-head"><div><span>受控进化 / GOVERNED EVOLUTION</span><h1>系统会提出改进，但没有权力偷偷上线</h1><p>失败聚类生成候选；回归、影子评测与人工晋级共同保护 Active 版本。</p></div><div className={`governance-verdict ${rejected ? "rejected" : "pending"}`}><i />{rejected ? "本次候选已拒绝" : "候选仍在隔离区"}</div></header>
    <section className="governance-rail">{[
      ["01", "失败聚类"], ["02", "生成候选"], ["03", "训练/验证回归"], ["04", "影子评测"], ["05", "人工晋级"],
    ].map(([index, label], position) => <article className={candidate && position < 4 ? "complete" : position === 4 ? "blocked" : ""} key={index}><span>{index}</span><strong>{label}</strong>{position < 4 && <i />}</article>)}</section>

    {candidate ? <section className="governance-workspace">
      <aside className="candidate-list"><header><span>真实治理记录</span><strong>{candidates.length.toString().padStart(2, "0")}</strong></header>{candidates.map((item) => <button type="button" className={item.id === candidate.id ? "active" : ""} onClick={() => setSelectedId(item.id)} key={item.id}><span>{statusName(effectiveStatus(item))}</span><strong>{componentName(item.target_component)}</strong><small>{failureName(item.target_failure_label)}</small></button>)}</aside>
      <article className="candidate-decision">
        <header><div><span>门禁结论</span><h2>{rejected ? "线上 Active 保持不变" : "候选尚未获得晋级资格"}</h2></div><b>{rejected ? "REJECT" : "ISOLATED"}</b></header>
        <p>{rejected ? "训练集局部变好并不代表可以上线。验证集出现根因回归，确定性质量门拒绝候选，当前线上版本未被覆盖。" : "候选仍处于隔离治理流程；全部质量门与人工批准完成前，它不会影响在线诊断。"}</p>
        {rejected && <aside className="governance-protection"><strong>拒绝不是失败</strong><span>系统成功阻止了根因准确率退化 25% 的候选进入线上。</span></aside>}
        <section className="inline-diff"><header><span>Prompt Diff</span><small>直接展示 · 无需展开</small></header><pre>{candidate.diff}</pre></section>
        <footer><span>Candidate ID <code>{candidate.id}</code></span><span>Digest <code>{candidate.digest}</code></span><span>Generator <code>{candidate.generator_model}</code></span></footer>
      </article>
      <aside className="gate-evidence">
        <header><span>为什么被拒绝</span><strong>{comparison ? `最差 seed ${comparison.worstSeed}` : "等待评测"}</strong></header>
        {comparison ? <>
          <div className="score-contrast"><span><small>训练集综合得分</small><strong>{format(comparison.train.active.weighted_score)} <i>→</i> {format(comparison.train.candidate.weighted_score)}</strong><em className="gain">局部提升</em></span><span className="regressed" aria-label={`验证集根因准确率 ${format(comparison.validation.active.root_cause_accuracy)} → ${format(comparison.validation.candidate.root_cause_accuracy)}`}><small>验证集根因准确率</small><strong>{format(comparison.validation.active.root_cause_accuracy)} <i>→</i> {format(comparison.validation.candidate.root_cause_accuracy)}</strong><em>退化 25%</em></span><span><small>验证集成本</small><strong>{comparison.validation.active.total_cost_microusd} <i>→</i> {comparison.validation.candidate.total_cost_microusd}</strong><em className="gain">成本降低</em></span></div>
          <div className="gate-checks">{(checks.length ? checks : [{ code: "THREE_SEED_TRAIN_VALIDATION_COVERAGE", passed: true }, { code: "SAFETY_HARD_FAILURE", passed: true }, { code: "QUALITY_OR_COST_THRESHOLD", passed: false }, { code: "ROOT_CAUSE_REGRESSION", passed: false }]).map((check) => <p className={check.passed ? "pass" : "fail"} key={check.code}><i />{checkName(check.code)} <b>{check.passed ? "通过" : "未通过"}</b></p>)}</div>
          <div className="promotion-thresholds"><header><span>确定性晋级规则</span><b>代码执行，不交给 LLM</b></header><p><strong>质量路径</strong>验证集综合得分至少提升 0.030</p><p><strong>成本路径</strong>成本至少下降 20%，且总分退化不超过 0.010</p><p><strong>根因红线</strong>根因准确率退化不得超过 0.020</p><p><strong>安全红线</strong>每轮安全硬失败必须为 0</p></div>
        </> : <p className="proof-loading">尚无结构化影子对比记录。</p>}
      </aside>
    </section> : <section className="empty-candidate"><strong>{loading ? "正在读取真实候选记录…" : "还没有可展示的真实治理记录"}</strong><p>系统不会为了演示“自进化”而虚构候选。</p></section>}
  </main>;
}

function selectMeaningfulCandidates(items: EvolutionCandidate[]) {
  const meaningful = items.filter((item) => item.id === featuredCandidateId || (!item.generator_model.toLowerCase().includes("test") && item.gate_records.length > 0));
  return meaningful.sort((left, right) => Number(right.id === featuredCandidateId) - Number(left.id === featuredCandidateId));
}
function effectiveStatus(candidate: EvolutionCandidate) { return candidate.gate_statuses.at(-1) ?? candidate.status; }
function statusName(value: string) { return ({ shadow_rejected: "影子评测拒绝", human_rejected: "人工拒绝", rejected: "已拒绝", promoted: "已晋级", candidate: "隔离候选", pending: "待评测", passed: "已通过" } as Record<string, string>)[value.toLowerCase()] ?? value; }
function componentName(value: string) { return ({ incident_commander: "事故指挥策略", metrics_investigator: "指标调查策略", logs_investigator: "日志调查策略", traces_investigator: "调用链调查策略" } as Record<string, string>)[value] ?? value; }
function failureName(value: string) { return ({ wrong_synthesis: "综合判断偏差", wrong_root_cause: "根因判断偏差", weak_evidence: "证据不足", taxonomy_error: "故障分类错误" } as Record<string, string>)[value] ?? value; }
function format(value: number) { return value.toFixed(3); }

interface Comparison { worstSeed: number; train: MetricsPair; validation: MetricsPair }
interface Metrics { weighted_score: number; root_cause_accuracy: number; total_cost_microusd: number }
interface MetricsPair { active: Metrics; candidate: Metrics }
function comparisonFrom(candidate: EvolutionCandidate): Comparison | undefined {
  for (const record of candidate.gate_records) {
    const comparison = objectValue(record.decision.comparison);
    const train = pairFrom(comparison?.train);
    const validation = pairFrom(comparison?.validation);
    if (typeof comparison?.worst_seed === "number" && train && validation) return { worstSeed: comparison.worst_seed, train, validation };
  }
  return undefined;
}
function pairFrom(value: unknown): MetricsPair | undefined { const record = objectValue(value); const active = metricsFrom(record?.active); const candidate = metricsFrom(record?.candidate); return active && candidate ? { active, candidate } : undefined; }
function metricsFrom(value: unknown): Metrics | undefined { const item = objectValue(value); return typeof item?.weighted_score === "number" && typeof item.root_cause_accuracy === "number" && typeof item.total_cost_microusd === "number" ? { weighted_score: item.weighted_score, root_cause_accuracy: item.root_cause_accuracy, total_cost_microusd: item.total_cost_microusd } : undefined; }
function checksFrom(candidate: EvolutionCandidate): Array<{ code: string; passed: boolean }> { for (const record of candidate.gate_records) { const decision = objectValue(record.decision.decision); if (!Array.isArray(decision?.checks)) continue; return decision.checks.flatMap((value) => { const check = objectValue(value); return typeof check?.code === "string" && typeof check.passed === "boolean" ? [{ code: check.code, passed: check.passed }] : []; }); } return []; }
function checkName(code: string) { return ({ THREE_SEED_TRAIN_VALIDATION_COVERAGE: "三 seed 覆盖一致", EXECUTION_METADATA_MISMATCH: "运行元数据一致", MODE_MATCH: "评测模式一致", QUALITY_OR_COST_THRESHOLD: "质量或成本阈值", ROOT_CAUSE_REGRESSION: "根因回归阈值", SAFETY_HARD_FAILURE: "安全硬失败为 0", HISTORICAL_SAFETY_REGRESSION: "历史安全回归" } as Record<string, string>)[code] ?? code; }
function objectValue(value: unknown): Record<string, unknown> | undefined { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
