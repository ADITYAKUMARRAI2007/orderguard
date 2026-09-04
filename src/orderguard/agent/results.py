"""Universal result envelope for the agent orchestrator.

Every connector call — commerce or otherwise — is normalized into a
``ConnectorResult`` before anything downstream (Decision Council, the UI, the
audit chain) ever sees it. ``payload`` is a Pydantic discriminated union, so a
normalizer bug becomes a validation error at parse time instead of a
``KeyError`` three frames later in a renderer.

Only ``CommerceResult`` (Swiggy, Shopify) and ``DevTaskResult`` (GitHub) are
wired to a live connector in this build. The rest — Calendar/Email/Task/File —
are real, typed extension points, not stubs pretending to be finished: no
connector in the registry produces them yet, because none of Gmail/Calendar/
Notion/Slack/etc. has a working backend connection today (see
``connector_registry.py``'s compatibility matrix for exactly why, per
service). Building a full pipeline for a connector with zero live access
would be the kind of fabricated completeness this project refuses elsewhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from ..commerce.search import ScoredOffer

__all__ = [
    "CommerceResult", "DevTaskResult", "CalendarResult", "EmailResult",
    "TaskResult", "FileResult", "CommunicationResult", "AutomationResult",
    "PaymentEvidenceResult", "UnsupportedResult",
    "ConnectorResultPayload", "ConnectorResult",
]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommerceResult(_Payload):
    result_type: Literal["commerce_candidates"] = "commerce_candidates"
    merchant: str
    offers: list[ScoredOffer]
    # The real delivery address these offers were actually found for, when
    # the connector's search is address-scoped (Swiggy Instamart is: its
    # own tool description requires calling get_addresses first, and the
    # spinIds it returns belong to the dark store serving THAT address).
    # Carried so approval can write to the same address the search used,
    # instead of offering every saved address as if all were equally valid
    # — see F-036/F-048 for what that costs. ``None`` for connectors with
    # no address concept (Shopify), which changes nothing for them.
    address_id: str | None = None


class DevTaskResult(_Payload):
    result_type: Literal["dev_task"] = "dev_task"
    source: str
    items: list[dict]


class CalendarResult(_Payload):
    result_type: Literal["calendar"] = "calendar"
    events: list[dict]


class EmailResult(_Payload):
    result_type: Literal["email"] = "email"
    messages: list[dict]


class TaskResult(_Payload):
    result_type: Literal["task"] = "task"
    tasks: list[dict]


class FileResult(_Payload):
    result_type: Literal["file"] = "file"
    files: list[dict]


class CommunicationResult(_Payload):
    result_type: Literal["communication"] = "communication"
    messages: list[dict]


class AutomationResult(_Payload):
    result_type: Literal["automation"] = "automation"
    runs: list[dict]


class PaymentEvidenceResult(_Payload):
    result_type: Literal["payment_evidence"] = "payment_evidence"
    evidence: list[dict]


class UnsupportedResult(_Payload):
    result_type: Literal["unsupported"] = "unsupported"
    reason: str


ConnectorResultPayload = Annotated[
    Union[
        CommerceResult, DevTaskResult, CalendarResult,
        EmailResult, TaskResult, FileResult, CommunicationResult,
        AutomationResult, PaymentEvidenceResult, UnsupportedResult,
    ],
    Field(discriminator="result_type"),
]


class ConnectorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: str
    capability: str
    operation: str
    risk_tier: Literal["R0", "R1", "R2", "R3"]
    execution_id: str
    observed_at: datetime
    provenance: str
    payload: ConnectorResultPayload
