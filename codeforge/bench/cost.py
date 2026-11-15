"""Cost & latency benchmark (R4): a fixed 20-request script run twice.

  baseline  : every agent uses the large model, semantic cache OFF
  optimized : model cascade (small for router/requirements/evaluator) + guarded semantic cache

Tokens, latency and cost are read from the `metrics` table written by the metering callback.
Quality is measured by running generated code against the HIDDEN reference tests.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func

from codeforge import llm
from codeforge.bench.chaos import isolated_run
from codeforge.config import get_settings
from codeforge.entrypoint import submit
from codeforge.graph import build_graph
from codeforge.sandbox.runner import run_pytest_sandboxed
from codeforge.store.db import Metric, init_db

ROOT = Path(__file__).resolve().parents[2]
COST_SCRIPT = ROOT / "evals" / "cost_script.json"
REFERENCE_DIR = ROOT / "evals" / "reference_tests"


@dataclass
class ModeResult:
    mode: str
    requests: int = 0
    ref_passed: int = 0
    cache_hits: int = 0
    wall_s: list[float] = field(default_factory=list)
    per_agent: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return sum(a["cost"] for a in self.per_agent.values())

    @property
    def total_tokens(self) -> int:
        return int(sum(a["in"] + a["out"] for a in self.per_agent.values()))

    @property
    def calls(self) -> int:
        return int(sum(a["calls"] for a in self.per_agent.values()))

    @property
    def pass_rate(self) -> float:
        return self.ref_passed / self.requests if self.requests else 0.0

    def pct(self, q: float) -> float:
        if not self.wall_s:
            return 0.0
        ordered = sorted(self.wall_s)
        return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def load_script() -> list[dict[str, str]]:
    data = json.loads(COST_SCRIPT.read_text(encoding="utf-8"))
    assert len(data) == 20, "the cost script must contain exactly 20 requests"
    return list(data)


def run_mode(mode: str, script: list[dict[str, str]], offline: bool) -> ModeResult:
    assert mode in {"baseline", "optimized"}
    import os

    result = ModeResult(mode=mode)
    saved_cache_env = os.environ.get("CODEFORGE_CACHE_ENABLED")
    with isolated_run(rate=0.0, outage=False, seed=7, offline=offline, fast=True):
        settings = get_settings()
        original_tiers = dict(settings.agent_tiers)
        if mode == "baseline":
            os.environ["CODEFORGE_CACHE_ENABLED"] = "0"
            for k in settings.agent_tiers:
                settings.agent_tiers[k] = "large"
        else:
            os.environ["CODEFORGE_CACHE_ENABLED"] = "1"
        try:
            graph = build_graph()
            for i, item in enumerate(script, start=1):
                tid = f"{mode}_{i}"
                t0 = time.perf_counter()
                vals = submit(item["prompt"], thread_id=tid, graph=graph).values
                result.wall_s.append(time.perf_counter() - t0)
                result.requests += 1
                result.cache_hits += 1 if vals.get("cache_hit") else 0
                if vals.get("code"):
                    ref = (REFERENCE_DIR / item["reference"]).read_text(encoding="utf-8")
                    result.ref_passed += run_pytest_sandboxed(vals["code"], ref, timeout_s=10).exit_code == 0
            with init_db()() as session:
                rows = (
                    session.query(
                        Metric.agent,
                        func.count(Metric.id),
                        func.sum(Metric.input_tokens),
                        func.sum(Metric.output_tokens),
                        func.sum(Metric.cost_usd),
                        func.sum(Metric.latency_s),
                    )
                    .filter(Metric.request_id.like(f"{mode}_%"))
                    .group_by(Metric.agent)
                    .all()
                )
            result.per_agent = {
                a: {"calls": c, "in": i or 0, "out": o or 0, "cost": cost or 0.0, "latency": lat or 0.0}
                for a, c, i, o, cost, lat in rows
            }
        finally:
            settings.agent_tiers.clear()
            settings.agent_tiers.update(original_tiers)
            if saved_cache_env is None:
                os.environ.pop("CODEFORGE_CACHE_ENABLED", None)
            else:
                os.environ["CODEFORGE_CACHE_ENABLED"] = saved_cache_env
    return result


def verdict(base: ModeResult, opt: ModeResult) -> dict[str, Any]:
    saving = (1 - opt.total_cost / base.total_cost) * 100 if base.total_cost else 0.0
    return {
        "saving_pct": saving,
        "no_quality_drop": opt.pass_rate >= base.pass_rate,
        "meets_target": saving >= 30.0 and opt.pass_rate >= base.pass_rate,
    }


def render_report(base: ModeResult, opt: ModeResult, meta: dict[str, Any]) -> str:
    v = verdict(base, opt)

    def agent_table(r: ModeResult) -> list[str]:
        out = ["| Agent | Calls | Input tok | Output tok | Cost (USD) |", "|---|---|---|---|---|"]
        for a, d in sorted(r.per_agent.items()):
            out.append(f"| {a} | {int(d['calls'])} | {int(d['in'])} | {int(d['out'])} | ${d['cost']:.6f} |")
        out.append(f"| **total** | **{r.calls}** | | | **${r.total_cost:.6f}** |")
        return out

    lines = [
        "# Cost & Latency Benchmark (R4)",
        "",
        f"- **Mode**: {meta['mode']}",
        f"- **Script**: fixed {base.requests}-request script (`evals/cost_script.json`)",
        ("- **Source of numbers**: `metrics` table written by the metering callback "
        "(tokens from provider `usage_metadata`; prices from `config.yaml`)"),
        "",
        "## Before: baseline (large model everywhere, no cache)",
        "",
        *agent_table(base),
        "",
        "## After: optimized (model cascade + guarded semantic cache)",
        "",
        *agent_table(opt),
        "",
        "## Comparison",
        "",
        "| Metric | Baseline | Optimized |",
        "|---|---|---|",
        f"| Total cost (USD) | ${base.total_cost:.6f} | ${opt.total_cost:.6f} |",
        f"| Total tokens | {base.total_tokens} | {opt.total_tokens} |",
        f"| Model calls | {base.calls} | {opt.calls} |",
        f"| Cache hits | {base.cache_hits} | {opt.cache_hits} |",
        (f"| Wall latency p50 / p95 (s) | {base.pct(0.5):.2f} / {base.pct(0.95):.2f} | "
        f"{opt.pct(0.5):.2f} / {opt.pct(0.95):.2f} |"),
        (f"| Hidden-reference pass rate | {base.ref_passed}/{base.requests} ({base.pass_rate:.0%}) | "
        f"{opt.ref_passed}/{opt.requests} ({opt.pass_rate:.0%}) |"),
        "",
        (f"**Saving: {v['saving_pct']:.1f}%** | quality drop: **{'NO' if v['no_quality_drop'] else 'YES'}** | "
        f"target (>=30% cheaper, no drop): **{'MET' if v['meets_target'] else 'NOT MET'}**"),
    ]
    if meta.get("golden"):
        lines += ["", f"Golden gate (pytest evals): {meta['golden']}"]
    if meta["mode"].startswith("SIMULATED"):
        lines += [
            "",
            ("> ⚠️ SIMULATED PROVIDER: token counts are estimated from text length (~4 chars/token) because "
            "no API key was set. Re-run with `GROQ_API_KEY` for real usage numbers."),
        ]
    return "\n".join(lines) + "\n"


def reset_after() -> None:
    llm.reset_breakers()
