"""Direct, non-LLM-mediated MCP tool calls.

Used ONLY to execute an action a user has already approved, with the EXACT
arguments they approved — never to let a model decide what to call or with
what arguments. An approved R1 write must execute precisely as approved;
routing it back through an LLM runtime (``runtime.run_turn``) would let the
model reinterpret it, which defeats the point of "approved."

Verified directly against the installed ``mcp`` package (a transitive
dependency of ``claude_agent_sdk``, confirmed present) rather than assumed:
a real connection to ``mcp.swiggy.com/im`` with a real bearer token, a real
``tools/list``, and real ``get_addresses``/``get_cart`` calls all succeeded
2026-08-31. ``CallToolResult.structured_content`` is what carries reliable,
parseable JSON — the ``.content`` text blocks are a human-readable summary
("Found 3 saved addresses...") meant for an LLM to read, not for a program
to parse.
"""

from __future__ import annotations

from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

__all__ = ["DirectMcpCallError", "call_tool_directly"]


class DirectMcpCallError(RuntimeError):
    """A direct MCP call failed to connect, authenticate, or execute."""


def _root_cause(exc: BaseException) -> BaseException:
    """Unwraps anyio/asyncio TaskGroup ``ExceptionGroup``s down to the actual
    failure. Without this, every transport error surfaces as the useless
    "unhandled errors in a TaskGroup (1 sub-exception)" instead of the real
    connection/auth/validation error underneath."""
    seen: list[BaseException] = []
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        if exc in seen:
            break
        seen.append(exc)
        exc = exc.exceptions[0]
    return exc


async def call_tool_directly(
    *, url: str, bearer_token: str, tool_name: str, arguments: dict[str, Any],
) -> dict[str, Any] | None:
    """Calls one MCP tool directly and returns its ``structured_content``.

    Returns ``None`` if the server gave no structured content at all — the
    caller must treat that as "nothing reliable to parse," never guess from
    the human-readable text instead.
    """
    headers = {"Authorization": f"Bearer {bearer_token}"}
    http_client = create_mcp_http_client(headers=headers)
    try:
        async with http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    if result.is_error:
                        text = "; ".join(
                            getattr(b, "text", "") for b in result.content if getattr(b, "text", None)
                        )
                        raise DirectMcpCallError(f"{tool_name} returned an error: {text or result.content}")
                    return result.structured_content
    except DirectMcpCallError:
        raise
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any transport/auth failure fails closed the same way
        cause = _root_cause(exc)
        if isinstance(cause, DirectMcpCallError):
            # anyio wrapped an already-clear DirectMcpCallError (e.g. a real
            # tool-level error from the server) in a TaskGroup ExceptionGroup
            # during structured-concurrency cleanup — surface it unchanged
            # instead of re-wrapping "X failed: DirectMcpCallError: X failed".
            raise cause from exc
        raise DirectMcpCallError(
            f"direct MCP call to {tool_name!r} at {url!r} failed: "
            f"{type(cause).__name__}: {cause}"
        ) from exc
