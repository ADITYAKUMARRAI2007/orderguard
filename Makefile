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
