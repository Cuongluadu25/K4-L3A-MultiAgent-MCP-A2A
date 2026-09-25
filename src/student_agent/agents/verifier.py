from __future__ import annotations

import re
from typing import Any

from ..contracts import Contracts
from ..models import CaseContext

EVIDENCE_REF_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{20,96}$")
CAUSE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")


class VerifierAgent:
    """Specialist agent responsible for verifying all business and contract invariants."""

    actor: str = "verifier_agent"

    def __init__(self, contracts: Contracts) -> None:
        self.contracts = contracts

    def verify(self, output: dict[str, Any], ctx: CaseContext) -> None:
        # 1. Verify case_id
        if output.get("case_id") != ctx.case_id:
            raise ValueError(
                f"Case ID mismatch: expected {ctx.case_id}, got {output.get('case_id')}"
            )

        # 2. Verify evidence references
        ev_refs = output.get("evidence_refs", [])
        if len(ev_refs) > 30:
            raise ValueError(f"evidence_refs exceeds maximum 30: {len(ev_refs)}")
        for ref in ev_refs:
            if not EVIDENCE_REF_PATTERN.fullmatch(ref):
                raise ValueError(f"Invalid evidence_ref format: {ref}")
            if not ctx.registry.get_by_ref(ref):
                raise ValueError(f"Unknown or cross-scope evidence_ref: {ref}")

        # 3. Verify financial resolution sum
        fin = output.get("financial_resolution", {})
        rec_refund = round(float(fin.get("recommended_refund_brl", 0.0)), 2)
        lines = fin.get("refund_lines", [])
        lines_sum = round(sum(float(line.get("amount_brl", 0.0)) for line in lines), 2)
        if abs(rec_refund - lines_sum) > 0.01:
            raise ValueError(
                f"Financial consistency violation: recommended_refund_brl ({rec_refund}) "
                f"!= sum of refund_lines ({lines_sum})"
            )

        # 4. Verify root cause analysis
        rc = output.get("root_cause_analysis", {})
        for cause in rc.get("ranked_causes", []):
            code = cause.get("cause_code", "")
            if not CAUSE_CODE_PATTERN.fullmatch(code):
                raise ValueError(f"Invalid cause_code format: {code}")

        # 5. Verify schema against public contract
        self.contracts.validate_output(output, f"Verification for {ctx.case_id}")

        # 6. Emit verification trace event
        ctx.trace.emit(
            case_id=ctx.case_id,
            event_type="verification_completed",
            actor=self.actor,
            decision_code="VERIFIED_PASS",
            attributes={
                "primary_issue": output["assessment"]["primary_issue"],
                "case_status": output["assessment"]["case_status"],
                "refund_brl": rec_refund,
            },
        )
