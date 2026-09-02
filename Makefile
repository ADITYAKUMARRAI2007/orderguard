.PHONY: test test-offline lint eval feature-matrix test-report dev

# Full test suite.
test:
	uv run pytest -v

# The rule that matters: everything must pass with NO api key.
test-offline:
	ANTHROPIC_API_KEY= LLM_API_KEY= uv run pytest -v

lint:
	uv run python -c "import orderguard; print('imports ok')"

# Run the demo shop.
shop:
	uv run uvicorn demo_store.app:app --reload --port 8002

# Run the guarded-cart API. It can search live allowlisted stores only when a
# user calls its search endpoint; tests never touch the network.
app:
	uv run uvicorn orderguard.app:app --reload --port 8000 --env-file .env

# One-command local launch: backend (:8000, auto-reload) and the real React
# frontend (:5173, Vite) together — Ctrl-C stops both. This is what a fresh
# clone should run; there is no more server-rendered /app route to open.
dev:
	@if [ ! -d frontend/node_modules ]; then cd frontend && npm install; fi
	@echo "Backend:  http://127.0.0.1:8000"
	@echo "Frontend: http://127.0.0.1:5173  <- open this"
	@trap 'kill 0' EXIT INT TERM; \
	(uv run uvicorn orderguard.app:app --reload --port 8000 --env-file .env) & \
	(cd frontend && npm run dev) & \
	wait

# The whole product in one run, against real stores. Nothing stubbed.
# No login, no payment; carts are anonymous and abandoned on exit.
demo:
	@set -a && . ./.env && set +a && uv run python scripts/demo.py

# Fifty adversarial purchase journeys through the real guard. Writes
# docs/BENCHMARK.md. Exits non-zero if the false-match rate is ever nonzero.
benchmark:
	uv run python scripts/benchmark.py

# Every evidence artifact this project makes, in one reproducible run: the
# fixed fifty, the injection curve, the Hostile Attack Lab, and the three
# baselines. No model calls, no network. Writes docs/BENCHMARK.md AND
# results/latest.json — the UI and README read the JSON so a number can
# never drift from what this actually measured.
eval:
	uv run --offline --no-sync python scripts/eval.py

# The full shipped-feature inventory, written from one list so
# docs/FEATURE_MATRIX.md and results/feature_matrix.json cannot drift apart.
feature-matrix:
	uv run --offline --no-sync python scripts/feature_matrix.py

# Runs the real backend suite and writes results/test_report.json — the
# checks-run count the UI shows is this file's numbers, never typed by hand.
test-report:
	uv run python scripts/test_report.py
