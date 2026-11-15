# Cost & Latency Benchmark (R4)

- **Mode**: SIMULATED provider (offline)
- **Script**: fixed 20-request script (`evals/cost_script.json`)
- **Source of numbers**: `metrics` table written by the metering callback (tokens from provider `usage_metadata`; prices from `config.yaml`)

## Before: baseline (large model everywhere, no cache)

| Agent | Calls | Input tok | Output tok | Cost (USD) |
|---|---|---|---|---|
| codegen | 20 | 3752 | 1175 | $0.003142 |
| evaluator | 20 | 4490 | 560 | $0.003092 |
| requirements | 20 | 2388 | 640 | $0.001915 |
| router | 20 | 2748 | 300 | $0.001858 |
| testgen | 20 | 3802 | 1160 | $0.003160 |
| **total** | **100** | | | **$0.013166** |

## After: optimized (model cascade + guarded semantic cache)

| Agent | Calls | Input tok | Output tok | Cost (USD) |
|---|---|---|---|---|
| codegen | 9 | 1690 | 513 | $0.001402 |
| evaluator | 9 | 2002 | 252 | $0.000120 |
| requirements | 9 | 1076 | 290 | $0.000077 |
| router | 9 | 1238 | 135 | $0.000073 |
| testgen | 9 | 1713 | 517 | $0.001419 |
| **total** | **45** | | | **$0.003091** |

## Comparison

| Metric | Baseline | Optimized |
|---|---|---|
| Total cost (USD) | $0.013166 | $0.003091 |
| Total tokens | 21015 | 9426 |
| Model calls | 100 | 45 |
| Cache hits | 0 | 11 |
| Wall latency p50 / p95 (s) | 0.18 / 0.35 | 0.32 / 0.53 |
| Hidden-reference pass rate | 20/20 (100%) | 20/20 (100%) |

**Saving: 76.5%** | quality drop: **NO** | target (>=30% cheaper, no drop): **MET**

> ⚠️ SIMULATED PROVIDER: token counts are estimated from text length (~4 chars/token) because no API key was set. Re-run with `GROQ_API_KEY` for real usage numbers.
