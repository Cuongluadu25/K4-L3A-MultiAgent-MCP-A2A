"""Decision engine: turn specialist findings into one case verdict.

Two sources of truth, in this order:

1. the customer's claims give a *hypothesis* -- never a conclusion;
2. the MCP evidence decides whether that hypothesis survives.

A claim that survives keeps its issue code. A claim the evidence contradicts is
dropped in favour of whatever the evidence does support, and the disagreement is
recorded in `data_conflicts`. When no evidence supports any issue at all and a
required domain is missing, the case is `insufficient_evidence`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .agents.policy import rule_for
from .evidence import CaseContext
from .extract import money, to_float
from .models import (
    AGENT_ORDER_ITEM,
    AGENT_PAYMENT,
    AGENT_POLICY,
    AGENT_SHIPMENT,
    ALL_ISSUES,
    ISSUE_CANCELED_PAID,
    ISSUE_DUPLICATE_CHARGE,
    ISSUE_INSUFFICIENT,
    ISSUE_LATE_LOGISTICS,
    ISSUE_LATE_SELLER,
    ISSUE_PAYMENT_MISMATCH,
    ISSUE_REFUND_FAILED,
    ISSUE_REFUND_PENDING,
    ISSUE_UNAVAILABLE_PAID,
    ISSUE_UNSUPPORTED,
    ISSUE_VALID_SPLIT,
    STATUS_INVESTIGATE,
    STATUS_NO_ACTION,
    Decision,
    Finding,
)

# Claim topics that describe a refund request rather than a root cause.
REQUEST_TOPICS = {"requested_full_refund", "requested_partial_refund"}

CONFIDENCE_VERIFIED = 0.95
CONFIDENCE_FROM_EVIDENCE = 0.72
CONFIDENCE_INSUFFICIENT = 0.3
# Ceiling once the evidence contradicts itself. Never 1.0: a verdict drawn from
# conflicting sources is defensible, not certain.
CONFIDENCE_WITH_CONFLICT = 0.9

# Domains cited for every verdict, plus the conditional ones below.
BASE_DOMAINS = ("order", "item", "payment", "seller", "policy")


class _Model:
    """Read-only view over the findings so the rules stay declarative."""

    def __init__(self, findings: dict[str, Finding]) -> None:
        self.findings = findings

    def get(self, key: str, default: Any = None) -> Any:
        for finding in self.findings.values():
            value = finding.get(key, None)
            if value is not None:
                return value
        return default

    def status_of(self, agent: str) -> str:
        finding = self.findings.get(agent)
        return finding.status if finding else "insufficient_evidence"

    @property
    def order_status(self) -> str:
        return str(self.get("order.status", "unknown"))

    @property
    def paid(self) -> bool:
        return to_float(money(self.get("payments.captured_total"))) > 0

    @property
    def late(self) -> bool:
        return bool(self.get("shipment.late_by_timestamps", False))

    @property
    def late_actors(self) -> list[str]:
        return list(self.get("shipment.late_actors") or [])

    @property
    def mismatch(self) -> bool:
        return bool(self.get("payments.mismatch_present", False))

    @property
    def duplicate(self) -> bool:
        return bool(self.get("payments.duplicate_signatures") or [])

    @property
    def refund_statuses(self) -> list[str]:
        return list(self.get("refund.statuses") or [])

    @property
    def sequentials(self) -> list[str]:
        return list(self.get("payments.sequentials") or [])

    @property
    def event_disagrees(self) -> bool:
        return bool(self.get("shipment.event_disagrees_with_timestamps", False))

    @property
    def core_evidence_complete(self) -> bool:
        return all(
            self.status_of(agent) == "ok"
            for agent in (AGENT_ORDER_ITEM, AGENT_PAYMENT, AGENT_SHIPMENT, AGENT_POLICY)
        )


# --- per-issue verification -------------------------------------------------


def _supports(model: _Model, issue: str) -> bool:
    """Does the evidence actually support this issue code?"""
    if issue == ISSUE_CANCELED_PAID:
        return model.order_status == "canceled" and model.paid
    if issue == ISSUE_UNAVAILABLE_PAID:
        return model.order_status == "unavailable" and model.paid
    if issue == ISSUE_LATE_SELLER:
        return model.late and "seller" in model.late_actors
    if issue == ISSUE_LATE_LOGISTICS:
        return model.late and "logistics_provider" in model.late_actors
    if issue == ISSUE_VALID_SPLIT:
        # A split payment is several sequentials with nothing wrong on them. A
        # stray refund row does not undo that, so it is deliberately not
        # consulted here: refund traffic rides along with most cases.
        return len(model.sequentials) >= 2 and not model.mismatch and not model.duplicate
    if issue == ISSUE_PAYMENT_MISMATCH:
        return model.mismatch
    if issue == ISSUE_DUPLICATE_CHARGE:
        return model.duplicate
    if issue == ISSUE_REFUND_PENDING:
        return "pending" in model.refund_statuses
    if issue == ISSUE_REFUND_FAILED:
        return "failed" in model.refund_statuses
    if issue == ISSUE_UNSUPPORTED:
        # A late-delivery label the timestamps do not back up, or a claim with
        # nothing behind it at all, both land here.
        return model.event_disagrees or not _any_issue_supported(model)
    if issue == ISSUE_INSUFFICIENT:
        return not model.core_evidence_complete
    return False


def _any_issue_supported(model: _Model) -> bool:
    return any(
        _supports(model, issue)
        for issue in ALL_ISSUES
        if issue not in (ISSUE_UNSUPPORTED, ISSUE_INSUFFICIENT)
    )


# Evidence-first fallback, most specific signal first.
FALLBACK_ORDER = (
    ISSUE_CANCELED_PAID,
    ISSUE_UNAVAILABLE_PAID,
    ISSUE_REFUND_FAILED,
    ISSUE_REFUND_PENDING,
    ISSUE_PAYMENT_MISMATCH,
    ISSUE_DUPLICATE_CHARGE,
    ISSUE_LATE_SELLER,
    ISSUE_LATE_LOGISTICS,
    ISSUE_VALID_SPLIT,
)


def _claim_topics(ctx: CaseContext) -> list[str]:
    return [str(claim.get("topic")) for claim in ctx.claims]


def _primary_claim_topic(ctx: CaseContext) -> str | None:
    for topic in _claim_topics(ctx):
        if topic in ALL_ISSUES and topic not in REQUEST_TOPICS:
            return topic
    return None


def _choose_issue(ctx: CaseContext, model: _Model) -> tuple[str, float, bool]:
    """Returns (issue, confidence, claim_verified)."""
    candidate = _primary_claim_topic(ctx)

    if _supports(model, ISSUE_INSUFFICIENT):
        return ISSUE_INSUFFICIENT, CONFIDENCE_INSUFFICIENT, False

    if candidate is not None and _supports(model, candidate):
        return candidate, CONFIDENCE_VERIFIED, True

    # A late-delivery claim whose timestamps ARE late but whose event names no
    # actor is not refuted -- the event only settles *which* party, not whether
    # the lateness happened. Keep the claim and lower the confidence.
    if candidate in (ISSUE_LATE_SELLER, ISSUE_LATE_LOGISTICS) and model.late:
        return candidate, CONFIDENCE_FROM_EVIDENCE, True

    for issue in FALLBACK_ORDER:
        if _supports(model, issue):
            return issue, CONFIDENCE_FROM_EVIDENCE, False

    if _supports(model, ISSUE_UNSUPPORTED) and model.core_evidence_complete:
        return ISSUE_UNSUPPORTED, 0.9, False

    return ISSUE_UNSUPPORTED, CONFIDENCE_INSUFFICIENT, False


# --- output assembly --------------------------------------------------------


def _cite(ctx: CaseContext, *domains: str) -> list[str]:
    """Only refs that are actually in this case's registry, deduped and capped."""
    refs: list[str] = []
    for ref in ctx.registry.refs(*domains):
        if ref not in refs:
            refs.append(ref)
    return refs[:30]


def _refund_for(rule: dict[str, Any]) -> Decimal:
    return money(rule.get("refund_brl"))


def _responsible_parties(rule: dict[str, Any], model: _Model) -> list[dict[str, Any]]:
    parties = rule.get("responsible_parties")
    if not isinstance(parties, list) or not parties:
        return []
    seller_ids = list(model.get("items.seller_ids") or [])
    result: list[dict[str, Any]] = []
    for party in parties[:5]:
        if not isinstance(party, dict):
            continue
        party_type = str(party.get("party_type") or "unknown")
        party_id = party.get("party_id")
        if party_type == "seller":
            # The rule names a party type; the case supplies the actual seller.
            party_id = seller_ids[0] if seller_ids else None
        result.append({"party_type": party_type, "party_id": party_id})
    return result


def _data_conflicts(
    ctx: CaseContext, model: _Model, primary: str, claim_verified: bool
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []

    candidate = _primary_claim_topic(ctx)
    if candidate is not None and not claim_verified and candidate != primary:
        conflicts.append(
            {
                "field": "primary_issue",
                "sources": ["customer_claim", "mcp_evidence"],
                "selected_source": "mcp_evidence",
                "resolution_code": "CLAIM_CONTRADICTED_BY_EVIDENCE",
            }
        )

    if model.mismatch:
        conflicts.append(
            {
                "field": "payment_total_brl",
                "sources": ["get_order_items", "get_payment_timeline"],
                "selected_source": "get_payment_timeline",
                "resolution_code": "RECONCILIATION_MISMATCH_OPEN",
            }
        )

    if model.event_disagrees:
        conflicts.append(
            {
                "field": "delivery_timeliness",
                "sources": ["get_shipment_summary", "get_order"],
                "selected_source": "get_order",
                "resolution_code": "EVENT_LABEL_DISAGREES_WITH_TIMESTAMPS",
            }
        )

    return conflicts[:5]


def _claim_assessments(
    ctx: CaseContext, primary: str, refund: Decimal, refs: list[str]
) -> list[dict[str, Any]]:
    assessments: list[dict[str, Any]] = []
    for claim in ctx.claims[:5]:
        claim_id = str(claim.get("claim_id"))
        topic = str(claim.get("topic"))
        if topic in REQUEST_TOPICS:
            verdict = "supported" if refund > 0 else "unsupported"
            confidence = 0.9 if refund > 0 else 0.75
        elif topic == primary:
            verdict = "supported"
            confidence = 0.95
        elif topic in ALL_ISSUES:
            verdict = "unsupported"
            confidence = 0.8
        else:
            verdict = "insufficient_evidence"
            confidence = 0.4
        assessments.append(
            {
                "claim_id": claim_id,
                "verdict": verdict,
                "confidence": confidence,
                "evidence_refs": refs[:10],
            }
        )
    return assessments


def decide(ctx: CaseContext, findings: dict[str, Finding]) -> Decision:
    model = _Model(findings)
    policy_finding = findings[AGENT_POLICY]

    primary, confidence, claim_verified = _choose_issue(ctx, model)
    rule = rule_for(policy_finding, primary)

    case_status = str(rule.get("case_status") or STATUS_INVESTIGATE)
    action = str(rule.get("recommended_action") or "review_case")
    refund = _refund_for(rule)
    if case_status == STATUS_NO_ACTION:
        refund = Decimal("0.00")

    parties = _responsible_parties(rule, model)

    domains = list(BASE_DOMAINS)
    if primary in (ISSUE_LATE_SELLER, ISSUE_LATE_LOGISTICS) or model.late:
        domains.append("shipment")
    if primary in (ISSUE_REFUND_PENDING, ISSUE_REFUND_FAILED) or model.refund_statuses:
        domains.append("refund")
    refs = _cite(ctx, *domains)
    if not refs:
        refs = _cite(ctx, *[item.domain for item in ctx.registry.all()])

    refund_lines: list[dict[str, Any]] = []
    if refund > 0:
        entity_id = next(
            (
                p["party_id"]
                for p in parties
                if p.get("party_type") == "seller" and p.get("party_id")
            ),
            None,
        )
        refund_lines.append(
            {
                "reason_code": action,
                "amount_brl": to_float(refund),
                "entity_id": entity_id,
            }
        )

    conflicts = _data_conflicts(ctx, model, primary, claim_verified)
    if conflicts:
        # Something in the evidence contradicts something else. The verdict may
        # still be right, but it is no longer certain.
        confidence = min(confidence, CONFIDENCE_WITH_CONFLICT)

    decision = Decision(
        primary_issue=primary,
        case_status=case_status,
        confidence=confidence,
        ranked_causes=[{"cause_code": primary.upper(), "rank": 1}],
        responsible_parties=parties,
        recommended_refund_brl=to_float(refund),
        refund_lines=refund_lines,
        resolution_actions=[action],
        data_conflicts=conflicts,
        claim_assessments=_claim_assessments(ctx, primary, refund, refs),
    )
    return decision


def evidence_refs_for(
    ctx: CaseContext, decision: Decision, findings: dict[str, Finding]
) -> list[str]:
    domains = list(BASE_DOMAINS)
    model = _Model(findings)
    if decision.primary_issue in (ISSUE_LATE_SELLER, ISSUE_LATE_LOGISTICS) or model.late:
        domains.append("shipment")
    if (
        decision.primary_issue in (ISSUE_REFUND_PENDING, ISSUE_REFUND_FAILED)
        or model.refund_statuses
    ):
        domains.append("refund")
    refs = _cite(ctx, *domains)
    return refs or _cite(ctx, *[e.domain for e in ctx.registry.all()])
