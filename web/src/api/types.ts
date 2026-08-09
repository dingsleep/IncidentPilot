import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type ApiPaths = paths;
export type Evidence = Schemas["EvidenceView"];
export type EvidenceKind = Schemas["EvidenceKind"];
export type Incident = Schemas["IncidentView"];
export type IncidentPage = Schemas["IncidentPage"];
export type IncidentStatus = Schemas["IncidentStatus"];
export type ManualIncidentRequest = Schemas["ManualIncidentRequest"];
export type Severity = Schemas["Severity"];
export type TimelineEvent = Schemas["TimelineEventView"];
export type EvaluationRun = Schemas["EvaluationRunView"];
export type EvaluationRunDetail = Schemas["EvaluationRunDetail"];
export type EvaluationCase = Schemas["EvaluationCaseView"];
export type CaseScore = Schemas["EvaluationCaseMetricsView"];
export type ActionProposalView = Schemas["ActionProposalView"];
export type ApprovalRequest = Schemas["ApprovalRequest"];
export interface EvolutionCandidate {
  id: string; kind: string; base_version: string; target_failure_label: string;
  target_component: string; generator_model: string; digest: string; status: string; diff: string;
  gate_statuses: string[]; rejection_reasons: string[];
  gate_records: Array<{ status: string; decision: Record<string, unknown>; human_rejection_reason: string | null }>;
}

export interface IncidentFilters {
  cursor?: string;
  limit?: number;
  severity?: Severity;
  status?: IncidentStatus;
  service?: string;
  createdFrom?: string;
  createdTo?: string;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  code?: string;
  correlation_id?: string;
}

export interface HypothesisView {
  id: string;
  root_cause_service: string;
  failure_mode: string;
  confidence: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids?: string[];
  missing_evidence?: string[];
}

export interface DiagnosisView {
  symptom_service: string;
  root_cause_service: string;
  dependency_service?: string | null;
  root_cause_category: string;
  root_cause_summary: string;
  confidence: number;
  evidence_ids: string[];
  customer_impact: string;
  diagnosis_limits?: string[];
}

export interface WorkbenchReport {
  incident_id: string;
  status?: IncidentStatus;
  evidence_ids?: string[];
  tool_call_ids?: string[];
  hypotheses?: HypothesisView[];
  diagnosis?: DiagnosisView;
  reports?: Array<{
    wave: number;
    report: {
      investigator: string;
      scope_services: string[];
    };
  }>;
}
