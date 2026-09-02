"""Deterministic connector eligibility — the one gate the LLM (via either
runtime) must pass through before it can call anything. It only ever picks
from what ``eligible_for`` returns; it never sees the full registry or a raw
connector URL.

``merchants.resolve_merchant()`` stays scoped to the commerce branch of
normalization, exactly as it already runs today in ``app.py`` — this engine
is what generalizes eligibility across categories without turning that
merchant-specific function into a universal router it was never built for
(a mistake an earlier draft of this plan made and a review caught).
"""

from __future__ import annotations

from ..connectors import ConnectorBackendType
from .connector_accounts import ConnectorAccountStore
from .connector_registry import REGISTRY, RegisteredConnector

__all__ = ["ConnectorEligibilityEngine"]

# Backend types this backend can actually call directly. Deliberately NOT an
# evidence-tier check: gating on evidence == "verified" would make an
# AVAILABLE_UNTESTED connector like GitHub permanently unreachable, since the
# only way it ever becomes verified is by being reached once. Evidence is a
# reporting/labeling concern (see connectors.py); reachability is this.
_REACHABLE_BACKENDS = {ConnectorBackendType.REMOTE_MCP, ConnectorBackendType.NATIVE_API_ADAPTER}
_NEVER_ELIGIBLE_EVIDENCE = {"restricted", "unavailable"}
_RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


class ConnectorEligibilityEngine:
    def __init__(self, accounts: ConnectorAccountStore):
        self._accounts = accounts

    def eligible_for(
        self,
        category: str,
        cli_connected_ids: frozenset[str] = frozenset(),
        *,
        runtime_name: str | None = None,
        owner_ref: str | None = None,
        region: str | None = None,
        allowed_connector_ids: frozenset[str] | None = None,
        max_risk_tier: str = "R0",
    ) -> list[RegisteredConnector]:
        """Category match + a backend type this process can actually call +
        not explicitly ruled out by policy (RESTRICTED/UNAVAILABLE evidence)
        + authenticated through this backend's owner-scoped
        ``ConnectorAccountStore``. ``cli_connected_ids`` is accepted only as
        a backwards-compatible, deliberately ignored argument: Claude Code's
        private connector session is not an OrderGuard credential source.
        Nothing here
        consults risk tier — that's applied per-tool when a
        ``ConnectorInvocationSpec`` is built, in ``orchestrator.py``, via
        ``connector_registry.tools_within_ceiling``.
        """
        out = []
        if owner_ref is not None and owner_ref != self._accounts.owner_ref:
            return out
        ceiling = _RISK_ORDER[max_risk_tier]
        for connector in REGISTRY:
            capabilities = connector.capabilities or (connector.category,)
            if category not in capabilities:
                continue
            if not connector.available or connector.health not in {"HEALTHY", "DEGRADED"}:
                continue
            if connector.backend_type not in _REACHABLE_BACKENDS:
                continue
            if connector.evidence.value in _NEVER_ELIGIBLE_EVIDENCE:
                continue
            if runtime_name is not None and runtime_name not in connector.runtime_compatibility:
                continue
            if region is not None and not (
                "GLOBAL" in connector.regions or region in connector.regions
            ):
                continue
            if allowed_connector_ids is not None and connector.id not in allowed_connector_ids:
                continue
            if not any(
                _RISK_ORDER[tool.risk_tier] <= ceiling
                and tool.risk_tier == "R0"
                and tool.mutation_classification == "READ"
                for tool in connector.tools
            ):
                continue
            if connector.auth == "connector_account":
                if not self._accounts.is_connected(connector.id):
                    continue
            out.append(connector)
        return out
