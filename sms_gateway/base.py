"""Abstract base class defining the provider-agnostic SMS gateway interface."""
from abc import ABC, abstractmethod

from sms_gateway.models import (
    BalanceInfo,
    BatchMessage,
    BatchResult,
    DeliveryStatus,
    HealthStatus,
    SendResult,
)


class SMSGateway(ABC):
    """Every SMS provider adapter must implement this interface."""

    provider_name: str  # set by subclass

    @abstractmethod
    async def send_sms(self, to: str, body: str) -> SendResult:
        """Send a single SMS message."""
        ...

    @abstractmethod
    async def send_batch(self, messages: list[BatchMessage]) -> BatchResult:
        """Send a batch of SMS messages."""
        ...

    @abstractmethod
    async def check_delivery(self, message_id: str) -> DeliveryStatus:
        """Poll delivery status for a previously sent message."""
        ...

    @abstractmethod
    async def get_balance(self) -> BalanceInfo:
        """Return remaining credits / balance for this provider."""
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Quick liveness check for the provider API."""
        ...