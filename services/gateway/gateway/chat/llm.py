"""LLM provider interface + the Gemini implementation.

The agent loop (agent.py) only knows `LLMProvider.step()`: "here is the
conversation so far and the tools you may use; what next?" Swapping Gemini for
another provider means writing one class with that method. This is the only
file that imports google.genai.

Conversation history uses a small provider-neutral format:
  {"role": "user", "text": ...}
  {"role": "assistant", "text": ..., "tool_calls": [ToolCall...], "raw": <provider object>}
  {"role": "tool", "id": ..., "name": ..., "result": "...", "is_error": bool}
"raw" keeps the provider's own message within one request. Gemini needs its
function-call parts sent back exactly as it produced them (they carry
"thought signatures"), so we never rebuild them ourselves.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import mcp.types as t


@dataclass
class ToolCall:
    id: str | None
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class LLMError(Exception):
    """The provider failed (bad key, quota, unknown model...). Message is safe to show."""


class LLMProvider(Protocol):
    model: str

    async def step(self, system: str, history: list[dict], tools: list[t.Tool]) -> Turn: ...


class GeminiProvider:
    def __init__(self, api_key: str, model: str):
        from google import genai            # imported here: only needed when Gemini is configured
        self.model = model
        self._client = genai.Client(api_key=api_key)

    def _contents(self, history: list[dict]) -> list:
        from google.genai import types
        contents: list[types.Content] = []
        for entry in history:
            role = entry["role"]
            if role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=entry["text"])]))
            elif role == "assistant":
                if entry.get("raw") is not None:
                    contents.append(entry["raw"])        # Gemini's own message, unchanged
                elif entry.get("text"):
                    contents.append(types.Content(role="model", parts=[types.Part(text=entry["text"])]))
            elif role == "tool":
                part = types.Part(function_response=types.FunctionResponse(
                    id=entry.get("id"), name=entry["name"],
                    response={"error": entry["result"]} if entry.get("is_error") else {"result": entry["result"]}))
                # Several tool results answering one model turn go in one message.
                if contents and contents[-1].role == "user" and contents[-1].parts and contents[-1].parts[0].function_response:
                    contents[-1].parts.append(part)
                else:
                    contents.append(types.Content(role="user", parts=[part]))
        return contents

    def declarations(self, tools: list[t.Tool]) -> list:
        """MCP tools -> Gemini function declarations. The MCP input schema is
        JSON Schema already, so it passes through unchanged."""
        from google.genai import types
        return [types.FunctionDeclaration(name=tool.name, description=tool.description or "",
                                          parameters_json_schema=tool.input_schema) for tool in tools]

    @staticmethod
    def to_turn(response) -> Turn:
        candidate = (response.candidates or [None])[0]
        content = candidate.content if candidate else None
        parts = (content.parts if content else None) or []
        text = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False)) or None
        calls = [ToolCall(id=p.function_call.id, name=p.function_call.name, arguments=dict(p.function_call.args or {}))
                 for p in parts if p.function_call]
        if text is None and not calls and candidate is not None and candidate.finish_reason:
            text = f"(the model returned no answer: {candidate.finish_reason})"
        return Turn(text=text, tool_calls=calls, raw=content)

    async def step(self, system: str, history: list[dict], tools: list[t.Tool]) -> Turn:
        from google.genai import errors, types
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=self.declarations(tools))] if tools else None,
            # We run the tool loop ourselves, so every call goes through the gateway.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=self._contents(history), config=config)
        except errors.APIError as exc:
            raise LLMError(f"Gemini error {exc.code}: {exc.message}") from exc
        return self.to_turn(response)
