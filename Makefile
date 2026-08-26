.PHONY: test test-offline lint

# Full test suite.
test:
	uv run pytest -v

# The rule that matters: everything must pass with NO api key.
test-offline:
	ANTHROPIC_API_KEY= LLM_API_KEY= uv run pytest -v

lint:
	uv run python -c "import orderguard; print('imports ok')"
