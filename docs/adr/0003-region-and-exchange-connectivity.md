# ADR-0003: Region and exchange connectivity

Status: Accepted (2026-10-03)

## Context
The platform runs in us-east-1 (owner decision). Binance.com geo-restricts US IP
ranges. Spike result (2026-10-03, Lambda python3.13 arm64, HTTP status codes):

| Endpoint | us-east-1 | sa-east-1 |
|---|---|---|
| `api.binance.com/api/v3/exchangeInfo` | 451 | 200 |
| `api.binance.com/api/v3/klines` | 451 | 200 |
| `data-api.binance.vision/api/v3/exchangeInfo` | 200 | 200 |

`data-api.binance.vision` is Binance's public market-data-only endpoint. It serves
the unauthenticated `/api/v3` market endpoints (exchangeInfo, ticker, klines,
depth, trades) and nothing that needs an API key.

## Decision
- **Market data (M2-M6):** all core components, including the market-data adapter,
  run in us-east-1 and call `https://data-api.binance.vision`. No gateway is
  needed for research, scoring, shadow trading, or approvals.
- **Signed endpoints (M7, or earlier if a signed read is required):** account,
  order, and `/sapi` calls (for example `capital/config/getall` for
  deposit/withdrawal status) run in a single `exchange-gateway` Lambda in
  sa-east-1. Core Lambdas invoke it cross-region with `lambda:InvokeFunction` on
  that one ARN. Static egress for Binance API-key IP whitelisting is solved in
  sa-east-1 only.
- `EXCHANGE_GATEWAY_REGION = sa-east-1` (signed endpoints only).
- Binance.US is not used (US persons only; owner is in Colombia).

## Consequences
- The `BinanceMarketAdapter` takes its base URL from configuration. It uses
  `data-api.binance.vision` in AWS and may use `api.binance.com` locally.
- Deposit/withdrawal-suspension checks (gap analysis B6) need either the
  gateway (signed read-only key) or Binance announcements. M3 decides which.
- If `data-api.binance.vision` starts blocking US ranges, the market-data adapter
  moves behind the gateway with no domain-code changes.

## Bedrock findings (same spike)
- `us.anthropic.claude-haiku-4-5-20251001-v1:0` failed: Anthropic use-case
  details have not been submitted for this account (Bedrock console, model access).
- `us.amazon.nova-2-lite-v1:0` was throttled: the account's Bedrock quotas are 0.
  These include cross-region requests and tokens per minute for Claude Haiku 4.5
  and Nova 2 Lite (adjustable), and Nova 2 Lite tokens per day (not adjustable).
- **M5 prerequisite:** submit the Anthropic use-case form and request
  Service Quotas increases for the chosen model's cross-region inference profile.
  Requests can take days, so start them well before M5.
