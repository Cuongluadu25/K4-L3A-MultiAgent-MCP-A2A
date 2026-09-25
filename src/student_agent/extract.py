"""Normalisation helpers shared by the specialist agents.

MCP payloads arrive as strings (money, timestamps, sequence numbers). These
helpers convert them once, in one place, so every agent compares the same types.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY_QUANT = Decimal("0.01")


def money(value: Any) -> Decimal:
    """Parse a BRL amount. Unknown or blank values become 0."""
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(MONEY_QUANT)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def to_float(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANT))


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp with offset. Returns None when absent."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def rows(evidence_data: Any) -> list[dict[str, Any]]:
    """MCP list payloads come back as a JSON array; tolerate a single object."""
    if isinstance(evidence_data, list):
        return [row for row in evidence_data if isinstance(row, dict)]
    if isinstance(evidence_data, dict):
        return [evidence_data]
    return []


def events(evidence_data: Any, key: str = "events") -> list[dict[str, Any]]:
    """MCP summary payloads carry a nested event list."""
    if isinstance(evidence_data, dict):
        return [row for row in evidence_data.get(key, []) if isinstance(row, dict)]
    return []


def event_types(evidence_data: Any, key: str = "events") -> list[str]:
    return [str(row.get("event_type", "")) for row in events(evidence_data, key)]


def unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
