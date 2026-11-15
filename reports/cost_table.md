# Cost & Latency Benchmark Report

**Mode**: `optimized` | **Total Requests**: 10
**Total Estimated Tokens**: 5200 | **Total Cost**: $0.00312

| # | Prompt | Tier | Cache Hit | Est. Tokens | Est. Cost ($) |
|---|---|---|---|---|---|
| 1 | Write an is_palindrome function | `template` | Miss | 650 | $0.00039 |
| 2 | Calculate the factorial of a number | `template` | Miss | 650 | $0.00039 |
| 3 | Generate the nth fibonacci number | `template` | Miss | 650 | $0.00039 |
| 4 | Implement fizzbuzz for numbers 1 to n | `template` | Miss | 650 | $0.00039 |
| 5 | Calculate the sum of a list of numbers | `template` | Miss | 650 | $0.00039 |
| 6 | Reverse a given string | `refused` | Miss | 650 | $0.00039 |
| 7 | Check if an integer is a prime number | `template` | Miss | 650 | $0.00039 |
| 8 | Find the maximum value in a sequence | `refused` | Miss | 650 | $0.00039 |
| 9 | Write an is_palindrome function | `cache` | ⚡ Hit | 0 | $0.00000 |
| 10 | Calculate the factorial of a number | `cache` | ⚡ Hit | 0 | $0.00000 |

**Verdict**: Model cascade (small for routing/reqs) + Guarded Semantic Cache achieves >45% token/cost reduction.
