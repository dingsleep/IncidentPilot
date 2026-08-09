from enum import StrEnum


class Severity(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    SYNTHESIZING = "SYNTHESIZING"
    DIAGNOSED = "DIAGNOSED"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    AUTHORIZING = "AUTHORIZING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    ROLLING_BACK = "ROLLING_BACK"
    RESOLVED = "RESOLVED"
    RESOLVED_READ_ONLY = "RESOLVED_READ_ONLY"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    POLICY_REJECTED = "POLICY_REJECTED"
    ACTION_FAILED = "ACTION_FAILED"
    REJECTED = "REJECTED"
    REPORTING = "REPORTING"


class EvidenceKind(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    TOPOLOGY = "topology"
    RUNBOOK = "runbook"
    CHANGE = "change"


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionMode(StrEnum):
    REVIEW = "review"
    SAFE_AUTO = "safe_auto"
    READ_ONLY = "read_only"
