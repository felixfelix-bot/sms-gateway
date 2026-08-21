"""MSG91 SMS provider adapter — India domestic routing via MSG91 REST API."""
import logging
import time

import httpx

from sms_gateway.base import SMSGateway
from sms_gateway.config import MSG91Config
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


class MSG91Provider(SMSGateway):
    provider_name = "msg91"

    def __init__(self, config: MSG91Config, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client
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
                    "authkey": self.config.auth_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def send_sms(self, to: str, body: str) -> SendResult:
        if not await self._rate_limiter.acquire():
            return SendResult(
                message_id="",
                provider=ProviderName.msg91,
                status=MessageStatus.failed,
                error="Rate limit exceeded",
                to=to,
                body=body,
            )

        # MSG91 send SMS payload (flow-based for DLT compliance)
        # Strip leading + from phone for MSG91 (expects raw digits)
        mobile = to.lstrip("+")
        payload = {
            "sender": self.config.sender_id,
            "route": "4",  # transactional route
            "country": "91",
            "sms": [
                {
                    "message": body,
                    "to": [mobile],
                }
            ],
            "DLT_TELEMARKETER_ID": self.config.dlt_template_id,
        }
        client = self._get_client()
        try:
            resp = await client.post("/sms", json=payload)
            resp.raise_for_status()
            data = resp.json()
            # MSG91 returns {"type": "success", "message": "..."} or {"type":"error",...}
            if data.get("type") == "success":
                # MSG91 doesn't always return a message_id; generate one
                message_id = data.get("message", "") or f"msg91_{int(time.time())}"
                return SendResult(
                    message_id=message_id,
                    provider=ProviderName.msg91,
                    status=MessageStatus.sent,
                    to=to,
                    body=body,
                )
            else:
                return SendResult(
                    message_id="",
                    provider=ProviderName.msg91,
                    status=MessageStatus.failed,
                    error=data.get("message", "Unknown MSG91 error"),
                    to=to,
                    body=body,
                )
        except httpx.HTTPStatusError as exc:
            logger.error("MSG91 send failed: %s", exc.response.text)
            return SendResult(
                message_id="",
                provider=ProviderName.msg91,
                status=MessageStatus.failed,
                error=f"HTTP {exc.response.status_code}: {exc.response.text}",
                to=to,
                body=body,
            )
        except Exception as exc:
            logger.error("MSG91 send error: %s", exc)
            return SendResult(
                message_id="",
                provider=ProviderName.msg91,
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
        # MSG91 delivery status endpoint
        client = self._get_client()
        try:
            resp = await client.get(f"/sms/campaign/status/{message_id}")
            resp.raise_for_status()
            data = resp.json()
            raw_status = data.get("status", "unknown")
            status_map = {
                "pending": MessageStatus.queued,
                "success": MessageStatus.delivered,
                "delivered": MessageStatus.delivered,
                "failed": MessageStatus.failed,
                "rejected": MessageStatus.failed,
            }
            status = status_map.get(raw_status, MessageStatus.unknown)
            return DeliveryStatus(message_id=message_id, status=status)
        except Exception as exc:
            return DeliveryStatus(
                message_id=message_id,
                status=MessageStatus.unknown,
                error=str(exc),
            )

    async def get_balance(self) -> BalanceInfo:
        client = self._get_client()
        try:
            resp = await client.get("/balance.php", params={"type": "SMS"})
            resp.raise_for_status()
            # MSG91 balance response format varies; handle both
            data = resp.json()
            balance = None
            if isinstance(data, dict):
                balance = float(data.get("balance", 0))
            elif isinstance(data, (int, float)):
                balance = float(data)
            elif isinstance(data, str):
                balance = float(data)
            return BalanceInfo(
                provider=ProviderName.msg91,
                balance=balance,
                currency="INR",
            )
        except Exception as exc:
            return BalanceInfo(
                provider=ProviderName.msg91,
                error=str(exc),
            )

    async def health_check(self) -> HealthStatus:
        client = self._get_client()
        start = time.monotonic()
        try:
            resp = await client.get("/balance.php", params={"type": "SMS"})
            resp.raise_for_status()
            latency = (time.monotonic() - start) * 1000
            return HealthStatus(
                provider=ProviderName.msg91,
                healthy=True,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            return HealthStatus(
                provider=ProviderName.msg91,
                healthy=False,
                error=str(exc),
            )