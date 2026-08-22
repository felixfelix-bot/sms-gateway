"""Fast2SMS provider adapter — India domestic routing via the Fast2SMS Dev API."""
import logging
import time
from datetime import datetime

import httpx

from sms_gateway.base import SMSGateway
from sms_gateway.config import Fast2SMSConfig
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


class Fast2SMSProvider(SMSGateway):
    """SMS gateway using Fast2SMS (https://www.fast2sms.com).

    API reference: https://docs.fast2sms.com
    - Send: POST /dev/bulkV2 (quick 'q' route or DLT 'dlt' route)
    - Delivery reports: GET /dev/dlr/{REQUEST_ID}
    - Wallet balance: POST /dev/wallet
    """

    provider_name = "fast2sms"

    # Fast2SMS status strings -> gateway MessageStatus
    DELIVERY_STATUS_MAP = {
        "delivered": MessageStatus.delivered,
        "in process": MessageStatus.queued,
        "queued": MessageStatus.queued,
        "pending": MessageStatus.queued,
        "sent": MessageStatus.queued,
        "failed": MessageStatus.failed,
        "rejected": MessageStatus.failed,
        "blocked": MessageStatus.failed,
    }

    def __init__(self, config: Fast2SMSConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client
        self._rate_limiter = TokenBucket(
            capacity=max(1.0, config.rate_limit),
            refill_rate=config.rate_limit,
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, creating it lazily."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(30.0),
                headers={
                    "authorization": self.config.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    def _normalize_number(self, to: str) -> str:
        """Fast2SMS expects bare 10-digit Indian mobile numbers."""
        mobile = to.lstrip("+")
        if len(mobile) == 12 and mobile.startswith("91"):
            mobile = mobile[2:]
        return mobile

    async def send_sms(self, to: str, body: str) -> SendResult:
        """Send a single SMS via POST /dev/bulkV2."""
        if not await self._rate_limiter.acquire():
            logger.warning("Fast2SMS rate limit exceeded")
            return SendResult(
                message_id="",
                provider=ProviderName.fast2sms,
                status=MessageStatus.failed,
                error="Rate limit exceeded",
                to=to,
                body=body,
            )

        mobile = self._normalize_number(to)
        payload: dict = {
            "route": self.config.route,
            "numbers": mobile,
        }
        if self.config.route == "dlt":
            # DLT route: `message` carries the DLT-approved template ID,
            # `variables_values` carries the pipe-separated variable content.
            payload["sender_id"] = self.config.sender_id
            payload["message"] = self.config.dlt_template_id
            payload["variables_values"] = body
        else:
            # Quick transactional route: full message text, no sender/template.
            payload["message"] = body

        client = self._get_client()
        try:
            resp = await client.post("/dev/bulkV2", json=payload)
            resp.raise_for_status()
            data = resp.json()
            # Success: {"return": true, "request_id": "...", "message": [...]}
            # Failure: {"return": false, "status_code": 416, "message": "..."}
            if data.get("return") in (True, "true", "True"):
                message_id = data.get("request_id") or f"fast2sms_{int(time.time())}"
                logger.info("Fast2SMS sent: %s -> %s", to, message_id)
                return SendResult(
                    message_id=message_id,
                    provider=ProviderName.fast2sms,
                    status=MessageStatus.sent,
                    to=to,
                    body=body,
                )
            msg = data.get("message", "Unknown Fast2SMS error")
            if isinstance(msg, list):
                msg = "; ".join(str(m) for m in msg)
            status_code = data.get("status_code")
            error = f"{status_code}: {msg}" if status_code else str(msg)
            logger.error("Fast2SMS send failed: %s", error)
            return SendResult(
                message_id="",
                provider=ProviderName.fast2sms,
                status=MessageStatus.failed,
                error=error,
                to=to,
                body=body,
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Fast2SMS HTTP error %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            return SendResult(
                message_id="",
                provider=ProviderName.fast2sms,
                status=MessageStatus.failed,
                error=f"HTTP {exc.response.status_code}: {exc.response.text}",
                to=to,
                body=body,
            )
        except Exception as exc:
            logger.error("Fast2SMS send exception: %s", exc)
            return SendResult(
                message_id="",
                provider=ProviderName.fast2sms,
                status=MessageStatus.failed,
                error=str(exc),
                to=to,
                body=body,
            )

    async def send_batch(self, messages: list[BatchMessage]) -> BatchResult:
        """Send a batch of SMS messages sequentially."""
        results: list[SendResult] = []
        for msg in messages:
            result = await self.send_sms(msg.to, msg.body)
            results.append(result)
        sent = sum(1 for r in results if r.status == MessageStatus.sent)
        return BatchResult(
            total=len(results),
            sent=sent,
            failed=len(results) - sent,
            results=results,
        )

    async def check_delivery(self, message_id: str) -> DeliveryStatus:
        """Check delivery status via GET /dev/dlr/{REQUEST_ID}."""
        client = self._get_client()
        try:
            resp = await client.get(f"/dev/dlr/{message_id}")
            resp.raise_for_status()
            data = resp.json()
            # Response may be a list of per-number entries or a single object.
            if isinstance(data, list):
                data = data[0] if data else {}
            raw_status = str(data.get("status", "unknown"))
            status = self.DELIVERY_STATUS_MAP.get(
                raw_status.lower().strip(), MessageStatus.unknown
            )
            delivered_at = None
            raw_dt = data.get("delivered_date") or data.get("delivered_at")
            if raw_dt:
                try:
                    delivered_at = datetime.strptime(str(raw_dt), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    logger.debug("Unparseable delivered_date from Fast2SMS: %s", raw_dt)
            return DeliveryStatus(
                message_id=message_id,
                status=status,
                delivered_at=delivered_at,
            )
        except Exception as exc:
            logger.error("Fast2SMS delivery check exception: %s", exc)
            return DeliveryStatus(
                message_id=message_id,
                status=MessageStatus.unknown,
                error=str(exc),
            )

    async def get_balance(self) -> BalanceInfo:
        """Get wallet balance via POST /dev/wallet."""
        client = self._get_client()
        try:
            resp = await client.post("/dev/wallet")
            resp.raise_for_status()
            data = resp.json()
            # {"return": "true", "wallet": "5566.0100", "sms_count": 27830}
            balance = None
            wallet = data.get("wallet")
            if wallet is not None:
                balance = float(wallet)
            return BalanceInfo(
                provider=ProviderName.fast2sms,
                balance=balance,
                currency="INR",
            )
        except Exception as exc:
            logger.error("Fast2SMS balance check exception: %s", exc)
            return BalanceInfo(
                provider=ProviderName.fast2sms,
                balance=None,
                error=str(exc),
            )

    async def health_check(self) -> HealthStatus:
        """Check Fast2SMS API health by pinging the wallet endpoint."""
        start = time.monotonic()
        try:
            client = self._get_client()
            resp = await client.post("/dev/wallet")
            resp.raise_for_status()
            latency_ms = (time.monotonic() - start) * 1000
            return HealthStatus(
                provider=ProviderName.fast2sms,
                healthy=True,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            logger.error("Fast2SMS health check failed: %s", exc)
            return HealthStatus(
                provider=ProviderName.fast2sms,
                healthy=False,
                error=str(exc),
            )
