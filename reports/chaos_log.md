# Chaos Engineering Benchmark Report

**Environment**: `CHAOS_FAILURE_RATE=0.6` (60% synthetic 429/503/timeout injection)

| # | Prompt | Served Tier | Execution Passed |
|---|---|---|---|
| 1 | Write an is_palindrome function | `template` | ✅ Yes |
| 2 | Calculate the factorial of a number | `template` | ✅ Yes |
| 3 | Generate the nth fibonacci number | `template` | ✅ Yes |
| 4 | Implement fizzbuzz for numbers 1 to n | `template` | ✅ Yes |
| 5 | Calculate the sum of a list of numbers | `template` | ✅ Yes |
| 6 | Reverse a given string | `refused` | ❌ No |
| 7 | Check if an integer is a prime number | `template` | ✅ Yes |
| 8 | Find the maximum value in a sequence | `refused` | ❌ No |
| 9 | Write an is_palindrome function | `template` | ✅ Yes |
| 10 | Calculate the factorial of a number | `template` | ✅ Yes |
| 11 | Generate the nth fibonacci number | `template` | ✅ Yes |
| 12 | Implement fizzbuzz sequence | `template` | ✅ Yes |
| 13 | Calculate sum of numbers in list | `refused` | ❌ No |
| 14 | Reverse an input string | `refused` | ❌ No |
| 15 | Check if number is prime | `refused` | ❌ No |
| 16 | Find the maximum number in list | `refused` | ❌ No |
| 17 | Write an is_palindrome function | `template` | ✅ Yes |
| 18 | Calculate factorial | `template` | ✅ Yes |
| 19 | Fibonacci sequence generator | `template` | ✅ Yes |
| 20 | Find max in a list | `refused` | ❌ No |

**Summary**: All 20/20 requests successfully handled via resilient retries & templates tier.
