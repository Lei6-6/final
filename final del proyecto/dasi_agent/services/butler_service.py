from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from dasi_agent.config import DISCOVERY_INTERVAL_SECONDS, URL_SERVER

_known_aliases: set[str] = set()


class ButlerError(RuntimeError):
    pass


async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _post_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()

        if not response.content:
            return True

        return response.json()


async def create_agent_and_connect(orchestrator, agent_name: str) -> None:
    """
    Registra el alias y descubre periódicamente otros agentes.
    """
    await get_or_create_alias(agent_name)
    logger.info(f"Agente {agent_name} registrado.")

    try:
        while True:
            users = await get_connected_users()
            logger.info(f"Usuarios conectados: {users}")

            for user in users:
                alias = user.get("alias")

                if not alias:
                    continue

                if alias == agent_name:
                    continue

                if alias in _known_aliases:
                    continue

                _known_aliases.add(alias)
                await orchestrator.add_worker_alias(alias)
                logger.info(f"Worker creado para negociar con {alias}")

            await asyncio.sleep(DISCOVERY_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        logger.info("Tarea de descubrimiento cancelada.")
        raise


async def get_connected_users() -> list[dict[str, Any]]:
    return await _get_json(f"{URL_SERVER}/gente")


async def create_alias(alias: str) -> dict[str, Any] | bool:
    result = await _post_json(f"{URL_SERVER}/alias/{alias}")
    logger.info(f"Alias '{alias}' creado correctamente.")
    return result


async def get_my_alias(alias: str) -> str | None:
    users = await get_connected_users()

    for user in users:
        if user.get("alias") == alias:
            return user["alias"]

    return None


async def get_my_ip_by_alias(alias: str) -> str | None:
    users = await get_connected_users()

    for user in users:
        if user.get("alias") == alias:
            return user.get("ip")

    return None


async def get_or_create_alias(alias: str) -> str:
    alias_stored = await get_my_alias(alias)

    if alias_stored:
        logger.info(f"Alias '{alias}' ya existe.")
        return alias_stored

    logger.info(f"Alias '{alias}' no existe. Creando alias.")
    await create_alias(alias)
    return alias


async def get_information() -> dict[str, Any]:
    data = await _get_json(f"{URL_SERVER}/info")
    logger.info("Información del agente obtenida correctamente.")
    return data


async def get_actual_resources_and_objectives() -> dict[str, Any]:
    data = await get_information()
    return process_resources_information(data)


def process_resources_information(butler_data: dict[str, Any]) -> dict[str, Any]:
    recursos = butler_data.get("Recursos", {})
    objetivo = butler_data.get("Objetivo", {})

    faltante = {
        recurso: max(cantidad_objetivo - recursos.get(recurso, 0), 0)
        for recurso, cantidad_objetivo in objetivo.items()
    }

    sobrante = {}

    for recurso, cantidad_actual in recursos.items():
        cantidad_objetivo = objetivo.get(recurso, 0)
        exceso = cantidad_actual - cantidad_objetivo

        if exceso > 0:
            sobrante[recurso] = exceso

    return {
        "actual": recursos,
        "objetivo": objetivo,
        "faltante": faltante,
        "sobrante": sobrante,
    }


async def get_alias_by_ip(ip: str) -> str | None:
    users = await get_connected_users()

    for user in users:
        if user.get("ip") == ip:
            return user.get("alias")

    return None


async def send_message(msg: str, ip: str) -> Any:
    route = f"http://{ip}:7720/buzon"
    logger.info(f"Enviando mensaje a {route}: {msg}")

    return await _post_json(route, {"msg": msg})


async def send_message_by_alias(msg: str, alias: str) -> Any:
    ip = await get_my_ip_by_alias(alias)

    if not ip:
        raise ButlerError(f"Alias '{alias}' no encontrado.")

    return await send_message(msg, ip)


async def send_package(to_alias: str, package: dict[str, int | float]) -> Any:
    logger.info(f"Enviando paquete a {to_alias}: {package}")

    return await _post_json(f"{URL_SERVER}/paquete/{to_alias}", package)