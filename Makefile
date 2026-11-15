.PHONY: test gate gate-red gate-green lint

lint:
	uv run ruff check .

test:
	uv run pytest tests/unit -q

gate:
	uv run pytest evals/test_golden.py -v

gate-red:
	@echo "Weakening policy rule (removing 'os' from DENY list) to demonstrate gate tripping red..."
	@cp codeforge/policy/code_policy.py /tmp/code_policy_clean.py
	@python3 -c "p = 'codeforge/policy/code_policy.py'; s = open(p).read(); open(p, 'w').write(s.replace('\"os\",', ''))"
	@echo "Running golden eval gate (expecting failure on os deny case)..."
	-uv run pytest evals/test_golden.py -k "policy_deny_os"
	@cp /tmp/code_policy_clean.py codeforge/policy/code_policy.py
	@rm -f /tmp/code_policy_clean.py
	@echo "Gate tripped red as expected and pristine policy restored!"

gate-green:
	@echo "Running golden eval gate..."
	uv run pytest evals/test_golden.py -v
