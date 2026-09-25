"""Payment agent: payment rows, payment lifecycle and refund lifecycle."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from ..evidence import CaseContext
from ..extract import events, money, rows, unique
from ..models import AGENT_PAYMENT, Finding

OPEN_STATUSES = {"open", "pending", "authorized"}


async def run(ctx: CaseContext) -> Finding:
    finding = Finding(agent=AGENT_PAYMENT)
    order_id = ctx.order_id

    payments_ev = await ctx.fetch("get_order_payments", actor=AGENT_PAYMENT, order_id=order_id)
    timeline_ev = await ctx.fetch("get_payment_timeline", actor=AGENT_PAYMENT, order_id=order_id)
    refund_ev = await ctx.fetch("get_refund_timeline", actor=AGENT_PAYMENT, order_id=order_id)

    pay_refs = tuple(
        ref
        for ref in (
            payments_ev.evidence_ref if payments_ev else None,
            timeline_ev.evidence_ref if timeline_ev else None,
        )
        if ref
    )
    refund_refs = (refund_ev.evidence_ref,) if refund_ev else ()

    if payments_ev is None:
        finding.status = "insufficient_evidence"
        finding.warnings.append("payment evidence unavailable")
        return finding

    payment_rows = rows(payments_ev.data)
    payment_events = events(timeline_ev.data) if timeline_ev else []
    refund_events = events(refund_ev.data) if refund_ev else []

    total = sum((money(row.get("payment_value")) for row in payment_rows), Decimal("0.00"))
    captured = sum(
        (
            money(event.get("amount_brl"))
            for event in payment_events
            if str(event.get("event_type")) == "captured"
            and str(event.get("status")) == "confirmed"
        ),
        Decimal("0.00"),
    )

    finding.add("payments.count", len(payment_rows), pay_refs)
    finding.add("payments.total", money(total), pay_refs)
    finding.add("payments.captured_total", money(captured), pay_refs)
    finding.add(
        "payments.types", unique([str(r.get("payment_type")) for r in payment_rows]), pay_refs
    )
    finding.add(
        "payments.sequentials",
        unique([str(r.get("payment_sequential")) for r in payment_rows]),
        pay_refs,
    )
    finding.add(
        "payments.installments",
        unique([str(r.get("payment_installments")) for r in payment_rows]),
        pay_refs,
    )

    # A row repeated with the same sequential, type and value is a duplicate charge.
    signature = Counter(
        (
            str(r.get("payment_sequential")),
            str(r.get("payment_type")),
            str(money(r.get("payment_value"))),
        )
        for r in payment_rows
    )
    duplicates = [key for key, count in signature.items() if count > 1]
    duplicate_rows = [
        r
        for r in payment_rows
        if (
            str(r.get("payment_sequential")),
            str(r.get("payment_type")),
            str(money(r.get("payment_value"))),
        )
        in duplicates
    ]
    duplicate_amount = sum(
        (money(r.get("payment_value")) for r in duplicate_rows[1:]), Decimal("0.00")
    )
    finding.add("payments.duplicate_signatures", [list(key) for key in duplicates], pay_refs)
    finding.add("payments.duplicate_amount", money(duplicate_amount), pay_refs)

    mismatches = [
        event
        for event in payment_events
        if str(event.get("event_type")) == "reconciliation_mismatch"
    ]
    open_mismatches = [e for e in mismatches if str(e.get("status")) in OPEN_STATUSES]
    mismatch_amount = sum((money(e.get("amount_brl")) for e in open_mismatches), Decimal("0.00"))
    finding.add("payments.mismatch_present", bool(open_mismatches), pay_refs)
    finding.add("payments.mismatch_amount", money(mismatch_amount), pay_refs)
    finding.add(
        "payments.mismatch_statuses", unique([str(e.get("status")) for e in mismatches]), pay_refs
    )

    finding.add(
        "payments.event_types", unique([str(e.get("event_type")) for e in payment_events]), pay_refs
    )

    refund_statuses = unique([str(e.get("status")) for e in refund_events])
    refund_amounts = [money(e.get("amount_brl")) for e in refund_events]
    finding.add("refund.present", bool(refund_events), refund_refs)
    finding.add("refund.statuses", refund_statuses, refund_refs)
    finding.add(
        "refund.amount", refund_amounts[0] if refund_amounts else Decimal("0.00"), refund_refs
    )
    finding.add("refund.total", money(sum(refund_amounts, Decimal("0.00"))), refund_refs)
    finding.add(
        "refund.event_types", unique([str(e.get("event_type")) for e in refund_events]), refund_refs
    )

    if len(payment_rows) > 0 and not payment_events:
        finding.warnings.append("payment lifecycle events unavailable")

    return finding
