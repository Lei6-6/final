from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from loguru import logger

from dasi_agent.config import AGENT_HOST, AGENT_NAME, AGENT_PORT
from dasi_agent.schemas.agent_message import AgentMessage, SendMessage
from dasi_agent.services.butler_service import (
    create_agent_and_connect,
    get_alias_by_ip,
    send_message_by_alias,
)
from dasi_agent.services.ollama_service import Orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Inicializando agente {AGENT_NAME}...")
    app.state.orch = Orchestrator()

    discovery_task = asyncio.create_task(
        create_agent_and_connect(app.state.orch, AGENT_NAME),
        name="agent-discovery",
    )

    try:
        yield
    finally:
        logger.info(f"Apagando agente {AGENT_NAME}...")
        discovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await discovery_task
        await app.state.orch.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"agent": AGENT_NAME, "status": "running"}


@app.post("/buzon")
async def receive_message(agent_message: AgentMessage, request: Request):
    try:
        client_host = request.client.host if request.client else "unknown"
        alias = await get_alias_by_ip(client_host) or client_host
        msg = agent_message.msg

        logger.info(f">> {alias} ({client_host}) dice: {msg}")

        await app.state.orch.add_worker_alias(alias)
        await app.state.orch.save_message(alias, msg)

        return {"ok": True}
    except Exception as exc:
        logger.exception(f"Error al recibir mensaje: {exc}")
        raise HTTPException(status_code=500, detail="Error al recibir mensaje") from exc


@app.post("/send-message")
async def send_message(data: SendMessage):
    try:
        response = await send_message_by_alias(data.message, data.alias)
        return response
    except Exception as exc:
        logger.exception(f"Error al enviar mensaje: {exc}")
        raise HTTPException(status_code=500, detail="Error al enviar mensaje") from exc


def run() -> None:
    uvicorn.run("dasi_agent.main:app", host=AGENT_HOST, port=AGENT_PORT, reload=False)


if __name__ == "__main__":
    run()