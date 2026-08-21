"""Telnyx SMS provider adapter — international routing via Telnyx REST API."""
import logging
import time

import httpx

from sms_gateway.base import SMSGateway
from sms_gateway.config import TelnyxConfig
from sms_gateway.models import (
    BalanceInfo,
    BatchMessage,
    BatchResult,
    DeliveryStatus,
    HealthStatus,
    MessageStatus,
    ProviderName,
    SendResult,
)
from sms_gateway.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)


class TelnyxProvider(SMSGateway):
    provider_name = "telnyx"

    def __init__(self, config: TelnyxConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client  # injected for tests
        self._rate_limiter = TokenBucket(
            capacity=max(1.0, config.rate_limit),
            refill_rate=config.rate_limit,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(30.0),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def send_sms(self, to: str, body: str) -> SendResult:
        if not await self._rate_limiter.acquire():
            return SendResult(
                message_id="",
                provider=ProviderName.telnyx,
                status=MessageStatus.failed,
                error="Rate limit exceeded",
                to=to,
                body=body,
            )

        payload = {
            "from": self.config.from_number,
            "to": to,
            "text": body,
            "messaging_profile_id": self.config.messaging_profile_id,
        }
        client = self._get_client()
        try:
            resp = await client.post("/messages", json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg_data = data.get("data", {})
            return SendResult(
                message_id=msg_data.get("id", ""),
                provider=ProviderName.telnyx,
                status=MessageStatus.sent,
                cost=None,
                to=to,
                body=body,
            )
        except httpx.HTTPStatusError as exc:
            logger.error("Telnyx send failed: %s", exc.response.text)
            return SendResult(
                message_id="",
                provider=ProviderName.telnyx,
                status=MessageStatus.failed,
                error=f"HTTP {exc.response.status_code}: {exc.response.text}",
                to=to,
                body=body,
            )
        except Exception as exc:
            logger.error("Telnyx send error: %s", exc)
            return SendResult(
                message_id="",
                provider=ProviderName.telnyx,
                status=MessageStatus.failed,
                error=str(exc),
                to=to,
                body=body,
            )

    async def send_batch(self, messages: list[BatchMessage]) -> BatchResult:
        results: list[SendResult] = []
        for msg in messages:
            result = await self.send_sms(msg.to, msg.body)
            results.append(result)
        sent = sum(1 for r in results if r.status == MessageStatus.sent)
        failed = len(results) - sent
        return BatchResult(total=len(results), sent=sent, failed=failed, results=results)

    async def check_delivery(self, message_id: str) -> DeliveryStatus:
        client = self._get_client()
        try:
            resp = await client.get(f"/messages/{message_id}")
            resp.raise_for_status()
            data = resp.json().get("data", {})
            # Telnyx status mapping
            raw_status = data.get("status", "unknown")
            status_map = {
                "queued": MessageStatus.queued,
                "sent": MessageStatus.sent,
                "delivered": MessageStatus.delivered,
                "delivery_failed": MessageStatus.failed,
                "failed": MessageStatus.failed,
            }
            status = status_map.get(raw_status, MessageStatus.unknown)
            delivered_at = None
            if status == MessageStatus.delivered and data.get("delivered_at"):
                delivered_at = data["delivered_at"]
            return DeliveryStatus(
                message_id=message_id,
                status=status,
                delivered_at=delivered_at,
            )
        except httpx.HTTPStatusError as exc:
            return DeliveryStatus(
                message_id=message_id,
                status=MessageStatus.unknown,
                error=f"HTTP {exc.response.status_code}",
            )
        except Exception as exc:
            return DeliveryStatus(
                message_id=message_id,
                status=MessageStatus.unknown,
                error=str(exc),
            )

    async def get_balance(self) -> BalanceInfo:
        # Telnyx doesn't have a simple balance endpoint; we report based on
        # whether the API key is valid via a lightweight call.
        client = self._get_client()
        try:
            resp = await client.get("/messaging_profiles", params={"page[size]": 1})
            resp.raise_for_status()
            return BalanceInfo(
                provider=ProviderName.telnyx,
                balance=None,
                currency="USD",
                error="Balance not available via API; profiles accessible.",
            )
        except Exception as exc:
            return BalanceInfo(
                provider=ProviderName.telnyx,
                error=str(exc),
            )

    async def health_check(self) -> HealthStatus:
        client = self._get_client()
        start = time.monotonic()
        try:
            resp = await client.get("/messaging_profiles", params={"page[size]": 1})
            resp.raise_for_status()
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(
                provider=ProviderName.telnyx,
                healthy=True,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            return HealthStatus(
                provider=ProviderName.telnyx,
                healthy=False,
                error=str(exc),
            )