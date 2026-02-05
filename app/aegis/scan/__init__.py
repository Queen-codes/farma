"""AEGIS scan (Gemini-native async + grounded tools).

This module is an incremental replacement for the legacy scan pipeline. It is designed for:
- True async Gemini calls via `client.aio.models.generate_content`
- Grounded evidence collection with citation spans
- Planner → tool execution → synthesis with thought signature preservation
- Bounded concurrency + real-time custom events
"""

from .graph import aegis_scan_graph, build_aegis_scan_graph
from .runner import run_aegis_scan

__all__ = ["aegis_scan_graph", "build_aegis_scan_graph", "run_aegis_scan"]
