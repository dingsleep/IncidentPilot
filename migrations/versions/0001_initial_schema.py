"""Initial online schema and least-privilege grants."""

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS langgraph_checkpoint AUTHORIZATION graph_worker_role;

        CREATE TABLE tenants (
            id varchar(64) PRIMARY KEY,
            name varchar(200) NOT NULL UNIQUE
        );
        CREATE TABLE actors (
            id varchar(64) PRIMARY KEY,
            tenant_id varchar(64) NOT NULL REFERENCES tenants(id),
            display_name varchar(200) NOT NULL,
            role varchar(32) NOT NULL
        );
        CREATE TABLE incidents (
            id varchar(64) PRIMARY KEY,
            tenant_id varchar(64) NOT NULL REFERENCES tenants(id),
            source varchar(100) NOT NULL,
            external_id varchar(200) NOT NULL,
            status varchar(32) NOT NULL,
            severity varchar(8) NOT NULL,
            title varchar(500) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_incident_source UNIQUE (tenant_id, source, external_id)
        );
        CREATE TABLE alerts (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            payload_json jsonb NOT NULL,
            received_at timestamptz NOT NULL
        );
        CREATE TABLE evidence (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            kind varchar(32) NOT NULL,
            source_system varchar(100) NOT NULL,
            summary text NOT NULL,
            query_json jsonb NOT NULL,
            raw_json jsonb,
            digest varchar(64) NOT NULL,
            source_uri text,
            observed_start timestamptz NOT NULL,
            observed_end timestamptz NOT NULL,
            truncated boolean NOT NULL DEFAULT false,
            collected_at timestamptz NOT NULL
        );
        CREATE TABLE hypotheses (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            wave integer NOT NULL,
            payload_json jsonb NOT NULL
        );
        CREATE TABLE diagnoses (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            payload_json jsonb NOT NULL,
            model_profile varchar(100) NOT NULL,
            prompt_version varchar(100) NOT NULL
        );
        CREATE TABLE action_proposals (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            payload_json jsonb NOT NULL,
            status varchar(32) NOT NULL,
            policy_result_json jsonb NOT NULL
        );
        CREATE TABLE approvals (
            id varchar(64) PRIMARY KEY,
            proposal_id varchar(64) NOT NULL REFERENCES action_proposals(id),
            actor_id varchar(64) NOT NULL REFERENCES actors(id),
            decision varchar(16) NOT NULL,
            reason text NOT NULL,
            expires_at timestamptz,
            grant_jws text,
            grant_digest varchar(64),
            nonce_used_at timestamptz
        );
        CREATE TABLE action_executions (
            id varchar(64) PRIMARY KEY,
            proposal_id varchar(64) NOT NULL REFERENCES action_proposals(id),
            idempotency_key varchar(200) NOT NULL,
            status varchar(32) NOT NULL,
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            result_json jsonb NOT NULL,
            CONSTRAINT uq_action_idempotency UNIQUE (idempotency_key)
        );
        CREATE TABLE verification_results (
            id varchar(64) PRIMARY KEY,
            execution_id varchar(64) NOT NULL REFERENCES action_executions(id),
            payload_json jsonb NOT NULL
        );
        CREATE TABLE audit_events (
            id varchar(64) PRIMARY KEY,
            tenant_id varchar(64) NOT NULL REFERENCES tenants(id),
            incident_id varchar(64) REFERENCES incidents(id),
            actor_type varchar(32) NOT NULL,
            actor_id varchar(64) NOT NULL,
            event_type varchar(100) NOT NULL,
            payload_json jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            prev_hash varchar(64),
            event_hash varchar(64) NOT NULL
        );
        CREATE TABLE analysis_jobs (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            job_type varchar(16) NOT NULL,
            resume_reference_id varchar(64),
            status varchar(32) NOT NULL,
            lease_owner varchar(100),
            lease_expires_at timestamptz,
            attempts integer NOT NULL DEFAULT 0,
            available_at timestamptz NOT NULL
        );
        CREATE TABLE service_heartbeats (
            process_name varchar(100) NOT NULL,
            instance_id varchar(100) NOT NULL,
            status varchar(32) NOT NULL,
            details_json jsonb NOT NULL,
            last_seen_at timestamptz NOT NULL,
            PRIMARY KEY (process_name, instance_id)
        );
        CREATE TABLE tool_calls (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            agent_name varchar(100) NOT NULL,
            tool_name varchar(100) NOT NULL,
            args_digest varchar(64) NOT NULL,
            result_digest varchar(64),
            duration_ms integer NOT NULL,
            status varchar(32) NOT NULL
        );
        CREATE TABLE model_calls (
            id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL REFERENCES incidents(id),
            agent_name varchar(100) NOT NULL,
            model_profile varchar(100) NOT NULL,
            input_tokens integer NOT NULL,
            output_tokens integer NOT NULL,
            cost_microusd integer NOT NULL,
            duration_ms integer NOT NULL,
            status varchar(32) NOT NULL
        );
        CREATE TABLE prompt_versions (
            id varchar(64) PRIMARY KEY,
            agent_name varchar(100) NOT NULL,
            version varchar(100) NOT NULL,
            content_digest varchar(64) NOT NULL,
            status varchar(32) NOT NULL
        );
        CREATE TABLE runbook_versions (
            id varchar(100) NOT NULL,
            version varchar(100) NOT NULL,
            content text NOT NULL,
            digest varchar(64) NOT NULL,
            metadata_json jsonb NOT NULL,
            PRIMARY KEY (id, version)
        );
        CREATE TABLE change_events (
            id varchar(64) PRIMARY KEY,
            service varchar(100) NOT NULL,
            change_type varchar(64) NOT NULL,
            summary text NOT NULL,
            occurred_at timestamptz NOT NULL
        );
        CREATE TABLE change_event_private_mappings (
            change_id varchar(64) PRIMARY KEY REFERENCES change_events(id),
            mapping_encrypted bytea NOT NULL,
            config_digest varchar(64) NOT NULL
        );

        CALL incidentpilot_apply_table_grants();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE change_event_private_mappings;
        DROP TABLE change_events;
        DROP TABLE runbook_versions;
        DROP TABLE prompt_versions;
        DROP TABLE model_calls;
        DROP TABLE tool_calls;
        DROP TABLE service_heartbeats;
        DROP TABLE analysis_jobs;
        DROP TABLE audit_events;
        DROP TABLE verification_results;
        DROP TABLE action_executions;
        DROP TABLE approvals;
        DROP TABLE action_proposals;
        DROP TABLE diagnoses;
        DROP TABLE hypotheses;
        DROP TABLE evidence;
        DROP TABLE alerts;
        DROP TABLE incidents;
        DROP TABLE actors;
        DROP TABLE tenants;
        DROP SCHEMA IF EXISTS langgraph_checkpoint;
        DROP EXTENSION IF EXISTS vector;
        """
    )
