import type {
  Evidence,
  EvaluationRun,
  EvaluationRunDetail,
  ActionProposalView,
  ApprovalRequest,
  EvolutionCandidate,
  Incident,
  IncidentFilters,
  IncidentPage,
  ManualIncidentRequest,
  ProblemDetails,
  TimelineEvent,
} from "./types";

export interface ApiClientOptions {
  actorId?: string;
  baseUrl?: string;
  correlationId?: () => string;
  fetchImpl?: typeof fetch;
}

export class ApiProblemError extends Error {
  readonly correlationId: string;
  readonly problem: ProblemDetails;

  constructor(problem: ProblemDetails, correlationId: string) {
    super(problem.detail || problem.title);
    this.name = "ApiProblemError";
    this.problem = problem;
    this.correlationId = correlationId;
  }
}

export function createApiClient(options: ApiClientOptions = {}) {
  const actorId = options.actorId ?? "local-viewer";
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const correlationId = options.correlationId ?? (() => crypto.randomUUID());
  const fetchImpl = options.fetchImpl ?? fetch;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const requestCorrelationId = correlationId();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-IncidentPilot-Actor", actorId);
    headers.set("X-Correlation-ID", requestCorrelationId);
    if (init.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetchImpl(`${baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      const problem = await readProblem(response);
      throw new ApiProblemError(
        problem,
        response.headers.get("X-Correlation-ID") ??
          problem.correlation_id ??
          requestCorrelationId,
      );
    }
    return (await response.json()) as T;
  }

  return {
    createIncident(payload: Omit<ManualIncidentRequest, "start_analysis" | "execution_mode"> & {
      start_analysis?: boolean;
      execution_mode?: ManualIncidentRequest["execution_mode"];
    }) {
      return request<{ incident: Incident; job_id: string | null }>("/incidents", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async startDemoRun(incidentId: string, scenarioId: string) {
      const response = await fetchImpl("/demo-api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ incident_id: incidentId, scenario_id: scenarioId }),
      });
      if (!response.ok) throw new Error("真实演示环境启动失败");
      return response.json() as Promise<{
        incident_id: string; scenario_id: string; status: string; real_execution: boolean;
      }>;
    },
    decideApproval(incidentId: string, proposalId: string, decision: ApprovalRequest) {
      return request<{ approval_id: string; job_id: string }>(
        `/incidents/${encodeURIComponent(incidentId)}/proposals/${encodeURIComponent(proposalId)}/approval`,
        { method: "POST", body: JSON.stringify(decision) },
      );
    },
    getActionProposal(incidentId: string, proposalId: string) {
      return request<ActionProposalView>(
        `/incidents/${encodeURIComponent(incidentId)}/proposals/${encodeURIComponent(proposalId)}`,
      );
    },
    getCurrentActionProposal(incidentId: string) {
      return request<ActionProposalView | null>(
        `/incidents/${encodeURIComponent(incidentId)}/proposals/current`,
      );
    },
    getEvaluationRun(runId: string) {
      return request<EvaluationRunDetail>(`/evaluations/runs/${encodeURIComponent(runId)}`);
    },
    getEvidence(incidentId: string, evidenceId: string) {
      return request<Evidence>(
        `/incidents/${encodeURIComponent(incidentId)}/evidence/${encodeURIComponent(evidenceId)}`,
      );
    },
    getIncident(incidentId: string) {
      return request<Incident>(`/incidents/${encodeURIComponent(incidentId)}`);
    },
    listEvidence(incidentId: string) {
      return request<Evidence[]>(`/incidents/${encodeURIComponent(incidentId)}/evidence`);
    },
    listEvaluationRuns(limit = 50) {
      return request<EvaluationRun[]>(`/evaluations/runs?limit=${encodeURIComponent(limit)}`);
    },
    listEvolutionCandidates() {
      return request<EvolutionCandidate[]>("/evolution/candidates");
    },
    listIncidents(filters: IncidentFilters = {}) {
      const query = new URLSearchParams();
      setQuery(query, "cursor", filters.cursor);
      setQuery(query, "limit", filters.limit);
      setQuery(query, "severity", filters.severity);
      setQuery(query, "status", filters.status);
      setQuery(query, "service", filters.service);
      setQuery(query, "created_from", filters.createdFrom);
      setQuery(query, "created_to", filters.createdTo);
      const suffix = query.size === 0 ? "" : `?${query.toString()}`;
      return request<IncidentPage>(`/incidents${suffix}`);
    },
    listTimeline(incidentId: string) {
      return request<TimelineEvent[]>(`/incidents/${encodeURIComponent(incidentId)}/timeline`);
    },
  };
}

export const api = createApiClient();

function setQuery(
  query: URLSearchParams,
  key: string,
  value: string | number | undefined,
): void {
  if (value !== undefined) query.set(key, String(value));
}

async function readProblem(response: Response): Promise<ProblemDetails> {
  try {
    const value = (await response.json()) as Partial<ProblemDetails>;
    return {
      type: value.type ?? "about:blank",
      title: value.title ?? response.statusText ?? "Request failed",
      status: value.status ?? response.status,
      detail: value.detail ?? "The API request could not be completed.",
      code: value.code,
      correlation_id: value.correlation_id,
    };
  } catch {
    return {
      type: "about:blank",
      title: response.statusText || "Request failed",
      status: response.status,
      detail: "The API returned an unreadable error response.",
    };
  }
}
