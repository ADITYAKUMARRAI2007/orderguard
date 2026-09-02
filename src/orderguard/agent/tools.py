"""Shared, runtime-agnostic tool/connector invocation contract.

An earlier draft of this plan claimed the Anthropic API runtime and the
Claude subscription (Agent SDK) runtime could share one MCP config shape.
That was checked directly against both runtimes' current docs and found
false: the Agent SDK's ``mcp_servers`` is a dict keyed by server name with
``mcp__<server>__<tool>`` tool names; the Messages API's MCP Connector is a
flat list plus a separate ``mcp_toolset`` block. ``ConnectorInvocationSpec``
is the one runtime-agnostic description both adapters translate from; they
never translate from each other.

The R3 (financial) exclusion lives here, once, because both adapters call
``allowed_tool_names()`` to build their tool lists — so there is exactly one
place in the codebase capable of admitting a financial tool into either
runtime, and it is the one place that always refuses. Not a Python
``assert``: asserts compile out under ``-O``, and this boundary must hold
even then.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RiskTier = Literal["R0", "R1", "R2", "R3"]
MutationClassification = Literal[
    "READ", "REVERSIBLE_WRITE", "EXTERNAL_COMMITMENT", "FINANCIAL"
]

__all__ = [
    "RiskTier", "MutationClassification", "ToolPermission",
    "ConnectorInvocationSpec", "FinancialToolExposureError",
    "NonReadToolExposureError", "CliManagedConnectorUnsupported",
    "allowed_tool_names",
]


class FinancialToolExposureError(RuntimeError):
    """Raised whenever an R3 (financial) tool would otherwise reach an LLM
    runtime's tool list. Callers must catch this, emit
    ``R3_TOOL_EXPOSURE_BLOCKED`` to the audit chain, and fail the request
    closed — never retry with the tool silently dropped and never proceed
    as if nothing happened.
    """

    def __init__(self, connector_id: str, tool_name: str):
        self.connector_id = connector_id
        self.tool_name = tool_name
        super().__init__(
            f"refused to expose R3 (financial) tool {tool_name!r} on "
            f"connector {connector_id!r} to an agent runtime"
        )


class NonReadToolExposureError(RuntimeError):
    """Raised while the runtime exposure stage is intentionally R0-only.

    R1/R2 tools remain catalogued so the control plane can display and govern
    them, but they cannot enter either LLM runtime until ActionProposal
    execution is wired end-to-end. This is a fail-closed staging boundary,
    not a silent filter.
    """

    def __init__(self, connector_id: str, tool_name: str, risk_tier: RiskTier):
        self.connector_id = connector_id
        self.tool_name = tool_name
        self.risk_tier = risk_tier
        super().__init__(
            f"refused to expose {risk_tier} mutation tool {tool_name!r} on "
            f"connector {connector_id!r}; agent runtime exposure is R0-only"
        )


class CliManagedConnectorUnsupported(RuntimeError):
    """Claude Code's private connector session is never an OrderGuard
    credential source. Connector auth must come from the owner-scoped,
    encrypted ConnectorAccount store for both runtimes."""


@dataclass(frozen=True)
class ToolPermission:
    name: str
    risk_tier: RiskTier
    mutation_classification: MutationClassification = "READ"


@dataclass(frozen=True)
class ConnectorInvocationSpec:
    """Runtime-agnostic. ``tools`` carries risk tiers, not bare strings, so
    the R3 check below has something to check — a prior draft carried only
    ``allowed_tool_names: list[str]`` and had no way to know which of those
    names was safe to offer.

    ``cli_managed`` remains as a legacy input solely so both adapters can
    reject it explicitly. OrderGuard never reads, extracts, or inherits a
    Claude Code connector credential; both runtimes receive connector auth
    only from an owner-scoped ``ConnectorAccount``.
    """

    connector_id: str
    url: str
    tools: tuple[ToolPermission, ...]
    bearer_token: str | None = None
    cli_managed: bool = False
    # Runtime MCP server names must be unique. A canonical connector such as
    # Shopify can therefore appear once per store while retaining one
    # connector_id for policy/provenance decisions.
    server_name: str | None = None
    resource_ref: str | None = None


def allowed_tool_names(spec: ConnectorInvocationSpec) -> tuple[str, ...]:
    """The one function both runtime adapters call to build their tool
    allow-list. Raises before either adapter can construct a wire-format
    tool list containing an R3 tool.
    """
    for tool in spec.tools:
        if tool.risk_tier == "R3":
            raise FinancialToolExposureError(spec.connector_id, tool.name)
        if tool.risk_tier != "R0" or tool.mutation_classification != "READ":
            raise NonReadToolExposureError(
                spec.connector_id, tool.name, tool.risk_tier
            )
    return tuple(t.name for t in spec.tools)
