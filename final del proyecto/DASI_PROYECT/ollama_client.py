"""
ollama_client.py
----------------
Async HTTP client for the Ollama REST API.
Returns None on any failure so callers can fall back gracefully.
"""

import httpx
from loguru import logger

from config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT


async def call_ollama(prompt: str) -> str | None:
    """
    Send a prompt to Ollama and return the raw response text.

    Returns None when Ollama times out, is unreachable, returns an empty
    body, or returns an unexpected response structure.
    """
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }

    logger.info(f"Calling Ollama (model='{OLLAMA_MODEL}')...")

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

        data = response.json()
        text = data.get("response", "").strip()

        if not text:
            logger.warning("Ollama returned an empty response body.")
            return None

        logger.info("Ollama responded successfully.")
        return text

    except httpx.TimeoutException:
        logger.warning("Ollama request timed out — activating fallback.")
        return None
    except httpx.RequestError as exc:
        logger.warning(f"Ollama unreachable: {exc} — activating fallback.")
        return None
    except Exception as exc:
        logger.error(f"Unexpected Ollama error: {exc} — activating fallback.")
        return None
