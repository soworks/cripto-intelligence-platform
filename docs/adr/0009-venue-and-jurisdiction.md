# ADR-0009: Venue and jurisdiction

Status: Accepted (2026-10-03)

## Context
The owner trades from Colombia. Binance.US serves US persons only. ADR-0003 already
sends public market data to `data-api.binance.vision` from us-east-1 and leaves
signed account and order calls for an sa-east-1 gateway at M7. Fee, tax residency,
and the data budget were still unrecorded, so later simulations could invent them.

## Decision
- The execution venue is Binance.com. Binance.US is not used.
- The owner is a Colombian tax resident. When fill records exist, lots use FIFO.
  This decision does not choose between *ganancia ocasional* and ordinary income.
- Simulations use a maker fee of 0.00075 and a taker fee of 0.00075, Binance spot
  VIP 0 with fees paid in BNB.
- Market data is read from `https://data-api.binance.vision`.
- The paid-data budget is 0 USD per month. Unlock calendars are checked by hand
  on the approval card, which is built in a later milestone.
- These values live in `policies/investment-policy.yaml` (`venue`, `tax`, `data`).
  Code reads them from the loaded policy.

## Consequences
- Slice 2's market adapter takes its base URL and fee rates from the policy.
- Signed endpoints stay behind the M7 gateway. This ADR does not create an API key.
- A change of venue, residency, fee tier, or a non-zero data budget is a new
  policy version, reviewed in a pull request.
