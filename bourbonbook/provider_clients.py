from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar, Token

import httpx
from openai import AsyncOpenAI

from bourbonbook.config import Settings

_shared_openai_client: ContextVar[AsyncOpenAI | None] = ContextVar(
    "shared_openai_client", default=None
)
_shared_ollama_client: ContextVar[httpx.AsyncClient | None] = ContextVar(
    "shared_ollama_client", default=None
)


def set_shared_openai_client(client: AsyncOpenAI | None) -> Token[AsyncOpenAI | None]:
    return _shared_openai_client.set(client)


def reset_shared_openai_client(token: Token[AsyncOpenAI | None]) -> None:
    _shared_openai_client.reset(token)


def set_shared_ollama_client(client: httpx.AsyncClient | None) -> Token[httpx.AsyncClient | None]:
    return _shared_ollama_client.set(client)


def reset_shared_ollama_client(token: Token[httpx.AsyncClient | None]) -> None:
    _shared_ollama_client.reset(token)


@asynccontextmanager
async def openai_client_session(settings: Settings):
    shared = _shared_openai_client.get()
    if shared is not None:
        yield shared
        return
    async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=120.0) as client:
        yield client


@asynccontextmanager
async def ollama_client_session():
    shared = _shared_ollama_client.get()
    if shared is not None:
        yield shared
        return
    async with httpx.AsyncClient(timeout=120) as client:
        yield client
