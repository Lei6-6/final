![Python](https://img.shields.io/badge/python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/framework-fastapi-green)
![Asyncio](https://img.shields.io/badge/concurrency-asyncio-purple)

**Autor(es):** Francisco Mollá Astrar, Jara Blasco Arroyo, Iremar Luhetsy Rivas Álvarez, Christian Iván Cola Pilicita, Lei Siting, Miguel Ángel Osuna Galindo

En el archivo **`ollama_service.py`** se realiza toda la gestión de mensajes con Ollama, así como la obtención de la respuesta.

En **`butler_service.py`** se conecta con el resto de agentes y se envían los mensajes, además de recibir los recursos y la información de Butler.

## INSTRUCCIONES DE LANZAMIENTO

Para ejecutar el programa, hay que preparar ollama:

```
# Solo la primera vez, para instalar lo necesario
./install_ollama.sh

# Para lanzar ollama una vez instalado
./run_ollama.sh
```
Una vez Ollama está instalado y corriendo, hay que lanzar el agente:

```
uv run main.py
```
Una vez terminado de ejecutar, hay que parar ollama.

```
./stop_ollama.sh
```
## Ejecución

En Windows, instalar Ollama manualmente y descargar el modelo:

```powershell
ollama pull mistral