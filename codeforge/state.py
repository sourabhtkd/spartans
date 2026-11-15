from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from codeforge.config import Identity


class RouteDecision(BaseModel):
    kind: Literal["python_function", "sql", "other"]
    language: str = "python"
    confidence: float = 1.0


class Param(BaseModel):
    name: str
    type: str = "Any"


class Requirements(BaseModel):
    function_name: str
    parameters: list[Param] = Field(default_factory=list)
    returns: str = "Any"
    edge_cases: list[str] = Field(default_factory=list)
    description: str = ""


class GeneratedCode(BaseModel):
    code: str


class GeneratedTests(BaseModel):
    tests: str


class PolicyDecision(BaseModel):
    verdict: Literal["allow", "needs_approval", "deny"]
    reasons: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    exit_code: int | None = None
    timed_out: bool = False
    passed: int = 0
    failed: int = 0
    stdout: str = ""
    last_failing_test: str | None = None
    cached: bool = False


class ApprovalDecision(BaseModel):
    approved: bool
    approver_id: str
    comment: str = ""


class HandoffTicket(BaseModel):
    ticket_id: str
    priority: Literal["P1", "P2", "P3"]
    sla_hours: int = 24
    request_masked: str
    attempts: int = 0
    last_failing_test: str | None = None
    reason: str = ""


class CodeForgeState(TypedDict, total=False):
    thread_id: str
    identity: Identity
    raw_request_masked: str
    history: list[dict[str, Any]]
    route: RouteDecision
    requirements: Requirements
    code: str
    tests: str
    policy: PolicyDecision
    approval: ApprovalDecision | None
    run_result: RunResult | None
    idempotency_key: str | None
    attempts: int
    served_tier: str
    cache_hit: bool
    ticket: HandoffTicket | None
    report: str
    events: list[dict[str, Any]]
