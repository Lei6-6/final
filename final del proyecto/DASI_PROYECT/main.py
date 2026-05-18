"""
main.py
-------
FastAPI entry point. Handles startup/shutdown, route definitions, and
high-level orchestration. No heavy business logic lives here.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from loguru import logger
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

import butler
import agents
from config import MY_PORT, AGENT_NAME, SERVER_URL
from models import IncomingMessage
from state_manager import state
from message_normalizer import normalize
from decision_engine import process_request, process_delivery
from events import emit, recent, stream as event_stream

_DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text()


# ---------------------------------------------------------------------------
# Background task: proactively request goal resources from all peers
# ---------------------------------------------------------------------------

async def _sync_from_butler_loop() -> None:
    """
    Every 20 seconds, fetch authoritative inventory from Butler and reconcile
    local state. Butler silently credits received resources without posting to
    /buzon, so this is the only way to detect incoming deliveries.
    """
    while True:
        await asyncio.sleep(20)
        info = await butler.get_agent_info()
        if not info:
            continue
        inventory = (
            info.get("Recursos") or info.get("recursos") or info.get("resources") or {}
        )
        before = state.snapshot()
        await state.sync_from_butler({k: int(v) for k, v in inventory.items() if isinstance(v, (int, float))})
        after = state.snapshot()

        # Emit to dashboard only when something actually changed
        if after["inventory"] != before["inventory"] or after["goal_needs"] != before["goal_needs"]:
            gained = {
                r: after["inventory"].get(r, 0) - before["inventory"].get(r, 0)
                for r in after["inventory"]
                if after["inventory"].get(r, 0) > before["inventory"].get(r, 0)
            }
            if gained:
                emit("delivery", from_="Butler", resources=gained)
                logger.info(f"[BUTLER SYNC] Received via Butler: {gained}")


async def _proactive_request_loop() -> None:
    await asyncio.sleep(15)

    while True:
        snap = state.snapshot()
        goal = snap["goal_needs"]

        if not goal:
            logger.info("All goals satisfied — stopping proactive requests.")
            return

        tradeable = {
            k: v for k, v in snap["inventory"].items()
            if k not in snap["target_resources"] and v > 0
        }

        request_msg = json.dumps({
            "kind": "request",
            "from_agent": AGENT_NAME,
            "resources": goal,
            "offered_resources": tradeable,
        })
        logger.info(
            f"[PROACTIVE] Requesting {list(goal.keys())} | "
            f"offering {list(tradeable.keys())}"
        )
        emit("proactive", resources=goal, offered=tradeable)
        await agents.broadcast_message(request_msg)
        await asyncio.sleep(45)


# ---------------------------------------------------------------------------
# Lifespan: startup registration and initial broadcast
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting agent '{AGENT_NAME}' on port {MY_PORT}")

    logger.info("Registering with Butler...")
    await butler.register_agent()

    info = await butler.get_agent_info()
    if info:
        inventory = (
            info.get("Recursos") or info.get("recursos") or info.get("resources") or {}
        )
        goal = (
            info.get("Objetivo") or info.get("objetivo") or info.get("goal") or {}
        )
        state.initialize(inventory, goal)
        logger.info(f"Agent info: inventory={inventory} | goal={goal}")
    else:
        logger.warning("Could not fetch agent info from Butler — starting with empty state.")

    async def _broadcast():
        active = await butler.get_active_agents()
        logger.info(f"Active agents: {active}")
        snap = state.snapshot()
        tradeable = {
            k: v for k, v in snap["inventory"].items()
            if k not in snap["target_resources"] and v > 0
        }
        inv_str = ", ".join(f"{k}({v})" for k, v in tradeable.items()) or "nada"
        goal_str = ", ".join(snap["goal_needs"].keys()) or "none"
        opening = (
            f"Hola! Soy {AGENT_NAME}. "
            f"Ofrezco: [{inv_str}]. "
            f"Necesito: [{goal_str}]."
        )
        await agents.broadcast_message(opening)

    asyncio.create_task(_broadcast())
    asyncio.create_task(_proactive_request_loop())
    asyncio.create_task(_sync_from_butler_loop())

    yield

    logger.warning("Agent shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(lifespan=lifespan)


@app.post("/buzon")
async def receive_message(message: IncomingMessage, request: Request):
    """Receive a message from another agent, normalize it, and process it."""
    sender_ip = request.client.host
    raw = message.msg
    logger.info(f"[INBOX] {sender_ip}: {raw[:120]}")
    emit("inbox", from_=sender_ip, text=raw[:120])

    normalized = await normalize(raw, from_agent=sender_ip)
    logger.info(
        f"[NORMALIZED] kind={normalized.kind} | resources={normalized.resources}"
    )

    if normalized.kind == "request":
        result = await process_request(normalized)
        logger.info(f"[DECISION] {result.decision} | resources={result.resources}")
        emit(
            result.decision,
            to=sender_ip,
            resources=result.resources,
            reason=result.reason,
        )
        return result.model_dump()

    if normalized.kind == "delivery":
        result = await process_delivery(normalized)
        logger.info(f"[DELIVERY] status={result.status}")
        emit("delivery", from_=sender_ip, resources=normalized.resources)
        return result.model_dump()

    if normalized.kind in ("accept", "reject"):
        logger.info(f"[ACK] '{normalized.kind}' received from {sender_ip}")
        return {"status": "ok", "message": f"Acknowledged: {normalized.kind}"}

    logger.warning(f"[UNKNOWN] Unrecognizable message from {sender_ip}")
    return {"status": "ok", "message": "Message received but could not be processed."}


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the real-time visual dashboard."""
    html = (
        _DASHBOARD_HTML
        .replace("__AGENT_NAME__", AGENT_NAME)
        .replace("__MY_PORT__", str(MY_PORT))
        .replace("__BUTLER_URL__", SERVER_URL)
    )
    return HTMLResponse(html)


@app.get("/api/state")
async def api_state():
    snap = state.snapshot()
    snap["target_resources"] = sorted(snap["target_resources"])
    return snap


@app.get("/api/agents")
async def api_agents():
    return await butler.get_active_agents() or []


@app.get("/api/events")
async def api_events():
    return recent()


@app.get("/api/stream")
async def api_stream():
    async def _generator():
        async for ev in event_stream():
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(_generator(), media_type="text/event-stream")


@app.post("/api/send")
async def api_send(payload: dict):
    ip = payload.get("ip", "").strip()
    message = payload.get("message", "").strip()
    if not ip or not message:
        return {"status": "error", "message": "Missing ip or message"}
    result = await agents.send_message_to_agent(ip, message)
    emit("send", to=ip, text=message[:100])
    return {"status": "ok", "result": result}


# Legacy debug endpoint kept for compatibility
@app.get("/state")
async def get_state():
    snap = state.snapshot()
    snap["target_resources"] = sorted(snap["target_resources"])
    return snap


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=MY_PORT, log_level="warning")
