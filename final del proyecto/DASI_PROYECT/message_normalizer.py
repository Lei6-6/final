"""
message_normalizer.py
---------------------
Converts any incoming message — JSON or natural language — into a
single NormalizedMessage before business logic runs.

Priority order:
  1. Parse as JSON directly.
  2. Rule/pattern-based extraction from plain text.
  3. Ollama LLM fallback.
  4. Return kind="unknown" rather than crashing.
"""

import json
import re
from loguru import logger

from models import NormalizedMessage
from ollama_client import call_ollama
from prompt_builder import build_normalization_prompt

VALID_RESOURCES: set[str] = {
    "arroz", "ladrillos", "madera", "piedra", "queso", "tela", "trigo", "oro",
    "vino", "aceite",
}

# Maps singular/alternate forms → canonical resource name used throughout the system
_RESOURCE_ALIASES: dict[str, str] = {
    "ladrillo": "ladrillos",
    "maderas":  "madera",
    "telas":    "tela",
    "quesos":   "queso",
    "vinos":    "vino",
    "aceites":  "aceite",
}

# Keywords that suggest each message kind
_KIND_KEYWORDS: dict[str, list[str]] = {
    "request":  ["quiero", "necesito", "me das", "tienes", "ofreces",
                 "dame", "intercambio", "por", "cambio", "puedes darme",
                 "ofrezco", "a cambio"],
    "delivery": ["te envío", "te mando", "aquí tienes", "entrego",
                 "enviando", "recibe", "te doy"],
    "accept":   ["acepto", "trato", "hecho", "ok", "de acuerdo",
                 "vale", "perfecto", "genial", "aceptado"],
    "reject":   ["rechazo", "no puedo", "imposible", "no tengo",
                 "no acepto"],
}

# Words that indicate the sender is OFFERING resources (they will give)
_OFFER_INDICATORS = ["ofrezco", "ofrecerte", "puedo ofrecerte", "te doy", "doy ",
                     "oferto", "puedo darte", "tengo para", "voy a darte", "quiero darte"]

# Separators that split barter messages into two halves
_BARTER_SEPARATORS = ["a cambio de", "a cambio por", "en intercambio por",
                      "en intercambio de", "por ", "para recibir"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _try_parse_json(text: str) -> dict | None:
    """Return a dict if text is valid JSON, else None."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _normalise_text(text: str) -> str:
    """Replace resource aliases with their canonical names."""
    t = text.lower()
    for alias, canonical in _RESOURCE_ALIASES.items():
        t = re.sub(rf"\b{alias}\b", canonical, t)
    return t


def _extract_resources(text: str) -> dict[str, int]:
    """Extract {resource: quantity} pairs from plain text."""
    text_lower = _normalise_text(text)
    result: dict[str, int] = {}
    for resource in VALID_RESOURCES:
        if resource not in text_lower:
            continue
        # Accept patterns like "2 arroz", "arroz: 3", "arroz x2"
        match = re.search(
            rf"(\d+)\s*(?:de\s+)?{resource}|{resource}[\s:x]+(\d+)",
            text_lower,
        )
        result[resource] = int(match.group(1) or match.group(2)) if match else 1
    return result


def _extract_have_offers(text: str) -> dict[str, int]:
    """
    Detect resources the sender explicitly says they HAVE or own.
    Patterns like "tengo vino", "me sobra arroz", "tengo 6 unidades de vino"
    indicate offered resources, not requests.
    """
    text_lower = _normalise_text(text)
    result: dict[str, int] = {}
    have_triggers = ["tengo ", "me sobran ", "me sobra ", "puedo ofrecer ", "ofrezco "]
    for resource in VALID_RESOURCES:
        if resource not in text_lower:
            continue
        for trigger in have_triggers:
            pos = 0
            while True:
                trigger_pos = text_lower.find(trigger, pos)
                if trigger_pos == -1:
                    break
                window = text_lower[trigger_pos: trigger_pos + 80]
                if resource in window:
                    qty = 1
                    for qp in [
                        rf"(\d+)\s*(?:de\s+)?(?:unidades?\s+(?:de\s+)?)?{resource}",
                        rf"{resource}\s*\(?(\d+)",
                    ]:
                        m = re.search(qp, window)
                        if m:
                            qty = int(m.group(1))
                            break
                    result[resource] = qty
                    break
                pos = trigger_pos + 1
            if resource in result:
                break
    return result


def _split_barter(text: str) -> tuple[dict[str, int], dict[str, int]]:
    """
    Try to split a barter message into (wanted_from_us, offered_to_us).

    Detects patterns like:
      "Ofrezco 2 madera a cambio de 1 trigo"  → wanted={trigo:1}, offered={madera:2}
      "Quiero 1 vino, te doy 2 trigo"         → wanted={vino:1},  offered={trigo:2}
      "Dame 1 arroz por 1 oro"                → wanted={arroz:1}, offered={oro:1}

    Returns ({}, {}) if no clear barter pattern is found.
    """
    text_lower = _normalise_text(text)

    # Find the first matching separator
    sep_found = None
    sep_pos = len(text_lower)
    for sep in _BARTER_SEPARATORS:
        pos = text_lower.find(sep)
        if pos != -1 and pos < sep_pos:
            sep_found = sep
            sep_pos = pos

    if sep_found is None:
        return {}, {}

    before = text_lower[:sep_pos]
    after = text_lower[sep_pos + len(sep_found):]

    before_resources = _extract_resources(before)
    after_resources = _extract_resources(after)

    # If neither half has resources, bail out
    if not before_resources and not after_resources:
        return {}, {}

    # Decide which side is an "offer" (they give) vs "want" (they request from us)
    before_is_offer = any(kw in before for kw in _OFFER_INDICATORS)

    if before_is_offer:
        # "Ofrezco X a cambio de Y" → offering X, wants Y from us
        return after_resources, before_resources
    else:
        # "Quiero X a cambio de Y" / "Dame X por Y" → wants X, offering Y
        return before_resources, after_resources


def _detect_kind(text: str) -> str:
    """Return the most likely message kind from keyword matching."""
    text_lower = _normalise_text(text)
    scores = {kind: 0 for kind in _KIND_KEYWORDS}
    for kind, keywords in _KIND_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[kind] += 1
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "unknown"


def _from_json(data: dict, raw_text: str) -> NormalizedMessage:
    """Build a NormalizedMessage from a parsed JSON dict."""
    kind = data.get("kind", "unknown")
    if kind not in ("request", "delivery", "accept", "reject", "unknown"):
        kind = "unknown"

    resources: dict[str, int] = {}
    for k, v in (data.get("resources") or {}).items():
        if isinstance(v, (int, float)) and v >= 0:
            resources[k] = int(v)

    offered_resources: dict[str, int] = {}
    for k, v in (data.get("offered_resources") or {}).items():
        if isinstance(v, (int, float)) and v >= 0:
            offered_resources[k] = int(v)

    return NormalizedMessage(
        from_agent=str(data.get("from_agent", "unknown")),
        kind=kind,
        resources=resources,
        offered_resources=offered_resources,
        raw_text=raw_text,
        metadata=data.get("metadata") or {},
    )


def _from_text(text: str) -> NormalizedMessage:
    """Build a NormalizedMessage using rule-based extraction."""
    wanted, offered = _split_barter(text)

    # Augment offered with explicit "tengo X / me sobra X" claims
    have_offers = _extract_have_offers(text)
    for r, q in have_offers.items():
        if r not in offered:
            offered[r] = q

    if wanted:
        # If a resource appears in both halves of a barter, it belongs to offered
        resources = {r: q for r, q in wanted.items() if r not in offered}
    else:
        # Remove resources the sender claims to have — they're offering, not requesting
        all_res = _extract_resources(text)
        resources = {r: q for r, q in all_res.items() if r not in offered}

    return NormalizedMessage(
        from_agent="unknown",
        kind=_detect_kind(text),
        resources=resources,
        offered_resources=offered,
        raw_text=text,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def normalize(raw: str, from_agent: str = "unknown") -> NormalizedMessage:
    """
    Normalize an incoming message into a NormalizedMessage.

    Args:
        raw:        The raw message string (JSON or natural language).
        from_agent: The sender identifier (IP or alias) supplied by the caller.
    Returns:
        A NormalizedMessage with kind != "unknown" when possible.
    """
    raw = raw.strip()
    logger.debug(f"Normalizing: {raw[:120]}")

    # --- 1. Try JSON parse ---
    data = _try_parse_json(raw)
    if data is not None:
        msg = _from_json(data, raw)
        if msg.from_agent == "unknown" and from_agent != "unknown":
            msg = msg.model_copy(update={"from_agent": from_agent})
        logger.info(
            f"Normalized via JSON | kind={msg.kind} "
            f"resources={msg.resources} offered={msg.offered_resources}"
        )
        return msg

    # --- 2. Rule-based ---
    msg = _from_text(raw)
    if msg.from_agent == "unknown":
        msg = msg.model_copy(update={"from_agent": from_agent})

    if msg.kind != "unknown":
        logger.info(
            f"Normalized via rules | kind={msg.kind} "
            f"resources={msg.resources} offered={msg.offered_resources}"
        )
        return msg

    # --- 3. Ollama fallback ---
    try:
        prompt = build_normalization_prompt(raw)
        response_text = await call_ollama(prompt)
        if response_text:
            ollama_data = _try_parse_json(response_text)
            if ollama_data:
                msg = _from_json(ollama_data, raw)
                if msg.from_agent == "unknown":
                    msg = msg.model_copy(update={"from_agent": from_agent})
                logger.info(
                    f"Normalized via Ollama | kind={msg.kind} resources={msg.resources}"
                )
                return msg
    except Exception as exc:
        logger.warning(f"Ollama normalization failed: {exc}")

    # --- 4. Unknown fallback ---
    logger.warning(f"Could not normalize message — returning 'unknown': {raw[:80]}")
    return NormalizedMessage(
        from_agent=from_agent,
        kind="unknown",
        resources={},
        raw_text=raw,
        metadata={},
    )
