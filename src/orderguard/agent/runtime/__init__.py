"""Two interchangeable ways to drive an LLM against a set of connectors.
Both produce the same ``AgentTurnResult`` shape (see ``base.py``); the
orchestrator never branches on which one is active."""
