"""Detect what's already connected in the user's own Claude Code environment
by asking the CLI itself — `claude mcp list` — rather than reading its local
credential store directly. This is read-only status detection, not
credential extraction: it never touches `~/.claude.json` or any token file,
and it never returns a token, only a name/URL/connected-or-not.

Why this exists: the Connectors screen may report what Claude Code can see,
while keeping that evidence clearly separate from OrderGuard authentication.
These sessions never make a connector eligible and their credentials are
never read or reused; an owner-scoped ConnectorAccount is still required.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

__all__ = ["DetectedConnector", "detect_claude_code_connectors"]

_LINE = re.compile(
    r"^(?P<name>[^:]+):\s+(?P<url>\S+)(?:\s+\((?P<transport>\w+)\))?\s+-\s+"
    r"(?P<symbol>[✔✘!])\s*(?P<status_text>.*)$"
)
_TIMEOUT_SECONDS = 15


@dataclass
class DetectedConnector:
    name: str
    url: str
    connected: bool
    status_text: str
    # True for entries added via `claude mcp add` on this machine
    # (e.g. "swiggy-instamart"); False for ones surfaced through claude.ai's
    # own consumer connector directory (prefixed "claude.ai " by the CLI) —
    # a different account-level integration this backend cannot assume is
    # reachable the same way, matching connectors.py's own
    # CLAUDE_DIRECTORY_ONLY distinction.
    cli_managed: bool


def detect_claude_code_connectors() -> tuple[list[DetectedConnector], str]:
    """Returns ``(connectors, error)``. Never raises: if the `claude` CLI
    isn't on PATH, times out, or its output doesn't parse, that's reported
    as an empty list plus a plain-text reason — never silently treated as
    "nothing connected" without saying why.
    """
    # Deliberately drop every inference-auth variable for this call. Detection
    # asks "what is connected on THIS machine", which the CLI answers from
    # the user's own logged-in keychain session — but the CLI itself prints:
    # "claude.ai connectors are disabled because ANTHROPIC_API_KEY or another
    # auth source is set and takes precedence over your claude.ai login."
    # A CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL
    # present in the parent process's environment (this backend's own, e.g.
    # from a BYOK session or a stray shell export) is enough to silently
    # blank out every account-level `claude.ai …` connector from the
    # listing, leaving only locally `claude mcp add`-ed servers. That failure
    # looks exactly like "the connectors were never added", so all three are
    # excluded here rather than debugged again later.
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    }

    try:
        result = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, env=env,
        )
    except FileNotFoundError:
        return [], "the `claude` CLI is not on this server's PATH"
    except subprocess.TimeoutExpired:
        return [], "`claude mcp list` timed out"

    if result.returncode != 0:
        return [], f"`claude mcp list` exited {result.returncode}: {result.stderr.strip()[:300]}"

    connectors: list[DetectedConnector] = []
    for line in result.stdout.splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        name = match.group("name").strip()
        connectors.append(DetectedConnector(
            name=name,
            url=match.group("url"),
            connected=match.group("symbol") == "✔",
            status_text=match.group("status_text").strip() or match.group("symbol"),
            cli_managed=not name.startswith("claude.ai "),
        ))
    return connectors, ""
