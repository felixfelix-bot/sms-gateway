"""Tests for the MSG91 provider adapter with mocked HTTP calls."""
import os
from unittest.mock import AsyncMock

import httpx
import pytest

os.environ.setdefault("TELNYX_API_KEY", "test-key")
os.environ.setdefault("TELNYX_MESSAGING_PROFILE_ID", "test-profile")
os.environ.setdefault("TELNYX_FROM_NUMBER", "+15551234567")
os.environ.setdefault("MSG91_AUTH_KEY", "test-auth")
os.environ.setdefault("MSG91_SENDER_ID", "TEST")
os.environ.setdefault("MSG91_DLT_TEMPLATE_ID", "test-dlt")

from sms_gateway.config import MSG91Config
from sms_gateway.models import BatchMessage, MessageStatus, ProviderName
from sms_gateway.providers.msg91 import MSG91Provider


class TestMSG91Send:
    """Test MSG91 SMS sending."""

    @pytest.mark.asyncio
    async def test_send_sms_success(self):
        mock_response = httpx.Response(
            200,
            json={"type": "success", "message": "msg91-abc123"},
            request=httpx.Request("POST", "https://api.msg91.com/api/v5/sms"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.sent
        assert result.message_id == "msg91-abc123"
        assert result.provider == ProviderName.msg91

    @pytest.mark.asyncio
    async def test_send_sms_failure(self):
        mock_response = httpx.Response(
            200,
            json={"type": "error", "message": "Insufficient balance"},
            request=httpx.Request("POST", "https://api.msg91.com/api/v5/sms"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.failed
        assert "Insufficient balance" in result.error

    @pytest.mark.asyncio
    async def test_send_sms_http_error(self):
        mock_response = httpx.Response(
            500,
            json={"error": "Server error"},
            request=httpx.Request("POST", "https://api.msg91.com/api/v5/sms"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.failed
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_send_batch(self):
        responses = [
            httpx.Response(
                200,
                json={"type": "success", "message": f"msg91-{i}"},
                request=httpx.Request("POST", "https://api.msg91.com/api/v5/sms"),
            )
            for i in range(3)
        ]
        response_iter = iter(responses)
        transport = httpx.MockTransport(lambda req: next(response_iter))
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        messages = [BatchMessage(to=f"+91987654321{i}", body=f"OTP {i}") for i in range(3)]
        result = await provider.send_batch(messages)
        assert result.total == 3
        assert result.sent == 3
        assert result.failed == 0


class TestMSG91Delivery:
    """Test MSG91 delivery status polling."""

    @pytest.mark.asyncio
    async def test_check_delivery_delivered(self):
        mock_response = httpx.Response(
            200,
            json={"status": "success"},
            request=httpx.Request("GET", "https://api.msg91.com/api/v5/sms/campaign/status/msg91-abc"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        status = await provider.check_delivery("msg91-abc")
        assert status.status == MessageStatus.delivered

    @pytest.mark.asyncio
    async def test_check_delivery_failed(self):
        mock_response = httpx.Response(
            200,
            json={"status": "failed"},
            request=httpx.Request("GET", "https://api.msg91.com/api/v5/sms/campaign/status/msg91-abc"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        status = await provider.check_delivery("msg91-abc")
        assert status.status == MessageStatus.failed


class TestMSG91Health:
    """Test MSG91 health check and balance."""

    @pytest.mark.asyncio
    async def test_health_check_ok(self):
        mock_response = httpx.Response(
            200,
            json={"balance": 1000.0},
            request=httpx.Request("GET", "https://api.msg91.com/api/v5/balance.php"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        health = await provider.health_check()
        assert health.healthy is True
        assert health.latency_ms is not None

    @pytest.mark.asyncio
    async def test_health_check_fail(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(500, request=req)
        )
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "bad-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        health = await provider.health_check()
        assert health.healthy is False

    @pytest.mark.asyncio
    async def test_get_balance(self):
        mock_response = httpx.Response(
            200,
            json={"balance": 5000.5},
            request=httpx.Request("GET", "https://api.msg91.com/api/v5/balance.php"),
        )
        transport = httpx.MockTransport(lambda req: mock_response)
        client = httpx.AsyncClient(
            base_url="https://api.msg91.com/api/v5",
            transport=transport,
            headers={"authkey": "test-auth"},
        )

        config = MSG91Config()
        provider = MSG91Provider(config, client=client)

        balance = await provider.get_balance()
        assert balance.provider == ProviderName.msg91
        assert balance.balance == 5000.5
        assert balance.currency == "INR"