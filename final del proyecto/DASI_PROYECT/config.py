import os

# Butler server
SERVER_URL: str = os.getenv("SERVER_URL", "http://147.96.80.104:7719/")

# Agent identity
AGENT_NAME: str = os.getenv("AGENT_NAME", "FCFC123456")
MY_PORT: int = int(os.getenv("MY_PORT", "7720"))

# Ollama
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "30.0"))

# General HTTP timeout for agent-to-agent calls (seconds)
HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "10.0"))

# Shorter timeout used only for Butler startup calls (register, info, peers)
BUTLER_TIMEOUT: float = float(os.getenv("BUTLER_TIMEOUT", "5.0"))

# Set to true to skip Butler registration and use mock data
LOCAL_TEST_MODE: bool = os.getenv("LOCAL_TEST_MODE", "false").lower() == "true"
