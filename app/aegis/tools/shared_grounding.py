"""Shared grounding utilities for data collation tools.

Provides reusable components for Google Search grounding with Gemini:
- GroundingSource: URI/title from search results
- GroundingMetadata: Search queries and sources
- extract_grounding_metadata(): Extracts metadata from Gemini response
- grounded_search(): Two-step grounded search with structured extraction
"""

import json
from pydantic import BaseModel, Field
from typing import List, Optional, Type, TypeVar
from datetime import datetime, timedelta

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY, MODEL_FLASH


class GroundingSource(BaseModel):
    """A grounding source from Google Search."""

    uri: str = Field(description="URL of the source")
    title: str = Field(description="Title of the source")


class GroundingMetadata(BaseModel):
    """Metadata from Google Search grounding."""

    search_queries: List[str] = Field(
        default_factory=list, description="Search queries executed by Gemini"
    )
    sources: List[GroundingSource] = Field(
        default_factory=list, description="Web sources used for grounding"
    )


def extract_grounding_metadata(response, debug: bool = False) -> GroundingMetadata:
    """Extract grounding metadata from Gemini response.

    Args:
        response: Gemini GenerateContentResponse
        debug: If True, prints debug info about response structure

    Returns:
        GroundingMetadata with search queries and source URIs
    """
    metadata = GroundingMetadata()

    try:
        if not response.candidates:
            return metadata

        grounding_meta = response.candidates[0].grounding_metadata
        if not grounding_meta:
            return metadata

        # Print all available attributes
        if debug:
            print(f"\ngrounding_metadata attributes:")
            for attr in dir(grounding_meta):
                if not attr.startswith("_"):
                    val = getattr(grounding_meta, attr, None)
                    if val is not None and not callable(val):
                        print(f"{attr}: {type(val).__name__} = {str(val)[:200]}")

        # Extract search queries
        if grounding_meta.web_search_queries:
            metadata.search_queries = list(grounding_meta.web_search_queries)

        # Extract grounding chunks (sources with URIs)
        if grounding_meta.grounding_chunks:
            for chunk in grounding_meta.grounding_chunks:
                if hasattr(chunk, "web") and chunk.web:
                    metadata.sources.append(
                        GroundingSource(
                            uri=chunk.web.uri or "",
                            title=chunk.web.title or "",
                        )
                    )

    except Exception as e:
        print(f"   Warning: Could not extract grounding metadata: {e}")

    return metadata


T = TypeVar("T", bound=BaseModel)


def grounded_search(
    search_prompt: str,
    extract_prompt: str,
    result_class: Type[T],
    debug: bool = False,
) -> tuple[Optional[T], GroundingMetadata]:
    """Perform search with extraction.

    : search: gets full grounding metadata with URIs
    : Structure extraction - parses the grounded response into structured data

    Args:
        search_prompt: Prompt for grounded web search
        extract_prompt: Prompt template for JSON extraction (use {grounded_text} placeholder)
        result_class: Pydantic model class for the result
        debug: If True, prints debug info

    Returns:
        Tuple of (parsed result or None, grounding metadata)
    """
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)

        #  search
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        search_config = types.GenerateContentConfig(tools=[grounding_tool])

        if debug:
            print("Grounded search...")

        search_response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=search_prompt,
            config=search_config,
        )

        # extract grounding metadata
        grounding = extract_grounding_metadata(search_response, debug=debug)
        grounded_text = search_response.text

        if debug:
            print(f"Search queries: {grounding.search_queries}")
            print(f"Sources found: {len(grounding.sources)}")
            for src in grounding.sources[:3]:
                print(f"{src.title}: {src.uri}...")
            print(f"\nGrounded Response Preview")
            print(f"{grounded_text}...")

        # Extract structured data
        full_extract_prompt = extract_prompt.replace("{grounded_text}", grounded_text)

        if debug:
            print("\nExtracting structured data...")

        extract_config = types.GenerateContentConfig(
            response_mime_type="application/json",
        )

        extract_response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=full_extract_prompt,
            config=extract_config,
        )

        # Parse response
        data = json.loads(extract_response.text)
        result = result_class(**data)

        return result, grounding

    except json.JSONDecodeError as e:
        print(f"error parsing JSON: {e}")
        # Return error marker dict instead of None - downstream can check for 'error' key
        error_result = {"_collection_error": f"JSON parse error: {str(e)}"}
        return error_result, grounding if 'grounding' in dir() else GroundingMetadata()
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback

        traceback.print_exc()
        # Return error marker dict instead of None
        error_result = {"_collection_error": f"Collection failed: {str(e)}"}
        return error_result, GroundingMetadata()


def get_date_range(days_back: int) -> tuple[str, int]:
    """Get formatted date range string and current year.

    Args:
        days_back: Number of days to look back

    Returns:
        Tuple of (date_range string, year)
    """
    today = datetime.now()
    start_date = today - timedelta(days=days_back)
    date_range = f"{start_date.strftime('%B %d, %Y')} to {today.strftime('%B %d, %Y')}"
    return date_range, today.year
