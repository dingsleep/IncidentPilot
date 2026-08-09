# Evaluation compare-payment-unreachable-seed11-20260719

Suite: `validation-v1`  
Candidate: `prompts-v1:deepseek-chat`

## Baseline vs multi

| Metric | Baseline | Multi | Delta |
|---|---:|---:|---:|
| Weighted score | 0.400 | 0.400 | +0.000 |
| Root-cause accuracy | 0.000 | 0.000 | +0.000 |
| Evidence fidelity | 1.000 | 1.000 | +0.000 |
| Cost (micro-USD) | 0 | 0 | +0 |
| Duration (ms) | 9131 | 27106 | +17975 |
| Tool calls | 3 | 3 | +0 |

## Failed Episodes

- [baseline/payment-unreachable-001](../eval-baseline-20260719081558-11/report.json) — 0.400; score below 1.0
- [multi/payment-unreachable-001](../eval-multi-20260719081712-11/report.json) — 0.400; score below 1.0
