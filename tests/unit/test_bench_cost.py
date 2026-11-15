from codeforge.bench.cost import (
    ModeResult,
    load_script,
    render_report,
    run_mode,
    verdict,
)


def test_cost_script_is_fixed_20_with_repeats():
    script = load_script()
    assert len(script) == 20
    prompts = [s["prompt"] for s in script]
    assert len(set(prompts)) < len(prompts)  # repeats exist so the cache can be exercised


def test_optimized_is_cheaper_with_no_quality_drop_offline():
    script = load_script()[:12]
    base = run_mode("baseline", script, offline=True)
    opt = run_mode("optimized", script, offline=True)
    assert base.cache_hits == 0 and opt.cache_hits > 0
    assert base.total_cost > 0 and base.calls > opt.calls  # numbers come from the metrics table
    v = verdict(base, opt)
    assert v["no_quality_drop"] and v["saving_pct"] >= 30
    assert "MET" in render_report(base, opt, {"mode": "SIMULATED provider (offline)"})


def test_verdict_is_computed_not_hardcoded():
    base = ModeResult("baseline", requests=4, ref_passed=4, per_agent={"a": {"calls": 1, "in": 1, "out": 1, "cost": 1.0, "latency": 0}})
    cheap = ModeResult("optimized", requests=4, ref_passed=4, per_agent={"a": {"calls": 1, "in": 1, "out": 1, "cost": 0.5, "latency": 0}})
    worse = ModeResult("optimized", requests=4, ref_passed=3, per_agent={"a": {"calls": 1, "in": 1, "out": 1, "cost": 0.1, "latency": 0}})
    assert verdict(base, cheap)["meets_target"] is True
    assert verdict(base, worse)["meets_target"] is False  # cheap but quality dropped
    assert "NOT MET" in render_report(base, worse, {"mode": "LIVE provider"})
