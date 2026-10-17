"""POST /api/chat: one turn of the chat agent for the logged-in user.

Stateless: the browser sends the visible conversation each time; nothing is
stored except the audit rows of the tool calls (channel "chat").
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..chat import token as chat_token
from ..chat.agent import run_chat_with_timeout
from ..chat.llm import LLMError
from .deps import UserDep

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=8000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool
    result: str | None
    error: str | None


class ChatOut(BaseModel):
    answer: str
    tool_calls: list[ToolCallOut]
    steps: int
    model: str


@router.post("/chat")
async def chat(body: ChatIn, user: UserDep, request: Request) -> ChatOut:
    state, cfg = request.app.state, request.app.state.settings
    provider = state.llm
    if provider is None:
        raise HTTPException(503, "chat is not configured: no LLM API key (an admin can run `make llm-secret`)")
    if body.messages[-1].role != "user":
        raise HTTPException(422, "the last message must be from the user")
    if user.id in state.chat_busy:       # one agent loop per user at a time (e.g. double click)
        raise HTTPException(429, "a chat request is already running for you")
    state.chat_busy.add(user.id)
    try:
        result = await run_chat_with_timeout(
            provider, timeout=cfg.chat_timeout_seconds,
            self_url=cfg.chat_self_url,
            token=chat_token.mint(state.chat_key, user.id),       # acts as THIS user, channel "chat"
            messages=[m.model_dump() for m in body.messages],
            max_steps=cfg.chat_max_steps,
        )
    except LLMError as exc:
        raise HTTPException(502, str(exc))
    except TimeoutError:
        raise HTTPException(504, f"the chat agent took longer than {cfg.chat_timeout_seconds:g}s")
    finally:
        state.chat_busy.discard(user.id)
    return ChatOut(answer=result.answer, tool_calls=result.tool_calls, steps=result.steps, model=provider.model)
