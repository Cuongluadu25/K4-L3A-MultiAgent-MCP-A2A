from __future__ import annotations

from typing import Any

from ..models import CaseContext, Finding


class ShipmentAgent:
    """Specialist agent responsible for logistics, shipping limits, and delivery timestamps."""

    actor: str = "shipment_agent"

    async def run(self, ctx: CaseContext) -> Finding:
        evidence_refs: list[str] = []
        facts: dict[str, Any] = {
            "delivered_carrier_at": None,
            "delivered_customer_at": None,
            "estimated_delivery_at": None,
            "shipping_limits": [],
            "events": [],
            "has_late_delivery": False,
            "late_actor": None,
            "shipment_ids": [f"ship_{ctx.claimed_order_id[:16]}"],
        }

        ship_ev = await ctx.fetch(
            "get_shipment_summary", actor=self.actor, order_id=ctx.claimed_order_id
        )
        if not ship_ev or not ship_ev.data:
            return Finding(
                agent=self.actor,
                facts=facts,
                evidence_refs=tuple(evidence_refs),
                status="ok",
            )

        evidence_refs.append(ship_ev.evidence_ref)
        data = ship_ev.data
        facts["delivered_carrier_at"] = data.get("delivered_carrier_at")
        facts["delivered_customer_at"] = data.get("delivered_customer_at")
        facts["estimated_delivery_at"] = data.get("estimated_delivery_at")
        facts["shipping_limits"] = data.get("shipping_limits", [])
        events = data.get("events", [])
        facts["events"] = events

        for ev in events:
            if ev.get("event_type") == "delivered_late":
                facts["has_late_delivery"] = True
                facts["late_actor"] = ev.get("actor")

        return Finding(
            agent=self.actor,
            facts=facts,
            evidence_refs=tuple(evidence_refs),
            status="ok",
        )
