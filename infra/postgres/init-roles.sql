\set ON_ERROR_STOP on

REVOKE ALL ON DATABASE incidentpilot FROM PUBLIC;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'incident_api_role') THEN
    CREATE ROLE incident_api_role LOGIN PASSWORD 'api-local-only';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'graph_worker_role') THEN
    CREATE ROLE graph_worker_role LOGIN PASSWORD 'worker-local-only';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'telemetry_mcp_role') THEN
    CREATE ROLE telemetry_mcp_role LOGIN PASSWORD 'telemetry-local-only';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'action_mcp_role') THEN
    CREATE ROLE action_mcp_role LOGIN PASSWORD 'action-local-only';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'evaluation_role') THEN
    CREATE ROLE evaluation_role LOGIN PASSWORD 'evaluation-local-only';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE incidentpilot TO
  incident_api_role,
  graph_worker_role,
  telemetry_mcp_role,
  action_mcp_role,
  evaluation_role;

CREATE OR REPLACE PROCEDURE incidentpilot_apply_table_grants()
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  IF to_regclass('public.tenants') IS NULL THEN
    RETURN;
  END IF;

  REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
  GRANT USAGE ON SCHEMA public TO incident_api_role, graph_worker_role,
    telemetry_mcp_role, action_mcp_role, evaluation_role;
  GRANT USAGE, CREATE ON SCHEMA langgraph_checkpoint TO graph_worker_role;

  GRANT SELECT ON tenants, actors, evidence, hypotheses, diagnoses,
    action_executions, verification_results, service_heartbeats,
    prompt_versions, runbook_versions, runbook_sections, change_events TO incident_api_role;
  GRANT SELECT, INSERT, UPDATE ON incidents, alerts, action_proposals,
    approvals, analysis_jobs, audit_events TO incident_api_role;

  GRANT SELECT ON tenants, actors, alerts, evidence, action_proposals,
    approvals, runbook_versions, runbook_sections, change_events,
    action_executions, verification_results, audit_events TO graph_worker_role;
  GRANT SELECT, INSERT, UPDATE ON incidents, hypotheses, diagnoses,
    action_proposals, analysis_jobs, service_heartbeats, tool_calls, model_calls
    TO graph_worker_role;
  GRANT INSERT ON audit_events TO graph_worker_role;

  GRANT SELECT ON incidents, runbook_versions, runbook_sections, change_events
    TO telemetry_mcp_role;
  GRANT SELECT, INSERT ON evidence, tool_calls TO telemetry_mcp_role;
  GRANT SELECT, INSERT, UPDATE ON service_heartbeats TO telemetry_mcp_role;

  GRANT SELECT ON incidents, action_proposals, approvals, change_events,
    change_event_private_mappings TO action_mcp_role;
  GRANT UPDATE (nonce_used_at) ON approvals TO action_mcp_role;
  GRANT SELECT, INSERT, UPDATE ON action_executions, verification_results
    TO action_mcp_role;
  GRANT SELECT, INSERT, UPDATE ON service_heartbeats TO action_mcp_role;
  GRANT INSERT ON verification_results TO graph_worker_role;

  GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, actors, incidents,
    alerts, evidence, change_events, change_event_private_mappings
    TO evaluation_role;
  GRANT SELECT ON diagnoses, tool_calls, model_calls, action_proposals,
    approvals, action_executions, verification_results TO evaluation_role;

  IF to_regclass('public.evaluation_runs') IS NOT NULL THEN
    GRANT SELECT, INSERT, UPDATE ON evaluation_runs, evaluation_cases
      TO evaluation_role;
    GRANT SELECT ON evaluation_runs, evaluation_cases TO incident_api_role;
  END IF;
END
$$;

REVOKE ALL ON PROCEDURE incidentpilot_apply_table_grants() FROM PUBLIC;
