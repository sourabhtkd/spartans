.PHONY: test gate gate-demo lint typecheck record bench-chaos bench-cost docker-build up down logs

lint:
	uv run ruff check .

typecheck:
	uv run mypy codeforge

test:
	uv run pytest tests -q -m "not live"

gate:
	uv run pytest evals -v -m "not live"

# Live red -> green demo: weaken a policy rule, watch the gate name the failing cases, restore, green.
gate-demo:
	uv run python scripts/gate_demo.py

# Record LLM-backed golden cases against a real provider (needs GROQ_API_KEY).
record:
	uv run pytest evals --record -m "not never"

bench-chaos:
	uv run python scripts/chaos_run.py

bench-cost:
	uv run python scripts/cost_benchmark.py

DC = docker compose --env-file .env.docker

docker-build:
	$(DC) build app

up:
	@test -f .env.docker || cp .env.docker.example .env.docker
	$(DC) up -d

down:
	$(DC) down

logs:
	$(DC) logs -f app langfuse-web
