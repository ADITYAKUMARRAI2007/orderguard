"""Which runtime is active, and BYOK Anthropic API key handling.

Three distinct things, named distinctly on purpose (a review flagged that
collapsing them was itself a gap):

    SERVER_MANAGED_API_KEY  -- ANTHROPIC_API_KEY from .env, this process's own key
    BYOK_SESSION_API_KEY    -- pasted by a user of this running instance
    SUBSCRIPTION_RUNTIME    -- CLAUDE_CODE_OAUTH_TOKEN, Agent SDK

A BYOK key lives in process memory only: never written to disk, never
logged, never echoed back beyond a masked ``sk-ant-...1234`` confirmation.
This matches the project's existing security posture (no secret in `.env.example`
beyond a name) applied to a runtime setting instead of a startup config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

RuntimeMode = Literal["server_managed_api_key", "byok_session_api_key", "subscription_runtime"]
AgentRuntimeChoice = Literal["api", "subscription"]

__all__ = ["RuntimeSettings", "mask_key"]


def mask_key(key: str) -> str:
    if len(key) <= 4:
        return "*" * len(key)
    return f"{key[:6]}...{key[-4:]}"


@dataclass
class RuntimeSettings:
    """One instance per process — this is a LOCAL_SINGLE_USER setting, the
    same scope as the rest of this build (see connector_accounts.py)."""

    _byok_key: str | None = None
    # None means "use the AGENT_RUNTIME env var" (app.py's original default);
    # set explicitly once a user picks a runtime from the Connectors screen
    # so switching doesn't require an .env edit and a restart.
    _agent_runtime_override: AgentRuntimeChoice | None = None

    def set_byok_key(self, key: str) -> None:
        self._byok_key = key or None

    def forget_byok_key(self) -> None:
        self._byok_key = None

    def set_agent_runtime(self, choice: AgentRuntimeChoice) -> None:
        self._agent_runtime_override = choice

    def agent_runtime_choice(self) -> AgentRuntimeChoice:
        if self._agent_runtime_override is not None:
            return self._agent_runtime_override
        return "subscription" if os.getenv("AGENT_RUNTIME", "").strip().lower() == "subscription" else "api"

    def active_api_key(self) -> tuple[str | None, RuntimeMode | None]:
        if self._byok_key:
            return self._byok_key, "byok_session_api_key"
        env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if env_key:
            return env_key, "server_managed_api_key"
        return None, None

    def status(self) -> dict:
        api_key, mode = self.active_api_key()
        oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        return {
            "server_managed_api_key": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
            "byok_session_api_key": bool(self._byok_key),
            "byok_masked": mask_key(self._byok_key) if self._byok_key else None,
            "subscription_runtime": bool(oauth_token),
            "active_api_mode": mode,
            "active_agent_runtime": self.agent_runtime_choice(),
        }
