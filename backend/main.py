"""FastAPI application entry point: lifespan, CORS, and router registration."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routes.architect_router import router as architect_router
from backend.routes.actor_router import router as actor_router
from backend.routes.chronos_router import router as chronos_router
from backend.routes.import_router import router as import_router
from backend.routes.world_router import router as world_router

logger = logging.getLogger(__name__)


async def _verify_ollama_reachable() -> None:
    """Probe Ollama's /api/tags endpoint and raise RuntimeError if unreachable."""
    url = f"{settings.ollama_url}/api/tags"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            logger.info("Ollama reachable at %s — models: %s", settings.ollama_url, response.json())
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {settings.ollama_url}: {exc}"
            ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup checks then graceful shutdown."""
    logger.info("Starting Nexus RPG Engine — verifying external services…")
    await _verify_ollama_reachable()
    logger.info("All startup checks passed. Application ready.")
    yield
    logger.info("Nexus RPG Engine shutting down.")


app = FastAPI(
    title="Nexus RPG Engine",
    description="Local-first, CPU-only AI RPG backend (Architect + Actor modes).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(architect_router, prefix="/architect", tags=["architect"])
app.include_router(actor_router, prefix="/actor", tags=["actor"])
app.include_router(chronos_router, prefix="/chronos", tags=["chronos"])
app.include_router(import_router, prefix="/import", tags=["import"])
app.include_router(world_router, prefix="/api", tags=["world"])


@app.get("/health", tags=["meta"])
async def health_check() -> dict[str, str]:
    """Liveness probe used by Docker and load balancers."""
    return {"status": "ok"}
