# Chaos Run Log (R5)

- **Mode**: SIMULATED provider (offline)
- **Failure rate**: 60% synthetic 429/503/timeouts | **Seed**: 42
- **Injected failures (total)**: 109

## Phase 1: chaos on every model call

| # | Prompt | Served tier | Injected failures | Tests passed | Ticket | Answered |
|---|---|---|---|---|---|---|
| 1 | Write an is_palindrome function | `fallback` | 5 | ✅ | — | ✅ |
| 2 | Calculate the factorial of a number | `template` | 13 | ✅ | — | ✅ |
| 3 | Generate the nth fibonacci number | `fallback` | 10 | ✅ | — | ✅ |
| 4 | Implement fizzbuzz for numbers 1 to n | `fallback` | 5 | ✅ | — | ✅ |
| 5 | Calculate the sum of a list of numbers | `fallback` | 9 | ✅ | — | ✅ |
| 6 | Reverse a given string | `fallback` | 14 | ✅ | — | ✅ |
| 7 | Check if an integer is a prime number | `fallback` | 12 | ✅ | — | ✅ |
| 8 | Find the maximum value in a sequence | `primary` | 4 | ✅ | — | ✅ |
| 9 | Write an is_palindrome function | `cache` | 0 | ✅ | — | ✅ |
| 10 | Calculate the factorial of a number | `cache` | 0 | ✅ | — | ✅ |
| 11 | Generate the nth fibonacci number | `cache` | 0 | ✅ | — | ✅ |
| 12 | Implement fizzbuzz sequence | `fallback` | 6 | ✅ | — | ✅ |
| 13 | Calculate sum of numbers in list | `cache` | 0 | ✅ | — | ✅ |
| 14 | Reverse an input string | `template` | 12 | ✅ | — | ✅ |
| 15 | Check if number is prime | `fallback` | 7 | ✅ | — | ✅ |
| 16 | Find the maximum number in list | `fallback` | 5 | ✅ | — | ✅ |
| 17 | Write an is_palindrome function | `cache` | 0 | ✅ | — | ✅ |
| 18 | Calculate factorial | `cache` | 0 | ✅ | — | ✅ |
| 19 | Fibonacci sequence generator | `primary` | 3 | ✅ | — | ✅ |
| 20 | Find max in a list | `primary` | 4 | ✅ | — | ✅ |

**Result: 20/20 requests answered** (tier distribution: cache=6, fallback=9, primary=3, template=2).
Final breaker states: {'primary': 'CLOSED', 'fallback': 'CLOSED'}

## Phase 2: total outage (TOTAL_OUTAGE=true)

| # | Prompt | Served tier | Tests passed | Ticket | Answered |
|---|---|---|---|---|---|
| 1 | Write an is_palindrome function | `template` | ✅ | — | ✅ |
| 2 | Calculate the factorial of a number | `template` | ✅ | — | ✅ |
| 3 | Generate the nth fibonacci number | `template` | ✅ | — | ✅ |
| 4 | Implement fizzbuzz for numbers 1 to n | `template` | ✅ | — | ✅ |
| 5 | Calculate the sum of a list of numbers | `template` | ✅ | — | ✅ |
| 6 | Implement an advanced ray tracing BVH algorithm | `refused` | — | tkt_90f78b55 | ✅ |
| 7 | Write a lock-free concurrent skip list | `refused` | — | tkt_5f0cf6e9 | ✅ |

- Template tasks return code + passing tests during outage: **YES**
- Unknown tasks get an honest refusal + ticket (never invented code): **YES**

> ⚠️ SIMULATED PROVIDER: no API key was set, so a deterministic stand-in model answered template-matching tasks. The chaos injection, retries, breakers and fallback are the real CodeForge code paths; model quality is not measured here.
