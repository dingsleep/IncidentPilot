# Local performance baseline

- Sample time: 2026-08-01 22:48:19 +0800
- Concurrency: 5; samples per endpoint: 25
- Model profile: HTTP/MCP: no model; Worker graph: scripted E2E agents
- Hardware environment: Windows-10-10.0.19045-SP0; CPU logical cores=16; Python=3.12.13; Docker=29.6.1
- API is a five-concurrent-read incident-list request; database is a pooled `SELECT 1`.

| Path | Samples | p50 ms | p95 ms |
|---|---:|---:|---:|
| API incident list (non-LLM) | 25 | 24.6 | 84.5 |
| PostgreSQL pooled read | 25 | 4.2 | 15.8 |
| SSE first event | 1 | 17.2 | 17.2 |
| Telemetry MCP probe | 25 | 217.1 | 347.4 |
| Job enqueue to claim | 5 | 18.9 | 108.1 |
| Worker graph E2E wall | 5 | 6810.0 | 7000.0 |

## Pool and memory

- PostgreSQL pool stats: `{'connections_num': 5, 'requests_num': 25, 'requests_queued': 25, 'connections_ms': 43, 'requests_wait_ms': 100, 'usage_ms': 45, 'pool_min': 1, 'pool_max': 5, 'pool_size': 5, 'pool_available': 4, 'requests_waiting': 0}`
- Memory samples: 7 at 15s intervals
- Persistent-growth verdict: PASS

### First snapshot

```text
otel-collector 194.2MiB / 200MiB
frontend-proxy 46.13MiB / 65MiB
frontend 113.8MiB / 250MiB
checkout 10.95MiB / 20MiB
flagd 50.97MiB / 75MiB
incidentpilot-db 90.06MiB / 15.54GiB
load-generator 1.129GiB / 1.465GiB
product-reviews 86.93MiB / 100MiB
recommendation 50.35MiB / 500MiB
fraud-detection 294.8MiB / 300MiB
product-catalog 12.41MiB / 20MiB
payment 120.1MiB / 140MiB
flagd-ui 196.1MiB / 200MiB
image-provider 7.191MiB / 120MiB
cart 57.56MiB / 160MiB
email 66.45MiB / 100MiB
shipping 10.5MiB / 20MiB
quote 27.12MiB / 40MiB
ad 291.7MiB / 300MiB
currency 12.39MiB / 20MiB
accounting 148.6MiB / 160MiB
llm 44.97MiB / 50MiB
postgresql 56.36MiB / 80MiB
kafka 593.6MiB / 620MiB
grafana 117.3MiB / 175MiB
prometheus 138.6MiB / 200MiB
jaeger 354.4MiB / 1.172GiB
opensearch 1018MiB / 1GiB
valkey-cart 7.773MiB / 20MiB
```

### Final snapshot

```text
otel-collector 160.3MiB / 200MiB
frontend-proxy 46.38MiB / 65MiB
frontend 113.6MiB / 250MiB
checkout 12.06MiB / 20MiB
flagd 53.14MiB / 75MiB
incidentpilot-db 90.84MiB / 15.54GiB
load-generator 1022MiB / 1.465GiB
product-reviews 87.7MiB / 100MiB
recommendation 50.36MiB / 500MiB
fraud-detection 294.9MiB / 300MiB
product-catalog 12.05MiB / 20MiB
payment 120.1MiB / 140MiB
flagd-ui 196.1MiB / 200MiB
image-provider 7.191MiB / 120MiB
cart 57.87MiB / 160MiB
email 66.42MiB / 100MiB
shipping 10.5MiB / 20MiB
quote 27.12MiB / 40MiB
ad 291.9MiB / 300MiB
currency 12.39MiB / 20MiB
accounting 148.6MiB / 160MiB
llm 45.21MiB / 50MiB
postgresql 58.53MiB / 80MiB
kafka 595.2MiB / 620MiB
grafana 106.6MiB / 175MiB
prometheus 139.3MiB / 200MiB
jaeger 353.4MiB / 1.172GiB
opensearch 1002MiB / 1GiB
valkey-cart 8.273MiB / 20MiB
```

A dash means that endpoint was not supplied and is not a passing measurement. M8.4 requires an authenticated MCP probe, an existing incident SSE URL, and a real worker run before its complete performance gate can be checked.
