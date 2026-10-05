# M4 — Position lifecycle and shadow portfolio

**Date:** 2026-10-05
**Status:** Slices 1–7 are merged ([#54](https://github.com/soworks/crypto-intelligence-platform/issues/54), [#57](https://github.com/soworks/crypto-intelligence-platform/issues/57), [#59](https://github.com/soworks/crypto-intelligence-platform/issues/59), [#61](https://github.com/soworks/crypto-intelligence-platform/issues/61), [#63](https://github.com/soworks/crypto-intelligence-platform/issues/63), [#65](https://github.com/soworks/crypto-intelligence-platform/issues/65), [#67](https://github.com/soworks/crypto-intelligence-platform/issues/67)). Slice 8 is merged ([#71](https://github.com/soworks/crypto-intelligence-platform/issues/71)). Slice 9 is merged ([#73](https://github.com/soworks/crypto-intelligence-platform/issues/73)). Slice 10 is the Engine Assurance Scorecard ([#75](https://github.com/soworks/crypto-intelligence-platform/issues/75)). [#22](https://github.com/soworks/crypto-intelligence-platform/issues/22) stays open. No live orders. Score v2 weights stay unfrozen. [#21](https://github.com/soworks/crypto-intelligence-platform/issues/21) stays open.

The roadmap in `docs/plans/2026-10-03-roadmap.md` is the requirements source. This plan only sequences the work. [#22](https://github.com/soworks/crypto-intelligence-platform/issues/22) is the parent. Implementation issues do not complete it.

## Order

Each slice is one implementation issue. Later slices call the portfolio book and the M3 decision record. They do not grow a second copy of eligibility or scoring, and they do not place an order.

1. **Portfolio book.** Point-in-time sleeves and manual off-exchange holdings. [#54](https://github.com/soworks/crypto-intelligence-platform/issues/54). Merged.
2. **Position state machine.** `PROPOSED` through `CLOSED`. Every transition is a ledger event. No order id. [#57](https://github.com/soworks/crypto-intelligence-platform/issues/57). Merged.
3. **Exit rules.** ATR stop, partial take-profit, chandelier, time stop, max holding, and the forced-exit triggers already in policy. A rule names an exit. It does not invent a fill. [#59](https://github.com/soworks/crypto-intelligence-platform/issues/59). Merged.
4. **Sizing.** Risk-per-trade, stop distance, beta, regime multiplier, and the minimum position. Below the minimum, no size. A size is not an order. [#61](https://github.com/soworks/crypto-intelligence-platform/issues/61). Merged.
5. **Limits and circuit breakers.** Open-position caps, sector cap, beta-weighted exposure, drawdown halts, loss streak, re-entry cooldown, monthly loss halt. [#63](https://github.com/soworks/crypto-intelligence-platform/issues/63). Merged.
6. **Symbol filter.** Live `exchangeInfo` filters and an exit simulation after fees. A failed filter skips the name. [#65](https://github.com/soworks/crypto-intelligence-platform/issues/65). Merged.
7. **Shadow fills.** Next-bar fills, fees, spread, slippage, partial fills. Sells are not blocked by a buy budget. [#67](https://github.com/soworks/crypto-intelligence-platform/issues/67). Merged.
8. **Core sleeve.** Weekly BTC/ETH mix from the existing DCA engine, on the core budget only. The reserve is USDC until a later rule deploys it. [#71](https://github.com/soworks/crypto-intelligence-platform/issues/71). Merged.
9. **Manual pilot executions.** Owner-executed October purchases recorded on `ALPHA_PILOT_2026_10`. They are not executor orders. [#73](https://github.com/soworks/crypto-intelligence-platform/issues/73). Merged.
10. **Engine Assurance Scorecard.** Four dimensions, reported separately, read from stored records. A recommendation is not a grade from absolute P&L. [#75](https://github.com/soworks/crypto-intelligence-platform/issues/75). This slice.
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

## Slice 5

`admit_entry` reads the published limits and circuit breakers. The caller supplies the open discovery lines, the proposed name, sector, beta, and size, the sleeve drawdown from its high-water mark, the loss streak and the days since the last loss, the month's realized loss as a fraction of the caller's portfolio, and the days since this symbol last closed. A missing fact is refused. It is not invented as zero.

A new entry is allowed only when every check is clear. The reasons, in order, are `review_and_flatten`, `halt_new_entries`, `loss_streak`, `monthly_loss`, `open_position_cap`, `sector_cap`, `beta_exposure`, `averaging_down`, `reentry_cooldown`, and `no_size`. Drawdown at the halt blocks new entries. Drawdown at the flatten level also names a review. That name is not a sell. Three consecutive losses block until `pause_days` have elapsed, and an unknown clock stays blocked. Five open discovery lines block another. Two lines in the candidate's sector block a third. Beta-weighted dollars above the exposure cap block; an equal weight is allowed. A symbol that is already open is averaging down, which the policy forbids. A symbol that closed fewer than the cooldown ago is blocked. An absent size is blocked. The document is `allowed`, `flatten`, and `reasons`. It has no order id.

## Out of slice 5

The symbol filter, shadow fills, the hourly monitor, the scorecard, live execution, and Score v2 weights. Applying a halt as a position transition is a later caller. Daily and monthly trade-dollar counters stay a separate roadmap item.

## Slice 6

`screen_symbol` reads one symbol from the caller's current `exchangeInfo`. It does not fetch the exchange. A name passes only when it is `TRADING`, quoted in USDT, spot-allowed, and lists `LIMIT`, `LIMIT_MAKER`, and `STOP_LOSS_LIMIT`. The required filters are `PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `PERCENT_PRICE_BY_SIDE`, `MAX_NUM_ALGO_ORDERS`, and `NOTIONAL` or `MIN_NOTIONAL`. Quantity rounds down to the lot step. A zero step stays unrounded. The entry and the exit price must sit on the tick and inside the percent-price band around the caller's reference price. The quantity must also conform to the market lot. Notional uses the rounded quantity. After the taker fee, the remaining base must clear a full exit and a partial exit of `partial_take_profit_fraction` at the exit price, each net of the taker fee and at least the minimum notional. An algo cap below one skips the name. The document is `passed`, `reasons`, and `quantity`. Quantity is absent when the name is skipped. It is not an order id.

## Out of slice 6

Shadow fills, paying fees in BNB, dust conversion, the hourly monitor, the scorecard, live execution, and Score v2 weights.

## Slice 7

`shadow_fill` prices one buy or one sell at the next session's open. There is no fill when that open is missing. The cost is the published taker fee plus half the caller's spread plus the caller's slippage, the same drag the replay uses. A buy pays the open times one plus that drag. A sell receives the open times one minus that drag. The drag must stay below one. A filled quantity below the request is partial. A zero quantity is unfilled. A buy that would push the day's or the month's buy spend above `max_daily_trade_usd` or `max_monthly_trade_usd` is not filled. A sell is filled anyway, and it does not spend the buy budget. The document is `side`, `reason`, `fill_price`, `quantity`, `fee_drag_usd`, and `budget_usd`. It has no order id.

## Out of slice 7

Paying fees in BNB, dust conversion, the hourly monitor, the scorecard, live execution, and Score v2 weights. Applying the fill as a position transition is a later caller.

## Slice 8

`plan_core_week` splits `core_monthly_usd` across the caller's week count, then splits that week's dollars with the DCA engine's `allocate` and `core_mix`. It does not read `starting_value_usd`. It does not spend the discovery budget. `reserve_deployed_usd` is zero: the reserve stays USDC. The document is `week_index`, `week_count`, `btc_usd`, `eth_usd`, and `reserve_deployed_usd`. It has no quantity and no order id.

The week index is 1-based and must fall inside the count. The first weeks take an equal cent share of the monthly core, rounded half up. The last week takes the residual, so the weeks still sum to the monthly core. Each week is allocated on its own, so a cent can move between BTC and ETH across the month. On the repository policy, four weeks are 98.00 BTC and 42.00 ETH, and those four weeks sum to 392.00 and 168.00. One week deploys the whole core month. A count below one, an index outside that count, or a share that rounds to nothing is refused. Publishing the document recomputes that week from the policy. A different mix, or a different week count, is refused even when the two legs still add up. The taker fee is not charged here, because this note does not turn the dollars into coins.

## Out of slice 8

Band rebalancing, reserve deployment, coin quantities, a shadow fill of the core legs, the hourly monitor, the scorecard, live execution, and Score v2 weights. October pilot capital is not this budget.

## Slice 9

`record_pilot_execution` records a purchase the owner already made. The cohort is `ALPHA_PILOT_2026_10`. The day must fall on 2026-10-19 through 2026-10-31. The caller supplies the decision id, the symbol, the base quantity, the quote dollars, how much pilot capital was already recorded, and a provenance string. The cap is 500 USD. The monthly 800 is not this cap. A purchase that would pass 500 is refused. The document is the cohort, decision id, symbol, day, quantity, quote, and provenance. It has no order id. It does not move a position.

## Out of slice 9

Live executor orders, position transitions, inventing the owner's inventory, the scorecard, and Score v2 weights. The 800 USD monthly contribution is not pilot capital.

## Slice 10

`report_scorecard` places stored results into four sections: decision integrity, signal quality, trade quality, and portfolio quality. It does not compute a grade, and it does not write score weights. A missing count is refused. It is not stored as zero.

Signal quality keeps each stored outcome. Excess is the absolute return minus the BTC return over the same horizon. An 8% loss while BTC fell 18% (excess +10%) stays a different row from an 8% loss while BTC rose 10% (excess -18%). A coefficient outside -1 to 1 is refused, so a score cannot be filed as a coefficient. The same decision and horizon cannot be listed twice.

Trade quality with an empty list says `no_trades`. It does not invent round trips. A listed trip has its realized R, MAE, MFE, fees, and slippage. Expectancy, payoff, and profit factor stay out of this slice until a formula is published. Portfolio comparisons are named one by one. A figure the caller does not have is null. Null is not zero. A signed zero is refused, so the document does not print `-0`.

The document keys are the four sections. There is no grade and no order id.

## Out of slice 10

Recomputing Sharpe, the information-coefficient study, or forward outcomes. A weekly report job. Score v2 weights. Live execution. The roadmap checkbox stays open because those formulas and the weekly job are not this function.
