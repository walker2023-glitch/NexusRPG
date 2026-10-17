.PHONY: setup dev clean logs shell-api pull-models help

# Default target
.DEFAULT_GOAL := help

# ── First-run: pull all required Ollama models ───────────────────────────────
setup:
	@echo ">>> Starting Ollama and pulling mistral:7b, nomic-embed-text, llava:7b..."
	docker compose up -d nexus-llm
	docker compose --profile setup up nexus-llm-init
	@echo ">>> Model setup complete. Run 'make dev' to start the full stack."

# ── Start the full stack (rebuild images on change) ──────────────────────────
dev:
	docker compose up --build

# ── Start in detached mode ────────────────────────────────────────────────────
up:
	docker compose up --build -d

# ── Tear down containers and delete named volumes ─────────────────────────────
clean:
	docker compose down -v
	@echo ">>> Containers stopped and volumes removed."

# ── Stop containers without removing volumes ──────────────────────────────────
stop:
	docker compose down

# ── Tail API logs ─────────────────────────────────────────────────────────────
logs:
	docker compose logs -f nexus-api

# ── Tail all service logs ─────────────────────────────────────────────────────
logs-all:
	docker compose logs -f

# ── Open a bash shell in the running API container ───────────────────────────
shell-api:
	docker exec -it nexus-api bash

# ── Open a bash shell in the frontend container ──────────────────────────────
shell-frontend:
	docker exec -it nexus-frontend sh

# ── Pull/refresh Ollama models without rebuilding everything ──────────────────
pull-models:
	docker compose --profile setup up nexus-llm-init

# ── Run backend tests (requires the API container to be running) ──────────────
test:
	docker exec -it nexus-api python -m pytest backend/tests/ -v

# ── Run tests locally (requires a local Python venv with deps installed) ──────
test-local:
	python -m pytest backend/tests/ -v

# ── Print this help message ───────────────────────────────────────────────────
help:
	@echo ""
	@echo "  Nexus RPG Engine — make targets"
	@echo ""
	@echo "  setup        Pull Ollama models (run once before first 'make dev')"
	@echo "  dev          Build and start all services (foreground)"
	@echo "  up           Build and start all services (background)"
	@echo "  stop         Stop containers (keep volumes)"
	@echo "  clean        Stop containers and delete named volumes"
	@echo "  logs         Tail nexus-api logs"
	@echo "  logs-all     Tail all service logs"
	@echo "  shell-api    Open bash in the running nexus-api container"
	@echo "  pull-models  Re-pull/update Ollama models"
	@echo "  test         Run pytest inside the nexus-api container"
	@echo "  test-local   Run pytest against a local Python environment"
	@echo ""
