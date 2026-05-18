# fdi-dasi-jackson

## Team

* Karina Vianey Oropeza Prado
* Paula Rodríguez Pérez
* Miguel Roberto Narváez Román
* Francys Patricio Paucarima Vizcaíno
* Zhaoyang Qi
* Jinyuan Wang

---

## What this project does

Implements an autonomous resource-exchange agent that participates in a multi-agent
trading game through a central **Butler** server. The agent:

- Registers with Butler on startup and loads its initial inventory and goal.
- Exposes a `/buzon` HTTP endpoint to receive messages from other agents.
- Accepts both **structured JSON** and **natural-language** messages and normalises
  them into a single internal format before any business logic runs.
- Processes `request` messages with rule-based filtering followed by LLM-assisted
  strategy selection (Ollama), falling back to rules if Ollama is unavailable.
- Processes `delivery` messages by updating its inventory and goal-completion status.
- Maintains concurrency-safe state through an async lock — inventory never goes negative
  and goal resources are automatically unblocked once their target is reached.

---

## Prerequisites

- [`uv`](https://github.com/astral-sh/uv) — package manager
- [`ollama`](https://ollama.com) — local LLM server

---

## Installation

```bash
# Install runtime + dev dependencies
uv sync --extra dev

# Pull the Ollama model (in a separate terminal)
ollama run mistral
```

---

## Configuration (`config.py`)

All values can be overridden with environment variables:

| Variable        | Default                        | Description                    |
|-----------------|--------------------------------|--------------------------------|
| `SERVER_URL`    | `http://172.16.82.138:7719/`   | Butler server base URL         |
| `AGENT_NAME`    | `Jackson`                      | Agent alias registered on Butler |
| `MY_PORT`       | `7720`                         | Port this agent listens on     |
| `OLLAMA_MODEL`  | `mistral`                      | Ollama model to use            |
| `OLLAMA_URL`    | `http://localhost:11434`       | Ollama server URL              |
| `OLLAMA_TIMEOUT`| `30.0`                         | Seconds before Ollama timeout  |
| `HTTP_TIMEOUT`  | `10.0`                         | Seconds for Butler/agent calls |
| `LOCAL_TEST_MODE` | `false`                      | Skip Butler registration       |

Example override:
```bash
AGENT_NAME=MyAgent MY_PORT=8000 uv run main.py
```

---

## Running

```bash
uv run main.py
```

---

## Running the tests

```bash
uv run pytest
```

---

## Architecture

```
main.py               FastAPI app, lifespan (registration + broadcast), route handlers
├── message_normalizer.py   JSON / NL → NormalizedMessage
├── decision_engine.py      Request & delivery business logic
│   ├── state_manager.py    Async-safe inventory / goal state (singleton)
│   ├── prompt_builder.py   All Ollama prompt templates
│   └── ollama_client.py    Async Ollama REST client
├── butler.py               Async Butler HTTP client
├── agents.py               Async agent-to-agent HTTP client
├── models.py               Pydantic models
└── config.py               Centralised configuration (env-var overridable)
```

### Message flow

```
POST /buzon
  │
  ▼
message_normalizer.normalize()
  │  JSON → parse directly
  │  NL   → rule-based → Ollama fallback → "unknown"
  ▼
NormalizedMessage { kind, resources, from_agent, ... }
  │
  ├── kind == "request"  → decision_engine.process_request()
  │     1. Snapshot state
  │     2. Split: forbidden (target / insufficient) vs exchangeable
  │     3. If nothing exchangeable → reject
  │     4. Call Ollama → validate strictly → fallback to rules on failure
  │     5. Deduct state atomically → send via Butler
  │     6. Return DecisionResponse
  │
  ├── kind == "delivery" → decision_engine.process_delivery()
  │     1. add_resources() — updates inventory + goal tracking
  │     2. Return DeliveryResponse
  │
  └── kind == "accept" | "reject" | "unknown" → acknowledge
```

### Ollama usage

The LLM is a **strategy advisor only**. It chooses among pre-filtered
`exchangeable` resources and returns one of `accept / offer / reject`.
Every field of its JSON response is re-validated by code before any state
is mutated. If validation fails, the rule-based fallback (accept all
exchangeable) takes over automatically.

---

## API endpoints

| Method | Path      | Description                                 |
|--------|-----------|---------------------------------------------|
| `POST` | `/buzon`  | Receive a message from another agent        |
| `GET`  | `/state`  | Debug: current inventory / goal snapshot    |

### `/buzon` payload

```json
{ "msg": "<JSON string or natural language>" }
```

### JSON message format (preferred)

```json
{
  "kind": "request" | "delivery" | "accept" | "reject",
  "resources": { "arroz": 2, "madera": 1 },
  "from_agent": "grupo7"
}
```

### Response for `request`

```json
{
  "decision": "accept" | "offer" | "reject",
  "resources": { "arroz": 2 },
  "reason": "brief explanation"
}
```

### Response for `delivery`

```json
{ "status": "ok", "message": "Resources received and state updated." }
```

---

## Local integration testing (two-agent setup)

1. Start **Agent A** on port 7720:
   ```bash
   MY_PORT=7720 AGENT_NAME=AgentA uv run main.py
   ```

2. Start **Agent B** on port 7721 in another terminal:
   ```bash
   MY_PORT=7721 AGENT_NAME=AgentB uv run main.py
   ```

3. Send a test request from B to A:
   ```bash
   curl -X POST http://localhost:7720/buzon \
     -H "Content-Type: application/json" \
     -d '{"msg": "{\"kind\":\"request\",\"resources\":{\"arroz\":1},\"from_agent\":\"AgentB\"}"}'
   ```

4. Inspect state:
   ```bash
   curl http://localhost:7720/state
   ```
