from __future__ import annotations

from typing import Any

from .agents import (
    OrderAgent,
    PaymentAgent,
    PolicyAgent,
    ShipmentAgent,
    VerifierAgent,
)
from .decision import (
    build_claim_assessments,
    build_data_conflicts,
    build_financial_resolution,
    build_resolution_actions,
    build_root_cause,
    determine_primary_issue,
)
from .evidence import EvidenceRegistry
from .mcp_gateway import EvidenceGateway
from .models import CaseContext
from .trace import TraceWriter


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent coordinator workflow to investigate an e-commerce dispute.
    
    Coordinates domain specialist agents (Order, Payment, Shipment, Policy) to gather
    authoritative MCP evidence, performs decision reasoning, and verifies all business
    and public contract invariants.
    """
    case_id: str = case["case_id"]
    registry = EvidenceRegistry(case_id)
    ctx = CaseContext(case, gateway, trace, registry)

    # 1. Order specialist investigation
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="order_agent",
        attributes={"task": "inspect_order_items_sellers"},
    )
    order_agent = OrderAgent()
    order_finding = await order_agent.run(ctx)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="order_agent",
        target="coordinator",
        decision_code=order_finding.status,
    )

    # 2. Payment specialist investigation
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="payment_agent",
        attributes={"task": "inspect_payment_and_refund_timeline"},
    )
    payment_agent = PaymentAgent()
    payment_finding = await payment_agent.run(ctx)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="payment_agent",
        target="coordinator",
        decision_code=payment_finding.status,
    )

    # 3. Shipment specialist investigation
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="shipment_agent",
        attributes={"task": "inspect_shipping_and_delivery_events"},
    )
    shipment_agent = ShipmentAgent()
    shipment_finding = await shipment_agent.run(ctx)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="shipment_agent",
        target="coordinator",
        decision_code=shipment_finding.status,
    )

    # 4. Policy specialist investigation
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="policy_agent",
        attributes={"task": "load_authoritative_policy_rules"},
    )
    policy_agent = PolicyAgent()
    policy_finding = await policy_agent.run(ctx)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="policy_agent",
        target="coordinator",
        decision_code=policy_finding.status,
    )

    # 5. Primary issue determination & policy matching
    primary_issue, confidence = determine_primary_issue(
        ctx, order_finding, payment_finding, shipment_finding
    )

    policy_rules = policy_finding.facts.get("rules", {})
    policy_rule = policy_rules.get(primary_issue, {})

    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy_agent",
        decision_code=primary_issue,
        evidence_refs=policy_finding.evidence_refs,
        attributes={"confidence": confidence},
    )

    case_status = policy_rule.get("case_status", "action_required")
    all_refs = registry.all_refs()

    # 6. Assemble candidate output
    output: dict[str, Any] = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "case_status": case_status,
            "confidence": round(confidence, 2),
        },
        "affected_entities": {
            "order_ids": [ctx.claimed_order_id] if ctx.claimed_order_id else [],
            "item_ids": order_finding.facts.get("item_ids", [])[:20],
            "seller_ids": order_finding.facts.get("seller_ids", [])[:20],
            "payment_references": payment_finding.facts.get("payment_references", [])[:20],
            "shipment_ids": shipment_finding.facts.get("shipment_ids", [])[:20],
        },
        "claim_assessments": build_claim_assessments(
            ctx, primary_issue, confidence, all_refs
        ),
        "root_cause_analysis": build_root_cause(
            primary_issue, policy_rule, order_finding
        ),
        "evidence_refs": all_refs,
        "data_conflicts": build_data_conflicts(primary_issue),
        "financial_resolution": build_financial_resolution(
            primary_issue, policy_rule, ctx.claimed_order_id
        ),
        "resolution_actions": build_resolution_actions(
            primary_issue, case_status, policy_rule
        ),
    }

    # 7. Verifier validation
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="verifier_agent",
        attributes={"task": "verify_output_and_evidence_invariants"},
    )
    verifier = VerifierAgent(trace.contracts)
    verifier.verify(output, ctx)

    return output
