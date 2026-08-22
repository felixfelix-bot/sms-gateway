"""FastAPI HTTP service for the SMS gateway."""
import logging
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sms_gateway.config import GatewayConfig, get_config
from sms_gateway.logging_config import new_request_id, request_id_var, setup_logging
from sms_gateway.models import (
    BalanceInfo,
    BatchRequest,
    BatchResult,
    DeliveryStatus,
    HealthStatus,
    ProviderName,
    SendRequest,
    SendResult,
)
from sms_gateway.router import SMSRouter

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SMS Gateway",
    description="Provider-agnostic SMS gateway supporting Telnyx (international), MSG91 (India domestic) and Fast2SMS (India domestic).",
    version="0.1.0",
)
bearer_scheme = HTTPBearer(auto_error=False)

# Global router instance (lazily initialized)
_router: SMSRouter | None = None
_config: GatewayConfig | None = None


def get_router() -> SMSRouter:
    global _router, _config
    if _router is None:
        _config = get_config()
        _router = SMSRouter(_config)
    return _router


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    config: GatewayConfig = Depends(get_config),
) -> str:
    if credentials is None or credentials.credentials != config.gateway_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Assign a request ID to every request for structured logging."""
    rid = request.headers.get("X-Request-ID", new_request_id())
    token = request_id_var.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    request_id_var.reset(token)
    return response


@app.post("/sms/send", response_model=SendResult)
async def send_sms(
    req: SendRequest,
    router: SMSRouter = Depends(get_router),
    _: str = Security(verify_token, scopes=[]),
):
    """Send a single SMS message."""
    logger.info("send_sms to=%s provider=%s", req.to, req.provider)
    result = await router.send(req.to, req.body, provider_override=req.provider)
    if result.status == "failed" and result.error:
        logger.warning("send_sms failed: %s", result.error)
    return result


@app.post("/sms/batch", response_model=BatchResult)
async def send_batch(
    req: BatchRequest,
    router: SMSRouter = Depends(get_router),
    _: str = Security(verify_token, scopes=[]),
):
    """Send a batch of SMS messages."""
    logger.info("send_batch count=%d", len(req.messages))
    results: list[SendResult] = []
    for msg in req.messages:
        result = await router.send(msg.to, msg.body)
        results.append(result)
    sent = sum(1 for r in results if r.status == "sent")
    failed = len(results) - sent
    return BatchResult(total=len(results), sent=sent, failed=failed, results=results)


@app.get("/sms/status/{message_id}", response_model=DeliveryStatus)
async def get_status(
    message_id: str,
    provider: Optional[ProviderName] = None,
    router: SMSRouter = Depends(get_router),
    _: str = Security(verify_token, scopes=[]),
):
    """Check delivery status for a message."""
    if provider:
        gateway = router._providers.get(provider.value)
    else:
        # Try to determine provider from message_id prefix or default to telnyx
        gateway = router.telnyx
    if gateway is None:
        raise HTTPException(status_code=400, detail="Unknown provider")
    return await gateway.check_delivery(message_id)


@app.get("/health")
async def health(
    router: SMSRouter = Depends(get_router),
):
    """Gateway + all providers health check (no auth required)."""
    providers: dict[str, dict] = {}
    all_healthy = True
    any_healthy = False
    for name, provider in router._providers.items():
        health_status = await provider.health_check()
        providers[name] = health_status.model_dump()
        all_healthy = all_healthy and health_status.healthy
        any_healthy = any_healthy or health_status.healthy
    if all_healthy:
        status_str = "healthy"
    elif any_healthy:
        status_str = "degraded"
    else:
        status_str = "unhealthy"
    return {"status": status_str, "providers": providers}


@app.get("/balance")
async def balance(
    router: SMSRouter = Depends(get_router),
    _: str = Security(verify_token, scopes=[]),
):
    """Get balance/credits for all providers."""
    providers: dict[str, dict] = {}
    for name, provider in router._providers.items():
        balance_info = await provider.get_balance()
        providers[name] = balance_info.model_dump()
    return {"providers": providers}


@app.get("/")
async def root():
    """Service info."""
    return {"service": "sms-gateway", "version": "0.1.0", "docs": "/docs"}