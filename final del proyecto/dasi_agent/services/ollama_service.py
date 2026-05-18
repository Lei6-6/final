from ollama import chat
from .butler_service import get_actual_resources_and_objectives, get_connected_users, send_message
from loguru import logger
from .butler_service import get_my_ip_by_alias, send_package
import asyncio
import requests
import json

MAX_MSGS = 15
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "enviar_respuesta",
            "description": "Envía una propuesta, respuesta o contraoferta al agente indicado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "Alias del agente destinatario.",
                    },
                    "mensaje": {
                        "type": "string",
                        "description": "Mensaje de negociación.",
                    },
                },
                "required": ["alias", "mensaje"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_package",
            "description": "Envía recursos cuando ya existe un acuerdo explícito.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "Alias del agente destinatario.",
                    },
                    "package": {
                        "type": "object",
                        "description": "Recursos a enviar.",
                        "additionalProperties": {"type": "number"},
                    },
                },
                "required": ["alias", "package"],
            },
        },
    },
]
# System promt, indica reglas y uso de tools


def get_system_prompt_by_alias(alias):
    return """Eres un agente negociador. Tu única forma de comunicarte es mediante JSON.
    Vas a negociar con el agente con el alias """ + alias + """ para conseguir los recursos que te faltan de tu objetivo.
    Reemplaza <alias_agente> por el alias del agente con el que estás negociando, <tu mensaje> por el mensaje que quieres enviarle, <nombre_tool> por el nombre de la herramienta que quieres usar y <parámetros de la tool> por los parámetros necesarios para usar la herramienta.
    FORMATO OBLIGATORIO — responde SIEMPRE y ÚNICAMENTE con este JSON, sin texto adicional:
    {"msg": "<mensaje para el agente>", "list_tools": ["<nombre_tool>"], "dict_parameters": {<parámetros de la tool>}}

    === TOOLS DISPONIBLES ===

    TOOL 1 — enviar_respuesta (úsala para cualquier mensaje o respuesta):
    {"msg": "tu mensaje", "list_tools": ["enviar_respuesta"], "dict_parameters": {"alias": "<alias_agente>", "mensaje": "<tu mensaje>"}}

    TOOL 2 — send_package (úsala SOLO cuando el otro agente diga "trato hecho", "aceptado", "vale", "de acuerdo" o equivalente):
    {"msg": "tu mensaje", "list_tools": ["send_package"], "dict_parameters": {"alias": "<alias_agente>", "package": {"<recurso>": <cantidad>}}}

    === EJEMPLOS ===

    Ejemplo 1 — saludo inicial:
    {"msg": "Hola! Tengo madera sobrante, ¿te interesa intercambiarla por hierro?", "list_tools": ["enviar_respuesta"], "dict_parameters": {"alias": <alias_agente>, "mensaje": "Hola! Tengo madera sobrante, ¿te interesa intercambiarla por hierro?"}}

    Ejemplo 2 — acuerdo alcanzado, enviar recursos:
    {"msg": "Trato hecho, te envío la madera.", "list_tools": ["send_package"], "dict_parameters": {"alias":<alias_agente>, "package": {"madera": 2}}}

    === REGLAS DE NEGOCIACIÓN ===
    1. Primera vez: saluda y propón un intercambio concreto
    2. Intercambios solo 1 a 1
    3. Negocia con recursos sobrantes Y con los recursos en "actual"
    4. Prioriza conseguir recursos faltantes del objetivo
    5. No reveles todos tus recursos innecesariamente
    6. Acepta intercambios favorables rápidamente
    7. El oro puede usarse en tratos pero no es un objetivo a conseguir
    === RESTRICCIONES ABSOLUTAS ===
    - NUNCA respondas con texto fuera del JSON
    - NUNCA uses list_tools vacío
    - SIEMPRE incluye "msg", "list_tools" y "dict_parameters"
    - El campo "msg" debe contener el mismo texto que el parámetro "mensaje"
    - NUNCA uses send_package sin confirmación explícita de acuerdo por ambas partes
    """
# tool para enviar una respuesta
async def enviar_respuesta(alias: str, msg: str):
    # Enviamos la respueta
    logger.debug(f'enviando respuesta a {alias}')
    # Obtenemos la ip a partir del alias
    ip = get_my_ip_by_alias(alias)
    if ip:
        await send_message(msg, ip)
    return f"Respuesta enviada a {alias}"

class Orchestrator:
    def __init__(self):
        self.conversaciones = {}
        self.locks = {}
        self.queue = asyncio.Queue()
        self.global_lock = asyncio.Lock()
        # buzón para que lea y escriba
        self.BUZON = {} 
    
    # sirve para guardar un mensaje en el buzón
    async def save_message(self, alias: str, msg: str):
        self.BUZON.setdefault(alias, []).append(msg)
        logger.debug(f"[{alias}] save_message{self.BUZON}")

    # tool para leer mensajes pendientes
    async def leer_buzon(self, alias: str, buzon):
        mensajes = {}
        mensajes[alias] = buzon.get(alias, []).copy()
        buzon[alias] = []
        logger.debug(f"[{alias}] leer buzon {buzon}")
        return mensajes[alias]

    # permite obtener el lock asociado al alias para el worker
    async def get_lock(self, alias):
        async with self.global_lock:
            if alias not in self.locks:
                self.locks[alias] = asyncio.Lock()
            return self.locks[alias]

    async def respuesta(self, alias, msg):
        # espera para procesar por orden cuando se accede al mismo alias
        lock = await self.get_lock(alias)

        async with lock:
            system_content = generate_system_propmt(alias)

            if alias not in self.conversaciones:
                # realizamos copia no lo pasamos por referencia
                self.conversaciones[alias] = [{'role': 'system', 'content': system_content}]
            # Es para actualizar el system prompt de un alias que se va a enviar a Ollama    
            list_act = self.conversaciones[alias]
            list_act.append({'role': 'user', 'content': msg})
            # logger.debug(f"[ollama] >> {list_act}")
            logger.debug(f"ollama insertando system promt")

            # nos quedamos solo con los últimos MAX_MSGS mensajes
            list_act = list_act[-MAX_MSGS:]
            # reintroducimos el system promt en la primera posición
            list_act[0]['role'] = 'system'
            list_act[0]['content'] = system_content
            logger.debug(f"[{alias}] >> {list_act}")

            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                    chat,
                    #'qwen3-vl:8b',
                    'mistral', 
                    messages= list_act,
                    format='json'
                    ),
                    timeout=180
                )
            except asyncio.TimeoutError:
                logger.error("Timeout en Ollama")
                return "Vuelve a preguntar, no he sido capaz de responder"

            mensaje = json.loads(response.message.content)

            if 'content' in mensaje:
                content = mensaje['content']
                if type(content) == str:
                    mensaje = json.loads(content)
                else:
                    mensaje = content

            logger.debug(f'respuesta: {mensaje}')
            
            for call in mensaje["list_tools"]:

                if call == "enviar_respuesta":
                    params = mensaje["dict_parameters"]
                    values = list(dict.values(params))
                    logger.debug(f"[enviar_respuesta] >> Enviando estos parametros: {values}")
                    result = await enviar_respuesta(values[0], mensaje['msg'])

                if call == "send_package":
                    params = mensaje["dict_parameters"]
                    values = list(dict.values(params))
                    logger.debug(f"[send_package] >> Enviando estos parametros: {values}")
                    result = await send_package(values[0], values[1])

                list_act.append({
                    "role": "tool",
                    "name": call,
                    "content": str(result)
                })

            # Se introduce en la conversación la respuesta
            list_act.append({'role': 'assistant', 'content': mensaje["msg"]})
            logger.debug(f"[ollama] >> respuesta recibida")
            self.conversaciones[alias] = list_act

            return mensaje["msg"]

    async def worker(self, alias, buzon):
        # Siempre envía un mensaje al iniciar
        try:
            resultado = await self.respuesta(alias, "Inicia una negociacion")
            logger.debug(f"[{alias}] -> {resultado}")
        except Exception as e:
            logger.error(f"Error en {alias}: {e}")

        logger.debug(f"[{alias}] Iniciando worker con buzon: {buzon}")
        while True:
            # Procesa los mensajes si lo hay
            mensajes = await self.leer_buzon(alias, buzon)
            if mensajes: 
                for mensaje in mensajes:
                    try:
                        mensaje = 'Haz una oferta para avanzar con la negociación utilizando este mensaje como contexto: ' + mensaje
                        resultado = await self.respuesta(alias, mensaje)
                        logger.debug(f"[{alias}] -> {resultado}")
                    except Exception as e:
                        logger.error(f"Error en {alias}: {e}")
            else: # Envía mensajes en caso de no tener mensajes pendientes
                try:
                    resultado = await self.respuesta(alias, "Avanza con la negociación utilizando los mensajes anteriores como contexto, si no tienes nada más que decir responde 'no tengo nada más que decir'")
                    logger.debug(f"[{alias}] -> {resultado}")
                except Exception as e:
                    logger.error(f"Error en {alias}: {e}")

    async def start_agents(self):
        info = get_connected_users()
        for user in info:
            asyncio.create_task(self.worker(user["alias"]))

    async def add_worker_alias(self, alias):
        logger.debug(f"Enviando buzon: {self.BUZON}")
        asyncio.create_task(self.worker(alias, self.BUZON))

    async def send(self, alias, mensaje):
        # Obtiene o crea el bucle de eventos
        loop = asyncio.get_running_loop()
        # Es una promesa
        future = loop.create_future()

        await self.queue.put({
            "alias": alias,
            "mensaje": mensaje,
            "future": future
        })

        return await future

#Se utiliza para generar el system prompt
def generate_system_propmt(alias):
    data = get_actual_resources_and_objectives()
    system_prompt = get_system_prompt_by_alias(alias)
    format_string = f"""{system_prompt}
                    Estos son los recursos que tienes para negociar:
                    {data}"""
    return format_string
# print(respuesta("a", "hablame de aviones"))


