# SMS Gateway — Provider-Agnostic SMS Service

A provider-agnostic SMS gateway built for the **Auroville election system** (auditable-voting project). It sends OTPs and ballot links to voter phone numbers using the optimal provider based on destination:

- **India (+91)** → **MSG91** (TRAI/DLT-compliant domestic routing)
- **International** → **Telnyx** (global reach, competitive pricing)

## Architecture Overview

```
                    ┌─────────────────────────────────┐
                    │         FastAPI HTTP API          │
                    │   /sms/send  /sms/batch  /health  │
                    └──────────────┬──────────────────┘
                                   │
                          ┌────────▼────────┐
                          │    SMSRouter     │
                          │  (auto-routing)  │
                          └───┬─────────┬───┘
                              │         │
                   ┌──────────▼──┐  ┌───▼──────────┐
                   │  Telnyx     │  │   MSG91       │
                   │  Adapter    │  │   Adapter     │
                   │ (intl SMS)  │  │ (India SMS)   │
                   └─────────────┘  └───────────────┘
```

### Key Components

| Component | File | Description |
|---|---|---|
| Abstract interface | `sms_gateway/base.py` | `SMSGateway` ABC all providers implement |
| Models | `sms_gateway/models.py` | Pydantic request/response models |
| Telnyx adapter | `sms_gateway/providers/telnyx.py` | Telnyx REST API integration |
| MSG91 adapter | `sms_gateway/providers/msg91.py` | MSG91 REST API integration |
| Auto-router | `sms_gateway/router.py` | Number-prefix routing + fallback logic |
| Rate limiter | `sms_gateway/rate_limiter.py` | Token bucket per provider |
| FastAPI app | `sms_gateway/app.py` | HTTP endpoints with Bearer auth |
| Config | `sms_gateway/config.py` | Env-var-driven configuration |
| Logging | `sms_gateway/logging_config.py` | Structured JSON logs with request IDs |

## Setup Instructions

### Prerequisites

- Python 3.12+
- A Telnyx account with API key and messaging profile
- An MSG91 account with auth key (for India SMS)

### Quick Start

```bash
# Clone
git clone https://github.com/felixfelix-bot/sms-gateway.git
cd sms-gateway

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the server
uvicorn sms_gateway.app:app --reload --port 8000
```

### Docker

```bash
cp .env.example .env
# Edit .env with your credentials
docker-compose up -d
```

The API will be available at `http://localhost:8000` with interactive docs at `/docs`.

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SMS_GATEWAY_TOKEN` | ✅ | `changeme` | Bearer token for API authentication |
| `SMS_ROUTING_MODE` | ❌ | `auto` | Routing mode: `auto`, `telnyx`, `msg91`, `fallback` |
| `SMS_FALLBACK_PRIMARY` | ❌ | `telnyx` | Primary provider in fallback mode |
| `SMS_GATEWAY_HOST` | ❌ | `0.0.0.0` | Server bind host |
| `SMS_GATEWAY_PORT` | ❌ | `8000` | Server bind port |
| `TELNYX_API_KEY` | ✅* | — | Telnyx API key (Bearer token) |
| `TELNYX_MESSAGING_PROFILE_ID` | ✅* | — | Telnyx messaging profile UUID |
| `TELNYX_FROM_NUMBER` | ✅* | — | Telnyx sender phone number (E.164) |
| `TELNYX_RATE_LIMIT` | ❌ | `5.0` | Telnyx requests/second limit |
| `MSG91_AUTH_KEY` | ✅* | — | MSG91 authentication key |
| `MSG91_SENDER_ID` | ❌ | `AUROVL` | MSG91 sender ID (6-char alpha) |
| `MSG91_DLT_TEMPLATE_ID` | ✅* | — | DLT template ID (India TRAI compliance) |
| `MSG91_RATE_LIMIT` | ❌ | `10.0` | MSG91 requests/second limit |

\* Required only if that provider is used by the routing mode.

## API Documentation

All endpoints (except `/health`) require `Authorization: Bearer <SMS_GATEWAY_TOKEN>` header.

### POST /sms/send — Send Single SMS

```bash
curl -X POST http://localhost:8000/sms/send \
  -H "Authorization: Bearer $SMS_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+919876543210", "body": "Your Auroville election OTP is 4821. Valid for 10 minutes."}'
```

**Response:**
```json
{
  "message_id": "msg91-abc123",
  "provider": "msg91",
  "status": "sent",
  "to": "+919876543210",
  "body": "Your Auroville election OTP is 4821. Valid for 10 minutes."
}
```

With explicit provider override:
```bash
curl -X POST http://localhost:8000/sms/send \
  -H "Authorization: Bearer $SMS_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+15551234567", "body": "Ballot link: https://vote.auroville.org/b/abc123", "provider": "telnyx"}'
```

### POST /sms/batch — Send Batch SMS

```bash
curl -X POST http://localhost:8000/sms/batch \
  -H "Authorization: Bearer $SMS_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"to": "+919876543210", "body": "OTP: 4821"},
      {"to": "+15551234567", "body": "Ballot link: https://vote.auroville.org/b/xyz"}
    ]
  }'
```

**Response:**
```json
{
  "total": 2,
  "sent": 2,
  "failed": 0,
  "results": [...]
}
```

### GET /sms/status/{message_id} — Check Delivery Status

```bash
curl http://localhost:8000/sms/status/telnyx-msg-123 \
  -H "Authorization: Bearer $SMS_GATEWAY_TOKEN"
```

### GET /health — Gateway Health (no auth)

```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "providers": {
    "telnyx": {"provider": "telnyx", "healthy": true, "latency_ms": 45.2},
    "msg91": {"provider": "msg91", "healthy": true, "latency_ms": 120.5}
  }
}
```

### GET /balance — Provider Balances

```bash
curl http://localhost:8000/balance \
  -H "Authorization: Bearer $SMS_GATEWAY_TOKEN"
```

## Provider Comparison

| Feature | Telnyx | MSG91 |
|---|---|---|
| Coverage | International (global) | India domestic |
| API Auth | Bearer token | Auth key header |
| Compliance | Standard telecom regs | TRAI/DLT compliant |
| Delivery callbacks | Webhook support | Polling + webhook |
| Pricing model | Per-message (USD) | Per-SMS credits (INR) |
| Rate limits | High (enterprise) | Moderate |
| Best for | International voters | Indian voters (+91) |
| Sender ID | Dynamic (number-based) | Fixed 6-char alpha |

## Routing Modes

| Mode | Behavior |
|---|---|
| `auto` (default) | +91 → MSG91, everything else → Telnyx |
| `telnyx` | All messages via Telnyx |
| `msg91` | All messages via MSG91 |
| `fallback` | Try primary (default: Telnyx), if fails try secondary (MSG91) |

## India-Specific Notes (TRAI/DLT Compliance)

India's TRAI (Telecom Regulatory Authority of India) mandates that all commercial SMS traffic must be registered under the **Distributed Ledger Technology (DLT)** platform. Key requirements:

1. **Sender ID Registration**: The 6-character sender ID (e.g., `AUROVL`) must be pre-approved on a DLT platform.
2. **Template Registration**: Every message template must be registered and approved. The `MSG91_DLT_TEMPLATE_ID` env var references this approved template.
3. **Content Matching**: The SMS body must match the registered template. Variable substitutions (like OTP values) are allowed within registered placeholder positions.
4. **Transactional Route**: Election OTPs qualify as transactional SMS (route `4`), which has higher delivery priority than promotional SMS.
5. **Scrubbing**: MSG91 handles TRAI scrubbing internally, but the template ID must be valid to avoid message rejection.

For the Auroville election system, ensure:
- The OTP template is registered on DLT before going live
- The sender ID `AUROVL` is approved
- Test with real Indian numbers before election day

## Development

### Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use `httpx.MockTransport` for mocking provider API calls — no real HTTP requests are made. Coverage target is ≥80%.

### Project Structure

```
sms-gateway/
├── sms_gateway/
│   ├── __init__.py
│   ├── config.py          # Environment-driven configuration
│   ├── models.py          # Pydantic models
│   ├── base.py            # SMSGateway abstract base class
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── telnyx.py      # Telnyx adapter
│   │   └── msg91.py       # MSG91 adapter
│   ├── router.py          # Auto-routing logic
│   ├── app.py             # FastAPI HTTP service
│   ├── rate_limiter.py    # Token bucket rate limiter
│   └── logging_config.py  # Structured JSON logging
├── tests/
│   ├── test_router.py     # Routing logic tests
│   ├── test_telnyx.py     # Telnyx adapter tests
│   ├── test_msg91.py      # MSG91 adapter tests
│   ├── test_app.py        # API endpoint tests
│   └── test_rate_limiter.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
└── README.md
```

## License

Part of the Auroville auditable-voting project.