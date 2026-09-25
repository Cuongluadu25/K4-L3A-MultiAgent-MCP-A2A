from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .evidence import EvidenceRegistry
    from .mcp_gateway import EvidenceGateway
    from .trace import TraceWriter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evidence:
    evidence_ref: str
    domain: str
    data: Any
    result_hash: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Finding:
    agent: str
    facts: dict[str, Any]
    evidence_refs: tuple[str, ...]
    status: str = "ok"  # "ok" | "insufficient_evidence" | "not_found"


class CaseContext:
    def __init__(
        self,
        case: dict[str, Any],
        gateway: EvidenceGateway,
        trace: TraceWriter,
        registry: EvidenceRegistry,
    ) -> None:
        self.case = case
        self.case_id: str = case["case_id"]
        self.claimed_order_id: str = (
            case.get("customer_request", {}).get("claimed_order_id") or ""
        )
        self.policy_version: str = case.get("policy_version") or "EC_POLICY_V1"
        self.gateway = gateway
        self.trace = trace
        self.registry = registry

    async def fetch(
        self,
        tool_name: str,
        *,
        actor: str,
        max_retries: int = 1,
        retry_delay: float = 0.2,
        **arguments: str,
    ) -> Evidence | None:
        """Call MCP tool with idempotent retry, register evidence, and emit trace."""
        for attempt in range(max_retries + 1):
            try:
                raw_envelope = await self.gateway.call(
                    tool_name, case_id=self.case_id, **arguments
                )
                evidence = self.registry.register(raw_envelope)
                self.trace.emit(
                    case_id=self.case_id,
                    event_type="tool_result_consumed",
                    actor=actor,
                    tool_name=tool_name,
                    evidence_refs=[evidence.evidence_ref],
                )
                return evidence
            except Exception as exc:
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay * (2**attempt))
                else:
                    logger.warning(
                        "Tool %s failed for case %s after %d attempts: %s",
                        tool_name,
                        self.case_id,
                        max_retries + 1,
                        exc,
                    )
        return None
