"""Configuration loaded from environment variables / .env file."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class TelnyxConfig:
    api_key: str = field(default_factory=lambda: os.getenv("TELNYX_API_KEY", ""))
    messaging_profile_id: str = field(
        default_factory=lambda: os.getenv("TELNYX_MESSAGING_PROFILE_ID", "")
    )
    base_url: str = field(
        default_factory=lambda: os.getenv("TELNYX_BASE_URL", "https://api.telnyx.com/v2")
    )
    from_number: str = field(
        default_factory=lambda: os.getenv("TELNYX_FROM_NUMBER", "")
    )
    rate_limit: float = field(
        default_factory=lambda: float(os.getenv("TELNYX_RATE_LIMIT", "5.0"))
    )


@dataclass
class MSG91Config:
    auth_key: str = field(default_factory=lambda: os.getenv("MSG91_AUTH_KEY", ""))
    sender_id: str = field(default_factory=lambda: os.getenv("MSG91_SENDER_ID", "AUROVL"))
    dlt_template_id: str = field(
        default_factory=lambda: os.getenv("MSG91_DLT_TEMPLATE_ID", "")
    )
    base_url: str = field(
        default_factory=lambda: os.getenv("MSG91_BASE_URL", "https://api.msg91.com/api/v5")
    )
    rate_limit: float = field(
        default_factory=lambda: float(os.getenv("MSG91_RATE_LIMIT", "10.0"))
    )


@dataclass
class GatewayConfig:
    # Gateway auth
    gateway_token: str = field(
        default_factory=lambda: os.getenv("SMS_GATEWAY_TOKEN", "changeme")
    )
    # Routing: auto | telnyx | msg91 | fallback
    routing_mode: str = field(
        default_factory=lambda: os.getenv("SMS_ROUTING_MODE", "auto")
    )
    # Fallback primary provider (used in fallback mode)
    fallback_primary: str = field(
        default_factory=lambda: os.getenv("SMS_FALLBACK_PRIMARY", "telnyx")
    )
    # HTTP
    host: str = field(default_factory=lambda: os.getenv("SMS_GATEWAY_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("SMS_GATEWAY_PORT", "8000")))
    # Sub-configs
    telnyx: TelnyxConfig = field(default_factory=TelnyxConfig)
    msg91: MSG91Config = field(default_factory=MSG91Config)


def get_config() -> GatewayConfig:
    """Return a fresh config snapshot from environment."""
    return GatewayConfig()