# Final evaluation status

Date: 2026-08-08  
Scope: frozen public validation, local engineering regression, and explicit release boundaries

## Result

The frozen v15 public candidate passed all four validation cases on three
preselected suite seeds. The local engineering regression is green. The project
does **not** claim a final private-holdout result: the holdout was **not run**,
not read, not decrypted, and not searched during this closure. M11 therefore
remains in progress rather than accepted.

## Current local regression (2026-08-08)

| Gate | Actual command/result |
|---|---|
| Python | `D:\software\ana\envs\tx_agent\python.exe -m pytest -q` -> `307 passed, 1 skipped` in `398.17s` |
| Python lint/type | `ruff check src tests` -> passed; `pyright` -> `0 errors` |
| Dependency/schema | `pip check` -> no broken requirements; `alembic check` -> no new upgrade operations |
| Web static/unit/build | typecheck and lint passed; Vitest -> `4 files / 6 tests passed`; production build passed |
| Browser E2E | isolated ports `8202/5181`, `npm run test:e2e` -> `2 passed` |
| Web dependency audit | `npm audit` -> `0 vulnerabilities`; compatible patches only, no `--force` |
| Compose definition | `docker compose --profile core config --quiet` -> passed |

## Historical local regression (2026-08-02)

| Gate | Actual command/result |
|---|---|
| Python | `D:\software\ana\envs\tx_agent\python.exe -m pytest -q` → `300 passed, 1 skipped` in `392.27s` |
| Python lint/type | `ruff check src tests` → passed; `pyright` → `0 errors, 0 warnings, 0 informations` |
| Dependency/schema | `pip check` → no broken requirements; `alembic check` → no new upgrade operations |
| Web static/unit/build | `npm run typecheck`, `npm run lint`, `npm run test` → `6 passed`, `npm run build` → passed |
| Browser E2E | with isolated ports `8202/5181`, `npm run test:e2e` → `2 passed` |
| Core containers | `docker compose --profile core build` → five images built; API, worker, telemetry MCP, Web, and DB health checks passed in an isolated `8201/5180` run |

The browser test uses alternate ports only to coexist with pre-existing local
processes. This is equivalent to the supported local-port override path, not a
deployment configuration change.

## Frozen public validation evidence (v15)

Candidate:
`p1-4d19782f3126:qwen3.7-flash:json_output:q-f4a05b7141c0:t-telemetry-v9:s-v15-a1-t8-m1`  
Suite: `validation-v2-score-v5`  
Profile: `fast`, `qwen3.7-flash`, temperature `0`, JSON structured output,
4,000-token per-call limit, scoped read-only telemetry only.

| Suite seed | Run | Cases | Aggregate | Root | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 64 | `eval-multi-20260807034300-64` | 4/4 | 1.000 | 1.000 | 1.000 | 0 | 2758 | 112510 |
| 71 | `eval-multi-20260807035301-71` | 4/4 | 1.000 | 1.000 | 1.000 | 0 | 2680 | 106421 |
| 79 | `eval-multi-20260808063914-79` | 4/4 | 1.000 | 1.000 | 1.000 | 0 | 3011 | 123489 |

Mean and worst-seed aggregate/root/Evidence are `1.000`; total cost is `8449`
micro-USD and total safety hard failures are `0`. Manual review covered the
product-catalog correlation failure, payment taxonomy ordering, recommendation
recovery-window semantics, and recommendation metric citation. All 15 public
flags were verified `off` after the final run.

These are public development results, not private-holdout or production claims.
The private package is unavailable and was not read, decrypted, searched, or
run. No more model calls were made after freezing these three results.

## Historical public evidence (through 2026-08-02)

The fixed candidate
`p1-9030b15a9d6b:qwen3.7-flash:json_output:q-2f310decbea1:t-telemetry-v9:s-v8-tax4`
recorded public train `eval-multi-20260730015313-41` and public validation
`eval-multi-20260730021852-41`, each `4/4` with aggregate/root/Evidence
`1.0/1.0/1.0` and zero safety hard failures. Its fair public validation
baseline `eval-baseline-20260730023140-41` aggregated `0.679`.

This is a public development result, not proof of generalized production or
holdout performance. The exact scenario/split/scorer boundary and rejected
candidate result are documented in [`../evaluation.md`](../evaluation.md).

## M11 validation recheck (2026-08-02)

The same frozen candidate was re-run on the public validation suite with
`qwen3.7-flash`, `json_output`, and the official DashScope OpenAI-compatible
endpoint. The process-only provider override did not modify `.env`, prompts,
scenario files, thresholds, or the candidate.

| Seed | Run | Aggregate | Root cause | Evidence | Safety failures | Cost (micro-USD) |
|---|---|---:|---:|---:|---:|---:|
| 41 | `eval-multi-20260802142109-41` | 0.9125 | 0.75 | 1.00 | 0 | 2633 |
| 43 | `eval-multi-20260802142837-43` | 0.9125 | 0.75 | 1.00 | 0 | 2812 |
| 47 | `eval-multi-20260802143547-47` | 0.8750 | 0.75 | 1.00 | 0 | 2883 |

This does **not** meet the frozen-evaluation quality bar, so no private
holdout was run. Manual review found that all three cache-leak trajectories
misattributed the root cause to `product-catalog`; the seed-47 cart result had
the correct diagnosis but failed its recovery check. Every run restored the
public flag configuration to `off`. These failures must be addressed through a
new, versioned candidate and independent development samples—not by lowering
the threshold or editing the current validation cases.

## Clean-process delivery verification (2026-08-09)

The pinned OpenTelemetry Demo and the complete local `core/actions` stack were
stopped without deleting volumes and restarted from fresh processes on the
documented `8201/5180` ports. API readiness, database, queue, Worker, Telemetry
MCP, Action MCP, Demo Runner, and Web were healthy; the isolated evaluation
image also completed its no-model `--help` probe. All public fault flags were
`off`, and the browser loaded the four product routes without console, page, or
HTTP errors. This verifies startup and isolation, not a private-holdout result.

## Deliberately uncompleted gates

- Private holdout three-seed evaluation was not performed. Public validation
  passed, but the required private package is unavailable and a holdout run
  requires a separate explicit user task.
- A real public demo GIF/video has not been recorded; no synthetic substitute is
  represented as evidence.
- CI workflows were not created because they require separate user
  authorization under the repository rules.
