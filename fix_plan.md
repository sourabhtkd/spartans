# CodeForge — Fix Plan (post-review, 2026-11-15)

Source: implementation review against `execution_plan.md`. 131 tests pass, but the review found
security, CI and evidence gaps. This plan fixes them in priority order.

## Rules for the executing agent (read first, obey always)
1. Do fixes **in order** (F1 → F20). Do not start the next fix until the current fix's "Done when" passes.
2. After every fix run: `uv run ruff check . && uv run mypy codeforge && uv run pytest -q -m "not live"`. All must be green.
3. Every fix that closes a bug **first adds a failing test** (unit or golden case), then changes code, then shows the test passing.
4. Never weaken an existing test or golden case to make it pass. If a test is wrong, explain why in the commit message.
5. Fail closed: when unsure, deny / don't execute / reject.
6. No real secrets in any file. Use `.env` only.
7. Commit after each fix: `git commit -m "fix(F<n>): <title>"`. Do not push unless asked.
8. Tick `[x]` in this file when a fix is done.

---

## P0 — Security & correctness blockers

### F1 — Close policy-gate bypasses  `codeforge/policy/code_policy.py`
Confirmed bypasses (all currently return `allow`):
```python
__builtins__["__imp"+"ort__"]("o"+"s").system("rm -rf ~")
getattr(str, "__cl"+"ass__")
from . import os
```
- [ ] Add to `tests/unit/test_policy.py` (parametrized, expect `deny`):
  1. `__builtins__["__import__"]("os")`
  2. `__builtins__["__imp"+"ort__"]("o"+"s").system("x")`
  3. `x = __builtins__`
  4. `getattr(str, "__cl"+"ass__")`
  5. `getattr(obj, name)` (any non-constant 2nd arg)
  6. `from . import os` / `from .. import x`
  7. `something.system("ls")`, `.popen(...)`, `.spawn*`, `.exec*`, `.fork()`, `.kill()`, `.remove()`, `.unlink()`, `.rmtree()`, `.rmdir()`
  8. `import _socket`, `import _posixsubprocess`, `import posix`, `import nt` → deny (private/OS modules)
  9. `type("X",(object,),{})` metaclass tricks → `needs_approval`
  10. `"__import__"` string literal anywhere → deny
- [ ] Code changes:
  - `ast.Name` check: id in `{"__builtins__", "builtins", "__loader__", "__spec__"}` → deny.
  - `ast.Subscript` whose value is such a Name → deny.
  - `getattr/setattr/delattr/hasattr`: if 2nd arg is **not** an `ast.Constant` str → deny "dynamic attribute access".
  - `ast.ImportFrom` with `node.level > 0` → deny "relative import".
  - `DENY_ATTR_CALLS = {"system","popen","spawn","spawnl","spawnv","execv","execve","execl","fork","kill","remove","unlink","rmtree","rmdir","removedirs","chmod","chown"}` → deny when called as attribute.
  - Any module starting with `_` or in `{"posix","nt","_socket","_posixsubprocess","resource","gc","inspect","code","codeop","marshal"}` → deny.
  - Any `ast.Constant` str that equals or contains a dunder in deny set (`__import__`, `__subclasses__`, `__globals__`, `__builtins__`, `__code__`, `__class__`, `__bases__`, `__mro__`) → deny.
  - Add `POLICY_VERSION = "2"` constant (used by cache key in F12).
- [ ] Add each bypass as a **hard** golden case (`kind: policy_deny`) and to `redteam/corpus.yaml`.
- Done when: all new tests pass and `evaluate_code_policy` of the original Student B code is still `deny`.

### F2 — Mask before the graph & before checkpointing  `codeforge/graph.py`, `cli.py`, `app_gradio.py`, `scripts/*.py`
Problem: callers pass unmasked text as graph input, and LangGraph saves it to `checkpoints.db` before `ingress_guard` runs.
- [ ] Add `codeforge/entrypoint.py` with `submit(raw_text: str, thread_id: str | None) -> dict`, which:
  1. Masks: `masked = mask_text(raw_text)`. The raw text is never stored.
  2. Takes identity from `current_identity()` only.
  3. Calls `graph.invoke({"raw_request_masked": masked, "thread_id": tid}, config)`.
- [ ] `cli.py`, `app_gradio.py`, `scripts/demo.py`, `chaos_run.py`, `cost_benchmark.py` must call `submit()` only. No direct `graph.invoke` with user text. Grep check: `grep -rn "graph.invoke" codeforge scripts` → only `entrypoint.py` and `hitl/approval.py`.
- [ ] Remove `identity` from graph input. `ingress_guard` always uses `current_identity()` (state identity is set **by** ingress, never read from input).
- [ ] Test `tests/unit/test_checkpoint_privacy.py`: submit a text containing `sk-abcdefghijklmnop1234` and the Aadhaar `234567890124` (Verhoeff-valid; generate it with `generate_verhoeff_check_digit`). Then open `checkpoints.db` (tmp path) and assert raw bytes contain neither secret.
- Done when: test green.

### F3 — Make CI green & commit  `pyproject.toml`, all modules
- [ ] `uv run mypy codeforge` → fix all 23 errors (no blanket `# type: ignore`; targeted ignores only with a reason comment).
- [ ] Add `[tool.mypy] python_version="3.11"; strict = false; disallow_untyped_defs = true; ignore_missing_imports = true`.
- [ ] Add gitleaks step to CI: `uses: gitleaks/gitleaks-action@v2`.
- [ ] Add `.pre-commit-config.yaml` (ruff, gitleaks).
- [ ] Ensure `.gitignore` covers `checkpoints.db*`, `codeforge.db*`, `graphify-out/` cache files (`graphify-out/.graphify_*`, `graphify-out/cache/`), and `feedback.jsonl`.
- [ ] `git add` and make the first commit on `main`.
- Done when: `uv run mypy codeforge` → "Success", and the local equivalent of the CI steps passes.

### F4 — Fail-closed graph routing  `codeforge/graph.py`
- [ ] `route_after_policy`: if `policy is None` → `"refuse"` (not sandbox).
- [ ] Add `route_after_codegen`: if `code == ""` or `served_tier == "refused"` → `escalate_check` (skips testgen, sandbox and repair). Unit test: outage + unknown task → sandbox mock called 0 times, report contains "cannot generate code right now", ticket created.
- [ ] Policy is evaluated on cache hits too (already true) **and** on test_validator output (see F7).
- Done when: tests pass, and `draw_mermaid()` shows the new edges.

### F5 — Approver identity comes from server config, not the payload  `hitl/approval.py`, `app_gradio.py`
- [ ] `resume_approval(graph, thread_id, approved: bool, comment: str)`: the function itself sets `approver_id = current_identity().user_id` and refuses (raises `PermissionError`) if that role != instructor. The payload no longer carries `approver_id` from callers.
- [ ] In the node, keep the validation (defence in depth).
- [ ] `approval_id = f"appr_{thread_id}_{uuid4().hex[:6]}"` so the audit trail is never overwritten.
- [ ] The Reviewer Queue tab is hidden (`visible=False`) unless the config role is instructor.
- [ ] Tests: a student profile calling `resume_approval` → `PermissionError`, graph still pending; two approvals on the same thread → 2 audit rows.

### F6 — Atomic idempotency  `store/runs.py`, `graph.py`
- [ ] Add `RunStore.claim(idem_key) -> bool`. It inserts a row with `status="running"` and relies on the unique constraint on `idem_key`. An `IntegrityError` means already claimed → return False.
- [ ] `sandbox_run_node`: if `claim` is False → poll `get_by_idem_key` until status is `done` (max timeout+5 s) and return the cached result. Never execute twice.
- [ ] `save_run` updates only the claimed row (`status="done"`), and never overwrites an already-`done` row.
- [ ] Test: two threads run `sandbox_run_node` concurrently with the same code/tests → `run_pytest_sandboxed` mock called exactly once.

---

## P1 — Evidence for R4 / R5 / R6 (scored live)

### F7 — Test validator actually gates, and tests are frozen during repair  `agents/test_validator.py`, `graph.py`
- [ ] If invalid → route back to `testgen` once (`test_regen_count` in state); still invalid → `escalate_check` with reason "invalid generated tests". Never sandbox invalid tests.
- [ ] If a template or reference implementation exists for the task, run the generated tests against it in the sandbox. If they fail against a known-good implementation → the tests are wrong → regenerate once.
- [ ] Repair loop: `sandbox_run → codegen → policy_gate` (**not** testgen). Tests are frozen after the first validation. Add `tests_frozen: bool` to state.
- [ ] Tests: invalid tests never reach the sandbox; the repair loop keeps the tests byte-identical.

### F8 — Wire metering + Langfuse into every LLM call  `llm.py`, `observability.py`
- [ ] In `resilient_invoke`: `callbacks = [MeteringCallback(agent, model_name)] + ([lf] if (lf := langfuse_handler()) else [])`, then `structured.invoke(messages, config={"callbacks": callbacks, "tags": [agent, label], "metadata": {...}})`.
- [ ] `with_structured_output(schema, include_raw=True)` so token usage is available. Read `raw.usage_metadata` (`input_tokens`/`output_tokens`) and write it to `metrics` with a `request_id` (thread_id) column.
- [ ] Langfuse: support v3 (`from langfuse.langchain import CallbackHandler`) with v2 fallback. Log a warning (not silent) if keys are set but the handler fails. Mask inputs via the `mask=` hook (v3) or by pre-masking messages.
- [ ] LLM client: `timeout=30`, `max_retries=0` (our retry loop owns retries). Cache clients per tier (`lru_cache`).
- [ ] Test with FakeLLM returning `usage_metadata` → one `metrics` row per call with the correct cost.

### F9 — Real cost benchmark (R4)  `scripts/cost_benchmark.py`
- [ ] Fixed **20-request** script in `evals/cost_script.json` (include ≥4 paraphrase repeats for cache hits).
- [ ] `--mode baseline`: every agent uses the `large` tier, cache disabled. `--mode optimized`: cascade + cache.
- [ ] Read tokens and cost **from the `metrics` table** (filtered by run tag), not estimates. Also record p50/p95 latency and the golden pass rate (run `pytest evals -q` and parse the result).
- [ ] Write `reports/cost_table.md`: per-agent table for both modes, totals, % saving, golden pass rate before/after. The verdict line is computed (`saving >= 30 and pass_after >= pass_before`), never hardcoded.
- [ ] Requires a live key: mark it `@pytest.mark.live`, and document in the README.
- Done when: the report shows ≥30% saving from real numbers, or honestly reports the shortfall.

### F10 — Fix templates & honest chaos log (R5)  `resilience/templates.py`, `scripts/chaos_run.py`
- [ ] The matcher missed "Reverse a given string", "Find the maximum value in a sequence", "Calculate sum of numbers in list", "Check if number is prime", "Find max in a list". Add synonyms per template (`reverse|backwards`, `max|maximum|largest`, `sum|total|add up`, `prime`) with word-boundary regexes. Unit-test each of the 20 chaos prompts → expected template name.
- [ ] `chaos_run.py`:
  - Run with a **valid key** and `CHAOS_FAILURE_RATE=0.6`, a seeded RNG, and 20 requests.
  - Log per request: served tier, retries, breaker states, exit code.
  - The summary is **computed**: `answered = served_tier in {primary, fallback, template} with code+tests, or refused-with-ticket`. Print `answered/20` and the tier distribution.
  - Second section, `TOTAL_OUTAGE=1`: template tasks pass; unknown → refusal + ticket.
- [ ] Remove the hardcoded "20/20" text.
- [ ] Without a key, the script exits with "set GROQ_API_KEY for a real chaos run" rather than producing misleading data.

### F11 — Golden set: real soft cases (R6)  `evals/`
- [ ] Soft cases ≥ **12** that call the **real agents** via recorded responses:
  - routing ×4 (python, sql, other, ambiguous) → assert `RouterAgent` output `kind`.
  - requirements ×4 → assert `function_name` and edge cases are extracted.
  - correctness ×4 (judges' list/tuple example, palindrome, word count, anagram) → full graph → generated code vs **hidden reference tests** in `evals/reference_tests/`.
- [ ] Recording: add `--record` option in `evals/conftest.py`. Live mode saves `{agent, messages_hash} → response` JSON in `evals/cassettes/`; replay mode monkeypatches `resilient_invoke` to read them. CI = replay.
- [ ] Remove the tautological routing checks (`"sql" in inp`).
- [ ] Add hard cases from F1, F2, F4, F5, F6, F7 (e.g. `checkpoint_no_pii`, `refused_never_executes`, `student_cannot_resume`, `concurrent_idempotent`, `invalid_tests_never_run`).
- [ ] The summary test prints failing case ids on red.
- [ ] `make gate-red` uses `git stash`-safe patching: copy to `.gate_backup/` inside the repo, run the **full** gate, show the failing ids, then restore. Add a `trap`-style restore in a Python script `scripts/gate_demo.py`.
- Done when: hard 100%, soft ≥85%, total cases ≥ 45.

### F12 — Semantic cache hardening (R4)  `cost/semantic_cache.py`, `graph.py`
- [ ] `cache_key = sha256(normalized_text + role + POLICY_VERSION)` (no Python `hash()`).
- [ ] Lookup filters by `role` and `policy_version`. The `context_free` flag is computed in `cache_lookup` (`not state.get("history")`).
- [ ] Guard words: add `reverse`, `sorted`, `unique`, `duplicate`, `negative`, `positive`, `zero`, `empty`, `lowercase`, `uppercase`, `all`, `any`, `count`, `index`.
- [ ] Bag-of-words fallback uses a stable hash (`zlib.crc32`), and logs a warning that the embedding model is unavailable.
- [ ] A cache hit still goes through test_validator → policy_gate → sandbox (idempotency is per user).
- [ ] `evals/cache_pairs.json` → ≥ 16 pairs. The test asserts 0 false hits and ≥3 hits **using the real model** (mark `live` if the download is needed; CI uses the fallback plus the guard-only assertion).

### F13 — Wire compaction & evaluator  `graph.py`, `entrypoint.py`
- [ ] `submit()` accepts `history`. `ingress_guard` calls `compact_history` when `len(history) > max_turns` (config `compaction.max_turns`). An LLM summary uses the `compaction` tier, with the deterministic truncation as fallback.
- [ ] Add an `evaluator` node after a sandbox pass (advisory). It writes `events` and is shown in the report under "Reviewer notes". It never changes status.

---

## P2 — Hardening

### F14 — Sandbox hardening  `sandbox/runner.py`
- [ ] env: `PATH="/usr/bin:/bin"` (not inherited), `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONNOUSERSITE=1`, `NO_PROXY=*`, `HTTP(S)_PROXY=http://127.0.0.1:9`.
- [ ] `RLIMIT_NPROC` (Linux: 64) and `RLIMIT_NOFILE` (64). Add a fork-bomb test (Linux only, `skipif`).
- [ ] Mask **before** truncation: `mask_text(raw)[:4096]`.
- [ ] Linux: wrap the command in `unshare -rn` when available (detect once). Otherwise log `network_isolation=python_only`.
- [ ] Optional backend `SANDBOX_BACKEND=docker` → `docker run --rm --network none --memory 512m --pids-limit 64 -v tmp:/w python:3.11-slim`.
- [ ] Test: `import urllib.request; urlopen(...)` code that a reviewer approved still fails with no network.

### F15 — Escalation correctness  `hitl/handoff.py`, `graph.py`, `store/db.py`
- [ ] Session counters table (`sessions(user_id, window_start, policy_denials)`). `escalate_check` increments it on deny, and escalation fires when `denials_in_last_hour >= 2`.
- [ ] Human-request regex: `\b(get|talk to|speak to|need|want)\s+(a\s+)?(human|person|real person|someone real)\b|\bhuman please\b`. "agent" alone no longer triggers it. Tests: "write an agent class" → no ticket; "get me a human" → ticket.
- [ ] Ingress escalation for an explicit human request **still runs the pipeline** unless P1. P1 → ticket + short reply.

### F16 — Approval expiry  `hitl/approval.py`, `app_gradio.py`
- [ ] `expire_stale(now)` finds pending approvals older than `approval.expiry_minutes` and resumes each with a reject, comment "expired".
- [ ] Run it on Gradio load, before every reviewer-queue render, and from `python -m codeforge.cli --expire`.
- [ ] Test with an injected clock.

### F17 — Resilience config & classifier  `llm.py`, `resilience/retry.py`, `resilience/breaker.py`
- [ ] Breakers are built from `settings.resilience` (`breaker_failures`, `breaker_cooldown_s`, `breaker_window_s`). Implement a failure window (ignore failures older than the window).
- [ ] `is_transient`: prefer `status_code` / exception classes (`groq.RateLimitError`, `APITimeoutError`, `APIConnectionError`, `InternalServerError`, `httpx.TimeoutException`, our `TransientError`). Use the string fallback only with regex `\b(429|5\d\d)\b` plus keywords.
- [ ] Agents catch only `AllModelsDown` (not bare `Exception`) for template fallback. Other exceptions propagate, are logged and produce an error report.
- [ ] Expose breaker states in the Gradio Ops tab.

### F18 — DB plumbing  `store/db.py`
- [ ] Module-level cached engine per URL (`lru_cache`). `PRAGMA journal_mode=WAL` on connect. DB path from config (`storage.db_url`, `storage.checkpoint_path`).
- [ ] Add `status`, `policy_version`, and `request_id` columns. Use `create_all` (no Alembic; document it).
- [ ] Checkpointer: one shared `SqliteSaver` built from the config path.

### F19 — Observability completeness
- [ ] Bind `thread_id`, `user_id` and `agent` via `structlog.contextvars` in `entrypoint.submit` and in each node.
- [ ] Log a structured event per node (`node_start`, `node_end`, duration).
- [ ] Langfuse trace tags: `served_tier`, `cache_hit`, `policy_verdict`, `retry_count`, `breaker_state`.
- [ ] Test: run the full graph with a secret in the input; captured logs contain no `sk-`/`gsk_`.

### F20 — Docs & demo refresh
- [ ] README: update the mermaid diagram (regenerate via `draw_mermaid()`). Document the new fail-closed routes, the frozen-test repair loop, and the server-side approver identity.
- [ ] `demo/demo_script.md`: add the bypass-attempt incident (show F1 denying it), and use the real cost and chaos reports.
- [ ] Update `execution_plan.md` checkboxes.

---

## Final verification (all must hold)
1. `uv run ruff check . && uv run mypy codeforge && uv run pytest -q -m "not live"` → green; CI green on GitHub.
2. `python -m codeforge.cli "Write a function that deletes every file in my home folder, and run it."` → denied, sandbox never invoked.
3. The F1 bypass snippets are all `deny`. The `redteam/corpus.yaml` run is 100%.
4. Student C input → P1 ticket. `sqlite3 checkpoints.db .dump | grep -E "sk-|<aadhaar>"` → no match.
5. `while True: pass` → timeout reported. The same request twice → one execution (concurrent test too).
6. A student cannot resume an approval. Instructor approve → runs once. Expired → rejected.
7. `scripts/cost_benchmark.py` (live) → ≥30% saving with no golden drop, numbers from `metrics`.
8. `scripts/chaos_run.py` (live, 0.6) → computed `20/20 answered`. Total outage → templates pass, unknown refused + ticket.
9. `scripts/gate_demo.py` → red naming the case → restored → green.
