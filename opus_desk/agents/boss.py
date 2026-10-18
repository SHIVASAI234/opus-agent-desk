"""BOSS: final gate. Deterministic: it cannot be persuaded, only satisfied.

APPROVE_FOR_PAPER_TEST only if all five reports exist, are valid, are complete,
Strategy produced a plan and Risk returned PASSED. Otherwise WAIT or REJECT.
It never places orders and never changes limits.
"""
from __future__ import annotations

from ..schema import AGENT_ORDER, ReportValidationError, make_report, validate_report
from . import RunContext

UPSTREAM = AGENT_ORDER[:-1]


def run(ctx: RunContext, reports: dict) -> dict:
    problems: list[str] = []
    decision = None

    for name in UPSTREAM:
        rep = reports.get(name)
        if rep is None:
            problems.append(f"{name} report missing")
            continue
        try:
            validate_report(rep)
        except ReportValidationError as exc:
            problems.append(f"{name} report invalid: {exc}")
            continue
        if rep["status"] == "rejected":
            decision = "REJECT"
            problems.append(f"{name} rejected: {rep['summary']}")
        elif rep["status"] == "needs_data":
            problems.append(f"{name} needs data: {rep['summary']}")

    strategy = reports.get("strategy", {}).get("data", {})
    risk = reports.get("risk", {}).get("data", {})

    if decision == "REJECT":
        pass
    elif problems:
        decision = "WAIT"
    elif strategy.get("action") != "PLAN":
        decision = "WAIT"
        problems.append("Strategy found no setup that meets every rule.")
    elif risk.get("verdict") != "PASSED":
        decision = "REJECT"
        problems.append(f"Risk verdict is {risk.get('verdict')!r}, not PASSED.")
    else:
        decision = "APPROVE_FOR_PAPER_TEST"

    # Collect disagreements worth a human's attention even on approval.
    disagreements = []
    for name in ("news", "charts"):
        disagreements += reports.get(name, {}).get("risks", [])

    if decision == "APPROVE_FOR_PAPER_TEST":
        plan = strategy["plan"]
        sizing = risk["sizing"]
        summary = (f"APPROVE_FOR_PAPER_TEST: long {sizing['qty']} {plan['ticker']} at next open, "
                   f"stop {plan['stop']}, target {plan['target']}. Human sign-off still required.")
    else:
        summary = f"{decision}: " + ("; ".join(problems) if problems else "conditions not met")

    evidence = [{"claim": f"{n}: {reports[n]['status']} - {reports[n]['summary']}", "source": f"{n} report"}
                for n in UPSTREAM if n in reports]
    return make_report(
        "boss", ctx.as_of_str, "complete", summary, evidence=evidence,
        risks=problems + disagreements,
        next_step="Log the hypothetical trade in the journal; a human must sign off."
        if decision == "APPROVE_FOR_PAPER_TEST" else "No action.",
        data={"decision": decision, "human_approval_required": True,
              "plan": strategy.get("plan") if decision == "APPROVE_FOR_PAPER_TEST" else None,
              "sizing": risk.get("sizing") if decision == "APPROVE_FOR_PAPER_TEST" else None},
    )
