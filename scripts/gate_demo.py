"""Live demo of the eval gate: weaken one policy rule -> gate RED (names the failing cases) -> restore -> GREEN.

Safe by construction: the original file is backed up inside the repo and restored in a `finally`
block, even if the demo is interrupted.

Usage: uv run python scripts/gate_demo.py
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "codeforge" / "policy" / "code_policy.py"
BACKUP_DIR = ROOT / ".gate_backup"
GATE = [sys.executable, "-m", "pytest", "evals", "-q", "-s", "-p", "no:cacheprovider", "-m", "not live"]


def run_gate(label: str) -> bool:
    print(f"\n=== {label} ===")
    proc = subprocess.run(GATE, cwd=ROOT, capture_output=True, text=True, check=False)
    failed = sorted(set(re.findall(r"FAILED evals/test_golden.py::test_golden_case\[([^\]]+)\]", proc.stdout)))
    summary = re.search(r"GOLDEN GATE: HARD \d[^\n]*", proc.stdout)
    print(summary.group(0) if summary else "(gate summary not printed)")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    print(lines[-1] if lines else "")
    if failed:
        print("failing cases:", ", ".join(failed))
    print("GATE:", "GREEN" if proc.returncode == 0 else "RED")
    return proc.returncode == 0


def main() -> int:
    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / "code_policy.py"
    shutil.copy2(POLICY, backup)
    try:
        original = POLICY.read_text(encoding="utf-8")
        weakened = original.replace('    "os",\n', "", 1)  # allow `import os`
        assert weakened != original, "could not weaken the rule (pattern not found)"
        print('Weakening one rule: removing "os" from DENY_IMPORTS ...')
        POLICY.write_text(weakened, encoding="utf-8")
        red = not run_gate("Gate with the weakened rule (expected RED)")
    finally:
        shutil.copy2(backup, POLICY)
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
        print("\nRule restored.")
    green = run_gate("Gate after restoring the rule (expected GREEN)")
    return 0 if (red and green) else 1


if __name__ == "__main__":
    sys.exit(main())
