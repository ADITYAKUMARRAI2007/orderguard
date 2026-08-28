.PHONY: test test-offline lint

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

# The whole product in one run, against real stores. Nothing stubbed.
# No login, no payment; carts are anonymous and abandoned on exit.
demo:
	@set -a && . ./.env && set +a && uv run python scripts/demo.py
