"""AEGIS crisis simulation.

Purpose:
- Run deterministic counterfactual projections from synthesis artifacts.
- Generate partner-facing policy brief recommendations with URI constraints.

Main flow:
- `runner.run_simulation_dag` -> `graph.simulator_graph` ->
  `nodes` (load inputs, projections, policy brief, persist, finalize).
"""

from __future__ import annotations
