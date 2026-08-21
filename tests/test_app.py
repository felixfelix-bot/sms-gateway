"""Tests for the FastAPI application endpoints."""
import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SMS_GATEWAY_TOKEN", "test-token")
os.environ.setdefault("TELNYX_API_KEY", "test-key")
os.environ.setdefault("TELNYX_MESSAGING_PROFILE_ID", "test-profile")
os.environ.setdefault("TELNYX_FROM_NUMBER", "+15551234567")
os.environ.setdefault("MSG91_AUTH_KEY", "test-auth")
os.environ.setdefault("MSG91_SENDER_ID", "TEST")
os.environ.setdefault("MSG91_DLT_TEMPLATE_ID", "test-dlt")

from sms_gateway.app import app, get_router
from sms_gateway.config import GatewayConfig
from sms_gateway.models import (
    BalanceInfo,
    DeliveryStatus,
    HealthStatus,
    MessageStatus,
    ProviderName,
    SendResult,
)
from sms_gateway.router import SMSRouter


@pytest.fixture
def test_router():
    """Create a router with mocked providers."""
    config = GatewayConfig()
    config.gateway_token = "test-token"
    router = SMSRouter(config)
    # Mock provider methods
    router.telnyx.send_sms = AsyncMock()
    router.msg91.send_sms = AsyncMock()
    router.telnyx.health_check = AsyncMock()
    router.msg91.health_check = AsyncMock()
    router.telnyx.get_balance = AsyncMock()
    router.msg91.get_balance = AsyncMock()
    router.telnyx.check_delivery = AsyncMock()
    router.msg91.check_delivery = AsyncMock()
    return router


@pytest.fixture
def client(test_router):
    """Create an async test client with mocked router."""
    app.dependency_overrides[get_router] = lambda: test_router
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestAuth:
    """Test API authentication."""

    @pytest.mark.asyncio
    async def test_send_without_token_returns_401(self, client):
        async with client as c:
            resp = await c.post("/sms/send", json={"to": "+15551234567", "body": "test"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_send_with_wrong_token_returns_401(self, client):
        async with client as c:
            resp = await c.post(
                "/sms/send",
                json={"to": "+15551234567", "body": "test"},
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, client, test_router):
        test_router.telnyx.health_check.return_value = HealthStatus(
            provider=ProviderName.telnyx, healthy=True, latency_ms=10.0
        )
        test_router.msg91.health_check.return_value = HealthStatus(
            provider=ProviderName.msg91, healthy=True, latency_ms=20.0
        )
        async with client as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_balance_requires_auth(self, client):
        async with client as c:
            resp = await c.get("/balance")
        assert resp.status_code == 401


class TestSendEndpoint:
    """Test POST /sms/send."""

    @pytest.mark.asyncio
    async def test_send_success(self, client, test_router):
        test_router.telnyx.send_sms.return_value = SendResult(
            message_id="msg-123",
            provider=ProviderName.telnyx,
            status=MessageStatus.sent,
            to="+15551234567",
            body="Hello",
        )
        async with client as c:
            resp = await c.post(
                "/sms/send",
                json={"to": "+15551234567", "body": "Hello"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == "msg-123"
        assert data["status"] == "sent"

    @pytest.mark.asyncio
    async def test_send_with_provider_override(self, client, test_router):
        test_router.msg91.send_sms.return_value = SendResult(
            message_id="msg91-123",
            provider=ProviderName.msg91,
            status=MessageStatus.sent,
            to="+919876543210",
            body="OTP",
        )
        async with client as c:
            resp = await c.post(
                "/sms/send",
                json={"to": "+919876543210", "body": "OTP", "provider": "msg91"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "msg91"


class TestBatchEndpoint:
    """Test POST /sms/batch."""

    @pytest.mark.asyncio
    async def test_batch_send(self, client, test_router):
        test_router.telnyx.send_sms.return_value = SendResult(
            message_id="msg-1",
            provider=ProviderName.telnyx,
            status=MessageStatus.sent,
            to="+15551234567",
            body="test",
        )
        test_router.msg91.send_sms.return_value = SendResult(
            message_id="msg-2",
            provider=ProviderName.msg91,
            status=MessageStatus.sent,
            to="+919876543210",
            body="test",
        )
        async with client as c:
            resp = await c.post(
                "/sms/batch",
                json={
                    "messages": [
                        {"to": "+15551234567", "body": "Hello 1"},
                        {"to": "+919876543210", "body": "Hello 2"},
                    ]
                },
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["sent"] == 2
        assert data["failed"] == 0

    @pytest.mark.asyncio
    async def test_batch_empty_rejected(self, client):
        async with client as c:
            resp = await c.post(
                "/sms/batch",
                json={"messages": []},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 422


class TestStatusEndpoint:
    """Test GET /sms/status/{message_id}."""

    @pytest.mark.asyncio
    async def test_get_status(self, client, test_router):
        test_router.telnyx.check_delivery.return_value = DeliveryStatus(
            message_id="msg-123",
            status=MessageStatus.delivered,
        )
        async with client as c:
            resp = await c.get(
                "/sms/status/msg-123",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "delivered"


class TestHealthEndpoint:
    """Test GET /health."""

    @pytest.mark.asyncio
    async def test_health_all_healthy(self, client, test_router):
        test_router.telnyx.health_check.return_value = HealthStatus(
            provider=ProviderName.telnyx, healthy=True, latency_ms=5.0
        )
        test_router.msg91.health_check.return_value = HealthStatus(
            provider=ProviderName.msg91, healthy=True, latency_ms=15.0
        )
        async with client as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["providers"]["telnyx"]["healthy"] is True
        assert data["providers"]["msg91"]["healthy"] is True

    @pytest.mark.asyncio
    async def test_health_degraded(self, client, test_router):
        test_router.telnyx.health_check.return_value = HealthStatus(
            provider=ProviderName.telnyx, healthy=False, error="Connection refused"
        )
        test_router.msg91.health_check.return_value = HealthStatus(
            provider=ProviderName.msg91, healthy=True, latency_ms=15.0
        )
        async with client as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"


class TestBalanceEndpoint:
    """Test GET /balance."""

    @pytest.mark.asyncio
    async def test_balance(self, client, test_router):
        test_router.telnyx.get_balance.return_value = BalanceInfo(
            provider=ProviderName.telnyx, balance=None, currency="USD"
        )
        test_router.msg91.get_balance.return_value = BalanceInfo(
            provider=ProviderName.msg91, balance=5000.0, currency="INR"
        )
        async with client as c:
            resp = await c.get("/balance", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"]["msg91"]["balance"] == 5000.0
        assert data["providers"]["msg91"]["currency"] == "INR"


class TestRootEndpoint:
    @pytest.mark.asyncio
    async def test_root(self, client):
        async with client as c:
            resp = await c.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "sms-gateway"