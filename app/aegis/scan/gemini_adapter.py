"""Gemini function-calling adapter used by the scan state worker.

Purpose:
- Encapsulate planner and synthesis calls against the Gemini API.
- Preserve planner model content so function-response replay follows expected
  tool-calling conversation format.
- Provide bounded retry and timeout handling for model calls.

Used by:
- `app.aegis.scan.state_worker.aegis_state_worker`.

Assumptions:
- Valid Google API key is provided by caller.
- Function declarations and tool result payloads follow Gemini SDK shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from google import genai
from google.genai import types
import asyncio


@dataclass
class Plan:
    """Planner output envelope reused by synthesis stage."""

    prompt: str
    model_content: Any
    function_calls: List[types.FunctionCall]


_T = TypeVar("_T")


class Gemini3Adapter:
    def __init__(
        self,
        *,
        api_key: str,
        model_planner: str,
        model_grounded: str,
        model_synth: str,
        thinking_level: str = "LOW",
        timeout_s: float | None = None,
        max_retries: int = 0,
    ) -> None:
        """Initialize adapter with model routing and resilience settings.

        Args:
            api_key: Gemini API key.
            model_planner: Model name used for tool-planning call.
            model_grounded: Reserved grounded model identifier (kept for config parity).
            model_synth: Model name used for post-tool synthesis call.
            thinking_level: Gemini thinking level (`LOW`, `MEDIUM`, `HIGH`).
            timeout_s: Optional per-call timeout in seconds.
            max_retries: Retry count for transient model-call failures.

        Returns:
            None.

        Raises:
            ValueError: Can propagate from invalid retry coercion inputs.

        Side Effects:
            Creates a Gemini client instance.

        Latency:
            Constant-time object initialization.
        """
        self.client = genai.Client(api_key=api_key)
        self.model_planner = model_planner
        self.model_grounded = model_grounded
        self.model_synth = model_synth
        self.thinking_level = thinking_level
        self.timeout_s = timeout_s
        self.max_retries = max(0, int(max_retries))

    async def _call_with_retry(
        self, coro_factory: Callable[[], Awaitable[_T]]
    ) -> _T:
        """Execute an async Gemini call with timeout + exponential backoff retries.

        Args:
            coro_factory: Zero-arg callable that returns a fresh awaitable per attempt.

        Returns:
            _T: Result returned by the awaited coroutine.

        Raises:
            Exception: Re-raises final exception after retries are exhausted.

        Side Effects:
            Performs sleeps between retries.

        Latency:
            Network-bound model call latency plus retry backoff delays.
        """
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self.max_retries:
            try:
                coro = coro_factory()
                if self.timeout_s and self.timeout_s > 0:
                    return await asyncio.wait_for(coro, timeout=self.timeout_s)
                return await coro
            except Exception as e:
                last_exc = e
                if attempt >= self.max_retries:
                    break
                # simple backoff
                await asyncio.sleep(0.6 * (2**attempt))
                attempt += 1
        assert last_exc is not None
        raise last_exc

    async def plan_tools(
        self,
        *,
        prompt: str,
        function_declarations: list[types.FunctionDeclaration],
    ) -> Plan:
        """Run planner turn and extract requested tool calls.

        Args:
            prompt: Planner instruction text.
            function_declarations: Available tool declarations for function calling.

        Returns:
            Plan: Planner content and ordered function call list.

        Raises:
            RuntimeError: If Gemini returns no candidates or no function calls.
            Exception: Can propagate transport/model-call failures.

        Side Effects:
            Makes network call to Gemini API.

        Latency:
            Depends on model inference and network latency.
        """
        resp = await self._call_with_retry(
            lambda: self.client.aio.models.generate_content(
                model=self.model_planner,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(function_declarations=function_declarations)],
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.AUTO
                        )
                    ),
                    thinking_config=types.ThinkingConfig(
                        thinking_level=self.thinking_level
                    ),
                ),
            )
        )

        if not getattr(resp, "candidates", None):
            raise RuntimeError("Planner returned no candidates")
        cand = resp.candidates[0]
        content = cand.content
        parts = getattr(content, "parts", None) or []

        calls: List[types.FunctionCall] = []
        for p in parts:
            fc = getattr(p, "function_call", None) or getattr(p, "functionCall", None)
            if fc:
                calls.append(fc)

        if not calls:
            raise RuntimeError("Planner returned no function calls")

        return Plan(prompt=prompt, model_content=content, function_calls=calls)

    def build_function_response_parts(
        self,
        *,
        plan: Plan,
        tool_results: Dict[str, Any],
    ) -> list[types.Part]:
        """Build `function_response` parts matching planner call order.

        Args:
            plan: Planner output with ordered function calls.
            tool_results: Tool payloads keyed by call ID and/or function name.

        Returns:
            list[types.Part]: Ordered function-response parts for synthesis turn.

        Raises:
            Does not raise intentionally.

        Side Effects:
            None.

        Latency:
            Linear in number of function calls.
        """
        parts: list[types.Part] = []
        for call in plan.function_calls:
            name = call.name or ""
            call_id = call.id
            response_payload = None
            if call_id is not None:
                response_payload = tool_results.get(str(call_id))
            if response_payload is None:
                response_payload = tool_results.get(name)
            if response_payload is None:
                response_payload = {"error": "missing_tool_result"}
            fr = types.FunctionResponse(
                id=call_id, name=name, response=response_payload
            )
            parts.append(types.Part(function_response=fr))
        return parts

    async def synthesize(
        self,
        *,
        plan: Plan,
        tool_results: Dict[str, Any],
        synth_prompt: str,
    ) -> str:
        """Run synthesis turn using planner context and tool responses.

        Args:
            plan: Planner output containing original model content.
            tool_results: Tool payloads keyed by call ID/name.
            synth_prompt: Final synthesis instruction text.

        Returns:
            str: Combined text output from synthesis model response.

        Raises:
            Exception: Can propagate model-call failures.

        Side Effects:
            Makes network call to Gemini API.

        Latency:
            Depends on model inference and network latency.
        """
        # Turn structure (NO INTERLEAVING):
        # 1) user prompt text content
        # 2) EXACT model content from Turn 1 (candidate.content, unchanged)
        # 3) user content containing ALL functionResponse parts

        function_response_parts = self.build_function_response_parts(
            plan=plan, tool_results=tool_results
        )

        contents = [
            types.Content(role="user", parts=[types.Part(text=synth_prompt)]),
            plan.model_content,
            # Gemini function-calling history expects function responses as tool turns.
            types.Content(role="tool", parts=function_response_parts),
        ]

        resp = await self._call_with_retry(
            lambda: self.client.aio.models.generate_content(
                model=self.model_synth,
                contents=contents,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level=self.thinking_level
                    ),
                ),
            )
        )

        if not getattr(resp, "candidates", None):
            return ""
        cand = resp.candidates[0]
        parts = getattr(cand.content, "parts", None) or []
        texts: list[str] = []
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                texts.append(str(t))
        return "\n".join(texts).strip()
