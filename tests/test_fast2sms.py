"""Tests for the Fast2SMS provider adapter with mocked HTTP calls."""
import json
import os
from datetime import datetime

import httpx
import pytest

os.environ.setdefault("TELNYX_API_KEY", "test-key")
os.environ.setdefault("TELNYX_MESSAGING_PROFILE_ID", "test-profile")
os.environ.setdefault("TELNYX_FROM_NUMBER", "+15551234567")
os.environ.setdefault("MSG91_AUTH_KEY", "test-auth")
os.environ.setdefault("MSG91_SENDER_ID", "TEST")
os.environ.setdefault("MSG91_DLT_TEMPLATE_ID", "test-dlt")
os.environ.setdefault("FAST2SMS_API_KEY", "test-key")
os.environ.setdefault("FAST2SMS_SENDER_ID", "FSTSMS")
os.environ.setdefault("FAST2SMS_ROUTE", "q")
os.environ.setdefault("FAST2SMS_DLT_TEMPLATE_ID", "111111")

from sms_gateway.config import Fast2SMSConfig
from sms_gateway.models import BatchMessage, MessageStatus, ProviderName
from sms_gateway.providers.fast2sms import Fast2SMSProvider


def _mock_client(handler, headers=None):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        base_url="https://www.fast2sms.com",
        transport=transport,
        headers=headers or {"authorization": "test-key"},
    )


class TestFast2SMSSend:
    """Test Fast2SMS SMS sending."""

    @pytest.mark.asyncio
    async def test_send_sms_success(self):
        mock_response = httpx.Response(
            200,
            json={
                "return": True,
                "request_id": "lwdtp7cjyqxvfe9",
                "message": ["Message sent successfully to 1 number"],
            },
            request=httpx.Request("POST", "https://www.fast2sms.com/dev/bulkV2"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.sent
        assert result.message_id == "lwdtp7cjyqxvfe9"
        assert result.provider == ProviderName.fast2sms

    @pytest.mark.asyncio
    async def test_send_sms_quick_route_payload(self):
        captured = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(req.read())
            return httpx.Response(
                200,
                json={"return": True, "request_id": "q-req-1", "message": ["ok"]},
            )

        client = _mock_client(handler)
        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.sent
        # Quick route: full message text, no sender/template fields
        assert captured["payload"] == {
            "route": "q",
            "numbers": "9876543210",
            "message": "Your OTP is 1234",
        }

    @pytest.mark.asyncio
    async def test_send_sms_dlt_route_payload(self):
        captured = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(req.read())
            return httpx.Response(
                200,
                json={"return": True, "request_id": "dlt-req-1", "message": ["ok"]},
            )

        client = _mock_client(handler)
        config = Fast2SMSConfig(
            api_key="test-key",
            sender_id="FSTSMS",
            route="dlt",
            dlt_template_id="111111",
        )
        provider = Fast2SMSProvider(config, client=client)

        result = await provider.send_sms("+919876543210", "4821")
        assert result.status == MessageStatus.sent
        # DLT route: message field carries the template ID, body -> variables_values
        assert captured["payload"] == {
            "route": "dlt",
            "numbers": "9876543210",
            "sender_id": "FSTSMS",
            "message": "111111",
            "variables_values": "4821",
        }

    @pytest.mark.asyncio
    async def test_send_sms_failure(self):
        mock_response = httpx.Response(
            200,
            json={
                "return": False,
                "status_code": 416,
                "message": "You don't have sufficient wallet balance",
            },
            request=httpx.Request("POST", "https://www.fast2sms.com/dev/bulkV2"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.failed
        assert "416" in result.error
        assert "wallet balance" in result.error

    @pytest.mark.asyncio
    async def test_send_sms_http_error(self):
        mock_response = httpx.Response(
            400,
            json={
                "return": False,
                "status_code": 412,
                "message": "Invalid Authentication, Check Authorization Key",
            },
            request=httpx.Request("POST", "https://www.fast2sms.com/dev/bulkV2"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        result = await provider.send_sms("+919876543210", "Your OTP is 1234")
        assert result.status == MessageStatus.failed
        assert "400" in result.error

    @pytest.mark.asyncio
    async def test_send_batch(self):
        responses = [
            httpx.Response(
                200,
                json={"return": True, "request_id": f"f2s-{i}", "message": ["ok"]},
                request=httpx.Request("POST", "https://www.fast2sms.com/dev/bulkV2"),
            )
            for i in range(3)
        ]
        response_iter = iter(responses)
        client = _mock_client(lambda req: next(response_iter))

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        messages = [BatchMessage(to=f"+91987654{i:04d}", body=f"OTP {i}") for i in range(3)]
        result = await provider.send_batch(messages)
        assert result.total == 3
        assert result.sent == 3
        assert result.failed == 0


class TestFast2SMSDelivery:
    """Test Fast2SMS delivery status polling."""

    @pytest.mark.asyncio
    async def test_check_delivery_delivered(self):
        mock_response = httpx.Response(
            200,
            json=[
                {
                    "phone": "9876543210",
                    "status": "Delivered",
                    "delivered_date": "2026-08-22 10:30:00",
                }
            ],
            request=httpx.Request("GET", "https://www.fast2sms.com/dev/dlr/f2s-abc"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        status = await provider.check_delivery("f2s-abc")
        assert status.status == MessageStatus.delivered
        assert status.delivered_at == datetime(2026, 8, 22, 10, 30, 0)

    @pytest.mark.asyncio
    async def test_check_delivery_in_process(self):
        mock_response = httpx.Response(
            200,
            json=[{"phone": "9876543210", "status": "In Process"}],
            request=httpx.Request("GET", "https://www.fast2sms.com/dev/dlr/f2s-abc"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        status = await provider.check_delivery("f2s-abc")
        assert status.status == MessageStatus.queued

    @pytest.mark.asyncio
    async def test_check_delivery_failed(self):
        mock_response = httpx.Response(
            200,
            json={"status": "Failed"},
            request=httpx.Request("GET", "https://www.fast2sms.com/dev/dlr/f2s-abc"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        status = await provider.check_delivery("f2s-abc")
        assert status.status == MessageStatus.failed

    @pytest.mark.asyncio
    async def test_check_delivery_error(self):
        client = _mock_client(lambda req: httpx.Response(500, request=req))

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        status = await provider.check_delivery("f2s-abc")
        assert status.status == MessageStatus.unknown
        assert status.error is not None


class TestFast2SMSHealth:
    """Test Fast2SMS health check and balance."""

    @pytest.mark.asyncio
    async def test_health_check_ok(self):
        mock_response = httpx.Response(
            200,
            json={"return": "true", "wallet": "5566.0100", "sms_count": 27830},
            request=httpx.Request("POST", "https://www.fast2sms.com/dev/wallet"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        health = await provider.health_check()
        assert health.healthy is True
        assert health.latency_ms is not None

    @pytest.mark.asyncio
    async def test_health_check_fail(self):
        client = _mock_client(lambda req: httpx.Response(500, request=req))

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        health = await provider.health_check()
        assert health.healthy is False

    @pytest.mark.asyncio
    async def test_get_balance(self):
        mock_response = httpx.Response(
            200,
            json={"return": "true", "wallet": "5566.0100", "sms_count": 27830},
            request=httpx.Request("POST", "https://www.fast2sms.com/dev/wallet"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        balance = await provider.get_balance()
        assert balance.provider == ProviderName.fast2sms
        assert balance.balance == 5566.01
        assert balance.currency == "INR"

    @pytest.mark.asyncio
    async def test_get_balance_error(self):
        mock_response = httpx.Response(
            401,
            json={"return": False, "status_code": 412, "message": "Invalid Authentication"},
            request=httpx.Request("POST", "https://www.fast2sms.com/dev/wallet"),
        )
        client = _mock_client(lambda req: mock_response)

        config = Fast2SMSConfig()
        provider = Fast2SMSProvider(config, client=client)

        balance = await provider.get_balance()
        assert balance.provider == ProviderName.fast2sms
        assert balance.balance is None
        assert balance.error is not None
