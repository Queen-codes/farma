import json
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY, MODEL_FLASH
from .shared import AEGIS_FOCUS_STATES
from .shared_grounding import (
    GroundingSource,
    GroundingMetadata,
    extract_grounding_metadata,
    get_date_range,
)


class ConflictEvent(BaseModel):
    """A single conflict or security event"""

    date: str = Field(description="Date of the event (YYYY-MM-DD or approximate)")
    location: str = Field(description="Specific location (town, LGA, state)")
    state: str = Field(description="Nigerian state where event occurred")
    lga: Optional[str] = Field(
        default=None, description="Local Government Area if identifiable"
    )
    event_type: Literal[
        "armed_attack",
        "kidnapping",
        "banditry",
        "terrorism",
        "communal_clash",
        "military_operation",
        "other",
    ] = Field(description="Type of security event")
    actors: Optional[str] = Field(
        default=None,
        description="Groups or actors involved (e.g., Boko Haram, bandits)",
    )
    fatalities: Optional[int] = Field(
        default=None, description="Reported fatalities if mentioned"
    )
    injuries: Optional[int] = Field(
        default=None, description="Reported injuries if mentioned"
    )
    abducted: Optional[int] = Field(
        default=None, description="Number of people abducted/kidnapped if mentioned"
    )
    summary: str = Field(description="Brief 1-2 sentence factual summary of the event")
    source: Optional[str] = Field(
        default=None, description="News source if identifiable"
    )


class ConflictSearchResult(BaseModel):
    """Result of searching for conflict events"""

    state_searched: str = Field(default="", description="State that was searched")
    search_date: str = Field(default="", description="Date this search was performed")
    timeframe: str = Field(default="", description="Timeframe of search")
    events: List[ConflictEvent] = Field(
        default_factory=list, description="List of conflict events found"
    )
    total_events: int = Field(default=0, description="Total events found")
    sources_consulted: List[str] = Field(
        default_factory=list, description="Source URIs from grounding"
    )
    grounding: Optional[GroundingMetadata] = Field(
        default=None, description="Google Search grounding metadata"
    )


def search_conflict_events(
    state: str,
    days_back: int = 7,
) -> Optional[ConflictSearchResult]:
    """
    Search for recent conflict/security events in a particular state of focus

    Returns:
        ConflictSearchResult with events and grounding metadata (URIs, search queries)
    """
    print(f"Conflict Search: {state} ({days_back} days)")

    if state not in AEGIS_FOCUS_STATES:
        print(f"{state} not in AEGIS focus states")

    date_range, year = get_date_range(days_back)

    search_prompt = f"""Search for ALL recent security incidents in {state} State, Nigeria.

IMPORTANT: Only include events from {date_range} (year {year}).
Do NOT include events from {year - 1} or earlier.

Search for:
- Armed attacks, ambushes, military operations
- Kidnappings and abductions
- Banditry and robbery
- Terrorist activities (Boko Haram, ISWAP, etc.)
- Communal/ethnic clashes

For each event, report:
- Exact date
- Location (town, LGA)
- What happened
- Casualties (killed, injured, abducted)
- Who was responsible

Be thorough and cite your sources."""

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)

        # Grounded search w/o json structure
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        search_config = types.GenerateContentConfig(tools=[grounding_tool])

        print("[Step 1] Grounded search...")
        search_response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=search_prompt,
            config=search_config,
        )

        # Extract grounding metadata
        grounding = extract_grounding_metadata(search_response, debug=True)

        print(f"Search queries: {grounding.search_queries}")
        print(f"Sources found: {len(grounding.sources)}")
        for src in grounding.sources:
            print(f"{src.title}")
            print(f"{src.uri}")

        grounded_text = search_response.text
        print(f"\n[Grounded Response Preview]")
        print(f"{grounded_text}...")

        # Extract structured data
        extract_prompt = f"""Extract structured data from this security report about {state} State, Nigeria.

SOURCE TEXT:
{grounded_text}

Extract each security event as JSON. Use this exact schema:
{{
  "events": [
    {{
      "date": "YYYY-MM-DD",
      "location": "town/area name",
      "state": "{state}",
      "lga": "Local Government Area or null",
      "event_type": "armed_attack|kidnapping|banditry|terrorism|communal_clash|military_operation|other",
      "actors": "group responsible or null",
      "fatalities": number or null,
      "injuries": number or null,
      "abducted": number or null,
      "summary": "1-2 sentence factual summary",
      "source": "news source name or null"
    }}
  ]
}}

Only include events with dates in {date_range}.
Return ONLY valid JSON, no other text."""

        print("\nExtracting structured data...")
        extract_config = types.GenerateContentConfig(
            response_mime_type="application/json",
        )

        extract_response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=extract_prompt,
            config=extract_config,
        )

        data = json.loads(extract_response.text)

        result = ConflictSearchResult(
            state_searched=state,
            search_date=datetime.now().strftime("%Y-%m-%d"),
            timeframe=date_range,
            events=[ConflictEvent(**e) for e in data.get("events", [])],
            total_events=len(data.get("events", [])),
            sources_consulted=[s.uri for s in grounding.sources],
            grounding=grounding,
        )

        # result logging
        total_fatalities = sum(e.fatalities or 0 for e in result.events)
        total_abducted = sum(e.abducted or 0 for e in result.events)
        print(f"\nFound: {result.total_events} events")
        if total_fatalities > 0:
            print(f"Fatalities: {total_fatalities}")
        if total_abducted > 0:
            print(f"Abducted: {total_abducted}")
        for event in result.events:
            print(f"[{event.event_type}] {event.location}: {event.summary}...")

        return result

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return None
