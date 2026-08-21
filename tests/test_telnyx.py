"""Tests for the Telnyx provider adapter with mocked HTTP calls."""
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("TELNYX_API_KEY", "test-key")
os.environ.setdefault("TELNYX_MESSAGING_PROFILE_ID", "test-profile")
os.environ.setdefault("TELNYX_FROM_NUMBER", "+15551234567")
os.environ.setdefault("MSG91_AUTH_KEY", "test-auth")
os.environ.setdefault("MSG91_SENDER_ID", "TEST")
os.environ.setdefault("MSG91_DLT_TEMPLATE_ID", "test-dlt")

from sms_gateway.config import TelnyxConfig
from sms_gateway.models import MessageStatus, ProviderName
from sms_gateway.providers.telnyx import TelnyxProvider


class TestTelnyxSend:
    """Test Telnyx SMS sending."""

    @pytest.mark.asyncio
    async def test_send_sms_success(self):
        """Successful send returns sent status with message_id."""
        mock_response = httpx.Response(
            200,
            json={
                "data": {
                    "id": "telnyx-msg-123",
                    "status": "queued",
                }
            },
            request=httpx.Request("POST", "https://api.telnyx.com/v2/messages"),
        )
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            headers={"Authorization": "Bearer test-key"},
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client._transport = transport

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        result = await provider.send_sms("+15551234567", "Hello from Telnyx")
        assert result.status == MessageStatus.sent
        assert result.message_id == "telnyx-msg-123"
        assert result.provider == ProviderName.telnyx

    @pytest.mark.asyncio
    async def test_send_sms_failure(self):
        """HTTP error returns failed status."""
        mock_response = httpx.Response(
            422,
            json={"errors": [{"detail": "Invalid phone number"}]},
            request=httpx.Request("POST", "https://api.telnyx.com/v2/messages"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer test-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        result = await provider.send_sms("invalid", "Hello")
        assert result.status == MessageStatus.failed
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_send_batch(self):
        """Batch send processes all messages."""
        from sms_gateway.models import BatchMessage

        responses = [
            httpx.Response(
                200,
                json={"data": {"id": f"msg-{i}", "status": "queued"}},
                request=httpx.Request("POST", "https://api.telnyx.com/v2/messages"),
            )
            for i in range(3)
        ]
        response_iter = iter(responses)
        transport = httpx.MockTransport(lambda req: next(response_iter))
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer test-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        messages = [BatchMessage(to=f"+15551234{i:03d}", body=f"Message {i}") for i in range(3)]
        result = await provider.send_batch(messages)
        assert result.total == 3
        assert result.sent == 3
        assert result.failed == 0


class TestTelnyxDelivery:
    """Test Telnyx delivery status polling."""

    @pytest.mark.asyncio
    async def test_check_delivery_delivered(self):
        mock_response = httpx.Response(
            200,
            json={"data": {"status": "delivered", "delivered_at": "2025-01-01T12:00:00Z"}},
            request=httpx.Request("GET", "https://api.telnyx.com/v2/messages/msg-123"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer test-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        status = await provider.check_delivery("msg-123")
        assert status.status == MessageStatus.delivered
        assert status.message_id == "msg-123"

    @pytest.mark.asyncio
    async def test_check_delivery_queued(self):
        mock_response = httpx.Response(
            200,
            json={"data": {"status": "queued"}},
            request=httpx.Request("GET", "https://api.telnyx.com/v2/messages/msg-123"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer test-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        status = await provider.check_delivery("msg-123")
        assert status.status == MessageStatus.queued


class TestTelnyxHealth:
    """Test Telnyx health check and balance."""

    @pytest.mark.asyncio
    async def test_health_check_ok(self):
        mock_response = httpx.Response(
            200,
            json={"data": []},
            request=httpx.Request("GET", "https://api.telnyx.com/v2/messaging_profiles"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer test-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        health = await provider.health_check()
        assert health.healthy is True
        assert health.latency_ms is not None

    @pytest.mark.asyncio
    async def test_health_check_fail(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(401, request=req)
        )
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer bad-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        health = await provider.health_check()
        assert health.healthy is False

    @pytest.mark.asyncio
    async def test_get_balance(self):
        mock_response = httpx.Response(
            200,
            json={"data": []},
            request=httpx.Request("GET", "https://api.telnyx.com/v2/messaging_profiles"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=transport,
            headers={"Authorization": "Bearer test-key"},
        )

        config = TelnyxConfig()
        provider = TelnyxProvider(config, client=client)

        balance = await provider.get_balance()
        assert balance.provider == ProviderName.telnyx