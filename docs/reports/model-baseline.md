# Model Baseline

Generated: 2026-07-16T09:25:02.364755+00:00

| Profile | Probe | Passed | Latency ms | Input tokens | Output tokens | Estimated USD |
|---|---|---:|---:|---:|---:|---:|
| strong | tool_selection | True | 1898 | 278 | 21 | 0.000139 |
| strong | parallel_tool_calls | True | 1520 | 322 | 62 | 0.000194 |
| strong | pydantic_schema | True | 1545 | 398 | 88 | 0.000250 |
| strong | error_repair | True | 1777 | 419 | 82 | 0.000254 |
| strong | long_evidence | True | 3066 | 6396 | 122 | 0.002888 |
| fast | tool_selection | True | 854 | 278 | 21 | 0.000045 |
| fast | parallel_tool_calls | True | 897 | 322 | 36 | 0.000055 |
| fast | pydantic_schema | True | 1263 | 398 | 92 | 0.000081 |
| fast | error_repair | True | 1200 | 419 | 100 | 0.000087 |
| fast | long_evidence | True | 1775 | 6396 | 108 | 0.000926 |

## Profile summary

| Profile | Model | Success | p50 ms | p95 ms | Input tokens | Output tokens | Estimated USD |
|---|---|---:|---:|---:|---:|---:|---:|
| fast | `deepseek-v4-flash` | 5/5 (100%) | 1200 | 1775 | 7813 | 357 | 0.001194 |
| strong | `deepseek-v4-pro` | 5/5 (100%) | 1777 | 3066 | 7813 | 375 | 0.003725 |

## Decision

- Use `deepseek-v4-pro` for the `strong` profile and `deepseek-v4-flash` for the
  `fast` profile.
- DeepSeek V4 defaults to thinking mode. These deterministic Tool Strategy
  probes explicitly used non-thinking mode because thinking mode rejects forced
  `tool_choice`; structured results were still validated locally with Pydantic.
- Costs use the 2026-07-16 official cache-miss input and output rates:
  [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing).
- The API key was supplied only through the benchmark process environment and
  was not written to this report, `.env`, source code, or Git.
