"""Policy agent: fetch and expose the published machine-readable policy.

The gateway hands back one policy document per requested version. Its `rules`
map issue codes to the authoritative case status, recommended action, refund
baseline and responsible party *type*.

The refund figure and any party id in that document are rule-level defaults, so
they are treated as a baseline: the model of the case decides the amount and the
concrete party id, and the policy decides status, action and party type.
"""

from __future__ import annotations

from typing import Any

from ..evidence import CaseContext
from ..models import AGENT_POLICY, Finding


async def run(ctx: CaseContext) -> Finding:
    finding = Finding(agent=AGENT_POLICY)

    policy_ev = await ctx.fetch("get_policy", actor=AGENT_POLICY, policy_version=ctx.policy_version)
    if policy_ev is None:
        finding.status = "insufficient_evidence"
        finding.warnings.append("policy document unavailable")
        return finding

    refs = (policy_ev.evidence_ref,)
    data: dict[str, Any] = policy_ev.data if isinstance(policy_ev.data, dict) else {}
    rules = data.get("rules") if isinstance(data.get("rules"), dict) else {}

    finding.add("policy.version", str(data.get("policy_version") or ctx.policy_version), refs)
    finding.add("policy.currency", str(data.get("currency") or "BRL"), refs)
    finding.add("policy.rule_codes", sorted(rules), refs)
    finding.add("policy.rules", rules, refs)

    if not rules:
        finding.status = "insufficient_evidence"
        finding.warnings.append("policy document carries no rules")

    return finding


def rule_for(finding: Finding, issue: str) -> dict[str, Any]:
    rules = finding.get("policy.rules") or {}
    rule = rules.get(issue)
    return rule if isinstance(rule, dict) else {}
