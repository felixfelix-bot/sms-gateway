"""Tests for the Telnyx account provisioning helper.

Written before the implementation (TDD): the RED state is "module does not
exist". All HTTP is faked with httpx.MockTransport so the tests assert BOTH the
decision logic and the exact mutating calls made (dry-run must make none).
"""
import json
import urllib.parse

import httpx
import pytest

from scripts.provision_telnyx import (
    ProvisionResult,
    ensure_messaging_profile,
    ensure_number,
    get_balance,
    provision,
    search_available_numbers,
)

BASE = "https://api.telnyx.com/v2"


def client(handler, *, api_key="test-key"):
    return httpx.Client(base_url=BASE, transport=httpx.MockTransport(handler),
                        headers={"Authorization": f"Bearer {api_key}"})


def recorder(routes):
    """routes: {(method, path_prefix): (status, body)}. Records every request.

    The httpx client carries a ``/v2`` base path, so the recorded/normal-matched
    path strips that prefix — routes are declared as the API path after /v2.
    """
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v2")
        seen.append((request.method, path, request.url.query.decode()))
        for (method, prefix), (status, body) in routes.items():
            if request.method == method and path.startswith(prefix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"errors": [{"detail": "no route"}]})

    return handler, seen


# --- balance -----------------------------------------------------------------

def test_get_balance_reads_available_credit():
    handler, seen = recorder({("GET", "/balance"): (200, {"data": {"available_credit": "-0.09", "balance": "-0.09"}})})
    with client(handler) as c:
        bal = get_balance(c)
    assert bal["available_credit"] == "-0.09"
    assert seen == [("GET", "/balance", "")]


# --- messaging profile -------------------------------------------------------

def test_ensure_messaging_profile_reuses_existing():
    handler, seen = recorder({("GET", "/messaging_profiles"): (200, {"data": [{"id": "prof-1", "name": "nosms"}]})})
    with client(handler) as c:
        prof_id, created = ensure_messaging_profile(c, "nosms", execute=True)
    assert (prof_id, created) == ("prof-1", False)
    assert all(m == "GET" for m, _, _ in seen), "must not create a duplicate profile"


def test_ensure_messaging_profile_creates_when_missing():
    handler, seen = recorder({
        ("GET", "/messaging_profiles"): (200, {"data": []}),
        ("POST", "/messaging_profiles"): (200, {"data": {"id": "prof-new", "name": "nosms"}}),
    })
    with client(handler) as c:
        prof_id, created = ensure_messaging_profile(c, "nosms", execute=True)
    assert (prof_id, created) == ("prof-new", True)
    assert ("POST", "/messaging_profiles", "") in seen


def test_ensure_messaging_profile_dry_run_never_posts():
    handler, seen = recorder({("GET", "/messaging_profiles"): (200, {"data": []})})
    with client(handler) as c:
        prof_id, created = ensure_messaging_profile(c, "nosms", execute=False)
    assert prof_id is None and created is False
    assert all(m == "GET" for m, _, _ in seen)


# --- number search -----------------------------------------------------------

def test_search_filters_to_sms_capable_numbers():
    body = {"data": [
        {"phone_number": "+12025550100", "features": [{"name": "sms"}, {"name": "voice"}],
         "region_information": [{"region_name": "DC"}]},
        {"phone_number": "+12025550101", "features": [{"name": "voice"}]},
    ]}
    handler, seen = recorder({("GET", "/available_phone_numbers"): (200, body)})
    with client(handler) as c:
        found = search_available_numbers(c, country="US", limit=5)
    assert [n["phone_number"] for n in found] == ["+12025550100"]
    assert "features%5B%5D=sms" in seen[0][2] or "features" in seen[0][2], "must ask the API for the sms feature filter"


def test_search_passes_area_code_as_ndc_filter():
    handler, seen = recorder({("GET", "/available_phone_numbers"): (200, {"data": []})})
    with client(handler) as c:
        search_available_numbers(c, country="US", area_code="415", limit=3)
    query = urllib.parse.unquote(seen[0][2])
    assert "filter[national_destination_code]=415" in query
    assert "filter[country_code]=US" in query


# --- buying / attaching a number --------------------------------------------

def test_ensure_number_buys_and_attaches_in_execute_mode():
    routes = {
        ("GET", "/phone_numbers"): (200, {"data": []}),
        ("GET", "/available_phone_numbers"): (200, {"data": [
            {"phone_number": "+12025550100", "features": [{"name": "sms"}]}]}),
        ("POST", "/number_orders"): (200, {"data": {"id": "ord-1", "phone_numbers": [
            {"id": "pn-1", "phone_number": "+12025550100"}]}}),
        ("PATCH", "/phone_numbers/pn-1"): (200, {"data": {"id": "pn-1"}}),
    }
    handler, seen = recorder(routes)
    with client(handler) as c:
        number, bought = ensure_number(c, "prof-1", country="US", execute=True)
    assert (number, bought) == ("+12025550100", True)
    methods = [m for m, _, _ in seen]
    assert "POST" in methods and "PATCH" in methods


def test_ensure_number_is_idempotent_when_already_owned():
    routes = {
        ("GET", "/phone_numbers"): (200, {"data": [
            {"id": "pn-9", "phone_number": "+12025550999", "messaging_profile_id": "prof-1"}]}),
    }
    handler, seen = recorder(routes)
    with client(handler) as c:
        number, bought = ensure_number(c, "prof-1", country="US", execute=True)
    assert (number, bought) == ("+12025550999", False)
    assert all(m == "GET" for m, _, _ in seen), "existing number must not be re-ordered"


def test_ensure_number_dry_run_makes_no_mutations():
    routes = {
        ("GET", "/phone_numbers"): (200, {"data": []}),
        ("GET", "/available_phone_numbers"): (200, {"data": [
            {"phone_number": "+12025550100", "features": [{"name": "sms"}]}]}),
    }
    handler, seen = recorder(routes)
    with client(handler) as c:
        number, bought = ensure_number(c, "prof-1", country="US", execute=False)
    assert number == "+12025550100" and bought is False
    assert all(m == "GET" for m, _, _ in seen)


# --- orchestration -----------------------------------------------------------

def test_provision_refuses_to_buy_on_unfunded_account():
    routes = {
        ("GET", "/balance"): (200, {"data": {"available_credit": "-0.09"}}),
        ("GET", "/messaging_profiles"): (200, {"data": [{"id": "prof-1", "name": "nosms"}]}),
        ("GET", "/phone_numbers"): (200, {"data": []}),
        ("GET", "/available_phone_numbers"): (200, {"data": [
            {"phone_number": "+12025550100", "features": [{"name": "sms"}]}]}),
        ("POST", "/number_orders"): (500, {"errors": [{"detail": "should never be called"}]}),
    }
    handler, seen = recorder(routes)
    with client(handler) as c:
        res = provision(c, execute=True, country="US", profile_name="nosms", min_balance=1.0)
    assert isinstance(res, ProvisionResult)
    assert res.funded is False
    assert res.bought is False
    assert all(m == "GET" for m, _, _ in seen), "unfunded account must not attempt a purchase"
    assert "fund" in res.summary.lower()


def test_provision_execute_buys_when_funded():
    routes = {
        ("GET", "/balance"): (200, {"data": {"available_credit": "20.00"}}),
        ("GET", "/messaging_profiles"): (200, {"data": [{"id": "prof-1", "name": "nosms"}]}),
        ("GET", "/phone_numbers"): (200, {"data": []}),
        ("GET", "/available_phone_numbers"): (200, {"data": [
            {"phone_number": "+12025550100", "features": [{"name": "sms"}]}]}),
        ("POST", "/number_orders"): (200, {"data": {"id": "ord-1", "phone_numbers": [
            {"id": "pn-1", "phone_number": "+12025550100"}]}}),
        ("PATCH", "/phone_numbers/pn-1"): (200, {"data": {"id": "pn-1"}}),
    }
    handler, _ = recorder(routes)
    with client(handler) as c:
        res = provision(c, execute=True, country="US", profile_name="nosms", min_balance=1.0)
    assert res.funded is True and res.bought is True
    assert res.phone_number == "+12025550100"
    assert res.messaging_profile_id == "prof-1"
    assert res.env_block()["TELNYX_FROM_NUMBER"] == "+12025550100"


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        provision(client(lambda r: httpx.Response(200, json={})), execute=False,
                  country="US", profile_name="nosms", api_key="")
    assert "TELNYX_API_KEY" in str(exc.value)


def test_balance_parser_tolerates_string_and_float():
    assert json.loads(json.dumps({"available_credit": "-0.09"}))["available_credit"] == "-0.09"


# --- base-url collision -------------------------------------------------------

def test_client_ignores_ai_endpoint_in_TELNYX_BASE_URL(monkeypatch):
    """TELNYX_BASE_URL may point at Telnyx's OpenAI-compatible endpoint — never reuse it."""
    from scripts.provision_telnyx import _client

    monkeypatch.setenv("TELNYX_BASE_URL", "https://api.telnyx.com/v2/ai/openai")
    monkeypatch.delenv("TELNYX_SMS_BASE_URL", raising=False)
    with _client("k") as c:
        assert str(c.build_request("GET", "/balance").url) == "https://api.telnyx.com/v2/balance"


def test_client_honours_TELNYX_SMS_BASE_URL(monkeypatch):
    from scripts.provision_telnyx import _client

    monkeypatch.setenv("TELNYX_SMS_BASE_URL", "https://proxy.internal/telnyx")
    with _client("k") as c:
        assert str(c.build_request("GET", "/balance").url) == "https://proxy.internal/telnyx/balance"
