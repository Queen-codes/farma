"""Gemini function declarations for scan grounded-tool planning.

Purpose:
- Define planner-visible function signatures for each grounded intelligence tool.

Used by:
- `app.aegis.scan.state_worker` through `all_declarations()`.

Assumptions:
- Each declaration name maps to a callable in `app.aegis.scan.tools`.
"""

from __future__ import annotations

from google.genai import types


def conflict_declaration() -> types.FunctionDeclaration:
    """Return function declaration for conflict-event collection tool."""
    return types.FunctionDeclaration(
        name="search_conflict_events",
        description="Use web search to find recent conflict/security incidents in a Nigerian state. Returns evidence with citations.",
        parameters={
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    )


def displacement_declaration() -> types.FunctionDeclaration:
    """Return function declaration for displacement/IDP collection tool."""
    return types.FunctionDeclaration(
        name="search_displacement",
        description="Use web search to find recent displacement/IDP signals in a Nigerian state. Returns evidence with citations.",
        parameters={
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    )


def food_security_declaration() -> types.FunctionDeclaration:
    """Return function declaration for food-security signal collection tool."""
    return types.FunctionDeclaration(
        name="search_food_security",
        description="Use web search to find recent food security/IPC signals in a Nigerian state. Returns evidence with citations.",
        parameters={
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    )


def economic_declaration() -> types.FunctionDeclaration:
    """Return function declaration for market/economic signal collection tool."""
    return types.FunctionDeclaration(
        name="search_economic_indicators",
        description="Use web search to find recent market/economic signals (prices, inflation, access) in a Nigerian state. Returns evidence with citations.",
        parameters={
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
        },
    )


def all_declarations() -> list[types.FunctionDeclaration]:
    """Return planner declaration list in deterministic order."""
    return [
        conflict_declaration(),
        displacement_declaration(),
        food_security_declaration(),
        economic_declaration(),
    ]
