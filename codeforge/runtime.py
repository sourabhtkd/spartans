"""Process-wide cached state (settings, LLM clients, circuit breakers, semantic cache).

`reset_runtime_state()` forgets all of it. Tests and benchmarks call it so each run starts clean
and picks up any environment variables they just changed.
"""
from codeforge import llm
from codeforge.config import clear_settings_cache
from codeforge.cost import semantic_cache


def reset_runtime_state() -> None:
    clear_settings_cache()
    llm.clear_llm_cache()
    llm.reset_breakers()
    semantic_cache.reset_semantic_cache()
