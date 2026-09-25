"""L3A coordinator.

Flow per case:

    coordinator ──task_assigned──▶ order/item, payment, shipment   (parallel-capable)
                ──handoff───────▶ policy agent
                ──handoff───────▶ verifier
                ──▶ output

`case_received` and `case_finalized` are emitted by the CLI around this call, so
this module never emits them again. Every MCP read goes through
`CaseContext.fetch`, which is the only place evidence refs are recorded.
"""

from __future__ import annotations

from typing import Any

from . import OUTPUT_SCHEMA_VERSION
from .agents import order_item, payment, policy, shipment
from .agents.verifier import as_trace_attributes, verify
from .decision import decide, evidence_refs_for
from .evidence import CaseContext
from .mcp_gateway import EvidenceGateway
from .models import (
    AGENT_COORDINATOR,
    AGENT_ORDER_ITEM,
    AGENT_PAYMENT,
    AGENT_POLICY,
    AGENT_SHIPMENT,
    AGENT_VERIFIER,
    Finding,
)
from .trace import TraceWriter

# Guard rails from the public output schema.
MAX_ID_SET = 20
MAX_EVIDENCE_REFS = 30


def _cap(values: list[Any], limit: int) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen[:limit]


def _assemble(
    ctx: CaseContext,
    decision: Any,
    findings: dict[str, Finding],
    evidence_refs: list[str],
) -> dict[str, Any]:
    order_item_finding = findings[AGENT_ORDER_ITEM]
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "case_id": ctx.case_id,
        "assessment": {
            "primary_issue": decision.primary_issue,
            "case_status": decision.case_status,
            "confidence": round(float(decision.confidence), 4),
        },
        "affected_entities": {
            "order_ids": [ctx.order_id],
            "item_ids": _cap(list(order_item_finding.get("items.ids") or []), MAX_ID_SET),
            "seller_ids": _cap(list(order_item_finding.get("items.seller_ids") or []), MAX_ID_SET),
            "payment_references": [],
            "shipment_ids": [],
        },
        "claim_assessments": decision.claim_assessments,
        "root_cause_analysis": {
            "ranked_causes": decision.ranked_causes,
            "responsible_parties": decision.responsible_parties,
        },
        "evidence_refs": _cap(list(evidence_refs), MAX_EVIDENCE_REFS),
        "data_conflicts": decision.data_conflicts,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": decision.recommended_refund_brl,
            "refund_lines": decision.refund_lines,
        },
        "resolution_actions": _cap(list(decision.resolution_actions), 8),
    }


def _repair(ctx: CaseContext, output: dict[str, Any]) -> dict[str, Any]:
    """Drop any ref that is not in this case's registry.

    Nothing outside the registry should ever reach the output; this is the last
    line of defence so an unexpected ref degrades to a missing citation rather
    than to a hard-gate failure.
    """
    output["evidence_refs"] = [
        ref for ref in output.get("evidence_refs", []) if ctx.registry.contains(ref)
    ]
    for claim in output.get("claim_assessments", []) or []:
        claim["evidence_refs"] = [
            ref for ref in claim.get("evidence_refs", []) if ctx.registry.contains(ref)
        ]
    return output


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Run the coordinator and specialist agents for one case."""
    ctx = CaseContext(case, gateway, trace)
    findings: dict[str, Finding] = {}

    specialists = (AGENT_ORDER_ITEM, AGENT_PAYMENT, AGENT_SHIPMENT)
    for agent in specialists:
        trace.emit(
            case_id=ctx.case_id,
            event_type="task_assigned",
            actor=AGENT_COORDINATOR,
            target=agent,
            decision_code="COLLECT_EVIDENCE",
            attributes={"order_id": ctx.order_id},
        )

    # The gateway holds a single MCP session, so specialists run in sequence
    # rather than concurrently on the same connection.
    for agent, module in (
        (AGENT_ORDER_ITEM, order_item),
        (AGENT_PAYMENT, payment),
        (AGENT_SHIPMENT, shipment),
    ):
        findings[agent] = await module.run(ctx)
        finding = findings[agent]
        trace.emit(
            case_id=ctx.case_id,
            event_type="handoff",
            actor=agent,
            target=AGENT_POLICY,
            decision_code=finding.status.upper(),
            evidence_refs=finding.all_refs()[:20] or None,
            attributes={"facts": len(finding.facts), "warnings": len(finding.warnings)},
        )

    findings[AGENT_POLICY] = await policy.run(ctx)
    policy_finding = findings[AGENT_POLICY]
    if policy_finding.status != "ok":
        trace.emit(
            case_id=ctx.case_id,
            event_type="handoff",
            actor=AGENT_POLICY,
            target=AGENT_VERIFIER,
            decision_code="POLICY_UNAVAILABLE",
        )

    decision = decide(ctx, findings)
    evidence_refs = evidence_refs_for(ctx, decision, findings)
    trace.emit(
        case_id=ctx.case_id,
        event_type="policy_decided",
        actor=AGENT_POLICY,
        target=decision.primary_issue,
        decision_code=decision.primary_issue.upper(),
        evidence_refs=evidence_refs[:20] or None,
        attributes={
            "case_status": decision.case_status,
            "refund_brl": decision.recommended_refund_brl,
            "confidence": decision.confidence,
        },
    )

    output = _assemble(ctx, decision, findings, evidence_refs)

    problems = verify(ctx, output, findings)
    if problems:
        output = _repair(ctx, output)
        problems = verify(ctx, output, findings)

    trace.emit(
        case_id=ctx.case_id,
        event_type="verification_completed",
        actor=AGENT_VERIFIER,
        target=decision.primary_issue,
        decision_code="PASS" if not problems else "REPAIRED" if len(problems) < 3 else "FAIL",
        evidence_refs=evidence_refs[:20] or None,
        attributes=as_trace_attributes(problems, not problems),
    )

    return output
