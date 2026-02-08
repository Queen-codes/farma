"""Seed 3 weeks of escalating AEGIS data for Marathon demo.

Creates:
- 3 AegisScan rows (Week 1 baseline, Week 2 escalation, Week 3 crisis)
- 3x3 StateIntelligence rows (Borno, Adamawa, Yobe per scan)
- ConflictEvent rows with LGA-level detail
- Synthesis rollup_json + assessment_json pre-populated

Run: python scripts/seed_marathon_demo.py

Demo flow after seeding:
  POST /api/aegis/marathon/run {"track_id":"demo-track","scan_id":1,"day_date":"2026-01-19"}
  POST /api/aegis/marathon/run {"track_id":"demo-track","scan_id":2,"prev_scan_id":1,"day_date":"2026-01-26"}
  POST /api/aegis/marathon/run {"track_id":"demo-track","scan_id":3,"prev_scan_id":2,"day_date":"2026-02-02"}
  GET  /api/aegis/marathon/demo-track/timeline
"""

import asyncio
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.aegis.db.connection import engine, async_session, init_db
from app.aegis.db.models import AegisScan, StateIntelligence, ConflictEvent


# Source URIs (realistic but not real — demo data)

SOURCES = [
    "https://acleddata.com/data-export-tool/nigeria-borno-2026-01",
    "https://acleddata.com/data-export-tool/nigeria-adamawa-2026-01",
    "https://acleddata.com/data-export-tool/nigeria-yobe-2026-01",
    "https://dtm.iom.int/nigeria/displacement-report-round-47",
    "https://dtm.iom.int/nigeria/displacement-report-round-48",
    "https://fews.net/west-africa/nigeria/food-security-outlook/january-2026",
    "https://reliefweb.int/report/nigeria/nigeria-humanitarian-update-january-2026",
    "https://www.humanitarianresponse.info/en/operations/nigeria/food-security",
    "https://data.unhcr.org/en/country/nga",
    "https://www.ipcinfo.org/ipc-country-analysis/details-map/en/c/1157800/",
]


def _src(*indices: int) -> list[str]:
    """Select source-URI entries by index from `SOURCES`.

    Args:
        *indices: Integer indexes into `SOURCES`.

    Returns:
        Ordered list of selected source URI strings.
    """
    return [SOURCES[i] for i in indices]


# LGA data for each state

BORNO_LGAS = [
    "Maiduguri",
    "Jere",
    "Konduga",
    "Bama",
    "Gwoza",
    "Monguno",
    "Dikwa",
    "Ngala",
]
ADAMAWA_LGAS = [
    "Yola North",
    "Yola South",
    "Mubi North",
    "Mubi South",
    "Michika",
    "Madagali",
]
YOBE_LGAS = ["Damaturu", "Potiskum", "Geidam", "Gashua", "Bursari"]

# Coordinates (approximate centroids)
LGA_COORDS = {
    "Maiduguri": (11.8311, 13.1510),
    "Jere": (11.8700, 13.0900),
    "Konduga": (11.6500, 13.2700),
    "Bama": (11.5200, 13.6900),
    "Gwoza": (11.0800, 13.6900),
    "Monguno": (12.6700, 13.6100),
    "Dikwa": (12.0300, 13.9200),
    "Ngala": (12.3400, 14.1900),
    "Yola North": (9.2300, 12.4600),
    "Yola South": (9.2000, 12.4800),
    "Mubi North": (10.2700, 13.2700),
    "Mubi South": (10.2300, 13.2500),
    "Michika": (10.6200, 13.3900),
    "Madagali": (10.8700, 13.6300),
    "Damaturu": (11.7500, 11.9600),
    "Potiskum": (11.7100, 11.0800),
    "Geidam": (12.8900, 11.9300),
    "Gashua": (12.8700, 11.0500),
    "Bursari": (12.5200, 11.8500),
}

# Week definitions: (state_name, risk_level, ipc, idp, conflict_events, fatalities, markets, idp_trend)

WEEK1 = {
    "Borno": ("ELEVATED", 3, 12000, 8, 23, "partially", "stable"),
    "Adamawa": ("LOW", 2, 3000, 2, 3, "fully", "stable"),
    "Yobe": ("MEDIUM", 2, 5000, 4, 8, "partially", "stable"),
}

WEEK2 = {
    "Borno": ("HIGH", 4, 18000, 15, 47, "partially", "increasing"),
    "Adamawa": ("ELEVATED", 3, 6000, 6, 12, "partially", "increasing"),
    "Yobe": ("MEDIUM", 2, 5500, 5, 9, "partially", "stable"),
}

WEEK3 = {
    "Borno": ("CRITICAL", 5, 32000, 22, 89, "closed", "increasing"),
    "Adamawa": ("HIGH", 4, 12000, 11, 31, "partially", "increasing"),
    "Yobe": ("ELEVATED", 3, 8000, 7, 15, "partially", "increasing"),
}

WEEKS = [
    ("2026-01-19", WEEK1, "SCAN-DEMO-W1"),
    ("2026-01-26", WEEK2, "SCAN-DEMO-W2"),
    ("2026-02-02", WEEK3, "SCAN-DEMO-W3"),
]

EVENT_TYPES = [
    "Armed clash",
    "Kidnapping/Forced disappearance",
    "Attack on civilians",
    "Remote explosive/Landmine/IED",
    "Sexual violence",
    "Strategic development",
]
ACTORS = [
    "Boko Haram",
    "ISWAP",
    "Military Forces of Nigeria",
    "Civilian militia",
    "Unidentified Armed Group",
]


def _make_events(
    state: str,
    lgas: list[str],
    n_events: int,
    n_fatalities: int,
    date_str: str,
) -> list[dict[str, Any]]:
    """Generate deterministic conflict-event rows distributed across LGAs.

    Args:
        state: State name for generated events.
        lgas: Candidate LGAs used for event location sampling.
        n_events: Number of events to generate.
        n_fatalities: Total fatalities budget distributed across events.
        date_str: Anchor date in `YYYY-MM-DD` format.

    Returns:
        List of event dictionaries matching `ConflictEvent`-compatible fields.
    """
    import random

    random.seed(hash(f"{state}-{date_str}"))
    events = []
    base_date = datetime.strptime(date_str, "%Y-%m-%d")
    fat_remaining = n_fatalities
    for i in range(n_events):
        lga = random.choice(lgas)
        lat, lon = LGA_COORDS.get(lga, (11.5, 13.0))
        lat += random.uniform(-0.05, 0.05)
        lon += random.uniform(-0.05, 0.05)
        fat = min(
            random.randint(0, max(1, fat_remaining // max(1, n_events - i))),
            fat_remaining,
        )
        fat_remaining -= fat
        event_date = base_date - timedelta(days=random.randint(0, 6))
        events.append(
            {
                "event_date": event_date.strftime("%Y-%m-%d"),
                "location": f"{lga}, {state}",
                "state": state,
                "lga": lga,
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "event_type": random.choice(EVENT_TYPES),
                "actors": random.choice(ACTORS),
                "fatalities": fat,
                "injuries": random.randint(0, fat * 2),
                "abducted": random.randint(0, 3) if random.random() > 0.7 else 0,
                "summary": f"{random.choice(EVENT_TYPES)} reported in {lga}, {state}. "
                f"{'Multiple casualties reported.' if fat > 2 else 'Situation monitored.'}",
                "source": random.choice(SOURCES[:3]),
            }
        )
    return events


def _make_assessment(
    scan_id: int,
    state: str,
    risk: str,
    ipc: int,
    idp: int,
    n_events: int,
    fatalities: int,
    markets: str,
    idp_trend: str,
    lgas: list[str],
    date_str: str,
) -> dict[str, Any]:
    """Build realistic `assessment_json` payload for one state.

    Args:
        scan_id: Parent scan database identifier.
        state: State name.
        risk: Risk-level label.
        ipc: IPC phase value.
        idp: Estimated number of displaced persons.
        n_events: Conflict event count.
        fatalities: Conflict fatalities count.
        markets: Market-operational status.
        idp_trend: Displacement trend label.
        lgas: LGA list in scope for the state.
        date_str: Week anchor date in `YYYY-MM-DD`.

    Returns:
        Assessment dictionary aligned with synthesis schema conventions.
    """
    import random

    random.seed(hash(f"{state}-{date_str}-assessment"))

    # Priority scoring
    score = min(ipc * 15, 75)
    if idp > 20000:
        score += 35
    elif idp > 10000:
        score += 25
    elif idp > 5000:
        score += 15
    elif idp > 2000:
        score += 8
    if n_events > 15:
        score += 15
    elif n_events > 8:
        score += 8
    elif n_events > 3:
        score += 4
    score = min(score, 100)
    if score >= 80:
        level = "CRITICAL"
    elif score >= 60:
        level = "HIGH"
    elif score >= 40:
        level = "ELEVATED"
    elif score >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"

    # LGA breakdown
    lga_breakdown = []
    idp_per_lga = max(1, idp // len(lgas))
    for lga in lgas:
        lga_events = max(1, n_events // len(lgas))
        lga_fat = max(0, fatalities // len(lgas))
        pop = random.randint(20000, 150000)
        needs = []
        if ipc >= 4:
            needs.append("emergency food")
        if ipc >= 3:
            needs.append("nutritional supplements")
        if idp > 5000:
            needs.extend(["shelter", "non-food items"])
        if n_events > 5:
            needs.append("protection services")
        needs.append("water/sanitation")
        if markets != "fully":
            needs.append("market support")
        if lga_fat > 3:
            needs.append("medical supplies")

        # Access routes
        if risk in ("HIGH", "CRITICAL"):
            route = f"Use {random.choice(['Maiduguri-Damboa', 'Mubi-Yola', 'Damaturu-Gashua'])} corridor with military escort. Avoid night movement."
        elif risk == "ELEVATED":
            route = f"Main highway accessible with precaution. Stage from {random.choice(['Maiduguri', 'Yola', 'Damaturu'])} hub."
        else:
            route = "Standard access via main roads. No significant restrictions."

        lga_breakdown.append(
            {
                "lga": lga,
                "population_at_risk": pop,
                "idp_estimate": idp_per_lga + random.randint(-500, 500),
                "conflict_events": lga_events,
                "fatalities": lga_fat,
                "needs": needs,
                "access_route": route,
                "risk_level": (
                    risk
                    if lga in lgas[:3]
                    else ("MEDIUM" if risk in ("HIGH", "CRITICAL") else "LOW")
                ),
            }
        )

    # Key findings (cite-able statements)
    findings = [
        {
            "finding": f"{state} recorded {n_events} conflict events with {fatalities} fatalities during the reporting period, {'a significant escalation' if n_events > 10 else 'consistent with recent trends'}.",
            "source_uris": _src(
                0 if state == "Borno" else 1 if state == "Adamawa" else 2
            ),
        },
        {
            "finding": f"An estimated {idp:,} internally displaced persons are in {state}, with the trend {idp_trend}.",
            "source_uris": _src(3, 4),
        },
        {
            "finding": f"Food insecurity classified at IPC Phase {ipc} affecting {'crisis-level' if ipc >= 3 else 'stressed'} populations across multiple LGAs.",
            "source_uris": _src(5, 9),
        },
        {
            "finding": f"Markets in {state} are {markets} operational, {'severely limiting' if markets == 'closed' else 'constraining' if markets == 'partially' else 'supporting'} food access for vulnerable populations.",
            "source_uris": _src(7),
        },
        {
            "finding": f"{'Armed groups continue to target civilian areas, particularly in ' + ', '.join(lgas[:3]) + '.' if n_events > 5 else 'Security situation remains tense but contained.'}",
            "source_uris": _src(6),
        },
    ]
    if risk in ("HIGH", "CRITICAL"):
        findings.append(
            {
                "finding": f"Humanitarian access to {state} is severely constrained by ongoing hostilities. Aid convoys require military escort in {', '.join(lgas[:2])}.",
                "source_uris": _src(6, 8),
            }
        )

    hotspots = [lga for lga in lgas[:4] if random.random() > 0.3]

    return {
        "scan_id": scan_id,
        "state": state,
        "summary": f"{state} is at {risk} risk level with IPC Phase {ipc} food insecurity and approximately {idp:,} IDPs. "
        f"{'Situation has deteriorated significantly' if risk in ('HIGH', 'CRITICAL') else 'Situation requires continued monitoring'}.",
        "risk_level": risk,
        "key_findings": findings,
        "metrics": {
            "priority_score": score,
            "priority_level": level,
            "ipc_phase": ipc,
            "food_insecurity_level": (
                "crisis" if ipc >= 3 else "stressed" if ipc >= 2 else "minimal"
            ),
            "idp_estimate": idp,
            "idp_trend": idp_trend,
            "markets_operational": markets,
            "conflict_events_count": n_events,
            "fatalities": fatalities,
            "conflict_hotspots_to_avoid": hotspots,
            "route_recommendation": f"{'Military escort required for all movements.' if risk == 'CRITICAL' else 'Use designated corridors; avoid hotspot LGAs.' if risk == 'HIGH' else 'Standard precautions advised.'}",
        },
        "lga_breakdown": lga_breakdown,
        "confidence": round(0.7 + random.uniform(0, 0.25), 2),
        "audit": {"allowed_uris_count": len(SOURCES), "tool_errors_summary": "none"},
    }


def _make_rollup(scan_id: int, assessments: list[dict[str, Any]]) -> dict[str, Any]:
    """Build `rollup_json` summary from per-state assessments.

    Args:
        scan_id: Parent scan database identifier.
        assessments: Per-state assessment dictionaries.

    Returns:
        Rollup dictionary containing rankings, allocations, and overview summary.
    """
    sorted_a = sorted(
        assessments, key=lambda a: a["metrics"]["priority_score"], reverse=True
    )
    rankings = []
    for i, a in enumerate(sorted_a):
        rankings.append(
            {
                "state": a["state"],
                "rank": i + 1,
                "rationale": a["summary"],
                "source_uris": _src(0, 3, 5),
            }
        )

    total_score = sum(a["metrics"]["priority_score"] for a in assessments) or 1
    allocations = []
    for a in sorted_a:
        pct = round(a["metrics"]["priority_score"] / total_score * 100, 1)
        allocations.append(
            {
                "state": a["state"],
                "allocation_pct": pct,
                "note": f"{a['state']}: {a['metrics']['priority_level']} priority, IPC {a['metrics']['ipc_phase']}",
                "source_uris": _src(5, 9),
            }
        )

    top = sorted_a[0]
    overall = (
        f"Northeast Nigeria humanitarian situation as of scan {scan_id}: "
        f"{top['state']} is the highest priority state at {top['risk_level']} risk "
        f"with IPC Phase {top['metrics']['ipc_phase']} and approximately "
        f"{top['metrics']['idp_estimate']:,} IDPs. "
        f"Across {len(assessments)} states, "
        f"{sum(a['metrics']['idp_estimate'] for a in assessments):,} people are displaced."
    )

    return {
        "scan_id": scan_id,
        "overall_summary": overall,
        "rankings": rankings,
        "allocations": allocations,
        "confidence": 0.82,
    }


def _make_conflict_raw(events: list[dict[str, Any]], state: str) -> dict[str, Any]:
    """Build `conflict_raw` payload matching scan pipeline JSON shape."""
    return {
        "data": {
            "events": events,
            "summary": f"Conflict data for {state}",
            "event_count": len(events),
            "fatalities": sum(e.get("fatalities", 0) for e in events),
        },
        "sources": [
            {"uri": s, "title": f"ACLED {state}"}
            for s in _src(0, 1, 2)
            if state.lower() in s.lower()
        ]
        or [{"uri": SOURCES[0], "title": "ACLED"}],
    }


def _make_displacement_raw(idp: int, trend: str, state: str) -> dict[str, Any]:
    """Build `displacement_raw` payload for seeded state intelligence rows."""
    return {
        "data": {
            "idp_estimate": idp,
            "idp_trend": trend,
            "source": "IOM DTM",
        },
        "sources": [{"uri": s, "title": "IOM DTM"} for s in _src(3, 4)],
    }


def _make_food_raw(ipc: int, level: str, state: str) -> dict[str, Any]:
    """Build `food_security_raw` payload for seeded state intelligence rows."""
    return {
        "data": {
            "ipc_phase": ipc,
            "food_insecurity_level": level,
            "source": "FEWS NET / IPC",
        },
        "sources": [{"uri": s, "title": "FEWS NET"} for s in _src(5, 9)],
    }


def _make_econ_raw(markets: str, state: str) -> dict[str, Any]:
    """Build `economic_raw` payload for seeded state intelligence rows."""
    return {
        "data": {
            "markets_operational": markets,
            "source": "Humanitarian Response",
        },
        "sources": [{"uri": s, "title": "Humanitarian Response"} for s in _src(7)],
    }


async def seed() -> None:
    """Seed three weekly scans with escalating intelligence and events.

    Returns:
        None.

    Raises:
        Exception: Propagates unexpected DB/session failures.

    Side Effects:
        Initializes DB schema and writes `AegisScan`, `StateIntelligence`,
        and `ConflictEvent` rows.
        Prints progress to stdout for demo operators.
    """
    await init_db()
    print("Seeding Marathon demo data (3 weeks)...\n")

    state_lgas = {"Borno": BORNO_LGAS, "Adamawa": ADAMAWA_LGAS, "Yobe": YOBE_LGAS}

    async with async_session() as session:
        for week_idx, (date_str, week_data, run_id) in enumerate(WEEKS):
            week_num = week_idx + 1
            print(f"--- Week {week_num}: {date_str} ({run_id}) ---")

            # Create scan
            scan = AegisScan(
                run_id=run_id,
                started_at=datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=None),
                completed_at=datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=None)
                + timedelta(minutes=3),
                status="completed",
                states_scanned=3,
                total_events=sum(d[3] for d in week_data.values()),
                total_fatalities=sum(d[4] for d in week_data.values()),
            )
            session.add(scan)
            await session.flush()
            scan_id = scan.id
            print(f"  Scan ID: {scan_id}")

            assessments = []
            for state_name, (
                risk,
                ipc,
                idp,
                n_events,
                fatalities,
                markets,
                idp_trend,
            ) in week_data.items():
                lgas = state_lgas[state_name]
                events = _make_events(state_name, lgas, n_events, fatalities, date_str)
                food_level = (
                    "crisis" if ipc >= 3 else "stressed" if ipc >= 2 else "minimal"
                )

                assessment = _make_assessment(
                    scan_id,
                    state_name,
                    risk,
                    ipc,
                    idp,
                    n_events,
                    fatalities,
                    markets,
                    idp_trend,
                    lgas,
                    date_str,
                )
                assessments.append(assessment)

                intel = StateIntelligence(
                    scan_id=scan_id,
                    state_name=state_name,
                    collected_at=datetime.strptime(date_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    ),
                    conflict_raw=_make_conflict_raw(events, state_name),
                    displacement_raw=_make_displacement_raw(idp, idp_trend, state_name),
                    food_security_raw=_make_food_raw(ipc, food_level, state_name),
                    economic_raw=_make_econ_raw(markets, state_name),
                    assessment_json=assessment,
                    synthesized_at=datetime.strptime(date_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    ),
                    synthesis_version="v2-demo",
                    conflict_events_count=n_events,
                    idp_estimate=idp,
                    idp_trend=idp_trend,
                    food_insecurity_level=food_level,
                    ipc_phase=ipc,
                    markets_operational=markets,
                )
                session.add(intel)

                # Add individual conflict events
                for ev in events:
                    await session.flush()  # ensure intel.id is set
                    ce = ConflictEvent(
                        state_intel_id=intel.id,
                        event_date=ev["event_date"],
                        location=ev["location"],
                        state=state_name,
                        lga=ev["lga"],
                        latitude=ev["latitude"],
                        longitude=ev["longitude"],
                        event_type=ev["event_type"],
                        actors=ev.get("actors"),
                        fatalities=ev["fatalities"],
                        injuries=ev.get("injuries", 0),
                        abducted=ev.get("abducted", 0),
                        summary=ev["summary"],
                        source=ev.get("source"),
                    )
                    session.add(ce)

                print(
                    f"  {state_name}: {risk}, IPC {ipc}, {idp:,} IDPs, {n_events} events, {fatalities} fatalities"
                )

            # Rollup
            rollup = _make_rollup(scan_id, assessments)
            scan.rollup_json = rollup
            await session.flush()
            print(f"  Rollup: {rollup['overall_summary'][:80]}...")

        await session.commit()

    print("\nDone! Demo data seeded successfully.")
    print("\nMarathon demo commands:")
    print(
        "  POST /api/aegis/marathon/run {track_id:'demo-track', scan_id:1, day_date:'2026-01-19'}"
    )
    print(
        "  POST /api/aegis/marathon/run {track_id:'demo-track', scan_id:2, prev_scan_id:1, day_date:'2026-01-26'}"
    )
    print(
        "  POST /api/aegis/marathon/run {track_id:'demo-track', scan_id:3, prev_scan_id:2, day_date:'2026-02-02'}"
    )
    print("  GET  /api/aegis/marathon/demo-track/timeline")


if __name__ == "__main__":
    asyncio.run(seed())
