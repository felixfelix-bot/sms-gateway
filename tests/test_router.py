"""Tests for the auto-routing logic."""
import os

import pytest

# Set env vars before importing
os.environ.setdefault("TELNYX_API_KEY", "test-key")
os.environ.setdefault("TELNYX_MESSAGING_PROFILE_ID", "test-profile")
os.environ.setdefault("TELNYX_FROM_NUMBER", "+15551234567")
os.environ.setdefault("MSG91_AUTH_KEY", "test-auth")
os.environ.setdefault("MSG91_SENDER_ID", "TEST")
os.environ.setdefault("MSG91_DLT_TEMPLATE_ID", "test-dlt")

from sms_gateway.config import GatewayConfig, MSG91Config, TelnyxConfig
from sms_gateway.models import ProviderName
from sms_gateway.providers.msg91 import MSG91Provider
from sms_gateway.providers.telnyx import TelnyxProvider
from sms_gateway.router import SMSRouter


class TestAutoRouting:
    """Test automatic provider selection based on phone number prefix."""

    def test_india_number_routes_to_msg91(self):
        router = SMSRouter()
        provider = router.route("+919876543210")
        assert isinstance(provider, MSG91Provider)

    def test_us_number_routes_to_telnyx(self):
        router = SMSRouter()
        provider = router.route("+15551234567")
        assert isinstance(provider, TelnyxProvider)

    def test_uk_number_routes_to_telnyx(self):
        router = SMSRouter()
        provider = router.route("+447700900123")
        assert isinstance(provider, TelnyxProvider)

    def test_india_without_plus_routes_to_msg91(self):
        router = SMSRouter()
        provider = router.route("919876543210")
        assert isinstance(provider, MSG91Provider)

    def test_random_country_routes_to_telnyx(self):
        router = SMSRouter()
        provider = router.route("+819012345678")
        assert isinstance(provider, TelnyxProvider)


class TestExplicitRouting:
    """Test explicit routing mode overrides."""

    def test_telnyx_mode_always_telnyx(self, monkeypatch):
        monkeypatch.setenv("SMS_ROUTING_MODE", "telnyx")
        config = GatewayConfig()
        router = SMSRouter(config)
        provider = router.route("+919876543210")
        assert isinstance(provider, TelnyxProvider)

    def test_msg91_mode_always_msg91(self, monkeypatch):
        monkeypatch.setenv("SMS_ROUTING_MODE", "msg91")
        config = GatewayConfig()
        router = SMSRouter(config)
        provider = router.route("+15551234567")
        assert isinstance(provider, MSG91Provider)


class TestFallbackRouting:
    """Test fallback routing mode."""

    @pytest.mark.asyncio
    async def test_fallback_uses_primary_first(self, monkeypatch):
        monkeypatch.setenv("SMS_ROUTING_MODE", "fallback")
        monkeypatch.setenv("SMS_FALLBACK_PRIMARY", "telnyx")
        config = GatewayConfig()

        # Create router with mock clients
        router = SMSRouter(config)

        # Mock the telnyx send to succeed
        from unittest.mock import AsyncMock, patch
        from sms_gateway.models import MessageStatus, SendResult

        mock_result = SendResult(
            message_id="test-id",
            provider=ProviderName.telnyx,
            status=MessageStatus.sent,
            to="+15551234567",
            body="test",
        )
        router.telnyx.send_sms = AsyncMock(return_value=mock_result)

        result = await router.send("+15551234567", "test message")
        assert result.status == MessageStatus.sent
        router.telnyx.send_sms.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_switches_to_secondary(self, monkeypatch):
        monkeypatch.setenv("SMS_ROUTING_MODE", "fallback")
        monkeypatch.setenv("SMS_FALLBACK_PRIMARY", "telnyx")
        config = GatewayConfig()
        router = SMSRouter(config)

        from unittest.mock import AsyncMock
        from sms_gateway.models import MessageStatus, SendResult

        # Primary (telnyx) fails
        fail_result = SendResult(
            message_id="",
            provider=ProviderName.telnyx,
            status=MessageStatus.failed,
            error="Connection error",
            to="+15551234567",
            body="test",
        )
        router.telnyx.send_sms = AsyncMock(return_value=fail_result)

        # Secondary (msg91) succeeds
        success_result = SendResult(
            message_id="msg91-id",
            provider=ProviderName.msg91,
            status=MessageStatus.sent,
            to="+15551234567",
            body="test",
        )
        router.msg91.send_sms = AsyncMock(return_value=success_result)

        result = await router.send("+15551234567", "test message")
        assert result.status == MessageStatus.sent
        assert result.provider == ProviderName.msg91
        router.telnyx.send_sms.assert_called_once()
        router.msg91.send_sms.assert_called_once()