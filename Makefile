.PHONY: test gate gate-red gate-green lint

lint:
	uv run ruff check .

test:
	uv run pytest tests/unit -q

gate:
	uv run pytest evals/test_golden.py -v

gate-red:
	@echo "Weakening policy rule (removing 'os' from DENY list) to demonstrate gate tripping red..."
	@sed -i.bak 's/"os",//g' codeforge/policy/code_policy.py
	@echo "Running golden eval gate (expecting failure on os deny case)..."
	-uv run pytest evals/test_golden.py -k "policy_deny_os"
	@git checkout codeforge/policy/code_policy.py
	@rm -f codeforge/policy/code_policy.py.bak
	@echo "Gate tripped red as expected and demonstrated!"

gate-green:
	@echo "Ensuring pristine code policy..."
	@git checkout codeforge/policy/code_policy.py
	@echo "Running golden eval gate..."
	uv run pytest evals/test_golden.py -v
