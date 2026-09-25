from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from student_agent.agents.verifier import VerifierAgent
from student_agent.contracts import Contracts
from student_agent.decision import (
    build_financial_resolution,
    build_root_cause,
)
from student_agent.evidence import EvidenceRegistry
from student_agent.models import CaseContext, Finding
from student_agent.trace import TraceWriter


class DummyGateway:
    pass


def test_evidence_registry_isolation() -> None:
    reg1 = EvidenceRegistry("CASE_001")
    ev1 = reg1.register({
        "schema_version": "day09-mcp-evidence-v1",
        "evidence_ref": "ev_11111111111111111111111111111111",
        "result_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "domain": "order",
        "data": {"order_id": "ord_1"},
    })
    assert reg1.get_by_ref("ev_11111111111111111111111111111111") == ev1
    assert reg1.refs_for("order") == ["ev_11111111111111111111111111111111"]

    reg2 = EvidenceRegistry("CASE_002")
    assert reg2.get_by_ref("ev_11111111111111111111111111111111") is None
    assert reg2.all_refs() == []


def test_financial_resolution_invariants() -> None:
    rule = {"recommended_action": "issue_refund", "refund_brl": 79.0}
    fin = build_financial_resolution("canceled_order_paid", rule, "order-123")
    assert fin["currency"] == "BRL"
    assert fin["recommended_refund_brl"] == 79.0
    assert len(fin["refund_lines"]) == 1
    assert fin["refund_lines"][0]["amount_brl"] == 79.0
    assert fin["refund_lines"][0]["entity_id"] == "order-123"

    rule_zero = {"recommended_action": "document_no_action", "refund_brl": 0.0}
    fin_zero = build_financial_resolution("valid_split_payment", rule_zero, "order-456")
    assert fin_zero["recommended_refund_brl"] == 0.0
    assert fin_zero["refund_lines"] == []


def test_root_cause_cause_code_format() -> None:
    rc = build_root_cause(
        "canceled_order_paid",
        {"responsible_parties": [{"party_type": "platform", "party_id": None}]},
        Finding(agent="order_agent", facts={"seller_ids": []}, evidence_refs=()),
    )
    assert len(rc["ranked_causes"]) == 1
    code = rc["ranked_causes"][0]["cause_code"]
    assert code.startswith("CAUSE_")
    assert rc["responsible_parties"][0]["party_type"] == "platform"


def test_verifier_enforces_financial_balance(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    registry = EvidenceRegistry("L3A_TEST_001")
    registry.register({
        "schema_version": "day09-mcp-evidence-v1",
        "evidence_ref": "ev_test_12345678901234567890",
        "result_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        "domain": "order",
        "data": {},
    })

    case = {"case_id": "L3A_TEST_001"}
    ctx = CaseContext(case, DummyGateway(), trace, registry)  # type: ignore[arg-type]
    verifier = VerifierAgent(contracts)

    output: dict[str, Any] = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": "L3A_TEST_001",
        "assessment": {
            "primary_issue": "canceled_order_paid",
            "case_status": "action_required",
            "confidence": 0.95,
        },
        "affected_entities": {
            "order_ids": ["ord-1"],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        },
        "claim_assessments": [],
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "CAUSE_ORDER_CANCELED_AFTER_PAYMENT", "rank": 1}],
            "responsible_parties": [{"party_type": "platform", "party_id": None}],
        },
        "evidence_refs": ["ev_test_12345678901234567890"],
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 100.0,
            "refund_lines": [
                {"reason_code": "issue_refund", "amount_brl": 50.0, "entity_id": "ord-1"}
            ],
        },
        "resolution_actions": ["issue_refund"],
    }

    with pytest.raises(ValueError, match="Financial consistency violation"):
        verifier.verify(output, ctx)

    # Balance it
    output["financial_resolution"]["refund_lines"].append(
        {"reason_code": "issue_refund_part2", "amount_brl": 50.0, "entity_id": "ord-1"}
    )
    verifier.verify(output, ctx)
