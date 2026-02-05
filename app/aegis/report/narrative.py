from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.aegis.report.report_data import ReportData


@dataclass
class NarrativeSections:
    executive_summary: str
    situation_analysis: str
    food_security_assessment: str
    displacement_analysis: str
    risk_assessment: str
    safe_routes_analysis: str
    recommendations: str
    farmer_loan_adjustments: str
    methodology: str
    references: List[str] = field(default_factory=list)
    state_annexes: Dict[str, str] = field(default_factory=dict)


def _state_line(assessment: Dict[str, Any]) -> str:
    state = assessment.get("state") or "Unknown"
    risk = assessment.get("risk_level") or "UNKNOWN"
    summary = (assessment.get("summary") or "").strip()
    if summary:
        summary = summary.replace("\n", " ").strip()
    return f"- {state}: **{risk}** — {summary}"


def render_template_narrative(report_data: ReportData, include_annexes: bool) -> NarrativeSections:
    rollup = report_data.rollup or {}
    rankings = rollup.get("rankings") or []

    # Executive summary
    top_states = ", ".join([r.get("state", "") for r in rankings[:3] if isinstance(r, dict) and r.get("state")]) or ", ".join(report_data.states[:3])
    overall = (rollup.get("overall_summary") or "").strip()
    if not overall:
        overall = (
            "This report summarizes AEGIS scan outputs and deterministic synthesis assessments. "
            "It highlights displacement pressure, food insecurity, and access constraints."
        )

    executive = (
        f"{overall}\n\n"
        f"Top priority states (rollup): {top_states or 'N/A'}.\n"
        "All findings are evidence-linked to scan-grounded sources (see References)."
    )

    # Situation analysis from per-state assessment summaries
    lines = [_state_line(a) for a in report_data.assessments_by_state.values()]
    situation = "Per-state situation snapshots:\n" + "\n".join(lines)

    # Food / displacement / risk sections: derive from key findings where possible.
    food_points: List[str] = []
    disp_points: List[str] = []
    risk_points: List[str] = []
    route_points: List[str] = []
    loan_points: List[str] = []

    for state_name, assessment in report_data.assessments_by_state.items():
        metrics = assessment.get("metrics") or {}
        try:
            ipc = int(metrics.get("ipc_phase") or 0)
        except Exception:
            ipc = 0
        idp = metrics.get("idp_estimate")
        markets = metrics.get("markets_operational")
        food_points.append(f"- {state_name}: IPC phase {ipc or 'unknown'}, markets {markets or 'unknown'}.")
        disp_points.append(f"- {state_name}: IDP estimate {idp if idp is not None else 'unknown'}, trend {metrics.get('idp_trend') or 'unknown'}.")
        risk_points.append(f"- {state_name}: risk level {assessment.get('risk_level') or 'unknown'}, confidence {assessment.get('confidence') or 'n/a'}.")
        routes = metrics.get("route_flags") or []
        if routes:
            route_points.append(f"- {state_name}: access notes — {', '.join([str(x) for x in routes])}.")
        flags = metrics.get("loan_risk_flags") or []
        if flags:
            loan_points.append(f"- {state_name}: FARMA adjustments — {', '.join([str(x) for x in flags])}.")

    food = "Food security highlights:\n" + "\n".join(food_points)
    displacement = "Displacement highlights:\n" + "\n".join(disp_points)
    risk = "Risk assessment:\n" + "\n".join(risk_points)
    routes = "Safe routes / access constraints:\n" + ("\n".join(route_points) if route_points else "- No route flags available in assessments.")
    recs = (
        "Recommendations:\n"
        "- Prioritize food assistance and protection in HIGH/CRITICAL states.\n"
        "- Pre-position supplies along safe corridors; avoid flagged no-go routes.\n"
        "- Coordinate with partners (WFP/OCHA/IOM) using LGA-aggregated outputs only."
    )
    loans = "FARMA loan adjustments:\n" + ("\n".join(loan_points) if loan_points else "- No FARMA adjustment flags available in assessments.")

    methodology = (
        "Methodology:\n"
        "- Scan: grounded evidence collection (conflict, displacement, food security, economic).\n"
        "- Synthesis: deterministic DAG; one bounded structured output per state + one rollup.\n"
        "- Privacy: outputs aggregated at LGA/state level; no camp coordinates."
    )

    annexes: Dict[str, str] = {}
    if include_annexes:
        for state_name, assessment in report_data.assessments_by_state.items():
            findings = assessment.get("key_findings") or []
            bullets = []
            for f in findings[:8]:
                finding_text = (f.get("finding") or "").strip()
                if not finding_text:
                    continue
                uris = f.get("source_uris") or []
                uri_txt = ""
                if uris:
                    uri_txt = f" (sources: {', '.join(uris[:3])})"
                bullets.append(f"- {finding_text}{uri_txt}")
            annexes[state_name] = f"{assessment.get('summary') or ''}\n\nKey findings:\n" + ("\n".join(bullets) if bullets else "- No findings.")

    return NarrativeSections(
        executive_summary=executive,
        situation_analysis=situation,
        food_security_assessment=food,
        displacement_analysis=displacement,
        risk_assessment=risk,
        safe_routes_analysis=routes,
        recommendations=recs,
        farmer_loan_adjustments=loans,
        methodology=methodology,
        references=report_data.uri_whitelist,
        state_annexes=annexes,
    )
