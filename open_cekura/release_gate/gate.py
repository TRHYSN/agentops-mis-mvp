"""Human-readable rendering of structured Release Gate facts."""

from __future__ import annotations

from open_cekura.domain.enums import GateDecision
from open_cekura.domain.models import ReleaseGateDecision


def render_gate_decision(decision: ReleaseGateDecision) -> str:
    if decision.decision is GateDecision.PASS:
        return "PASS"
    lines = ["FAIL" if decision.decision is GateDecision.BLOCK else "WARN"]
    if decision.blockers:
        lines.append("Blockers:")
        lines.extend(f"- {fact.message}" for fact in decision.blockers)
    if decision.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {fact.message}" for fact in decision.warnings)
    return "\n".join(lines)


__all__ = ["render_gate_decision"]
