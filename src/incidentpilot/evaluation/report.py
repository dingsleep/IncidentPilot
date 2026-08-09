from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from incidentpilot.domain import DomainModel
from incidentpilot.evaluation.metrics import (
    CaseScore,
    ModeComparison,
    RunAggregate,
)


class EvaluationReport(DomainModel):
    run_id: str
    suite_version: str
    candidate_version: str
    baseline: RunAggregate
    multi: RunAggregate
    comparison: ModeComparison
    cases: list[CaseScore]


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path


def write_mode_report(
    *,
    run_id: str,
    suite_version: str,
    candidate_version: str,
    aggregate: RunAggregate,
    cases: list[CaseScore],
    output_root: Path,
) -> ReportPaths:
    target = output_root / run_id
    target.mkdir(parents=True, exist_ok=False)
    payload = {
        "run_id": run_id,
        "suite_version": suite_version,
        "candidate_version": candidate_version,
        "aggregate": aggregate.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    json_path = target / "report.json"
    markdown_path = target / "report.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# Evaluation {run_id}",
        "",
        f"Suite: `{suite_version}`  ",
        f"Candidate: `{candidate_version}`  ",
        f"Mode: `{aggregate.mode}`",
        "",
        "| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {aggregate.weighted_score:.3f} | {aggregate.root_cause_accuracy:.3f} | "
        f"{aggregate.evidence_fidelity:.3f} | {aggregate.safety_hard_failures} | "
        f"{aggregate.total_cost_microusd} | {aggregate.total_duration_ms} |",
        "",
        "## Failed Episodes",
        "",
    ]
    failed = [case for case in cases if case.hard_failures or case.total < 1]
    lines.extend(
        f"- [{case.scenario_id}]({case.trajectory_uri or 'report.json'}) — "
        f"{case.total:.3f}; {', '.join(case.hard_failures) or 'score below 1.0'}"
        for case in failed
    )
    if not failed:
        lines.append("None.")
    markdown_path.write_text("\n".join([*lines, ""]), encoding="utf-8")
    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def write_report(report: EvaluationReport, *, output_root: Path) -> ReportPaths:
    target = output_root / report.run_id
    target.mkdir(parents=True, exist_ok=False)
    json_path = target / "report.json"
    markdown_path = target / "report.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def _markdown(report: EvaluationReport) -> str:
    comparison = report.comparison
    lines = [
        f"# Evaluation {report.run_id}",
        "",
        f"Suite: `{report.suite_version}`  ",
        f"Candidate: `{report.candidate_version}`",
        "",
        "## Baseline vs multi",
        "",
        "| Metric | Baseline | Multi | Delta |",
        "|---|---:|---:|---:|",
        f"| Weighted score | {report.baseline.weighted_score:.3f} | "
        f"{report.multi.weighted_score:.3f} | {comparison.weighted_score_delta:+.3f} |",
        f"| Root-cause accuracy | {report.baseline.root_cause_accuracy:.3f} | "
        f"{report.multi.root_cause_accuracy:.3f} | "
        f"{comparison.root_cause_accuracy_delta:+.3f} |",
        f"| Evidence fidelity | {report.baseline.evidence_fidelity:.3f} | "
        f"{report.multi.evidence_fidelity:.3f} | "
        f"{comparison.evidence_fidelity_delta:+.3f} |",
        f"| Cost (micro-USD) | {report.baseline.total_cost_microusd} | "
        f"{report.multi.total_cost_microusd} | {comparison.cost_microusd_delta:+d} |",
        f"| Duration (ms) | {report.baseline.total_duration_ms} | "
        f"{report.multi.total_duration_ms} | {comparison.duration_ms_delta:+d} |",
        f"| Tool calls | {report.baseline.total_tool_calls} | "
        f"{report.multi.total_tool_calls} | {comparison.tool_call_delta:+d} |",
        "",
        "## Failed Episodes",
        "",
    ]
    failed = [case for case in report.cases if case.hard_failures or case.total < 1]
    if not failed:
        lines.append("None.")
    for case in failed:
        target = case.trajectory_uri or f"report.json#{case.scenario_id}"
        reasons = ", ".join(case.hard_failures) or "score below 1.0"
        lines.append(f"- [{case.mode}/{case.scenario_id}]({target}) — {case.total:.3f}; {reasons}")
    lines.append("")
    return "\n".join(lines)
