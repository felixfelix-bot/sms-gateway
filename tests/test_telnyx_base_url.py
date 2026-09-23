"""Regression tests for the Telnyx base-URL collision.

Machines that also drive Telnyx's OpenAI-compatible endpoint export
``TELNYX_BASE_URL=https://api.telnyx.com/v2/ai/openai``. Reusing that value for
SMS makes every send 404 ("Resource not found") — a failure that looks like a
bad endpoint or a bad key rather than a config collision. Found live
2026-09-23 while provisioning the nosms account on this host.
"""
import pytest

from sms_gateway.config import DEFAULT_TELNYX_API_BASE, TelnyxConfig, telnyx_base_url


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TELNYX_SMS_BASE_URL", raising=False)
    monkeypatch.delenv("TELNYX_BASE_URL", raising=False)


def test_defaults_to_the_api_root(monkeypatch):
    assert telnyx_base_url() == DEFAULT_TELNYX_API_BASE


def test_ignores_an_ai_endpoint_in_TELNYX_BASE_URL(monkeypatch):
    monkeypatch.setenv("TELNYX_BASE_URL", "https://api.telnyx.com/v2/ai/openai")
    assert telnyx_base_url() == DEFAULT_TELNYX_API_BASE


def test_honours_a_legacy_root_TELNYX_BASE_URL(monkeypatch):
    monkeypatch.setenv("TELNYX_BASE_URL", "https://api.telnyx.com/v2")
    assert telnyx_base_url() == "https://api.telnyx.com/v2"


def test_explicit_sms_base_url_wins(monkeypatch):
    monkeypatch.setenv("TELNYX_BASE_URL", "https://api.telnyx.com/v2/ai/openai")
    monkeypatch.setenv("TELNYX_SMS_BASE_URL", "https://proxy.internal/telnyx")
    assert telnyx_base_url() == "https://proxy.internal/telnyx"


def test_telnyx_config_uses_the_safe_resolver(monkeypatch):
    monkeypatch.setenv("TELNYX_BASE_URL", "https://api.telnyx.com/v2/ai/openai")
    monkeypatch.setenv("TELNYX_API_KEY", "k")
    cfg = TelnyxConfig()
    assert cfg.base_url == DEFAULT_TELNYX_API_BASE
    assert cfg.api_key == "k"
