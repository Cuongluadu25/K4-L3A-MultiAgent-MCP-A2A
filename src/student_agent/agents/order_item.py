"""Order/item agent: order row, item/seller rows and seller records."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..evidence import CaseContext
from ..extract import money, parse_dt, rows, unique
from ..models import AGENT_ORDER_ITEM, Finding


async def run(ctx: CaseContext) -> Finding:
    finding = Finding(agent=AGENT_ORDER_ITEM)
    order_id = ctx.order_id

    order_ev = await ctx.fetch("get_order", actor=AGENT_ORDER_ITEM, order_id=order_id)
    items_ev = await ctx.fetch("get_order_items", actor=AGENT_ORDER_ITEM, order_id=order_id)
    sellers_ev = await ctx.fetch("get_sellers", actor=AGENT_ORDER_ITEM, order_id=order_id)

    order_refs = (order_ev.evidence_ref,) if order_ev else ()
    item_refs = (items_ev.evidence_ref,) if items_ev else ()
    seller_refs = (sellers_ev.evidence_ref,) if sellers_ev else ()

    if order_ev is None or items_ev is None:
        finding.status = "insufficient_evidence"
        finding.warnings.append("order or item evidence unavailable")
        return finding

    order: dict[str, Any] = order_ev.data if isinstance(order_ev.data, dict) else {}
    item_rows = rows(items_ev.data)
    seller_rows = rows(sellers_ev.data) if sellers_ev else []

    finding.add("order.status", str(order.get("order_status") or "unknown"), order_refs)
    finding.add("order.purchase_at", parse_dt(order.get("order_purchase_timestamp")), order_refs)
    finding.add("order.approved_at", parse_dt(order.get("order_approved_at")), order_refs)
    finding.add("order.carrier_at", parse_dt(order.get("order_delivered_carrier_date")), order_refs)
    finding.add(
        "order.delivered_at", parse_dt(order.get("order_delivered_customer_date")), order_refs
    )
    finding.add(
        "order.estimated_at", parse_dt(order.get("order_estimated_delivery_date")), order_refs
    )
    finding.add(
        "order.customer_id",
        order.get("customer_unique_id") or order.get("customer_id"),
        order_refs,
    )

    item_ids = unique([str(row.get("order_item_id")) for row in item_rows])
    seller_ids = unique([str(row.get("seller_id")) for row in item_rows])
    if sellers_ev:
        seller_ids = unique([*seller_ids, *[str(row.get("seller_id")) for row in seller_rows]])

    total_price = sum((money(row.get("price")) for row in item_rows), Decimal("0.00"))
    total_freight = sum((money(row.get("freight_value")) for row in item_rows), Decimal("0.00"))

    finding.add("items.count", len(item_rows), item_refs)
    finding.add("items.ids", item_ids, item_refs)
    finding.add("items.total_price", money(total_price), item_refs)
    finding.add("items.total_freight", money(total_freight), item_refs)
    finding.add("items.seller_ids", seller_ids, item_refs + seller_refs)
    finding.add(
        "items.shipping_limits",
        [
            {"seller_id": row.get("seller_id"), "shipping_limit_at": row.get("shipping_limit_date")}
            for row in item_rows
        ],
        item_refs,
    )

    if not item_rows:
        finding.status = "insufficient_evidence"
        finding.warnings.append("order has no item rows")

    return finding
