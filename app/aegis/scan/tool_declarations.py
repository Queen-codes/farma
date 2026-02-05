from __future__ import annotations

from google.genai import types


def conflict_declaration() -> types.FunctionDeclaration:
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
    return [
        conflict_declaration(),
        displacement_declaration(),
        food_security_declaration(),
        economic_declaration(),
    ]
