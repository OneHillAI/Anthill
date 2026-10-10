.PHONY: install test lint format typecheck test-setup smoke certs serve-orchestrator alpha autostart app dmg meili demo clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -e ".[dev,docs]"
	@echo "Done. Activate with: source .venv/bin/activate"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check anthill tests

format:
	.venv/bin/ruff format anthill tests

typecheck:
	.venv/bin/mypy anthill

# End-to-end setup test: fresh state → org created → dashboard reachable.
test-setup:
	bash scripts/test-setup.sh

certs:
	bash scripts/gen-dev-certs.sh certs

# Start the web dashboard (alpha):
alpha:
	@echo "Open http://localhost:8000 in your browser."
	@echo "First run: you will see the setup screen."
	ANTHILL_LOCAL_ONLY=$${ANTHILL_LOCAL_ONLY-1} .venv/bin/anthill web --port 8000

# Install macOS auto-start (Ollama + dashboard on login; survives reboot):
autostart:
	bash scripts/install-autostart.sh

# Build the dev/fallback launcher (dist/Anthill-DevBuild.app) - double-click, no Terminal
# window, no Rust/Tauri toolchain needed. NOT the official release (no native window, no
# auto-update) - that ships from anthill.run/download. See scripts/build-app.sh's header.
app:
	bash scripts/build-app.sh

# Build the dev/fallback installer (dist/Anthill-DevBuild.dmg) - drag-to-Applications, same
# caveat as `make app` above:
dmg:
	bash scripts/build-dmg.sh

# Run Meilisearch locally (Docker) to test the opt-in self-hosted wiki retrieval.
# Then put MEILI_URL=http://localhost:7700 in .env and restart Anthill.
meili:
	@echo "Starting Meilisearch on http://localhost:7700  (Ctrl-C to stop)"
	@echo "Set MEILI_URL=http://localhost:7700 in .env, then restart Anthill."
	docker run --rm -p 7700:7700 getmeili/meilisearch:latest

# End-to-end smoke test (Ollama must be running):
#   make smoke
smoke:
	@rm -rf /tmp/anthill-smoke && \
	.venv/bin/anthill -w /tmp/anthill-smoke init && \
	.venv/bin/anthill -w /tmp/anthill-smoke info && \
	.venv/bin/anthill -w /tmp/anthill-smoke ingest demo-docs/architecture-decisions.md && \
	.venv/bin/anthill -w /tmp/anthill-smoke ask "which database did we pick for billing?" && \
	echo "--- second ask (cache hit expected) ---" && \
	.venv/bin/anthill -w /tmp/anthill-smoke ask "which database did we pick for billing?"

# Run the orchestrator locally (no Docker):
serve-orchestrator:
	ANTHILL_ORG_WIKI=/tmp/anthill-org-wiki .venv/bin/uvicorn anthill.orchestrator.app:app --host 0.0.0.0 --port 8080

# Local demo (no Docker - Ollama must be running):
demo:
	bash scripts/demo.sh

# Full docker-compose demo (requires Docker):
demo-docker:
	docker compose up --build

clean:
	rm -rf .venv *.egg-info anthill/**/__pycache__ anthill/__pycache__ .pytest_cache
