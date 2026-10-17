"""Async Ollama HTTP wrapper — all LLM calls in the Nexus engine go through here."""

import asyncio
import logging
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubled on each attempt


def _backoff(attempt: int) -> float:
    """Return exponential backoff delay for the given attempt index (0-based)."""
    return _RETRY_BASE_DELAY * (2 ** attempt)


async def _post_with_retry(
    url: str,
    payload: dict[str, Any],
    timeout: float = 120.0,
) -> dict[str, Any]:
    """POST *payload* to *url* with retry/backoff. Raise on persistent failure."""
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS - 1:
                    delay = _backoff(attempt)
                    logger.warning(
                        "Ollama POST %s failed (attempt %d/%d): %s — retrying in %.1fs",
                        url, attempt + 1, _RETRY_ATTEMPTS, exc, delay,
                    )
                    await asyncio.sleep(delay)
    raise RuntimeError(
        f"Ollama POST {url} failed after {_RETRY_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


async def _get_with_retry(
    url: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """GET *url* with retry/backoff. Raise on persistent failure."""
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS - 1:
                    delay = _backoff(attempt)
                    logger.warning(
                        "Ollama GET %s failed (attempt %d/%d): %s — retrying in %.1fs",
                        url, attempt + 1, _RETRY_ATTEMPTS, exc, delay,
                    )
                    await asyncio.sleep(delay)
    raise RuntimeError(
        f"Ollama GET {url} failed after {_RETRY_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


async def generate(
    model: str,
    prompt: str,
    system: str = "",
    options: dict[str, Any] | None = None,
) -> str:
    """Send a text generation request to Ollama and return the response string.

    Uses stream=False so the full response arrives in one JSON body.
    Raises RuntimeError if Ollama is unreachable or returns a non-200 status.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options or {},
    }
    if system:
        payload["system"] = system

    url = f"{settings.ollama_url}/api/generate"
    logger.debug("generate → model=%s prompt_len=%d", model, len(prompt))
    data = await _post_with_retry(url, payload)
    return data["response"]


async def embed(text: str) -> list[float]:
    """Return a dense embedding vector for *text* using nomic-embed-text.

    Input is capped at 2 048 characters to stay within the model's context window.
    """
    capped = text[:2048]
    payload = {"model": "nomic-embed-text", "prompt": capped}
    url = f"{settings.ollama_url}/api/embeddings"
    logger.debug("embed → text_len=%d (capped=%d)", len(text), len(capped))
    data = await _post_with_retry(url, payload)
    return data["embedding"]


async def generate_vision(
    prompt: str,
    image_b64: str,
    model: str = "llava:7b",
) -> str:
    """Send a vision request (image + prompt) to Ollama and return the response string.

    *image_b64* must be a raw base-64 string (no data-URI prefix).
    num_ctx=2048 and num_predict=350 keep CPU latency manageable.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {
            "num_ctx": 2048,
            "num_predict": 350,
        },
    }
    url = f"{settings.ollama_url}/api/generate"
    logger.debug("generate_vision → model=%s image_b64_len=%d", model, len(image_b64))
    data = await _post_with_retry(url, payload, timeout=180.0)
    return data["response"]


async def ensure_model(model_name: str) -> bool:
    """Verify *model_name* is available locally; pull it if not.

    Returns True when the model is ready, raises RuntimeError on pull failure.
    """
    tags_url = f"{settings.ollama_url}/api/tags"
    logger.info("ensure_model → checking for %s", model_name)
    data = await _get_with_retry(tags_url)

    available = {m["name"] for m in data.get("models", [])}
    if model_name in available:
        logger.info("ensure_model → %s already present", model_name)
        return True

    logger.info("ensure_model → %s not found, pulling…", model_name)
    pull_url = f"{settings.ollama_url}/api/pull"
    await _post_with_retry(pull_url, {"name": model_name, "stream": False}, timeout=600.0)
    logger.info("ensure_model → %s pull complete", model_name)
    return True
