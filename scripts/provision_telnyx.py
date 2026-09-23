#!/usr/bin/env python3
"""Idempotent Telnyx account provisioning for the SMS gateway / nosms.

What it does, in order:
  1. reads the account balance
  2. ensures a messaging profile exists (creates one if not)
  3. ensures an SMS-capable number is owned and attached to that profile
     (buys one only when the account is funded)

Dry-run by default: with no ``--execute`` every step is read-only and the
mutating calls are never made. That keeps provisioning safe to run from an
agent session before money has been added.

Usage:
    python -m scripts.provision_telnyx                      # plan only
    python -m scripts.provision_telnyx --execute            # buy if funded
    python -m scripts.provision_telnyx --execute --area-code 415

Environment:
    TELNYX_API_KEY              (required)
    TELNYX_SMS_BASE_URL         (optional, default https://api.telnyx.com/v2)

NOTE: do NOT reuse ``TELNYX_BASE_URL`` here. On machines that also call Telnyx's
OpenAI-compatible endpoint that variable is set to
``https://api.telnyx.com/v2/ai/openai``, and every SMS call then 404s with
"Resource not found" instead of failing loudly.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

import httpx

DEFAULT_BASE_URL = "https://api.telnyx.com/v2"
# A number costs roughly a dollar a month; leave headroom for a first test batch.
DEFAULT_MIN_BALANCE = 1.0


@dataclass
class ProvisionResult:
    funded: bool = False
    balance: str = "0.00"
    messaging_profile_id: str | None = None
    profile_created: bool = False
    phone_number: str | None = None
    bought: bool = False
    summary: str = ""
    notes: list[str] = field(default_factory=list)

    def env_block(self) -> dict[str, str]:
        return {
            "TELNYX_MESSAGING_PROFILE_ID": self.messaging_profile_id or "",
            "TELNYX_FROM_NUMBER": self.phone_number or "",
        }


def _client(api_key: str, base_url: str | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=base_url or os.getenv("TELNYX_SMS_BASE_URL", DEFAULT_BASE_URL),
        timeout=httpx.Timeout(30.0),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )


def _check(resp: httpx.Response, what: str) -> dict:
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise RuntimeError(f"{what} failed: HTTP {resp.status_code} {detail}")
    return resp.json()


def get_balance(client: httpx.Client) -> dict:
    return _check(client.get("/balance"), "balance lookup").get("data", {})


def list_messaging_profiles(client: httpx.Client) -> list[dict]:
    return _check(client.get("/messaging_profiles"), "messaging profile list").get("data", [])


def ensure_messaging_profile(client: httpx.Client, name: str, execute: bool) -> tuple[str | None, bool]:
    """Return (profile_id, created). Creates only when executing and none exists."""
    profiles = list_messaging_profiles(client)
    for profile in profiles:
        if profile.get("name") == name:
            return profile.get("id"), False
    if profiles and not execute:
        return profiles[0].get("id"), False
    if not execute:
        return None, False
    created = _check(
        client.post("/messaging_profiles", json={"name": name}),
        "messaging profile create",
    ).get("data", {})
    return created.get("id"), True


def list_owned_numbers(client: httpx.Client) -> list[dict]:
    return _check(client.get("/phone_numbers", params={"page[size]": 50}), "owned number list").get("data", [])


def search_available_numbers(
    client: httpx.Client,
    country: str = "US",
    area_code: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Available numbers in `country` that support SMS."""
    params: dict[str, object] = {
        "filter[country_code]": country,
        "filter[features][]": "sms",
        "page[size]": max(limit, 1),
    }
    if area_code:
        params["filter[national_destination_code]"] = area_code
    data = _check(client.get("/available_phone_numbers", params=params), "number search").get("data", [])
    out = []
    for item in data:
        features = {f.get("name") for f in item.get("features", []) if isinstance(f, dict)}
        if "sms" in features:
            out.append(item)
    return out[:limit]


def ensure_number(
    client: httpx.Client,
    profile_id: str | None,
    country: str = "US",
    execute: bool = False,
    area_code: str | None = None,
    allow_buy: bool | None = None,
) -> tuple[str | None, bool]:
    """Return (phone_number, bought). Buys+attaches only when allowed."""
    if allow_buy is None:
        allow_buy = execute

    for owned in list_owned_numbers(client):
        if profile_id and owned.get("messaging_profile_id") not in (None, profile_id):
            continue
        return owned.get("phone_number"), False

    candidates = search_available_numbers(client, country=country, area_code=area_code)
    if not candidates:
        return None, False
    candidate = candidates[0]
    if not allow_buy or not execute:
        return candidate.get("phone_number"), False

    order = _check(
        client.post("/number_orders", json={"phone_numbers": [{"phone_number": candidate["phone_number"]}]}),
        "number order",
    ).get("data", {})
    ordered = (order.get("phone_numbers") or [{}])[0]
    number_id = ordered.get("id")
    number = ordered.get("phone_number") or candidate["phone_number"]
    if number_id and profile_id:
        _check(
            client.patch(f"/phone_numbers/{number_id}", json={"messaging_profile_id": profile_id}),
            "number attach",
        )
    return number, True


def provision(
    client: httpx.Client,
    execute: bool = False,
    country: str = "US",
    profile_name: str = "nosms",
    api_key: str | None = None,
    min_balance: float = DEFAULT_MIN_BALANCE,
    area_code: str | None = None,
) -> ProvisionResult:
    if api_key is not None and not api_key:
        raise SystemExit("TELNYX_API_KEY is not set — add it to the environment (or ~/.hermes/.env) first.")

    res = ProvisionResult()
    balance = get_balance(client)
    raw_credit = balance.get("available_credit", "0.00")
    res.balance = str(raw_credit)
    try:
        res.funded = float(raw_credit) >= min_balance
    except (TypeError, ValueError):
        res.funded = False

    profile_id, created = ensure_messaging_profile(client, profile_name, execute=execute)
    res.messaging_profile_id, res.profile_created = profile_id, created

    number, bought = ensure_number(
        client,
        profile_id,
        country=country,
        execute=execute,
        area_code=area_code,
        allow_buy=res.funded and execute,
    )
    res.phone_number, res.bought = number, bought

    if not res.funded:
        res.summary = (
            f"Account balance is {res.balance} USD (< {min_balance}) — fund the account before a number "
            "can be bought or any message sent."
        )
    elif not execute:
        res.summary = f"Plan only. Balance {res.balance} USD; would use profile {profile_id} and number {number}."
    elif res.phone_number and not bought:
        res.summary = f"Already provisioned: profile {profile_id}, number {res.phone_number}."
    elif res.phone_number and bought:
        res.summary = f"Provisioned: bought {res.phone_number} and attached it to profile {profile_id}."
    else:
        res.summary = f"No SMS-capable number available in {country}; nothing bought."

    if created:
        res.notes.append(f"created messaging profile {profile_id!r}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision the Telnyx account for SMS (idempotent).")
    parser.add_argument("--execute", action="store_true", help="actually create/buy (default: plan only)")
    parser.add_argument("--country", default="US", help="two-letter country code to search numbers in")
    parser.add_argument("--area-code", default=None, help="preferred area code (NDC)")
    parser.add_argument("--profile-name", default="nosms", help="messaging profile name to ensure")
    parser.add_argument("--min-balance", type=float, default=DEFAULT_MIN_BALANCE)
    args = parser.parse_args(argv)

    api_key = os.getenv("TELNYX_API_KEY", "")
    if not api_key:
        raise SystemExit("TELNYX_API_KEY is not set — export it or source ~/.hermes/.env first.")

    with _client(api_key) as client:
        res = provision(
            client,
            execute=args.execute,
            country=args.country,
            profile_name=args.profile_name,
            api_key=api_key,
            min_balance=args.min_balance,
            area_code=args.area_code,
        )

    print(f"mode          : {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print(f"balance (USD) : {res.balance}")
    print(f"funded        : {res.funded}")
    print(f"profile       : {res.messaging_profile_id} (created={res.profile_created})")
    print(f"number        : {res.phone_number} (bought={res.bought})")
    print(f"summary       : {res.summary}")
    for note in res.notes:
        print(f"note          : {note}")
    if res.phone_number or res.messaging_profile_id:
        print("\n# add to the gateway environment:")
        for key, value in res.env_block().items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
