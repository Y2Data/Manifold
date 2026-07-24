from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routing.backends import BackendError
from app.routing.classifier import Tier
from app.routing.router import route
from app.store import get_project, recent_decisions, summary

router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    project_id: int
    messages: list[Message]
    connection_id: int | None = None  # None = auto-decide; explicit id = force that connection
    tier: str | None = None  # None = auto-classify; "SIMPLE" | "MEDIUM" | "COMPLEX" = pin


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    if get_project(req.project_id) is None:
        raise HTTPException(404, "project not found")

    user_messages = [m.content for m in req.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(400, "no user message in request")
    prompt = user_messages[-1]

    forced_tier = None
    if req.tier is not None:
        try:
            forced_tier = Tier(req.tier.upper())
        except ValueError:
            raise HTTPException(400, f"invalid tier: {req.tier}")

    try:
        decisions = await route(
            req.project_id, prompt, forced_connection_id=req.connection_id, forced_tier=forced_tier
        )
    except BackendError as exc:
        raise HTTPException(400, str(exc)) from exc

    choices = [
        {
            "index": i,
            "message": {"role": "assistant", "content": d["result"].text},
            "finish_reason": "stop",
            "x_routing": {k: v for k, v in d.items() if k != "result"},
        }
        for i, d in enumerate(decisions)
    ]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": decisions[0]["model"],
        "choices": choices,
    }


@router.get("/decisions")
async def get_decisions(limit: int = 50):
    return recent_decisions(limit)


@router.get("/summary")
async def get_summary():
    return summary()
