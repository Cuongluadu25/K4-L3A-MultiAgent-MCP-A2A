from __future__ import annotations

from typing import Any

from ..models import CaseContext, Finding


class OrderAgent:
    """Specialist agent responsible for order lifecycle, items, and sellers."""

    actor: str = "order_agent"

    async def run(self, ctx: CaseContext) -> Finding:
        evidence_refs: list[str] = []
        facts: dict[str, Any] = {
            "order_id": ctx.claimed_order_id,
            "order_status": None,
            "item_ids": [],
            "seller_ids": [],
            "items": [],
            "sellers": [],
        }

        order_ev = await ctx.fetch("get_order", actor=self.actor, order_id=ctx.claimed_order_id)
        if not order_ev or not order_ev.data:
            return Finding(
                agent=self.actor,
                facts=facts,
                evidence_refs=tuple(evidence_refs),
                status="insufficient_evidence",
            )

        evidence_refs.append(order_ev.evidence_ref)
        order_data = order_ev.data
        facts["order_status"] = order_data.get("order_status")
        facts["customer_id"] = order_data.get("customer_id")
        facts["order_purchase_timestamp"] = order_data.get("order_purchase_timestamp")
        facts["order_approved_at"] = order_data.get("order_approved_at")
        facts["order_delivered_carrier_date"] = order_data.get("order_delivered_carrier_date")
        facts["order_delivered_customer_date"] = order_data.get("order_delivered_customer_date")
        facts["order_estimated_delivery_date"] = order_data.get("order_estimated_delivery_date")

        items_ev = await ctx.fetch(
            "get_order_items", actor=self.actor, order_id=ctx.claimed_order_id
        )
        if items_ev and items_ev.data:
            evidence_refs.append(items_ev.evidence_ref)
            items_list = items_ev.data if isinstance(items_ev.data, list) else [items_ev.data]
            facts["items"] = items_list
            item_ids: list[str] = []
            seller_ids: list[str] = []
            total_price = 0.0
            total_freight = 0.0
            for item in items_list:
                item_id = item.get("order_item_id")
                if item_id and item_id not in item_ids:
                    item_ids.append(item_id)
                seller_id = item.get("seller_id")
                if seller_id and seller_id not in seller_ids:
                    seller_ids.append(seller_id)
                try:
                    total_price += float(item.get("price") or 0.0)
                    total_freight += float(item.get("freight_value") or 0.0)
                except (ValueError, TypeError):
                    pass
            facts["item_ids"] = item_ids
            facts["seller_ids"] = seller_ids
            facts["total_price"] = round(total_price, 2)
            facts["total_freight"] = round(total_freight, 2)

        sellers_ev = await ctx.fetch(
            "get_sellers", actor=self.actor, order_id=ctx.claimed_order_id
        )
        if sellers_ev and sellers_ev.data:
            evidence_refs.append(sellers_ev.evidence_ref)
            sellers_list = (
                sellers_ev.data if isinstance(sellers_ev.data, list) else [sellers_ev.data]
            )
            facts["sellers"] = sellers_list
            for s in sellers_list:
                sid = s.get("seller_id")
                if sid and sid not in facts["seller_ids"]:
                    facts["seller_ids"].append(sid)

        return Finding(
            agent=self.actor,
            facts=facts,
            evidence_refs=tuple(evidence_refs),
            status="ok",
        )
