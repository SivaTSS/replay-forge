"""ASGI entry point for the configured local ReplayForge runtime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from replayforge.api.app import create_app
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings

runtime = build_runtime(RuntimeSettings())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    yield
    runtime.close()


app = create_app(runtime.api_services)
app.router.lifespan_context = lifespan
