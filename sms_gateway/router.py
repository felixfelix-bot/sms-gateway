"""Auto-routing logic — selects provider based on destination number or config."""
import logging

from sms_gateway.base import SMSGateway
from sms_gateway.config import GatewayConfig
from sms_gateway.models import MessageStatus, ProviderName, SendResult
from sms_gateway.providers.fast2sms import Fast2SMSProvider
from sms_gateway.providers.msg91 import MSG91Provider
from sms_gateway.providers.telnyx import TelnyxProvider

logger = logging.getLogger(__name__)


class SMSRouter:
    """Routes SMS messages to the appropriate provider."""

    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig()
        self.telnyx = TelnyxProvider(self.config.telnyx)
        self.msg91 = MSG91Provider(self.config.msg91)
        self.fast2sms = Fast2SMSProvider(self.config.fast2sms)
        self._providers: dict[str, SMSGateway] = {
            "telnyx": self.telnyx,
            "msg91": self.msg91,
            "fast2sms": self.fast2sms,
        }

    @property
    def routing_mode(self) -> str:
        return self.config.routing_mode

    def route(self, to: str) -> SMSGateway:
        """Determine which provider to use for a destination number."""
        mode = self.routing_mode

        if mode == "telnyx":
            return self.telnyx
        if mode == "msg91":
            return self.msg91
        if mode == "fast2sms":
            return self.fast2sms
        if mode == "fallback":
            primary = self.config.fallback_primary
            return self._providers.get(primary, self.telnyx)

        # Default: auto routing
        return self._auto_route(to)

    def _auto_route(self, to: str) -> SMSGateway:
        """+91 → MSG91, everything else → Telnyx."""
        normalized = to.lstrip("+").lstrip(" ")
        if normalized.startswith("91"):
            return self.msg91
        return self.telnyx

    async def send(self, to: str, body: str, provider_override: ProviderName | None = None) -> SendResult:
        """Send SMS with automatic or explicit routing, including fallback."""
        if provider_override:
            provider = self._providers.get(provider_override.value)
            if provider is None:
                return SendResult(
                    message_id="",
                    provider=provider_override,
                    status=MessageStatus.failed,
                    error=f"Unknown provider: {provider_override}",
                    to=to,
                    body=body,
                )
            return await provider.send_sms(to, body)

        if self.routing_mode == "fallback":
            return await self._send_with_fallback(to, body)

        provider = self.route(to)
        return await provider.send_sms(to, body)

    async def _send_with_fallback(self, to: str, body: str) -> SendResult:
        """Try primary provider; if it fails, try the secondary."""
        primary_name = self.config.fallback_primary
        secondary_name = {
            "telnyx": "msg91",
            "msg91": "telnyx",
            "fast2sms": "msg91",
        }.get(primary_name, "msg91")

        primary = self._providers[primary_name]
        result = await primary.send_sms(to, body)

        if result.status == MessageStatus.sent:
            return result

        # Primary failed — try secondary
        logger.warning(
            "Primary provider %s failed (%s), falling back to %s",
            primary_name,
            result.error,
            secondary_name,
        )
        secondary = self._providers[secondary_name]
        fallback_result = await secondary.send_sms(to, body)
        if fallback_result.status == MessageStatus.sent:
            return fallback_result

        # Both failed — return the primary error
        return SendResult(
            message_id="",
            provider=ProviderName[primary_name],
            status=MessageStatus.failed,
            error=f"Primary: {result.error}; Fallback: {fallback_result.error}",
            to=to,
            body=body,
        )