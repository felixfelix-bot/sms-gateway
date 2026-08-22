"""Pydantic models for requests, responses, and internal data structures."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class MessageStatus(str, Enum):
    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"
    unknown = "unknown"


class ProviderName(str, Enum):
    telnyx = "telnyx"
    msg91 = "msg91"
    fast2sms = "fast2sms"


# ── Internal data structures ───────────────────────────────────────────

class BatchMessage(BaseModel):
    to: str
    body: str


# ── API Request Models ─────────────────────────────────────────────────

class SendRequest(BaseModel):
    to: str = Field(..., description="Destination phone number in E.164 format")
    body: str = Field(..., min_length=1, description="SMS message body")
    provider: Optional[ProviderName] = Field(
        None, description="Override auto-routing with a specific provider"
    )


class BatchRequest(BaseModel):
    messages: list[BatchMessage] = Field(..., min_length=1, max_length=1000)


# ── API Response Models ────────────────────────────────────────────────

class SendResult(BaseModel):
    message_id: str
    provider: ProviderName
    status: MessageStatus
    cost: Optional[float] = None
    error: Optional[str] = None
    to: str = ""
    body: str = ""


class BatchResult(BaseModel):
    total: int
    sent: int
    failed: int
    results: list[SendResult]


class DeliveryStatus(BaseModel):
    message_id: str
    status: MessageStatus
    delivered_at: Optional[datetime] = None
    error: Optional[str] = None


class BalanceInfo(BaseModel):
    provider: ProviderName
    balance: Optional[float] = None
    currency: str = "USD"
    error: Optional[str] = None


class HealthStatus(BaseModel):
    provider: ProviderName
    healthy: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None