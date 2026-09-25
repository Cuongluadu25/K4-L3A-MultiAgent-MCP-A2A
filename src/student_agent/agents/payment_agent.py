from __future__ import annotations

import contextlib
from typing import Any

from ..models import CaseContext, Finding


class PaymentAgent:
    """Specialist agent responsible for payments, transactions, and refund timelines."""

    actor: str = "payment_agent"

    async def run(self, ctx: CaseContext) -> Finding:
        evidence_refs: list[str] = []
        facts: dict[str, Any] = {
            "payments": [],
            "events": [],
            "refund_events": [],
            "has_reconciliation_mismatch": False,
            "mismatch_amount": 0.0,
            "has_duplicate_charge": False,
            "duplicate_amount": 0.0,
            "is_split_payment": False,
            "has_refund_pending": False,
            "has_refund_failed": False,
            "total_payment_value": 0.0,
            "payment_references": [],
        }

        # 1. Fetch payment timeline
        timeline_ev = await ctx.fetch(
            "get_payment_timeline", actor=self.actor, order_id=ctx.claimed_order_id
        )
        if timeline_ev and timeline_ev.data:
            evidence_refs.append(timeline_ev.evidence_ref)
            timeline_data = timeline_ev.data
            facts["payments"] = timeline_data.get("payments", [])
            facts["events"] = timeline_data.get("events", [])
        else:
            # Fallback to get_order_payments
            pay_ev = await ctx.fetch(
                "get_order_payments", actor=self.actor, order_id=ctx.claimed_order_id
            )
            if pay_ev and pay_ev.data:
                evidence_refs.append(pay_ev.evidence_ref)
                facts["payments"] = pay_ev.data if isinstance(pay_ev.data, list) else [pay_ev.data]

        payments = facts["payments"]
        events = facts["events"]

        # Calculate payment references & totals
        total_pay = 0.0
        pay_refs: list[str] = []
        seen_keys: list[tuple[str, str, str]] = []
        has_duplicate = False
        duplicate_val = 0.0

        for idx, p in enumerate(payments):
            seq = str(p.get("payment_sequential", idx + 1))
            ptype = str(p.get("payment_type", "payment"))
            val_str = str(p.get("payment_value", 0.0))
            ref = f"{ctx.claimed_order_id}-seq{seq}-{ptype}"
            if ref in pay_refs:
                ref = f"{ref}-{idx}"
            pay_refs.append(ref)

            try:
                val = float(val_str)
                total_pay += val
            except (ValueError, TypeError):
                val = 0.0

            key = (seq, ptype, val_str)
            if key in seen_keys:
                has_duplicate = True
                duplicate_val = val
            else:
                seen_keys.append(key)

        facts["total_payment_value"] = round(total_pay, 2)
        facts["payment_references"] = pay_refs[:20]

        # Check reconciliation mismatch in events
        for ev in events:
            ev_type = ev.get("event_type")
            if ev_type == "reconciliation_mismatch":
                facts["has_reconciliation_mismatch"] = True
                with contextlib.suppress(ValueError, TypeError):
                    facts["mismatch_amount"] = float(ev.get("amount_brl") or 0.0)

        facts["has_duplicate_charge"] = has_duplicate
        facts["duplicate_amount"] = duplicate_val

        # Check split payment: multiple payments of different types or sequentials
        if len(payments) > 1 and not has_duplicate:
            facts["is_split_payment"] = True

        # 2. Fetch refund timeline only when relevant to avoid cross-timeline contamination
        claims = ctx.case.get("customer_request", {}).get("claims", [])
        claimed_topics = [
            c.get("topic") for c in claims if c.get("topic") != "requested_full_refund"
        ]
        target_topic = claimed_topics[0] if claimed_topics else None

        if target_topic in ["refund_pending", "refund_failed"]:
            refund_ev = await ctx.fetch(
                "get_refund_timeline",
                actor=self.actor,
                max_retries=0,
                order_id=ctx.claimed_order_id,
            )
            if refund_ev and refund_ev.data:
                evidence_refs.append(refund_ev.evidence_ref)
                refund_data = refund_ev.data
                r_events = refund_data.get("events", [])
                facts["refund_events"] = r_events
                for rev in r_events:
                    status = rev.get("status")
                    if status == "pending":
                        facts["has_refund_pending"] = True
                    elif status == "failed":
                        facts["has_refund_failed"] = True

        return Finding(
            agent=self.actor,
            facts=facts,
            evidence_refs=tuple(evidence_refs),
            status="ok",
        )
