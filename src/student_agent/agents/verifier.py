"""Verifier agent: the last gate before an output is allowed to be written.

Checks the invariants that the scorer treats as hard gates (evidence ownership,
entity scope, case id) plus the cross-field consistency rules. Returns a list of
violation codes; an empty list means the output may be finalised.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..evidence import CaseContext
from ..extract import money, to_float
from ..models import (
    AGENT_ORDER_ITEM,
    AGENT_POLICY,
    AGENT_VERIFIER,
    STATUS_ACTION,
    STATUS_INVESTIGATE,
    STATUS_NO_ACTION,
    Finding,
)
from .policy import rule_for

REQUIRED_TOP_LEVEL = (
    "schema_version",
    "case_id",
    "assessment",
    "affected_entities",
    "root_cause_analysis",
    "evidence_refs",
    "data_conflicts",
    "financial_resolution",
    "resolution_actions",
)

ENTITY_KEYS = ("order_ids", "item_ids", "seller_ids", "payment_references", "shipment_ids")


def _violation(code: str, detail: str) -> str:
    return f"{code}:{detail}"


def verify(
    ctx: CaseContext,
    output: dict[str, Any],
    findings: dict[str, Finding],
) -> list[str]:
    problems: list[str] = []

    # 1. Structural completeness (full schema compliance is enforced by the CLI).
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in output]
    if missing:
        problems.append(_violation("MISSING_FIELD", ",".join(missing)))

    # 2. Case id must round-trip unchanged.
    if output.get("case_id") != ctx.case_id:
        problems.append(_violation("CASE_ID_MISMATCH", str(output.get("case_id"))))

    # 3. Every cited ref must exist in this case's registry. Nothing outside it
    #    may reach the output, and nothing invented may appear in it.
    cited: list[str] = []
    for ref in output.get("evidence_refs", []) or []:
        cited.append(ref)
    for claim in output.get("claim_assessments", []) or []:
        cited.extend(claim.get("evidence_refs", []) or [])
    for ref in cited:
        if not ctx.registry.contains(ref):
            problems.append(_violation("UNKNOWN_EVIDENCE_REF", ref))
    if len(set(cited)) != len(cited):
        problems.append(_violation("DUPLICATE_EVIDENCE_REF", str(len(cited))))
    if not cited:
        problems.append(_violation("NO_EVIDENCE_CITED", ctx.case_id))

    # 4. Entity scope: every id we publish must come from this order's evidence.
    known_items = set(findings[AGENT_ORDER_ITEM].get("items.ids") or [])
    known_sellers = set(findings[AGENT_ORDER_ITEM].get("items.seller_ids") or [])
    entities = output.get("affected_entities", {}) or {}
    for key in ENTITY_KEYS:
        if key not in entities:
            problems.append(_violation("MISSING_ENTITY_SET", key))
    for item_id in entities.get("item_ids", []) or []:
        if item_id not in known_items:
            problems.append(_violation("ENTITY_OUT_OF_SCOPE", f"item:{item_id}"))
    for seller_id in entities.get("seller_ids", []) or []:
        if seller_id not in known_sellers:
            problems.append(_violation("ENTITY_OUT_OF_SCOPE", f"seller:{seller_id}"))
    order_ids = entities.get("order_ids", []) or []
    if order_ids and order_ids != [ctx.order_id]:
        problems.append(_violation("ENTITY_OUT_OF_SCOPE", f"order:{order_ids}"))

    # 5. Money totals: the headline refund must equal the sum of its lines.
    financial = output.get("financial_resolution", {}) or {}
    headline = money(financial.get("recommended_refund_brl"))
    lines = financial.get("refund_lines", []) or []
    line_total = sum((money(line.get("amount_brl")) for line in lines), Decimal("0.00"))
    if headline != line_total:
        problems.append(_violation("REFUND_TOTAL_MISMATCH", f"{headline}!={line_total}"))
    if not lines and headline > 0:
        problems.append(_violation("REFUND_LINES_MISSING", str(headline)))

    # 6. Status / refund / action must tell one story.
    assessment = output.get("assessment", {}) or {}
    status = assessment.get("case_status")
    actions = output.get("resolution_actions", []) or []
    if status == STATUS_NO_ACTION and headline > 0:
        problems.append(_violation("NO_ACTION_WITH_REFUND", str(headline)))
    if status == STATUS_ACTION and headline == 0 and not actions:
        problems.append(_violation("ACTION_WITHOUT_REMEDY", ctx.case_id))
    if not actions:
        problems.append(_violation("NO_RESOLUTION_ACTION", ctx.case_id))
    if len(set(actions)) != len(actions):
        problems.append(_violation("DUPLICATE_ACTION", ",".join(actions)))

    # 7. A responsible seller must be one of this order's sellers, and the
    #    party blamed must be the party the policy blames for this issue -- a
    #    seller fault may not be billed to the carrier.
    parties = (output.get("root_cause_analysis", {}) or {}).get("responsible_parties", []) or []
    for party in parties:
        party_id = party.get("party_id")
        if party.get("party_type") == "seller" and party_id and party_id not in known_sellers:
            problems.append(_violation("SELLER_OUT_OF_SCOPE", str(party_id)))
    expected_types = [
        str(item.get("party_type"))
        for item in (rule_for(findings[AGENT_POLICY], str(assessment.get("primary_issue"))).get(
            "responsible_parties"
        ) or [])
        if isinstance(item, dict)
    ]
    actual_types = [str(party.get("party_type")) for party in parties]
    if expected_types and actual_types != expected_types:
        problems.append(
            _violation("PARTY_TYPE_MISMATCH", f"{actual_types}!={expected_types}")
        )

    # 8. Confidence must be a probability, an investigation is never certain,
    #    and conflicting evidence forbids a certainty claim.
    confidence = assessment.get("confidence")
    conflicts = output.get("data_conflicts", []) or []
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        problems.append(_violation("CONFIDENCE_OUT_OF_BOUNDS", str(confidence)))
    else:
        if status == STATUS_INVESTIGATE and float(confidence) > 0.85:
            problems.append(_violation("INVESTIGATION_OVERCONFIDENT", str(confidence)))
        if conflicts and float(confidence) > 0.9:
            problems.append(_violation("OVERCONFIDENT_WITH_CONFLICT", str(confidence)))

    # 9. Claims must be answered one-for-one, with unique ids.
    claim_ids = [str(claim.get("claim_id")) for claim in output.get("claim_assessments", []) or []]
    if len(set(claim_ids)) != len(claim_ids):
        problems.append(_violation("DUPLICATE_CLAIM_ID", ",".join(claim_ids)))
    expected_claims = {str(claim.get("claim_id")) for claim in ctx.claims}
    for claim_id in expected_claims - set(claim_ids):
        problems.append(_violation("UNANSWERED_CLAIM", claim_id))

    # 10. A refund line must not be negative and must name a reason.
    for line in lines:
        if money(line.get("amount_brl")) < 0:
            problems.append(_violation("NEGATIVE_REFUND_LINE", str(line.get("amount_brl"))))
        if not str(line.get("reason_code") or "").strip():
            problems.append(_violation("REFUND_LINE_WITHOUT_REASON", ctx.case_id))

    if to_float(headline) < 0:
        problems.append(_violation("NEGATIVE_REFUND", str(headline)))

    return problems


def as_trace_attributes(problems: list[str], passed: bool) -> dict[str, Any]:
    return {
        "verifier": AGENT_VERIFIER,
        "passed": passed,
        "violation_count": len(problems),
        "violations": ",".join(problems[:10]) or "none",
    }
