"""Shared types for the L3A multi-agent workflow.

Frozen interface: every agent reads these, only the coordinator owns the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Lifecycle constants ----------------------------------------------------

ISSUE_CANCELED_PAID = "canceled_order_paid"
ISSUE_UNAVAILABLE_PAID = "unavailable_order_paid"
ISSUE_LATE_SELLER = "late_delivery_seller"
ISSUE_LATE_LOGISTICS = "late_delivery_logistics"
ISSUE_VALID_SPLIT = "valid_split_payment"
ISSUE_PAYMENT_MISMATCH = "payment_mismatch"
ISSUE_DUPLICATE_CHARGE = "duplicate_charge"
ISSUE_REFUND_PENDING = "refund_pending"
ISSUE_REFUND_FAILED = "refund_failed"
ISSUE_UNSUPPORTED = "unsupported_claim"
ISSUE_INSUFFICIENT = "insufficient_evidence"

ALL_ISSUES = (
    ISSUE_CANCELED_PAID,
    ISSUE_UNAVAILABLE_PAID,
    ISSUE_LATE_SELLER,
    ISSUE_LATE_LOGISTICS,
    ISSUE_VALID_SPLIT,
    ISSUE_PAYMENT_MISMATCH,
    ISSUE_DUPLICATE_CHARGE,
    ISSUE_REFUND_PENDING,
    ISSUE_REFUND_FAILED,
    ISSUE_UNSUPPORTED,
    ISSUE_INSUFFICIENT,
)

STATUS_ACTION = "action_required"
STATUS_NO_ACTION = "no_action"
STATUS_INVESTIGATE = "needs_investigation"

# Actor names used in the observable trace. Kept stable so the workflow
# component can match actor collaboration across lifecycles.
AGENT_COORDINATOR = "coordinator"
AGENT_ORDER_ITEM = "order-item-agent"
AGENT_PAYMENT = "payment-agent"
AGENT_SHIPMENT = "shipment-agent"
AGENT_POLICY = "policy-agent"
AGENT_VERIFIER = "verifier"


# --- Evidence ---------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """One validated MCP evidence envelope, scoped to exactly one case."""

    evidence_ref: str
    domain: str
    data: Any
    tool_name: str


@dataclass(frozen=True)
class Fact:
    """A single normalized claim about the case, always backed by evidence."""

    key: str
    value: Any
    evidence_refs: tuple[str, ...] = ()


@dataclass
class Finding:
    """What one specialist agent concluded. Never contains guessed data."""

    agent: str
    facts: list[Fact] = field(default_factory=list)
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)

    def add(self, key: str, value: Any, refs: tuple[str, ...] | list[str] = ()) -> None:
        self.facts.append(Fact(key, value, tuple(refs)))

    def get(self, key: str, default: Any = None) -> Any:
        for fact in self.facts:
            if fact.key == key:
                return fact.value
        return default

    def refs_for(self, *keys: str) -> list[str]:
        wanted = set(keys)
        refs: list[str] = []
        for fact in self.facts:
            if fact.key in wanted:
                refs.extend(fact.evidence_refs)
        return refs

    def all_refs(self) -> list[str]:
        refs: list[str] = []
        for fact in self.facts:
            for ref in fact.evidence_refs:
                if ref not in refs:
                    refs.append(ref)
        return refs


# --- Decision ---------------------------------------------------------------


@dataclass
class Decision:
    """The policy agent's verdict, ready to be assembled into the output."""

    primary_issue: str = ISSUE_INSUFFICIENT
    case_status: str = STATUS_INVESTIGATE
    confidence: float = 0.3
    ranked_causes: list[dict[str, Any]] = field(default_factory=list)
    responsible_parties: list[dict[str, Any]] = field(default_factory=list)
    recommended_refund_brl: float = 0.0
    refund_lines: list[dict[str, Any]] = field(default_factory=list)
    resolution_actions: list[str] = field(default_factory=list)
    data_conflicts: list[dict[str, Any]] = field(default_factory=list)
    claim_assessments: list[dict[str, Any]] = field(default_factory=list)
