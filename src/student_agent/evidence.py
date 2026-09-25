from __future__ import annotations

import re
from typing import Any

from .models import Evidence

EVIDENCE_REF_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{20,96}$")


class EvidenceRegistry:
    """Manages authoritative MCP evidence scoped strictly to a single case.
    
    Prevents cross-case contamination, duplicate references, and invalid formats.
    """

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self._by_ref: dict[str, Evidence] = {}
        self._by_domain: dict[str, list[Evidence]] = {}

    def register(self, envelope: dict[str, Any]) -> Evidence:
        ref = envelope.get("evidence_ref", "")
        if not EVIDENCE_REF_PATTERN.fullmatch(ref):
            raise ValueError(f"Invalid evidence reference format: {ref}")

        domain = envelope.get("domain", "unknown")
        result_hash = envelope.get("result_hash", "")
        data = envelope.get("data")
        warnings = tuple(envelope.get("warnings") or ())

        evidence = Evidence(
            evidence_ref=ref,
            domain=domain,
            data=data,
            result_hash=result_hash,
            warnings=warnings,
        )

        self._by_ref[ref] = evidence
        self._by_domain.setdefault(domain, []).append(evidence)
        return evidence

    def get_by_ref(self, ref: str) -> Evidence | None:
        return self._by_ref.get(ref)

    def refs_for(self, *domains: str) -> list[str]:
        """Return unique evidence references matching any of the specified domains."""
        result: list[str] = []
        for domain in domains:
            for ev in self._by_domain.get(domain, []):
                if ev.evidence_ref not in result:
                    result.append(ev.evidence_ref)
        return result

    def all_refs(self) -> list[str]:
        """Return all unique registered evidence references, capped at 30."""
        return list(self._by_ref.keys())[:30]

    def has_domain(self, domain: str) -> bool:
        return domain in self._by_domain and len(self._by_domain[domain]) > 0
