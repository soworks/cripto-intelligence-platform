# Prospective lane fields — 2026-10-07

The cohort is the 154 symbols that cleared the market-cap floor on the 13:39Z classification. Session close is `2026-10-08T00:00:00Z`. This measurement is still before that close. It is not a finalized session and it is not a daily scan.

Source contract: `docs/plans/2026-10-07-prospective-lane-fields.md`.

## Clocks

The 13:39Z `/coins/markets` body was not retained. Rank, circulating supply, and FDV are a later reading of that same endpoint for the stored CoinGecko ids. No second Binance ticker map was requested.

CoinGecko `last_updated`: `2026-10-07T14:21:30Z`. CIP retrieval: `2026-10-07T14:23:39.013550Z`.
History retrievals run from `2026-10-07T14:23:40Z` through `2026-10-07T14:24:42Z`. The start date is the open time of the first daily kline, not the retrieval time.

## What was filled

| Field | Symbols | Filled |
|---|---:|---:|
| `circulating_ratio` | 154 | 154 |
| `history_days` | 154 | 154 |
| `market_cap_rank` | 80 normal | 80 |
| `fdv_to_market_cap` | 80 normal | 80 |
| `unlock_schedule_known` | 74 high-risk | 0 true |

`history_days` runs from 13 to 3338. BTCUSDT starts at `2017-08-17T00:00:00Z`, which is the first Vision daily bar. The marketing catalog's `2023-01-01` stamp was not used.

All 74 high-risk schedules are `unavailable`. Provider id and source timestamp are null. None were stored as `false`, and none received `unlock_schedule_unknown`. DefiLlama emissions returned HTTP 402 and was not used.

## Funnel

**67 of 154** now pass the eligibility lane gates. All 67 are normal-lane. **87** do not.

The 87 rejections, as combinations:

| Symbols | Lane | Reasons |
|---:|---|---|
| 68 | high-risk | `missing_unlock_schedule` |
| 5 | high-risk | `missing_unlock_schedule`, `circulating_ratio_below_minimum` |
| 1 | high-risk | `missing_unlock_schedule`, `history_below_minimum` |
| 7 | normal | `circulating_ratio_below_minimum`, `fdv_to_market_cap_above_maximum` |
| 4 | normal | `history_below_minimum` |
| 1 | normal | `market_cap_rank_above_ceiling` (`BONKUSDT`, rank 151) |
| 1 | normal | `circulating_ratio_below_minimum`, `fdv_to_market_cap_above_maximum`, `history_below_minimum` (`HYPEUSDT`, 13 days) |

No high-risk symbol advances. A known schedule with no future unlock was not observed, because no source tracks these ids.

## Next gate

The 67 that pass eligibility stop on the existing liquidity and manipulation gate. Those inputs were not captured. Each of the 67 is missing median quote volume, day quote volume, median spread, spread snapshots, depth, turnover, volume z-score, price move, spike-candle count, trade-size dispersion, taker-buy ratio, Binance volume share, and the stablecoin peg. That gate is not implemented in this slice.

## Sealed session

2026-10-06 session tree before `e8f97d5b70dc4540ac513c20e66cf66bfa60dafb9dacbc8049cae3238836d249`, after the same hash.
2026-10-06 decision tree before `a5ce59f7e5a5815f73d7cf93d760ed3f47a43e09033cd9d0d124840f35199d5e`, after the same hash.
Neither tree was written. `run_daily_scan` was not called. `prod_decisions_started_at` and `score_v2_shadow_started_at` were not written.
