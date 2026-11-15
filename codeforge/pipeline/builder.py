"""Builds the LangGraph state machine. Read EDGES top to bottom to follow a request."""
import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from codeforge.config import get_settings
from codeforge.pipeline import nodes, routing
from codeforge.pipeline.tracing import traced_node
from codeforge.state import CodeForgeState

# node name -> function
NODES = {
    "ingress_guard": nodes.ingress_guard,
    "cache_lookup": nodes.cache_lookup,
    "router": nodes.router_node,
    "requirements": nodes.requirements_node,
    "codegen": nodes.codegen_node,
    "testgen": nodes.testgen_node,
    "test_validator": nodes.test_validator_node,
    "policy_gate": nodes.policy_gate_node,
    "refuse": nodes.refuse_node,
    "human_approval": nodes.human_approval_node,
    "sandbox_run": nodes.sandbox_run_node,
    "evaluator": nodes.evaluator_node,
    "cache_store": nodes.cache_store_node,
    "escalate_check": nodes.escalate_check_node,
    "report": nodes.report_node,
}

# Always go from A to B.
EDGES = [
    (START, "ingress_guard"),
    ("requirements", "codegen"),
    ("refuse", "escalate_check"),
    ("evaluator", "cache_store"),
    ("cache_store", "escalate_check"),
    ("escalate_check", "report"),
    ("report", END),
]

# After this node, ask the routing function where to go next.
DECISIONS = {
    "ingress_guard": routing.route_after_ingress,
    "cache_lookup": routing.route_after_cache,
    "router": routing.route_after_router,
    "codegen": routing.route_after_codegen,
    "testgen": routing.route_after_testgen,
    "test_validator": routing.route_after_validator,
    "policy_gate": routing.route_after_policy,
    "human_approval": routing.route_after_approval,
    "sandbox_run": routing.route_after_sandbox,
}

_checkpointers: dict[str, SqliteSaver] = {}


def shared_checkpointer() -> SqliteSaver:
    """One SqliteSaver per checkpoint file (env CODEFORGE_CHECKPOINT_PATH, else config)."""
    path = get_settings().checkpoint_path
    if path not in _checkpointers:
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        _checkpointers[path] = SqliteSaver(connection)
    return _checkpointers[path]


def build_graph(checkpointer: Any | None = None) -> Any:
    """Compile the CodeForge graph. Pass a checkpointer to keep state somewhere else (tests use memory)."""
    graph = StateGraph(CodeForgeState)
    for name, node in NODES.items():
        graph.add_node(name, traced_node(name, node))
    for source, target in EDGES:
        graph.add_edge(source, target)
    for source, decide in DECISIONS.items():
        graph.add_conditional_edges(source, decide)
    return graph.compile(checkpointer=checkpointer or shared_checkpointer())
