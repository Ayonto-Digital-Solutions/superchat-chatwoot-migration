# Simple commands for the SuperChat -> Chatwoot migration tool.
# Usage: make build | make extract | make chatwoot | make chatwoot-attach

COMPOSE = docker compose run --rm app

.PHONY: help build extract chatwoot chatwoot-attach attachments reassign all state clean reset

help:
	@echo "Available commands:"
	@echo "  make build            - build the Docker image"
	@echo "  make extract          - Phase 1: pull everything from SuperChat -> raw/"
	@echo "  make chatwoot         - Phase 2: 1:1 import into Chatwoot (real data)"
	@echo "  make chatwoot-attach  - like chatwoot, including file attachments"
	@echo "  make attachments      - collect all attachments into out/attachments/"
	@echo "  make reassign         - re-assign agents for already-imported conversations"
	@echo "  make all              - extract + chatwoot in sequence"
	@echo "  make state            - show state.json"
	@echo "  make clean            - remove containers + state files"
	@echo "  make reset            - DANGER: delete raw/, out/ and state"

build:
	docker compose build

extract:
	$(COMPOSE) python -m src.extract

chatwoot:
	$(COMPOSE) python -m src.to_chatwoot

chatwoot-attach:
	$(COMPOSE) python -m src.to_chatwoot --with-attachments

attachments:
	$(COMPOSE) python -m src.collect_attachments

reassign:
	$(COMPOSE) python -m src.to_chatwoot --reassign

all: extract chatwoot

state:
	@cat state.json 2>/dev/null || echo "No state.json yet - run 'make extract' first."

# Removes containers + leftover state files.
clean:
	-docker compose down
	-rm -rf state.json chatwoot_state.json
	@echo "Cleaned up."

# Full reset of downloaded + generated data (keeps .env and code).
reset:
	-docker compose down
	-rm -rf raw out state.json chatwoot_state.json
	@echo "Deleted raw/, out/ and state."
