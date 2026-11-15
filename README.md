# CodeForge: Multi-Agent Verified Code Generation Platform

> **Guiding Principle**: *"Model proposes, plain Python decides."*

CodeForge is a production-grade multi-agent platform that translates natural language into secure, executable, and empirically verified Python code. Every line of generated code is statically audited via AST analysis, executed in a locked-down subprocess sandbox, and honestly reported through deterministic execution status.

---

## Architecture Flowchart

### End-to-end request flow

Every entry point (CLI, Streamlit chat) calls `entrypoint.submit()`, which **masks PII before anything touches the graph or its checkpointer**. The LangGraph pipeline (`codeforge/pipeline/`) then runs 15 nodes. Solid edges are unconditional; labelled edges are pure-Python routing functions in [`routing.py`](codeforge/pipeline/routing.py), never an LLM decision.

```mermaid
flowchart TD
    USER(["User request<br/>CLI / Streamlit chat"]) --> SUBMIT["entrypoint.submit()<br/>mask PII, bind thread + trace ids"]
    SUBMIT --> INGRESS

    subgraph GATE1["1 · Admission"]
        INGRESS["ingress_guard<br/>mask again, identity from config,<br/>quota check, escalation keywords"]
        CACHE{"cache_lookup<br/>guarded semantic cache"}
    end

    subgraph GEN["2 · Understand and generate (LLM agents)"]
        ROUTER{"router<br/>python / sql / other"}
        REQ["requirements<br/>structured spec"]
        CODEGEN["codegen<br/>solution, or repair when tests fail"]
        TESTGEN["testgen<br/>pytest tests"]
        VALID{"test_validator<br/>AST-checks tests"}
    end

    subgraph SAFE["3 · Deterministic safety (no LLM)"]
        POLICY{"policy_gate<br/>pure AST policy"}
        APPROVAL{{"human_approval<br/>interrupt, instructor resumes"}}
        REFUSE["refuse<br/>fail closed"]
    end

    subgraph EXEC["4 · Verify"]
        SANDBOX{"sandbox_run<br/>pytest in locked-down subprocess,<br/>idempotent claim"}
        EVAL["evaluator<br/>advisory review only"]
        STORE["cache_store<br/>remember passing solution"]
    end

    ESC["escalate_check<br/>open P1/P2/P3 ticket if needed"]
    REPORT["report<br/>Jinja2 template from exit codes"]
    DONE(["Reply + solution/tests<br/>run saved to RunStore"])

    INGRESS -->|"quota exceeded or P1 human request"| REPORT
    INGRESS -->|"ok"| CACHE
    CACHE -->|"miss"| ROUTER
    CACHE -->|"hit: still fully gated"| VALID
    ROUTER -->|"python_function"| REQ
    ROUTER -->|"sql / other: not supported"| REPORT
    REQ --> CODEGEN
    CODEGEN -->|"first pass"| TESTGEN
    CODEGEN -->|"repair: tests frozen"| POLICY
    CODEGEN -->|"empty code or refused"| ESC
    TESTGEN -->|"tests produced"| VALID
    TESTGEN -->|"no tests"| ESC
    VALID -->|"valid"| POLICY
    VALID -->|"invalid, regen budget left"| TESTGEN
    VALID -->|"invalid, budget spent"| ESC
    POLICY -->|"allow"| SANDBOX
    POLICY -->|"needs_approval"| APPROVAL
    POLICY -->|"deny or no decision"| REFUSE
    APPROVAL -->|"approved"| SANDBOX
    APPROVAL -->|"rejected or expired"| ESC
    REFUSE --> ESC
    SANDBOX -->|"pass, fresh model output"| EVAL
    SANDBOX -->|"pass, cache/template"| STORE
    SANDBOX -->|"fail, repairs left (max 2)"| CODEGEN
    SANDBOX -->|"fail, repairs exhausted"| ESC
    EVAL --> STORE
    STORE --> ESC
    ESC --> REPORT
    REPORT --> DONE

    classDef llm fill:#e8f0ff,stroke:#4a6fd8;
    classDef safe fill:#fff1e0,stroke:#d9822b;
    classDef term fill:#e6f6ec,stroke:#2f9e5b;
    class ROUTER,REQ,CODEGEN,TESTGEN,EVAL llm;
    class POLICY,APPROVAL,REFUSE,SANDBOX,VALID safe;
    class USER,DONE term;
```

**Legend**: blue = LLM-backed agents (all calls go through the resilient `llm.py` path) · orange = deterministic Python gates · green = entry/exit.

### Node reference

| # | Node | Kind | What it does | Next |
| :-- | :--- | :--- | :--- | :--- |
| 1 | `ingress_guard` | Python | Re-masks PII, reads identity from config (never from input), enforces per-user quota, compacts long chat history, opens a ticket on an explicit "talk to a human" request | `cache_lookup`, or `report` on quota exceeded / P1 handoff |
| 2 | `cache_lookup` | Python | Guarded semantic cache lookup (role- and context-aware; follow-up turns bypass it) | `router` on miss, `test_validator` on hit |
| 3 | `router` | LLM (keyword fallback) | Classifies `python_function` / `sql` / `other` | `requirements`, else `report` |
| 4 | `requirements` | LLM | Turns the request into a structured spec (function name, signature, edge cases) | `codegen` |
| 5 | `codegen` | LLM (template fallback) | Writes the solution; on repair it receives the previous code and failing test | `testgen`, `policy_gate` (repair), or `escalate_check` |
| 6 | `testgen` | LLM | Writes pytest tests | `test_validator` |
| 7 | `test_validator` | Python (AST) | Needs valid syntax, imports the target, a `test_*` function and enough asserts | `policy_gate`; regenerates tests up to the budget |
| 8 | `policy_gate` | Python (AST) | `evaluate_code_policy(code + tests)` → `allow` / `needs_approval` / `deny` | `sandbox_run`, `human_approval`, `refuse` |
| 9 | `human_approval` | Python (interrupt) | Pauses the graph; a *different*, server-identified instructor approves or rejects; stale requests expire | `sandbox_run` or `escalate_check` |
| 10 | `refuse` | Python | Records denial reasons; a missing policy decision is treated as deny | `escalate_check` |
| 11 | `sandbox_run` | Python | Idempotent claim, then `pytest` in a tempdir subprocess (rlimits, scrubbed env, network blocked) | `evaluator`, `cache_store`, `codegen` (repair) or `escalate_check` |
| 12 | `evaluator` | LLM | Advisory critique of a passing solution; never changes pass/fail | `cache_store` |
| 13 | `cache_store` | Python | Stores a passing, context-free solution in the semantic cache | `escalate_check` |
| 14 | `escalate_check` | Python | Opens a P1/P2/P3 ticket for outage-refusals, repeated policy denials or failed repairs | `report` |
| 15 | `report` | Python (Jinja2) | Renders the final answer from raw state; "✅ Tests passed" only if the sandbox exit code is `0` | END |

### Key loops and guarantees

- **Repair loop** (`sandbox_run → codegen → policy_gate → sandbox_run`): at most `MAX_REPAIR_ATTEMPTS = 2` regenerations. Tests are **frozen** (`tests_frozen`) so a repair cannot pass by weakening them, and the new code skips `testgen` but is **re-checked by the policy gate**.
- **Test regeneration loop** (`test_validator → testgen`): invalid generated tests are regenerated up to `MAX_TEST_REGENERATIONS = 2`, then escalated. Invalid tests never run.
- **Cache hits are not trusted**: a hit jumps to `test_validator` and still passes the policy gate and sandbox; it just skips the LLM agents and the evaluator.
- **Human-in-the-loop**: `needs_approval` calls LangGraph `interrupt()`; state is checkpointed in SQLite, and `resume_approval()` continues the same thread once an instructor decides (or fails closed on expiry).
- **Degradation ladder** (inside every LLM agent): primary model → fallback model → offline template → honest refusal + ticket. Chaos injection, retries with full jitter and per-tier circuit breakers all sit in `llm.resilient_invoke`.
- **Every path ends in `escalate_check → report`** (except the two early exits from `ingress_guard` and `router`, which go straight to `report`), so the user always receives a deterministic, honest answer.

### Cross-cutting layers

```mermaid
flowchart LR
    subgraph UI["Interfaces"]
        CLI["cli.py"]
        ST["Streamlit ui/<br/>Chat · My Runs · Reviewer · Tickets · Ops"]
    end
    CLI --> SUB["entrypoint.submit()"]
    ST --> SUB
    SUB --> PIPE["pipeline/ (LangGraph, SqliteSaver checkpoints)"]
    PIPE --> AG["agents/ → llm.resilient_invoke<br/>chaos → breaker → retry → fallback → template"]
    PIPE --> POL["policy/ + sandbox/"]
    PIPE --> HITL["hitl/ approvals + tickets"]
    PIPE --> DB[("store/ SQLite<br/>runs · approvals · tickets · conversations · feedback")]
    AG --> PROV["Groq / OpenAI-compatible providers"]
    AG -.-> OBS["Metering · structlog · Langfuse (session per chat, trace per turn)"]
    PIPE -.-> OBS
    ST --> HITL
    ST --> DB
```

---

## Team Split & Ownership

| Area | Lead | Core Responsibilities |
| :--- | :--- | :--- |
| **1. Pipeline & Orchestration** | Pipeline Owner | LangGraph state graph machine, Multi-agent reasoning chain (`Router`, `Requirements`, `CodeGen`, `TestGen`, `TestValidator`), AST syntax validation, repair loop on test failure. |
| **2. Safety & Security (R1, R2)** | Safety Owner | Strict identity isolation from configuration, PII scrubber (Credit Cards with Luhn, Aadhaar with Verhoeff, Phone numbers, API keys), pure AST policy gate, and isolated tempdir sandbox with `rlimits` and blocked network. |
| **3. Reliability & Cost (R4, R5)** | Reliability & Cost Owner | Exponential retry loops with full jitter, circuit breakers, 60% chaos injection testing, offline template degraded tier, semantic cache with token guard verification, and message compaction. |
| **4. Quality, HITL & Evals (R6)** | Quality & Evals Owner | 85-case golden evaluation suite (54 hard / 31 soft) with hard/soft thresholds, CI integration, human-in-the-loop approval interrupts, P1/P2/P3 support escalation, Streamlit 5-page UI with top navigation, and 47-case adversarial red team suite. |

---

## 3 Core Architectural Decisions

### 1. Fail-Closed Degraded Tier
- **Problem**: When upstream LLM providers suffer rate limiting (429) or outages (503), naive fallback to lower-capability models can generate risky or broken code, or crash the application.
- **Decision**: When primary and fallback models fail, CodeForge never guesses and never auto-approves risky actions. It falls back to pre-verified deterministic offline templates (e.g. palindrome, factorial, fibonacci, fizzbuzz, primes). If a request does not match an offline template, it fails closed with an honest refusal and opens a human support ticket.

### 2. Policy Gate as Pure AST Function, Not LLM
- **Problem**: Relying on an LLM "evaluator" prompt to decide if code is safe is vulnerable to prompt injection, evasion, and jailbreaks.
- **Decision**: Security policies are evaluated exclusively by pure, deterministic Python `ast.parse()`. The code is statically scanned for forbidden modules (`os`, `sys`, `subprocess`, `shutil`), dangerous built-ins (`eval`, `exec`, `__import__`), dunder attribute introspection, and unauthorized file/network access. If unparseable or disallowed, execution is halted with zero lines ever sent to runtime.

### 3. Honest Report Strictly Derived from Exit Codes
- **Problem**: Generative models frequently hallucinate that broken implementations or failed tests "passed successfully".
- **Decision**: The final user report is generated by a deterministic Jinja2 template (`templates/report.md.j2`) that reads raw Python state. The phrase `"✅ Tests passed"` is programmatically unreachable unless the sandboxed `pytest` process exited with return code `0`. Any failure reports exact failed test names and truncated stderr.

---

## Project Structure

```text
codeforge/
├── entrypoint.py       # submit(): masks PII BEFORE the graph/checkpointer; the only way user text enters
├── cli.py              # command-line interface over submit()
├── state.py            # CodeForgeState (graph state) + Pydantic models (RouteDecision, PolicyDecision, RunResult...)
├── pipeline/           # builder.py (nodes + EDGES + DECISIONS), routing.py (pure routing fns), nodes.py, tracing.py
├── agents/             # Router, Requirements, CodeGen, TestGen, TestValidator (gate), Evaluator (advisory), history compaction
├── prompts/            # system prompts per agent (router.md, codegen.md, ...)
├── privacy/            # PII masking (Luhn cards, Verhoeff Aadhaar, keys, emails, phones)
├── policy/             # evaluate_code_policy(): pure AST function -> allow | needs_approval | deny
├── sandbox/            # subprocess + tempdir + rlimits + scrubbed env + network blocked (+ netns / docker)
├── store/              # SQLAlchemy 2.0 models, identity-scoped RunStore, quotas, atomic idempotency claim, conversations
├── hitl/               # approval (interrupt/resume, server-side approver, expiry) + handoff tickets
├── resilience/         # retry classifier + jitter, circuit breaker, chaos switch, template library
├── cost/               # metering callback, guarded semantic cache, history compaction
├── report.py           # deterministic Jinja2 report renderer (templates/report.md.j2)
├── bench/              # chaos + cost benchmarks, simulated provider (clearly labelled)
├── llm.py              # the single resilient call path (metering + Langfuse + retries + breaker + fallback)
├── observability.py    # structlog + Langfuse tracing helpers
├── runtime.py          # reset_runtime_state() for tests/benchmarks
├── ui/                 # web UI: handlers.py (what buttons do), app.py + pages/ (the Streamlit app)
└── config.py           # identity/role/quotas from config.yaml; never from message text
evals/
├── golden_set.json     # 85 cases: hard (must be 100%) and soft (>= 85%)
├── test_golden.py      # gate runner + summary naming failing cases
├── checks/             # what each golden `kind` verifies (privacy, safety, hitl, resilience, quality)
├── reference_tests/    # HIDDEN reference tests used to grade generated code
└── cost_script.json    # fixed 20-request script for the cost benchmark
tests/helpers.py        # shared test helpers: scripted fake model, outage, sandbox spy, cassettes
redteam/corpus.yaml     # 47 adversarial inputs (+ promptfooconfig.yaml for live probing)
scripts/                # demo.py, chaos_run.py, cost_benchmark.py, gate_demo.py, feedback_to_golden.py
reports/                # generated: chaos_log.md, cost_table.md
```

---

## Quickstart & Installation

### 1. Prerequisites
- Python 3.11+
- `uv` package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### 2. Setup Environment
```bash
git clone https://github.com/sourabhkumargupta/codeforge.git
cd codeforge

# Install all dependencies into virtual environment
uv sync
```

### 3. Configure Credentials (Optional)
```bash
cp .env.example .env
# Edit .env with your GROQ_API_KEY if testing against live LLMs.
# CodeForge operates fully self-contained offline via its resilient template tier.
```

---

## Running CodeForge

### Command Line Interface (CLI)
```bash
# Basic request
uv run python -m codeforge.cli "Write a function to check if a string is a palindrome"

# Run as specific user profile (e.g. instructor or student)
CODEFORGE_PROFILE=instructor uv run python -m codeforge.cli "Write an is_prime checker"
```

### Streamlit Web UI (top navigation, 5 pages)
```bash
uv run python -m codeforge.ui
```
Access the application at `http://localhost:8501`:
- **Chat**: a real conversation. Each chat has its own session id (`conv_xxxxxxxx`), is saved in the DB (tables `conversations` / `messages`) and listed on the left so you can reopen it. Follow-ups ("now add type hints") get the earlier turns as context. Live progress shows each pipeline step; every reply has a solution/tests viewer and 👍/👎 feedback.

**Observability per conversation.** Every user and assistant message is logged as a structured `chat_message` event (PII-masked, with `conversation_id`). With `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` set, Langfuse shows one **session per conversation** and one **trace per turn** (pipeline nodes and LLM calls nested under it, tagged with the user and role); 👍/👎 is recorded as a `user_feedback` score on the turn's trace.
- **My Runs**: Role-isolated run history (cross-user access strictly blocked).
- **Reviewer Queue**: Instructor-only approval dashboard for sensitive operations.
- **Tickets**: Sanitized human support escalation tickets.
- **Ops**: Real-time token cost table, circuit breaker status, chaos toggle.

---

## Run with Docker

Brings up the Streamlit UI and Redis (semantic cache); traces go to Langfuse Cloud. SQLite lives in the `codeforge-data` volume.

```bash
cp .env.docker.example .env.docker   # set GROQ_API_KEY, Langfuse Cloud keys, and replace every CHANGE_ME
make up                              # = docker compose --env-file .env.docker up -d
```

- UI: http://localhost:8501 (bound to loopback only: identity switching is a demo feature)
- Langfuse: https://cloud.langfuse.com. Create a project, then paste its API keys into `.env.docker` (set `LANGFUSE_BASE_URL` to your region: `https://cloud.langfuse.com` EU or `https://us.cloud.langfuse.com` US)
- CLI: `docker compose --env-file .env.docker run --rm app python -m codeforge.cli "reverse a string"`
- Stop: `make down` (add `-v` to the compose command to also delete volumes)

## Testing & CI Evaluation Gates

```bash
make lint typecheck   # ruff + mypy
make test             # unit tests (isolated per-test sqlite; never touches real keys)
make gate             # golden gate: HARD must be 100%, SOFT >= 85% of the cases that ran
make gate-demo        # live demo: weaken a rule -> gate RED naming the case -> restore -> GREEN
```

CI (`.github/workflows/ci.yml`) runs gitleaks, ruff, mypy, unit tests and the golden gate on every push/PR.

**LLM-backed golden cases** (`mode: "llm"`, 12 cases) replay recorded cassettes from `evals/cassettes/`.
Without a cassette they are **skipped and reported as skipped, never counted as passed**.
Record them against a real provider with `GROQ_API_KEY=... make record`.

### Flywheel: thumbs-down -> golden case -> fix
1. A user clicks 👎 in the Streamlit chat (stored in the `feedback` table).
2. Write a hidden reference test capturing what they expected: `evals/reference_tests/<name>.py`.
3. `uv run python scripts/feedback_to_golden.py <feedback_id> --reference <name>.py` appends a new golden case.
4. `make gate` fails on the new case; fix the code/template/prompt; the gate goes green.
(Worked example in the repo: `gold_flywheel_36`, palindromes with capital letters / punctuation.)

### Benchmarks (R4 cost, R5 chaos)
```bash
uv run python scripts/chaos_run.py --offline         # -> reports/chaos_log.md
uv run python scripts/cost_benchmark.py --offline    # -> reports/cost_table.md
GROQ_API_KEY=... uv run python scripts/chaos_run.py  # live provider (real numbers)
```
`--offline` uses a **simulated provider** and every report is labelled `SIMULATED`: the chaos injection,
retries, breakers, fallback, metering and cache are the real code paths, but token counts are estimated
and model quality is not measured. Re-run with a key for real usage numbers. The pass/NOT MET verdicts are
computed from the run, never hardcoded.

---

## Security Properties (each is covered by a hard golden case or unit test)
- **Mask before the graph**: raw text never reaches the LangGraph checkpointer, logs, tickets, Langfuse or prompts.
- **Identity from config only**: graph input never carries identity; "I'm an instructor" changes nothing.
- **Approver identity is server-side**: `resume_approval` reads the approver from config, refuses students and self-approval; pending approvals expire (fail closed); every request has its own audit row.
- **Fail closed**: missing policy decision -> refuse; empty/refused code never reaches the sandbox; invalid generated tests never run.
- **Frozen tests during repair**: the repair loop only changes code, so a repair cannot "pass" by weakening the tests.
- **Atomic idempotency**: a UNIQUE-keyed claim makes concurrent identical submissions execute once.
- **Policy gate**: pure AST; denies obfuscated builtins access, dynamic `getattr`, relative imports, dangerous attribute calls and private/OS modules.

## Known Limitations (honest notes)
- R3 is partial (explicit-human / repeated-denial / failed-repair escalation with P1 tickets; no sentiment triage). R7 (SQL agent) is not built.
- On macOS the sandbox cannot apply an address-space rlimit or a network namespace; it still enforces timeout, CPU/file/fd limits, scrubbed env and a Python-level socket block. On Linux `unshare -rn` and `RLIMIT_NPROC` are used when available; `SANDBOX_BACKEND=docker` (see `Dockerfile.sandbox`) gives full isolation.
- The Streamlit profile dropdown is a **demo-only**, process-global identity switch; the server binds to `127.0.0.1` by default (`CODEFORGE_HOST` to override). Do not expose it publicly.
- Schema is created with `create_all` (no migrations); delete the local sqlite files after model changes.

---

## Live Demo Narrative
To present or replay the four core incidents:
```bash
uv run python scripts/demo.py   # 5 incidents (model scripted for 1-3 so it runs offline)
```
See [`demo/demo_script.md`](demo/demo_script.md) for the complete presenter narration.
