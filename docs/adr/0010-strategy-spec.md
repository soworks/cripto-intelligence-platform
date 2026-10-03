# ADR-0010: Strategy spec

Status: Accepted (2026-10-03)

## Context
The platform had a scanner and a risk gate, and no statement of what edge it
harvests, how long it holds, or what evidence would retire the discovery sleeve.
ADR-0008 is the Terraform-modules decision, so this strategy spec is ADR-0010.
The roadmap had reserved 0008 for it.

## Decision
One strategy, stored in the policy under `portfolio` and `strategy`:

Regime-filtered momentum in liquid Binance USDT alts versus BTC. No new
discovery entries while the regime is risk-off. Holds last 14 to 56 days. The
discovery sleeve stops if, after 40 closed trades or 6 months, it trails both a
random-selection baseline and BTC dollar-cost averaging, net of the ADR-0009
fees. Stopped discovery capital is added to the core sleeve.

Capital plan, recorded 2026-10-03:

- Starting value 650 USD, held as unallocated cash. The coin inventory is
  approximate until the Coinbase to Binance transfer on 2026-10-05.
- Monthly contribution 800 USD, split core 0.70 (560 USD), discovery 0.20
  (160 USD), and USDC reserve 0.10 (80 USD).
- Core sleeve: weekly BTC/ETH dollar-cost average, 70/30, which is 392 USD of
  BTCUSDT and 168 USD of ETHUSDT from each contribution. No LLM and no score.

The regime classifier, exits, sizing formula, and the kill-rule evaluator are
later slices. This ADR commits the numbers and the rule. Operating mode stays
SHADOW. This decision adds no order code and no scoring code.

## Consequences
- Schema version 2 rejects a policy that omits these fields or breaks their bounds.
- The Phase 1 risk caps (`max_trade_usd` 75 and the daily and monthly caps) are
  not the sleeve budgets. The sizing slice replaces them.
- `discovery_max_portfolio_pct` of 0.10 is not the 0.20 discovery contribution.
- After 2026-10-05, a policy change sets `holdings_are_approximate` to false and
  replaces `starting_value_usd` with the counted total.
