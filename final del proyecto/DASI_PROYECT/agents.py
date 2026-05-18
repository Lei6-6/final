import httpx
from loguru import logger

import butler
from config import AGENT_NAME, MY_PORT, HTTP_TIMEOUT


async def send_message_to_agent(agent_ip: str, message: str) -> dict | None:
    """Sends a message directly to another agent's /buzon endpoint."""
    url = f"http://{agent_ip}:{MY_PORT}/buzon"
    payload = {"msg": message}
    logger.debug(f"Sending to {url}: {message[:80]}")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.success(f"Message sent to {agent_ip}")
            return response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as error:
            logger.error(f"Failed to send message to {agent_ip}: {error}")
            return None


async def broadcast_message(message: str) -> None:
    """Sends a message to all active agents except self."""
    agents = await butler.get_active_agents()
    if not agents:
        logger.warning("No active agents found for broadcasting.")
        return

    for agent in agents:
        alias = agent.get("alias", "")
        ip = agent.get("ip")
        if alias != AGENT_NAME and ip and ip != "127.0.0.1":
            logger.info(f"Broadcasting to '{alias}' ({ip})")
            await send_message_to_agent(ip, message)
