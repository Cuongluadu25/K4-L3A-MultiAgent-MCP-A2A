from __future__ import annotations

from typing import Any

from ..models import CaseContext, Finding


class PolicyAgent:
    """Specialist agent responsible for authoritative policy interpretation."""

    actor: str = "policy_agent"

    async def run(self, ctx: CaseContext) -> Finding:
        evidence_refs: list[str] = []
        facts: dict[str, Any] = {
            "policy_version": ctx.policy_version,
            "currency": "BRL",
            "rules": {},
        }

        policy_ev = await ctx.fetch(
            "get_policy",
            actor=self.actor,
            policy_version=ctx.policy_version,
        )
        if policy_ev and policy_ev.data:
            evidence_refs.append(policy_ev.evidence_ref)
            data = policy_ev.data
            facts["currency"] = data.get("currency", "BRL")
            facts["rules"] = data.get("rules", {})

        return Finding(
            agent=self.actor,
            facts=facts,
            evidence_refs=tuple(evidence_refs),
            status="ok",
        )
