# M4 — Position lifecycle and shadow portfolio

**Date:** 2026-10-05
**Status:** Slices 1–3 are merged ([#54](https://github.com/soworks/crypto-intelligence-platform/issues/54), [#57](https://github.com/soworks/crypto-intelligence-platform/issues/57), [#59](https://github.com/soworks/crypto-intelligence-platform/issues/59)). Slice 4 is sizing. [#22](https://github.com/soworks/crypto-intelligence-platform/issues/22) stays open. No live orders. Score v2 weights stay unfrozen. [#21](https://github.com/soworks/crypto-intelligence-platform/issues/21) stays open.

The roadmap in `docs/plans/2026-10-03-roadmap.md` is the requirements source. This plan only sequences the work. [#22](https://github.com/soworks/crypto-intelligence-platform/issues/22) is the parent. Implementation issues do not complete it.

## Order

Each slice is one implementation issue. Later slices call the portfolio book and the M3 decision record. They do not grow a second copy of eligibility or scoring, and they do not place an order.

1. **Portfolio book.** Point-in-time sleeves and manual off-exchange holdings. [#54](https://github.com/soworks/crypto-intelligence-platform/issues/54). Merged.
2. **Position state machine.** `PROPOSED` through `CLOSED`. Every transition is a ledger event. No order id. [#57](https://github.com/soworks/crypto-intelligence-platform/issues/57). Merged.
3. **Exit rules.** ATR stop, partial take-profit, chandelier, time stop, max holding, and the forced-exit triggers already in policy. A rule names an exit. It does not invent a fill. [#59](https://github.com/soworks/crypto-intelligence-platform/issues/59). Merged.
4. **Sizing.** Risk-per-trade, stop distance, beta, regime multiplier, and the minimum position. Below the minimum, no size. A size is not an order. This slice.
5. **Limits and circuit breakers.** Open-position caps, sector cap, beta-weighted exposure, drawdown halts, loss streak, re-entry cooldown, monthly loss halt.
6. **Symbol filter.** Live `exchangeInfo` filters and an exit simulation after fees. A failed filter skips the name.
7. **Shadow fills.** Next-bar fills, fees, spread, slippage, partial fills. Sells are not blocked by a buy budget.
8. **Core sleeve.** Weekly BTC/ETH mix from the existing DCA engine, on the core budget only. The reserve is USDC until a later rule deploys it.
9. **Manual pilot executions.** Owner-executed October purchases recorded on `ALPHA_PILOT_2026_10`. They are not executor orders.
10. **Engine Assurance Scorecard.** Four dimensions, reported separately, read from stored records. A recommendation is not a grade from absolute P&L.
11. **Prod shadow.** Shared Terraform module, prod stack, and the shadow clock. An execution attempt while SHADOW alarms.

Holdings stay approximate until the owner supplies the inventory. Do not invent quantities. `max_holding_days` stays 56. October capital is not the monthly budget.

## Slice 1

`open_book` copies the policy sleeve budgets and `holdings_are_approximate`. It does not turn `starting_value_usd` or `core_mix` into quantities. An empty holding list is valid. A manual line needs a symbol, a positive quantity, venue `off_exchange`, and a provenance string. Sleeve cash is a stored decimal or missing. Missing is not zero. An empty inventory cannot be marked definitive.

The file is `portfolio/date={session}/book.json`. The same bytes are a no-op. A different payload is refused. The document has no order id.

## Out of slice 1

Sizing, stops, the state machine, shadow fills, the scorecard, live execution, and Score v2 weights.

## Slice 2

A position follows one decision. `position_id` is the SHA-256 of that decision id. It is not an order id. The record has no quantity and no notional.

The states are `PROPOSED`, `APPROVED`, `ENTRY_PENDING`, `OPEN`, `PARTIAL_EXIT`, `EXIT_PENDING`, and `CLOSED`. A proposal that is not taken can close without opening. An open position reaches `CLOSED` only through `EXIT_PENDING`. `PARTIAL_EXIT` is optional. `CLOSED` has no next state. A move cannot change the decision, the symbol, or the cohort.

The state item is `PK=POSITION#<id>`, `SK=STATE`. Each move is one `POSITION_TRANSITIONED` ledger event in the same transaction. Replaying that move is a duplicate event. A conflicting state is refused. The payload has `from_state`, `to_state`, and `reason`. It has no order id.

## Out of slice 2

Exit rules, sizing, limits, shadow fills, the hourly monitor, the scorecard, live execution, and Score v2 weights.

## Slice 3

`name_exit` reads the policy exit block and caller-supplied facts: entry, mark, highest price, ATR, days held, return versus BTC, and which forced triggers were observed. It returns one rule name or no exit. No exit is a hold. The result has no price, quantity, or order id, and it does not change position state.

The initial stop distance is the tighter of `initial_stop_atr_mult` times ATR and `initial_stop_max_pct` of entry. The chandelier turns on after the high reaches `trailing_activate_after_r` and then sits `trailing_atr_mult` ATRs under that high. If both stops are touched, the higher stop is the one named. A partial is named only for an `OPEN` position that has not already taken one, once the mark is `partial_take_profit_r` above the stop distance. The time stop needs both `time_stop_days` and a BTC-relative return at or below the ceiling. Day `max_holding_days` (56) names the max hold.

Order when several are true: forced trigger, binding stop, max hold, time stop, partial. Forced triggers are reported in policy order. An exit is named only for `OPEN` or `PARTIAL_EXIT`.

## Out of slice 3

Sizing, limits, shadow fills, the hourly monitor, the scorecard, live execution, and Score v2 weights. Applying the name as a position transition is a later caller.

## Slice 4

`size_position` uses the caller’s portfolio value. It does not read `starting_value_usd`. Risk is `risk_per_trade_pct_of_portfolio` times that value. The uncapped size is risk divided by the stop distance, which is a fraction. The result is the minimum of that amount, `max_trade_usd`, and `max_discovery_asset_pct` times the portfolio, then multiplied by the published regime’s `size_mult` and divided by `max(beta, 1)`. A beta below 1 does not increase the size.

The minimum is `max(min_position_usd_floor, min_notional_multiple * symbol_min_notional)`. Below that minimum, `size_usd` is absent. `RISK_OFF` and a missing regime are also absent, not zero. The document is `size_usd`, `minimum_usd`, and `reason`. It has no quantity and no order id.

## Out of slice 4

Limits, shadow fills, the hourly monitor, the scorecard, live execution, and Score v2 weights.
