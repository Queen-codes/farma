"""In-process websocket pub/sub bus for lightweight live status messages.

This module keeps connected websocket clients and broadcasts short status
updates (for example job event summaries) to all active listeners.
"""

from __future__ import annotations

import asyncio
from typing import Set

from fastapi import WebSocket


class ThinkingBus:
    """Manage websocket subscribers and broadcast messages safely."""

    def __init__(self) -> None:
        """Initialize empty websocket subscriber set and async lock.

        Returns:
            None.

        Raises:
            None.

        Side Effects:
            Allocates lock and in-memory client registry.

        Latency:
            Constant-time initialization.
        """
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a websocket subscriber.

        Args:
            websocket: FastAPI websocket connection to attach.

        Returns:
            None.

        Raises:
            Exception: Propagates websocket accept failures.

        Side Effects:
            Accepts websocket handshake and mutates client set.

        Latency:
            Includes websocket accept handshake and lock acquisition.
        """
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove websocket subscriber if present.

        Args:
            websocket: Connection to remove from subscriber set.

        Returns:
            None.

        Raises:
            None: Missing clients are ignored.

        Side Effects:
            Mutates in-memory client set.

        Latency:
            Constant-time set discard under lock.
        """
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, message: str) -> None:
        """Send message to all connected subscribers (best-effort).

        Args:
            message: Text payload to send to each client.

        Returns:
            None.

        Raises:
            None: Per-client send failures are handled by auto-disconnect.

        Side Effects:
            Performs websocket sends and removes dead connections.

        Latency:
            Linear in active client count and network/socket send time.
        """
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                await self.disconnect(ws)


thinking_bus = ThinkingBus()
