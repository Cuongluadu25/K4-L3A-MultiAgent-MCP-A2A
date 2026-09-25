"""Shipment agent: delivery timestamps, shipping limits and shipment events."""

from __future__ import annotations

from typing import Any

from ..evidence import CaseContext
from ..extract import events, parse_dt, unique
from ..models import AGENT_SHIPMENT, Finding


async def run(ctx: CaseContext) -> Finding:
    finding = Finding(agent=AGENT_SHIPMENT)

    shipment_ev = await ctx.fetch(
        "get_shipment_summary", actor=AGENT_SHIPMENT, order_id=ctx.order_id
    )
    if shipment_ev is None:
        finding.status = "insufficient_evidence"
        finding.warnings.append("shipment evidence unavailable")
        return finding

    refs = (shipment_ev.evidence_ref,)
    data: dict[str, Any] = shipment_ev.data if isinstance(shipment_ev.data, dict) else {}
    shipment_events = events(data)

    delivered_at = parse_dt(data.get("delivered_customer_at"))
    estimated_at = parse_dt(data.get("estimated_delivery_at"))
    carrier_at = parse_dt(data.get("delivered_carrier_at"))

    finding.add("shipment.status", str(data.get("order_status") or "unknown"), refs)
    finding.add("shipment.delivered_at", delivered_at, refs)
    finding.add("shipment.estimated_at", estimated_at, refs)
    finding.add("shipment.carrier_at", carrier_at, refs)

    # Timeliness is decided by the timestamps, not by the event label: the
    # gateway can carry a `delivered_late` event for an order that still landed
    # inside the estimate, which is exactly how an unsupported claim shows up.
    late_by_timestamps = bool(
        delivered_at is not None and estimated_at is not None and delivered_at > estimated_at
    )
    late_days = (
        (delivered_at - estimated_at).days
        if delivered_at is not None and estimated_at is not None and delivered_at > estimated_at
        else 0
    )
    finding.add("shipment.late_by_timestamps", late_by_timestamps, refs)
    finding.add("shipment.late_days", late_days, refs)

    late_events = [
        e
        for e in shipment_events
        if str(e.get("event_type")) == "delivered_late" and str(e.get("status")) == "confirmed"
    ]
    finding.add("shipment.late_event_present", bool(late_events), refs)
    finding.add(
        "shipment.event_types", unique([str(e.get("event_type")) for e in shipment_events]), refs
    )
    finding.add("shipment.late_actors", unique([str(e.get("actor")) for e in late_events]), refs)

    # The event label and the timestamps disagreeing is a real source conflict.
    finding.add(
        "shipment.event_disagrees_with_timestamps",
        bool(late_events) and not late_by_timestamps,
        refs,
    )
    finding.add("shipment.shipping_limits", data.get("shipping_limits") or [], refs)

    return finding
