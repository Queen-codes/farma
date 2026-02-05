from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
import asyncio


@dataclass
class Plan:
    prompt: str
    model_content: Any
    function_calls: List[types.FunctionCall]


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
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_planner = model_planner
        self.model_grounded = model_grounded
        self.model_synth = model_synth
        self.thinking_level = thinking_level
        self.timeout_s = timeout_s
        self.max_retries = max(0, int(max_retries))

    async def _call_with_retry(self, coro_factory):
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
        """Planner turn (no grounding): returns multiple functionCall parts in one response."""
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
        """Build FunctionResponse parts in the same order as the function calls."""
        parts: list[types.Part] = []
        for call in plan.function_calls:
            name = call.name or ""
            call_id = call.id
            response_payload = tool_results.get(name, {"error": "missing_tool_result"})
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
        """Synthesis turn: preserve exact planner model content, then provide all functionResponses."""
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
            types.Content(role="user", parts=function_response_parts),
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
