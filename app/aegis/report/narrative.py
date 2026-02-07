"""Narrative generation utilities for AEGIS report text sections.

Purpose:
- Build LLM payload and prompts for formal humanitarian narrative sections.
- Provide deterministic template fallback when LLM generation fails.

Used by:
- `app.aegis.report.nodes.generate_narrative_node`.

Assumptions:
- URI whitelist is precomputed in `ReportData`.
- LLM mode requires `GOOGLE_API_KEY`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY
from app.aegis.report.report_data import ReportData


@dataclass
class NarrativeSections:
    """Structured text sections consumed by the PDF rendering stage."""

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


def _build_numbered_references(report_data: ReportData) -> tuple[List[str], Dict[str, int]]:
    """Build a numbered reference list and URI-to-number mapping."""
    uris = report_data.uri_whitelist or []
    uri_to_num: Dict[str, int] = {}
    for i, uri in enumerate(uris, 1):
        uri_to_num[uri] = i
    return uris, uri_to_num


def _build_llm_payload(report_data: ReportData, uri_to_num: Dict[str, int]) -> dict:
    """Build the payload sent to Gemini for narrative generation."""
    total_events, total_fatalities = report_data.totals()
    rollup = report_data.rollup or {}

    # Build per-state summaries with citation numbers
    state_data = []
    for state_name, assessment in report_data.assessments_by_state.items():
        metrics = assessment.get("metrics") or {}
        findings_with_refs = []
        for f in (assessment.get("key_findings") or []):
            refs = []
            for uri in (f.get("source_uris") or []):
                if uri in uri_to_num:
                    refs.append(uri_to_num[uri])
            findings_with_refs.append({
                "text": f.get("finding", ""),
                "citation_numbers": refs,
            })

        lga_data = assessment.get("lga_breakdown") or []

        state_data.append({
            "state": state_name,
            "risk_level": assessment.get("risk_level", "UNKNOWN"),
            "summary": assessment.get("summary", ""),
            "metrics": metrics,
            "findings": findings_with_refs,
            "lga_breakdown": lga_data,
        })

    sim_data = None
    if report_data.simulation:
        sim = report_data.simulation
        sim_data = {
            "simulation_id": sim.get("simulation_id"),
            "scenario": sim.get("scenario_json"),
            "projections": sim.get("projections_json"),
            "policy_brief": sim.get("policy_brief_json"),
        }

    return {
        "scan_id": report_data.scan_id,
        "generated_at": report_data.generated_at,
        "total_events": total_events,
        "total_fatalities": total_fatalities,
        "overall_summary": rollup.get("overall_summary", ""),
        "rankings": rollup.get("rankings", []),
        "allocations": rollup.get("allocations", []),
        "states": state_data,
        "simulation": sim_data,
    }


NARRATIVE_SYSTEM_PROMPT = """\
You are writing a formal Humanitarian Situation Report for Northeast Nigeria, \
in the style used by OCHA (United Nations Office for the Coordination of Humanitarian Affairs).

This document will be submitted to humanitarian organizations (WFP, UNHCR, IOM, ICRC) \
as a request for coordinated aid. It must be:
- Authoritative and evidence-based
- Written in third-person, formal analytical prose
- Structured for decision-makers who need to allocate resources quickly
- Specific about WHO needs help, WHAT they need, WHERE they are, and HOW to reach them

CITATION FORMAT (CRITICAL):
- Use numbered inline citations like [1], [2], [3] corresponding to the reference list.
- Every factual claim MUST have at least one citation.
- Example: "An estimated 32,000 people have been displaced in Borno State.[1][4]"
- The citation numbers are provided in the input data alongside each finding.

SECTION REQUIREMENTS:
Return a JSON object with exactly these keys:

1. "executive_summary": 2-3 paragraphs. Lead with the most critical finding. Include total \
   people affected, highest-priority states, and the most urgent action needed. End with \
   the funding/resource ask.

2. "situation_analysis": State-by-state analysis. For each state, describe the security \
   situation, key developments, and how the situation has evolved. Use citations.

3. "food_security_assessment": Detail IPC phases per state, food insecurity levels, market \
   disruptions. Explain what this means for affected populations in practical terms \
   (e.g., "families are reducing meals to one per day").

4. "displacement_analysis": IDP numbers per state and trend. Where are people moving? \
   What are conditions like in displacement sites? Use specific LGA-level data.

5. "risk_assessment": Which areas are most dangerous? What are the threats? How are they \
   changing? Include the risk level classification per state.

6. "safe_routes_analysis": For EACH state, describe: (a) which LGAs are accessible, \
   (b) recommended delivery corridors, (c) areas to avoid and why, \
   (d) staging/logistics hubs. This is critical for aid convoy planning.

7. "recommendations": 5-8 specific, actionable recommendations. Each should name a \
   responsible actor (e.g., "WFP should..."), a specific action, a target area, \
   and urgency level. Do NOT write generic platitudes.

8. "farmer_loan_adjustments": Based on risk levels and food security data, recommend \
   which areas FARMA should adjust loan terms: defer payments, reduce amounts, or \
   pause disbursements. Be specific per state/LGA.

9. "methodology": Brief description of data sources and analytical approach.

10. "state_annexes": A dict mapping each state name to a detailed 3-5 paragraph annex \
    with LGA-level breakdown: population at risk, needs, access routes, and specific \
    recommendations for that state. Include all LGA data from the breakdown.
"""


async def generate_narrative_llm(
    report_data: ReportData,
    include_annexes: bool = True,
    thinking_level: str = "low",
    model: str = "gemini-3-flash-preview",
    timeout_s: float = 120.0,
) -> NarrativeSections:
    """Generate the full report narrative using Gemini."""
    uris, uri_to_num = _build_numbered_references(report_data)
    payload = _build_llm_payload(report_data, uri_to_num)

    prompt = (
        NARRATIVE_SYSTEM_PROMPT
        + "\n\nINPUT DATA (JSON):\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
        + "\n\nNUMBERED REFERENCE LIST:\n"
        + "\n".join(f"[{i+1}] {uri}" for i, uri in enumerate(uris))
        + "\n\nGenerate the JSON response now."
    )

    if not include_annexes:
        prompt += "\nSkip state_annexes (return empty dict for that key)."

    client = genai.Client(api_key=GOOGLE_API_KEY)

    # Determine thinking level
    lvl = (thinking_level or "low").upper()
    thinking_enum = getattr(types.ThinkingLevel, lvl, types.ThinkingLevel.LOW)

    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=thinking_enum),
        temperature=0.3,
        response_mime_type="application/json",
    )

    coro = client.aio.models.generate_content(
        model=model, contents=prompt, config=config
    )
    resp = await asyncio.wait_for(coro, timeout=timeout_s)

    # Parse response
    text = getattr(resp, "text", None) or ""
    if not text:
        try:
            parts = resp.candidates[0].content.parts or []
            text = "\n".join(getattr(p, "text", "") for p in parts).strip()
        except Exception:
            text = ""

    if not text:
        raise RuntimeError("Gemini returned empty narrative response")

    obj = json.loads(text)

    return NarrativeSections(
        executive_summary=obj.get("executive_summary", ""),
        situation_analysis=obj.get("situation_analysis", ""),
        food_security_assessment=obj.get("food_security_assessment", ""),
        displacement_analysis=obj.get("displacement_analysis", ""),
        risk_assessment=obj.get("risk_assessment", ""),
        safe_routes_analysis=obj.get("safe_routes_analysis", ""),
        recommendations=obj.get("recommendations", ""),
        farmer_loan_adjustments=obj.get("farmer_loan_adjustments", ""),
        methodology=obj.get("methodology", ""),
        references=uris,
        state_annexes=obj.get("state_annexes", {}) if include_annexes else {},
    )


def render_template_narrative(report_data: ReportData, include_annexes: bool) -> NarrativeSections:
    """Fallback template-based narrative (no LLM). Used when narrative_mode='template'."""
    uris, uri_to_num = _build_numbered_references(report_data)
    rollup = report_data.rollup or {}
    rankings = rollup.get("rankings") or []

    top_states = ", ".join([r.get("state", "") for r in rankings[:3] if isinstance(r, dict) and r.get("state")]) or ", ".join(report_data.states[:3])
    overall = (rollup.get("overall_summary") or "").strip()
    if not overall:
        overall = (
            "This report summarizes AEGIS scan outputs and deterministic synthesis assessments. "
            "It highlights displacement pressure, food insecurity, and access constraints."
        )

    total_events, total_fatalities = report_data.totals()
    executive = (
        f"{overall}\n\n"
        f"Top priority states: {top_states or 'N/A'}. "
        f"Total conflict events: {total_events}. Total fatalities: {total_fatalities}.\n"
        "All findings are evidence-linked to scan-grounded sources (see References)."
    )

    # State-by-state
    lines = []
    for state_name, a in report_data.assessments_by_state.items():
        risk = a.get("risk_level", "UNKNOWN")
        summary = (a.get("summary") or "").replace("\n", " ").strip()
        lines.append(f"- {state_name}: {risk} - {summary}")
    situation = "Per-state situation snapshots:\n" + "\n".join(lines)

    food_points, disp_points, risk_points, route_points, loan_points = [], [], [], [], []
    for state_name, assessment in report_data.assessments_by_state.items():
        metrics = assessment.get("metrics") or {}
        ipc = metrics.get("ipc_phase", 0)
        idp = metrics.get("idp_estimate")
        markets = metrics.get("markets_operational")
        food_points.append(f"- {state_name}: IPC Phase {ipc or 'unknown'}, markets {markets or 'unknown'}.")
        disp_points.append(f"- {state_name}: {idp if idp is not None else 'unknown':,} IDPs, trend {metrics.get('idp_trend', 'unknown')}.")
        risk_points.append(f"- {state_name}: {assessment.get('risk_level', 'unknown')}, confidence {assessment.get('confidence', 'n/a')}.")

        # LGA-level route info
        for lga in (assessment.get("lga_breakdown") or []):
            if lga.get("access_route"):
                route_points.append(f"- {state_name}/{lga['lga']}: {lga['access_route']}")

        hotspots = metrics.get("conflict_hotspots_to_avoid") or []
        if hotspots:
            route_points.append(f"- {state_name} hotspots to avoid: {', '.join(hotspots)}.")

        flags = metrics.get("loan_risk_flags") or []
        if flags:
            loan_points.append(f"- {state_name}: {', '.join(str(x) for x in flags)}.")

    recs = (
        "Recommendations:\n"
        "- Prioritize food assistance and protection in HIGH/CRITICAL states.\n"
        "- Pre-position supplies along safe corridors; avoid flagged no-go routes.\n"
        "- Coordinate with partners (WFP/OCHA/IOM) using LGA-aggregated outputs only."
    )

    methodology = (
        "Methodology:\n"
        "- Scan: grounded evidence collection (conflict, displacement, food security, economic).\n"
        "- Synthesis: deterministic DAG; one bounded structured output per state + one rollup.\n"
        "- Privacy: outputs aggregated at LGA/state level; no camp coordinates."
    )

    if report_data.simulation:
        sim = report_data.simulation
        scen = (sim.get("scenario_json") or {}).get("scenario") if isinstance(sim.get("scenario_json"), dict) else sim.get("scenario_json")
        proj = sim.get("projections_json") or {}
        brief = sim.get("policy_brief_json") or {}
        sim_lines = [
            f"Simulation ID: {sim.get('simulation_id')}",
            f"Crisis type: {(scen or {}).get('crisis_type') if isinstance(scen, dict) else ''}",
            f"Projected IDP delta: {((proj.get('humanitarian') or {}).get('idp_delta'))}",
            f"Projected food need (MT): {((proj.get('humanitarian') or {}).get('food_mt'))}",
            f"Projected funding gap (USD): {((proj.get('humanitarian') or {}).get('funding_gap_usd'))}",
            f"Policy brief: {brief.get('summary') or ''}",
        ]
        methodology += "\n\nCrisis Simulation:\n" + "\n".join(f"- {l}" for l in sim_lines if l)

    annexes: Dict[str, str] = {}
    if include_annexes:
        for state_name, assessment in report_data.assessments_by_state.items():
            parts = [assessment.get("summary", ""), ""]
            # LGA breakdown
            for lga in (assessment.get("lga_breakdown") or []):
                parts.append(
                    f"{lga['lga']} ({lga.get('risk_level', 'N/A')}): "
                    f"{lga.get('population_at_risk', 0):,} at risk, "
                    f"{lga.get('idp_estimate', 0):,} IDPs, "
                    f"{lga.get('conflict_events', 0)} events. "
                    f"Needs: {', '.join(lga.get('needs', []))}. "
                    f"Access: {lga.get('access_route', 'N/A')}."
                )
            # Key findings with citations
            parts.append("\nKey findings:")
            for f in (assessment.get("key_findings") or [])[:8]:
                text = (f.get("finding") or "").strip()
                if not text:
                    continue
                refs = []
                for uri in (f.get("source_uris") or []):
                    if uri in uri_to_num:
                        refs.append(f"[{uri_to_num[uri]}]")
                ref_str = "".join(refs)
                parts.append(f"- {text}{ref_str}")
            annexes[state_name] = "\n".join(parts)

    return NarrativeSections(
        executive_summary=executive,
        situation_analysis=situation,
        food_security_assessment="Food security highlights:\n" + "\n".join(food_points),
        displacement_analysis="Displacement highlights:\n" + "\n".join(disp_points),
        risk_assessment="Risk assessment:\n" + "\n".join(risk_points),
        safe_routes_analysis="Safe routes / access constraints:\n" + ("\n".join(route_points) if route_points else "- No route data available."),
        recommendations=recs,
        farmer_loan_adjustments="FARMA loan adjustments:\n" + ("\n".join(loan_points) if loan_points else "- No FARMA adjustment flags available."),
        methodology=methodology,
        references=uris,
        state_annexes=annexes,
    )
