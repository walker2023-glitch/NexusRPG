# Nexus RPG Engine

Local-first, **CPU-only** AI RPG engine for homelab hardware. Build a knowledge graph in **Architect** mode, then play interactive stories in **Actor** mode—with branching timelines (**Chronos**), rule validation (**Axiom**), and semantic search (**LanceDB**). No cloud APIs, no GPU required.

**Repository:** [github.com/walker2023-glitch/NexusRPG](https://github.com/walker2023-glitch/NexusRPG)

---

## Features

| Mode | What it does |
|------|----------------|
| **Architect** | Extract entities and relationships from text or scraped URLs into `world_state.json` |
| **Actor** | Generate narrative scenes with Freytag-stage pacing and three structured choices (A/B/C) |
| **Chronos** | Branching story history via ZSTD-compressed snapshots and JSON Patch deltas |
| **Axiom** | Deterministic world-rule validation (health caps, immutable status, location-bound items) |
| **Import** | Background jobs for `.md`/`.txt` lore and image vision (LLaVA) indexing |

**Stack:** Next.js 14 · FastAPI · Ollama · LanceDB · docker-compose

**Models (via Ollama):** `mistral:7b`, `nomic-embed-text`, `llava:7b`

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2
- Enough disk/RAM for three GGUF models (CPU inference; no CUDA)
- **Make** (optional; commands below include plain `docker compose` equivalents)

---

## Quick start

```bash
git clone https://github.com/walker2023-glitch/NexusRPG.git
cd NexusRPG

cp .env.example .env
# Edit .env if needed (defaults work for local Docker)

make setup    # start Ollama + pull models (first time only)
make dev      # build and run frontend, API, and LLM
```

**URLs**

| Service | URL |
|---------|-----|
| Frontend (Map / Play / Timeline) | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Ollama | http://localhost:11434 |

Without Make:

```bash
docker compose up -d nexus-llm
docker compose --profile setup up nexus-llm-init
docker compose up --build
```

---

## Project layout

```
NexusRPG/
├── backend/          # FastAPI: routes, Chronos, Axiom, RAG, import pipeline
├── frontend/         # Next.js App Router: /map, /play, /timeline
├── data/             # world_state.json, axiom_rules.json (runtime LanceDB/Chronos in volumes)
├── docker-compose.yml
├── Makefile
└── test_smoke.sh     # curl-based smoke test (API must be running)
```

---

## Makefile targets

| Target | Description |
|--------|-------------|
| `make setup` | Pull Ollama models (`mistral:7b`, `nomic-embed-text`, `llava:7b`) |
| `make dev` | `docker compose up --build` |
| `make up` | Same, detached |
| `make stop` | Stop containers (keep volumes) |
| `make clean` | Stop and remove named volumes |
| `make logs` | Follow `nexus-api` logs |
| `make shell-api` | Bash inside API container |
| `make test` | Run pytest inside API container |
| `make help` | List targets |

---

## Environment variables

Copy `.env.example` → `.env`. Never commit `.env`.

| Variable | Purpose |
|----------|---------|
| `OLLAMA_URL` | Ollama base URL (in Docker: `http://nexus-llm:11434`) |
| `WORLD_STATE_PATH` | Path to graph JSON |
| `LANCEDB_PATH` | Embedded vector DB directory |
| `CHRONOS_PATH` | Commit graph storage |
| `AXIOM_RULES_PATH` | JSON rule definitions |
| `NEXT_PUBLIC_API_URL` | Browser-facing API URL (e.g. `http://localhost:8000`) |

---

## API overview

| Prefix | Endpoints |
|--------|-----------|
| `/api` | `world-state`, `nodes/{id}`, `active-location`, `health` |
| `/architect` | `extract`, `scrape` |
| `/actor` | `scene`, `choose` |
| `/chronos` | `commit`, `timeline`, `fork`, `checkout`, `prune`, … |
| `/import` | `image`, `text`, `status/{job_id}`, `stream/{job_id}` (SSE) |

---

## Tests

```bash
# Inside Docker (after stack is up)
make test

# Local (with venv + deps installed)
make test-local
```

Chronos engine tests live in `backend/tests/test_chronos.py`.

Smoke test (bash, API on localhost:8000):

```bash
./test_smoke.sh
```

---

## Design constraints

- **No** hosted LLM/vector DB services—Ollama + LanceDB only  
- **No** GPU/CUDA code paths  
- Node IDs use typed prefixes: `char_`, `loc_`, `item_`, `org_`, `event_`, `concept_`  
- LanceDB queries filter by `location_key` (active scene or `global`)

See `.currsorrules` for full coding conventions.

---

## Troubleshooting

| Issue | Hint |
|-------|------|
| `nexus-llm` unhealthy | Ollama can be slow on CPU; wait for healthcheck / increase `start_period` in compose |
| Frontend can’t reach API | Set `NEXT_PUBLIC_API_URL` to a URL your **browser** can use (not `nexus-api`) |
| `ModuleNotFoundError: backend` | Rebuild API image; Dockerfile uses `PYTHONPATH=/app` and `COPY . ./backend/` |
| Missing models | Run `make setup` or `docker compose --profile setup up nexus-llm-init` |

---

## License

No license file is included yet. Add one if you plan to accept contributions or redistribute.

---

## Contributing

Issues and pull requests welcome on GitHub. Please avoid committing secrets or local `data/lancedb` / `data/chronos` runtime files.
