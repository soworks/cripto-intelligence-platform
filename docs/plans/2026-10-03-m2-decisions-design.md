# M2 slice 1 — Decisions design

**Date:** 2026-10-03
**Status:** Approved 2026-10-03
**Scope:** Record the owner decisions and load them through the fail-closed policy. No market adapter, no history ingest, no backtest, no scoring.

## Goal

`policies/investment-policy.yaml` at schema version 2 is the only source of the capital plan, venue, fee, tax residency, data budget, and strategy kill rule. Two accepted ADRs state the same decisions in prose. Loading any other shape fails closed.

## Owner record

| Decision | Value |
|---|---|
| Starting portfolio | 650 USD, approximate until the Coinbase → Binance inventory arrives |
| Inventory due | 2026-10-05 |
| Monthly contribution | 800 USD |
| Contribution split | core 0.70, discovery 0.20, reserve 0.10 |
| Core mix | BTCUSDT 0.70, ETHUSDT 0.30 of the core sleeve |
| Tax residency | CO |
| Cost basis | FIFO |
| Paid market data | 0 USD per month |
| Spot fee | 0.00075 maker and 0.00075 taker (Binance VIP 0, fees paid in BNB) |
| Execution venue | binance.com |
| Market data base URL | https://data-api.binance.vision |

Derived dollars, computed from the contribution and the fractions, never stored a second time:

| Sleeve | USD per month |
|---|---|
| Core | 560 |
| of which BTCUSDT | 392 |
| of which ETHUSDT | 168 |
| Discovery | 160 |
| USDC reserve | 80 |

The 650 USD starting balance is unallocated cash. It is not split across sleeves. The 70/20/10 split applies only to each monthly contribution.

The public GitHub repository already settled the GitHub-plan question. This slice does not reopen it.

## Documents

- Create `docs/adr/0009-venue-and-jurisdiction.md`. ADR-0008 is the Terraform-modules decision, so the venue ADR uses 0009, matching the roadmap's venue number.
- Create `docs/adr/0010-strategy-spec.md`. The roadmap had reserved 0008 for the strategy; that number is taken.
- Modify `policies/investment-policy.yaml` to schema version 2.
- Modify `src/cip/domain/policy.py` and `tests/unit/domain/test_policy.py`.
- Mark the settled rows in `docs/plans/2026-10-03-roadmap.md` under "Open owner inputs". Leave the holdings inventory unchecked, with the due date 2026-10-05.

## ADR-0009 — Venue and jurisdiction

Status: Accepted (2026-10-03).

- The execution venue is Binance.com. Binance.US is excluded.
- The owner is a Colombian tax resident. Fill records, when they exist, use FIFO. This ADR does not give tax advice and does not choose between *ganancia ocasional* and ordinary income.
- Every simulation uses a maker fee rate of 0.00075 and a taker fee rate of 0.00075, the Binance spot VIP 0 schedule with fees paid in BNB.
- Market data is read from `https://data-api.binance.vision` in us-east-1, as ADR-0003 already decided. Signed account and order calls stay behind the sa-east-1 gateway and are out of scope until M7.
- The paid-data budget is 0 USD per month. Unlock calendars are checked by hand on the approval card. That card is not built in this slice.

## ADR-0010 — Strategy spec

Status: Accepted (2026-10-03).

One strategy:

> Regime-filtered momentum in liquid Binance USDT alts versus BTC. No new discovery entries while the regime is risk-off. Holds last 14 to 56 days (2–8 weeks). The discovery sleeve stops if, after 40 closed trades or 6 months, it trails both a random-selection baseline and BTC dollar-cost averaging, net of the ADR-0009 fees. Stopped discovery capital is added to the core sleeve.

Core sleeve: a weekly BTC/ETH dollar-cost average at the 70/30 mix above. No LLM and no score. Rebalancing rules, the regime classifier, exits, and the kill-rule evaluator are later slices. This ADR only commits the numbers and the rule.

Operating mode remains SHADOW. This slice adds no order code and no scoring code.

## Policy schema v2

`schema_version` is the integer `2`. Version `1` is rejected. Existing sections `universe`, `discovery`, `trading`, `risk`, `ai`, and `execution` stay as they are, including the Phase 1 placeholder risk caps (`max_trade_usd` 75, `max_daily_trade_usd` 150, `max_monthly_trade_usd` 750) and `discovery_max_portfolio_pct` of 0.10. Those caps are not the sleeve budgets, and 0.10 is not the 0.20 discovery contribution. The sizing slice replaces the caps.

New sections, all required:

```yaml
portfolio:
  core_assets: [BTCUSDT, ETHUSDT]
  discovery_max_portfolio_pct: 0.10
  starting_value_usd: 650
  holdings_are_approximate: true
  holdings_detail_due: 2026-10-05
  monthly_contribution_usd: 800
  contribution_split:
    core: 0.70
    discovery: 0.20
    reserve: 0.10
  core_mix:
    BTCUSDT: 0.70
    ETHUSDT: 0.30

venue:
  execution_venue: binance.com
  market_data_base_url: https://data-api.binance.vision
  maker_fee_rate: 0.00075
  taker_fee_rate: 0.00075

tax:
  residency: CO
  cost_basis_method: FIFO

data:
  monthly_budget_usd: 0

strategy:
  bar_interval: 1d
  bar_close: "00:00:00Z"
  min_holding_days: 14
  max_holding_days: 56
  kill_after_closed_trades: 40
  kill_after_months: 6
```

Units follow Phase 1. Contribution and core-mix values are fractions. Fee rates are fractions of notional (`0.00075` = 0.075%). `monthly_budget_usd` is an integer. `holdings_detail_due` is a calendar date.

### Loader rules

Fail closed, same as today: a missing file, duplicate YAML key, unknown field, or invalid value raises `PolicyError`. The SHA-256 version is still the hash of the raw file bytes.

- `execution_venue` is only `binance.com`.
- `market_data_base_url` is only `https://data-api.binance.vision`.
- `residency` is only `CO`.
- `cost_basis_method` is only `FIFO`.
- `monthly_budget_usd` is only the integer `0`. A float `0.0` is rejected.
- `bar_interval` is only `1d`. `bar_close` is only `00:00:00Z`.
- `starting_value_usd` and `monthly_contribution_usd` are greater than 0 and at most 1_000_000.
- `maker_fee_rate` and `taker_fee_rate` are greater than 0 and at most 0.01.
- `contribution_split` has exactly the keys `core`, `discovery`, and `reserve`. Each value is in `(0, 1]`. The three values sum to 1 within an absolute tolerance of `1e-9`.
- `core_mix` has the same keys as `portfolio.core_assets`, compared as a set. Each value is in `(0, 1]`. The values sum to 1 within `1e-9`.
- `holdings_detail_due` is required while `holdings_are_approximate` is true. It may be omitted when `holdings_are_approximate` is false.
- `min_holding_days` is at least 1. `max_holding_days` is greater than or equal to `min_holding_days` and at most 3650.
- `kill_after_closed_trades` is at least 1 and at most 100_000. `kill_after_months` is at least 1 and at most 120.

`PortfolioPolicy` exposes these read-only derived amounts as `Decimal` values quantized to cents, half up. Products use `Decimal` constructed from the canonical decimal strings of the inputs, so binary float error cannot change a cent.

- `core_monthly_usd` = 560.00
- `discovery_monthly_usd` = 160.00
- `reserve_monthly_usd` = 80.00
- `core_btc_monthly_usd` = 392.00
- `core_eth_monthly_usd` = 168.00

Tests assert those five amounts on the repository file.

## Tests

Extend `tests/unit/domain/test_policy.py`. Keep the current fail-closed cases. Add one rejection test for each rule in the loader section above, plus one test that the repository file yields the derived USD amounts and `schema_version == 2`. A boolean `schema_version` stays rejected. Coverage stays at 100% branch coverage.

## Out of scope

- `BinanceMarketAdapter`, fixtures, and the dev smoke test (slice 2).
- Delisted kline ingest and the point-in-time universe (slice 3).
- The backtest harness and benchmarks (slice 4).
- Regime classifier, exit rules, sizing formula, circuit breakers, fundamentals gates, and approval TTL. Those hypothesis blocks land with the code that enforces them.
- Replacing the Phase 1 `risk` caps.
- Coin-level balances. The follow-up is a policy PR after the 2026-10-05 transfer, setting `holdings_are_approximate` to false and replacing `starting_value_usd` with the counted total.
- Bedrock, Telegram, and any order path.

## Later slices

Slice 2 reads `venue.market_data_base_url` and `venue` fee rates. Slice 4 reads the contribution, the core mix, the fee rates, and the kill thresholds. Neither slice may hard-code those numbers.
