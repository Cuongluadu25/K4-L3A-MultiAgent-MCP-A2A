"""Evidence bookkeeping and the per-case agent context.

Every MCP response that a conclusion leans on passes through CaseContext.fetch,
which validates it, records it in a per-case registry and emits a
`tool_result_consumed` trace event carrying the tool name and evidence ref.

The registry is created once per case and never reused, which is what keeps
evidence refs from leaking across cases (a hard gate).
"""

from __future__ import annotations

import asyncio
from typing import Any

from .mcp_gateway import EvidenceGateway
from .models import AGENT_COORDINATOR, Evidence
from .trace import TraceWriter

# Tools whose "nothing to report" answer arrives as a tool error rather than an
# empty payload. Treat the error as an empty result, not as a workflow failure.
TOLERATE_TOOL_ERROR = frozenset({"get_refund_timeline"})

# A full run is 100 cases x 8 reads = 800 calls over one connection, so a
# single dropped connection must not cost the whole run. ~12s of patience per
# call before giving up.
RETRYABLE_ATTEMPTS = 6
RETRY_BACKOFF_SECONDS = 0.8
RETRY_BACKOFF_CAP = 4.0


class EvidenceRegistry:
    """Per-case store of MCP evidence. One instance per case, never shared."""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self._by_ref: dict[str, Evidence] = {}

    def register(self, evidence: Evidence) -> Evidence:
        existing = self._by_ref.get(evidence.evidence_ref)
        if existing is not None:
            if existing.domain != evidence.domain or existing.data != evidence.data:
                raise ValueError(
                    f"evidence ref {evidence.evidence_ref} was reused with different content"
                )
            return existing
        self._by_ref[evidence.evidence_ref] = evidence
        return evidence

    def all(self) -> list[Evidence]:
        return list(self._by_ref.values())

    def refs(self, *domains: str) -> list[str]:
        """Evidence refs, optionally filtered to a set of MCP domains."""
        wanted = set(domains)
        return [
            item.evidence_ref
            for item in self._by_ref.values()
            if not wanted or item.domain in wanted
        ]

    def data(self, *domains: str) -> list[Any]:
        wanted = set(domains)
        return [item.data for item in self._by_ref.values() if not wanted or item.domain in wanted]

    def contains(self, evidence_ref: str) -> bool:
        return evidence_ref in self._by_ref

    def __len__(self) -> int:
        return len(self._by_ref)


class CaseContext:
    """Everything an agent needs for exactly one case."""

    def __init__(
        self,
        case: dict[str, Any],
        gateway: EvidenceGateway,
        trace: TraceWriter,
    ) -> None:
        self.case = case
        self.case_id: str = case["case_id"]
        self.gateway = gateway
        self.trace = trace
        self.registry = EvidenceRegistry(self.case_id)
        self.warnings: list[str] = []

    # -- case accessors ------------------------------------------------------

    @property
    def order_id(self) -> str:
        return self.case["customer_request"]["claimed_order_id"]

    @property
    def policy_version(self) -> str:
        return self.case["policy_version"]

    @property
    def claims(self) -> list[dict[str, str]]:
        return list(self.case["customer_request"]["claims"])

    # -- evidence access -----------------------------------------------------

    async def fetch(
        self,
        tool_name: str,
        *,
        actor: str = AGENT_COORDINATOR,
        tolerate_error: bool = False,
        **args: str,
    ) -> Evidence | None:
        """Call one MCP tool, validate the envelope, record it and trace it.

        `actor` is the specialist on whose behalf the read happens, so the audit
        trail attributes each tool result to the agent that needed it.

        Returns None when the tool reports "nothing here" and the caller allows
        that outcome. Never invents evidence on failure.
        """
        tolerate = tolerate_error or tool_name in TOLERATE_TOOL_ERROR
        last_error: Exception | None = None
        for attempt in range(RETRYABLE_ATTEMPTS):
            try:
                payload = await self.gateway.call(tool_name, case_id=self.case_id, **args)
                break
            except Exception as exc:  # noqa: BLE001 - network flakiness is expected
                last_error = exc
                if attempt == RETRYABLE_ATTEMPTS - 1:
                    if tolerate:
                        self.warnings.append(f"{tool_name}:no_data")
                        return None
                    raise
                delay = min(RETRY_BACKOFF_SECONDS * (attempt + 1), RETRY_BACKOFF_CAP)
                await asyncio.sleep(delay)
        else:  # pragma: no cover - unreachable, loop always breaks or raises
            raise RuntimeError(f"MCP tool {tool_name} exhausted retries: {last_error}")

        evidence_ref = payload.get("evidence_ref")
        domain = payload.get("domain")
        if not isinstance(evidence_ref, str) or not isinstance(domain, str):
            raise ValueError(f"MCP tool {tool_name} returned an incomplete evidence envelope")

        evidence = Evidence(
            evidence_ref=evidence_ref,
            domain=domain,
            data=payload.get("data"),
            tool_name=tool_name,
        )
        self.registry.register(evidence)

        warnings = payload.get("warnings") or []
        for warning in warnings:
            self.warnings.append(f"{tool_name}:{warning}")

        self.trace.emit(
            case_id=self.case_id,
            event_type="tool_result_consumed",
            actor=actor,
            tool_name=tool_name,
            target=domain,
            evidence_refs=[evidence_ref],
            attributes={"domain": domain, "attempts": attempt + 1},
        )
        return evidence
