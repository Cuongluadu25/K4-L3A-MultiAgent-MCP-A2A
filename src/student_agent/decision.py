from __future__ import annotations

from typing import Any

from .models import CaseContext, Finding


def determine_primary_issue(
    ctx: CaseContext,
    order_f: Finding,
    payment_f: Finding,
    shipment_f: Finding,
) -> tuple[str, float]:
    """Determine the primary issue and calibrated confidence based on authoritative evidence."""
    order_status = order_f.facts.get("order_status")

    if order_f.status == "insufficient_evidence" or order_status is None:
        return "insufficient_evidence", 0.30

    claims = ctx.case.get("customer_request", {}).get("claims", [])
    claimed_topics = [c.get("topic") for c in claims if c.get("topic") != "requested_full_refund"]
    target_topic = claimed_topics[0] if claimed_topics else None

    # Check target topic against authoritative evidence
    if target_topic == "canceled_order_paid" and order_status == "canceled":
        return "canceled_order_paid", 0.95

    if target_topic == "unavailable_order_paid" and order_status == "unavailable":
        return "unavailable_order_paid", 0.95

    if target_topic == "late_delivery_seller" and shipment_f.facts.get("has_late_delivery"):
        return "late_delivery_seller", 0.95

    if target_topic == "late_delivery_logistics" and shipment_f.facts.get("has_late_delivery"):
        return "late_delivery_logistics", 0.95

    if target_topic == "payment_mismatch" and payment_f.facts.get("has_reconciliation_mismatch"):
        return "payment_mismatch", 0.95

    if target_topic == "duplicate_charge" and payment_f.facts.get("has_duplicate_charge"):
        return "duplicate_charge", 0.95

    if target_topic == "refund_failed" and payment_f.facts.get("has_refund_failed"):
        return "refund_failed", 0.90

    if target_topic == "refund_pending" and payment_f.facts.get("has_refund_pending"):
        return "refund_pending", 0.85

    if target_topic == "valid_split_payment" and payment_f.facts.get("is_split_payment"):
        return "valid_split_payment", 0.95

    if target_topic == "unsupported_claim":
        return "unsupported_claim", 0.95

    # Fallback to direct evidence-based determination
    if order_status == "canceled":
        return "canceled_order_paid", 0.95
    if order_status == "unavailable":
        return "unavailable_order_paid", 0.95
    if payment_f.facts.get("has_refund_failed"):
        return "refund_failed", 0.90
    if payment_f.facts.get("has_refund_pending"):
        return "refund_pending", 0.85
    if payment_f.facts.get("has_reconciliation_mismatch"):
        return "payment_mismatch", 0.95
    if payment_f.facts.get("has_duplicate_charge"):
        return "duplicate_charge", 0.95
    if shipment_f.facts.get("has_late_delivery"):
        actor = shipment_f.facts.get("late_actor")
        return ("late_delivery_seller" if actor == "seller" else "late_delivery_logistics"), 0.95
    if payment_f.facts.get("is_split_payment"):
        return "valid_split_payment", 0.95

    return "unsupported_claim", 0.90


def build_root_cause(
    primary_issue: str,
    policy_rule: dict[str, Any],
    order_f: Finding,
) -> dict[str, Any]:
    cause_codes = {
        "canceled_order_paid": "CAUSE_ORDER_CANCELED_AFTER_PAYMENT",
        "unavailable_order_paid": "CAUSE_INVENTORY_UNAVAILABLE",
        "late_delivery_seller": "CAUSE_SELLER_DISPATCH_DELAY",
        "late_delivery_logistics": "CAUSE_LOGISTICS_TRANSIT_DELAY",
        "payment_mismatch": "CAUSE_PAYMENT_GATEWAY_MISMATCH",
        "duplicate_charge": "CAUSE_DUPLICATE_PAYMENT_CAPTURED",
        "refund_failed": "CAUSE_REFUND_EXECUTION_FAILURE",
        "refund_pending": "CAUSE_REFUND_PROCESSING_WINDOW",
        "valid_split_payment": "CAUSE_SPLIT_PAYMENT_TRANSACTION",
        "unsupported_claim": "CAUSE_NORMAL_ORDER_FULFILLMENT",
        "insufficient_evidence": "CAUSE_INSUFFICIENT_EVIDENCE",
    }

    cause_code = cause_codes.get(primary_issue, "CAUSE_GENERAL_ANOMALY")
    ranked_causes = [{"cause_code": cause_code, "rank": 1}]

    # Determine responsible parties
    rule_parties = policy_rule.get("responsible_parties", [])
    responsible_parties: list[dict[str, Any]] = []

    for party in rule_parties:
        ptype = party.get("party_type", "unknown")
        pid = party.get("party_id")
        if ptype == "seller":
            seller_ids = order_f.facts.get("seller_ids", [])
            pid = seller_ids[0] if seller_ids else pid
        else:
            pid = None
        responsible_parties.append({"party_type": ptype, "party_id": pid})

    if not responsible_parties:
        responsible_parties.append({"party_type": "unknown", "party_id": None})

    return {
        "ranked_causes": ranked_causes,
        "responsible_parties": responsible_parties[:5],
    }


def build_financial_resolution(
    primary_issue: str,
    policy_rule: dict[str, Any],
    order_id: str,
) -> dict[str, Any]:
    raw_refund = policy_rule.get("refund_brl", 0.0)
    try:
        refund_amount = round(float(raw_refund), 2)
    except (ValueError, TypeError):
        refund_amount = 0.0

    refund_lines: list[dict[str, Any]] = []
    if refund_amount > 0.0:
        reason_code = str(policy_rule.get("recommended_action") or "issue_refund")[:80]
        refund_lines.append({
            "reason_code": reason_code,
            "amount_brl": refund_amount,
            "entity_id": order_id[:128],
        })

    return {
        "currency": "BRL",
        "recommended_refund_brl": refund_amount,
        "refund_lines": refund_lines,
    }


def build_resolution_actions(
    primary_issue: str,
    case_status: str,
    policy_rule: dict[str, Any],
) -> list[str]:
    rec_action = policy_rule.get("recommended_action")

    if case_status == "no_action":
        return ["document_no_action"]

    if case_status == "needs_investigation":
        return ["monitor_refund", "notify_customer_wait"]

    actions: list[str] = []
    if rec_action:
        actions.append(str(rec_action)[:80])

    if primary_issue in ["canceled_order_paid", "unavailable_order_paid"]:
        actions.extend(["notify_customer"])
    elif primary_issue == "late_delivery_seller":
        actions.extend(["penalize_seller", "notify_customer"])
    elif primary_issue == "late_delivery_logistics":
        actions.extend(["logistics_escalation", "notify_customer"])
    elif primary_issue in ["payment_mismatch", "duplicate_charge"]:
        actions.extend(["notify_customer"])
    elif primary_issue == "refund_failed":
        actions.extend(["escalate_payment_gateway"])
    else:
        actions.append("notify_customer")

    # Ensure unique and length bounds
    unique_actions: list[str] = []
    for a in actions:
        if a and a not in unique_actions:
            unique_actions.append(a[:80])
    return unique_actions[:8]


def build_claim_assessments(
    ctx: CaseContext,
    primary_issue: str,
    confidence: float,
    all_refs: list[str],
) -> list[dict[str, Any]]:
    claims = ctx.case.get("customer_request", {}).get("claims", [])
    assessments: list[dict[str, Any]] = []

    for c in claims[:5]:
        cid = str(c.get("claim_id", ""))[:64]
        topic = c.get("topic", "")

        if topic == primary_issue:
            verdict = "supported"
            conf = confidence
        elif topic == "requested_full_refund":
            if primary_issue in ["canceled_order_paid", "unavailable_order_paid"]:
                verdict = "supported"
                conf = confidence
            elif primary_issue in [
                "late_delivery_seller",
                "late_delivery_logistics",
                "duplicate_charge",
                "payment_mismatch",
                "refund_pending",
            ]:
                verdict = "partially_supported"
                conf = min(confidence, 0.90)
            elif primary_issue == "refund_failed":
                verdict = "supported"
                conf = min(confidence, 0.90)
            elif primary_issue in ["valid_split_payment", "unsupported_claim"]:
                verdict = "unsupported"
                conf = confidence
            else:
                verdict = "insufficient_evidence"
                conf = 0.30
        else:
            verdict = "unsupported"
            conf = confidence

        assessments.append({
            "claim_id": cid,
            "verdict": verdict,
            "confidence": round(conf, 2),
            "evidence_refs": all_refs[:20],
        })

    return assessments


def build_data_conflicts(
    primary_issue: str,
) -> list[dict[str, Any]]:
    if primary_issue in ["unsupported_claim", "valid_split_payment"]:
        return [
            {
                "field": "customer_request.claim_validity",
                "sources": ["customer_claim", "mcp_authoritative_records"],
                "selected_source": "mcp_authoritative_records",
                "resolution_code": "CUSTOMER_CLAIM_NOT_SUBSTANTIATED",
            }
        ]
    return []
