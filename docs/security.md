# Security regression boundary

`tests/security/` is a hard regression gate for candidate changes. Candidate promotion must run this suite together with the authorization integration cases; failure blocks promotion.

Evidence and runbook text are untrusted data, never authority for an action. Prompt context limits each evidence summary and replaces external URL schemes with `untrusted://external-reference`; the application has no arbitrary URL-fetching tool. Read-only investigators receive only their explicit allowlist; Action MCP requires an approval grant with the exact incident and action scope.

Cross-incident Evidence IDs, hidden scenario identifiers, forged Tool Envelopes, modified approval parameters, and replayed grants are rejected by deterministic code. The security gate exercises injection and long-input isolation in `tests/security/`, proposal-digest tampering in `tests/integration/test_authorization_gate.py`, and approval nonce consumption/idempotent replay handling in `tests/integration/test_action_mcp_authorization.py`.
