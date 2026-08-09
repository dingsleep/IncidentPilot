# Known limitations

Date: 2026-08-08

- **Demo versus production.** The system is validated against the local
  OpenTelemetry Demo. Its traffic shape, telemetry completeness, service
  catalog, fault mechanics, and tenancy do not represent a production estate.
- **Compose control boundary.** The core stack is local-only and binds host
  ports to loopback. The optional write profile requires explicit local keys,
  exposes only mapping-backed flagd rollback, and does not mount a Docker
  Socket; no public Action MCP or target-control credential is exposed.
- **Model variability.** Provider structured-output behavior and diagnosis
  quality can vary. Public results are versioned evidence, not a promise that a
  different model, prompt, seed, or incident will perform identically.
- **Public validation scope.** The frozen v15 candidate reached aggregate,
  root-cause accuracy, and Evidence fidelity `1.000` on three seeds of four
  public validation cases, with no safety hard failures. This is still a small
  local Demo suite and is not evidence of broad incident, provider, topology,
  or production generalization. Earlier failed candidates remain recorded in
  the final evaluation report.
- **Evaluation coverage.** Public train/validation results are not a private
  holdout result. The private package is unavailable and has not been read,
  decrypted, searched, or run. Once a holdout is observed, that suite must not
  be tuned against.
- **GenAI telemetry semantics.** Current GenAI semantic-convention fields are
  development instrumentation. They are bounded and redacted, but should not be
  treated as a stable cross-vendor production standard without review.
- **Operational scale.** PostgreSQL, the queue, and Compose are suitable for a
  local reference workflow, not a demonstrated HA, multi-region, or large-scale
  production deployment.
- **Video evidence.** The repository has no recorded real-fault GIF/video yet.
  Readers should run the documented local demo instead of relying on a mock or
  edited recording.
