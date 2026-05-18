from typing import Literal
from pydantic import BaseModel


class IncomingMessage(BaseModel):
    """Raw payload received on /buzon."""
    msg: str


class NormalizedMessage(BaseModel):
    """
    Unified internal representation of any incoming message.

    Every message — JSON or natural language — is converted to this
    format before any business logic runs.
    """
    from_agent: str
    kind: Literal["request", "delivery", "accept", "reject", "unknown"]
    resources: dict[str, int] = {}         # what they WANT from us
    offered_resources: dict[str, int] = {} # what they're willing to GIVE us
    raw_text: str
    metadata: dict = {}


class DecisionResponse(BaseModel):
    """Response returned after processing a resource request."""
    decision: Literal["accept", "offer", "reject"]
    resources: dict[str, int]
    reason: str


class DeliveryResponse(BaseModel):
    """Confirmation returned after processing a delivery."""
    status: Literal["ok", "error"]
    message: str
