"""
decision_engine.py
------------------
Core business logic for processing incoming resource messages.

Rules:
- The LLM suggests strategy; code enforces all constraints.
- State is mutated ONLY through state_manager.
- If Ollama fails at any point, rule-based fallback takes over.
"""

import json
from loguru import logger

import butler
import agents
from config import AGENT_NAME
from models import NormalizedMessage, DecisionResponse, DeliveryResponse
from state_manager import state
from prompt_builder import build_decision_prompt
from ollama_client import call_ollama


# ---------------------------------------------------------------------------
# Request processing
# ---------------------------------------------------------------------------

async def process_request(msg: NormalizedMessage) -> DecisionResponse:
    """
    Process a 'request' message and return a decision.

    Flow:
      1. Read current state snapshot.
      2. Split requested resources into forbidden vs exchangeable.
      3. If nothing is exchangeable, reject immediately.
      4. Ask Ollama for a strategy; fall back to rules on any failure.
      5. Validate Ollama output strictly (code owns constraints).
      6. Atomically update state and send resources via Butler.
      7. Return the decision.
    """
    snap = state.snapshot()
    inventory: dict[str, int] = snap["inventory"]
    goal_needs: dict[str, int] = snap["goal_needs"]
    target_resources: set[str] = snap["target_resources"]
    requested: dict[str, int] = msg.resources

    if not requested:
        # They may have offered goal resources without an explicit request — counter-request them
        await _counter_request_if_valuable(msg, target_resources)
        return DecisionResponse(decision="reject", resources={}, reason="Empty request.")

    # Goal resources the requester is offering us
    incoming_goal = {
        r: q for r, q in msg.offered_resources.items()
        if r in target_resources and q > 0
    }

    # --- Split: forbidden vs exchangeable ---
    forbidden: dict[str, int] = {}
    exchangeable: dict[str, int] = {}

    for resource, qty in requested.items():
        if not isinstance(qty, int) or qty <= 0:
            forbidden[resource] = qty
        elif resource in target_resources:
            forbidden[resource] = qty
        elif inventory.get(resource, 0) < qty:
            forbidden[resource] = qty
        else:
            exchangeable[resource] = qty

    if forbidden:
        logger.info(f"Forbidden resources (will not trade): {list(forbidden.keys())}")

    if not exchangeable:
        logger.info("No exchangeable resources available — rejecting.")
        return DecisionResponse(
            decision="reject",
            resources={},
            reason=f"Cannot provide: {list(forbidden.keys())}",
        )

    # Don't give away resources if the requester offers nothing that helps our goal
    if goal_needs and not incoming_goal:
        logger.info("Requester offers no goal resources — rejecting to preserve inventory.")
        return DecisionResponse(
            decision="reject",
            resources={},
            reason="No goal resources offered in exchange.",
        )

    # --- Get decision from Ollama or rule-based fallback ---
    decision_data = await _get_decision(
        inventory, goal_needs, target_resources, requested, exchangeable
    )

    decision = decision_data["decision"]
    resources = decision_data["resources"]
    reason = decision_data["reason"]

    # --- Execute: update state then send ---
    if decision in ("accept", "offer") and resources:
        success = await state.deduct_resources(resources)
        if not success:
            logger.warning("State deduction failed after decision — rejecting.")
            return DecisionResponse(
                decision="reject", resources={}, reason="Inventory check failed."
            )

        # Resolve IP to alias for Butler call
        alias = await butler.get_alias_for_ip(msg.from_agent)
        if alias:
            await butler.send_resources(alias, resources)
        else:
            logger.warning(
                f"Could not resolve alias for '{msg.from_agent}'; "
                "resources deducted locally but Butler send skipped."
            )

        # If they promised goal resources in exchange, request delivery now
        await _request_promised_resources(msg, target_resources)

    # When rejecting, if sender mentioned having goal resources, counter-request them
    if decision == "reject":
        await _counter_request_if_valuable(msg, target_resources)

    logger.info(f"Decision: {decision} | resources={resources}")
    return DecisionResponse(decision=decision, resources=resources, reason=reason)


async def _counter_request_if_valuable(
    msg: NormalizedMessage,
    target_resources: set[str],
) -> None:
    """
    Even when rejecting a request, if the sender mentioned holding goal resources,
    immediately send them a structured request offering our tradeable inventory.
    This turns a dead-end rejection into a new trade opportunity.
    """
    if not msg.offered_resources or msg.from_agent in ("unknown", ""):
        return

    goal_offers = {
        r: qty for r, qty in msg.offered_resources.items()
        if r in target_resources and qty > 0
    }
    if not goal_offers:
        return

    snap = state.snapshot()
    tradeable = {
        k: v for k, v in snap["inventory"].items()
        if k not in snap["target_resources"] and v > 0
    }
    if not tradeable:
        return

    request_msg = json.dumps({
        "kind": "request",
        "from_agent": AGENT_NAME,
        "resources": goal_offers,
        "offered_resources": tradeable,
    })
    logger.info(
        f"Counter-requesting goal resources from {msg.from_agent}: "
        f"{goal_offers} (offering {list(tradeable.keys())})"
    )
    await agents.send_message_to_agent(msg.from_agent, request_msg)


async def _request_promised_resources(
    msg: NormalizedMessage,
    target_resources: set[str],
) -> None:
    """
    After accepting a barter, send a structured JSON request back to the sender
    asking them to deliver what they promised (if it overlaps with our goals).
    """
    if not msg.offered_resources or msg.from_agent in ("unknown", ""):
        return

    # Only ask for resources that are in our goal
    wanted = {
        r: qty for r, qty in msg.offered_resources.items()
        if r in target_resources and qty > 0
    }
    if not wanted:
        return

    request_msg = json.dumps({
        "kind": "request",
        "from_agent": AGENT_NAME,
        "resources": wanted,
        "offered_resources": {},
    })
    logger.info(f"Requesting promised goal resources from {msg.from_agent}: {wanted}")
    await agents.send_message_to_agent(msg.from_agent, request_msg)


async def _get_decision(
    inventory: dict[str, int],
    goal_needs: dict[str, int],
    target_resources: set[str],
    requested: dict[str, int],
    exchangeable: dict[str, int],
) -> dict:
    """Try Ollama first; return rule-based dict on any failure."""
    # Ollama attempt
    prompt = build_decision_prompt(
        inventory, goal_needs, target_resources, requested, exchangeable
    )
    response_text = await call_ollama(prompt)

    if response_text:
        try:
            data = json.loads(response_text)
            validated = _validate_ollama_decision(data, exchangeable, target_resources, inventory)
            if validated:
                logger.info(f"Using Ollama decision: {validated['decision']}")
                return validated
            logger.warning("Ollama decision failed validation — using rule-based fallback.")
        except json.JSONDecodeError:
            logger.warning("Ollama returned non-JSON — using rule-based fallback.")
    else:
        logger.info("Ollama unavailable — using rule-based fallback.")

    # Rule-based fallback: accept all exchangeable
    return {
        "decision": "accept",
        "resources": exchangeable,
        "reason": "Rule-based fallback: accepting all available exchangeable resources.",
    }


def _validate_ollama_decision(
    data: dict,
    exchangeable: dict[str, int],
    target_resources: set[str],
    inventory: dict[str, int],
) -> dict | None:
    """
    Strictly validate an Ollama decision dict.

    Returns the cleaned dict on success, None if any constraint is violated.
    """
    decision = data.get("decision")
    if decision not in ("accept", "offer", "reject"):
        logger.warning(f"Ollama returned invalid decision value: '{decision}'")
        return None

    resources = data.get("resources", {})
    if not isinstance(resources, dict):
        logger.warning("Ollama 'resources' field is not a dict.")
        return None

    reason = str(data.get("reason", ""))

    if decision in ("accept", "offer"):
        for resource, qty in resources.items():
            if not isinstance(qty, int) or qty < 0:
                logger.warning(f"Ollama invalid qty for '{resource}': {qty}")
                return None
            if resource in target_resources:
                logger.warning(f"Ollama tried to trade target resource: '{resource}'")
                return None
            if resource not in exchangeable:
                logger.warning(f"Ollama included non-requested resource: '{resource}'")
                return None
            if qty > inventory.get(resource, 0):
                logger.warning(
                    f"Ollama exceeded stock for '{resource}': {qty} > {inventory.get(resource)}"
                )
                return None

    return {"decision": decision, "resources": resources, "reason": reason}


# ---------------------------------------------------------------------------
# Delivery processing
# ---------------------------------------------------------------------------

async def process_delivery(msg: NormalizedMessage) -> DeliveryResponse:
    """
    Process a 'delivery' message: add received resources to inventory and
    update goal tracking.
    """
    resources = msg.resources
    if not resources:
        return DeliveryResponse(status="error", message="Delivery contained no resources.")

    await state.add_resources(resources)
    snap = state.snapshot()
    logger.info(f"Delivery processed | new state: {snap}")
    return DeliveryResponse(status="ok", message="Resources received and state updated.")
