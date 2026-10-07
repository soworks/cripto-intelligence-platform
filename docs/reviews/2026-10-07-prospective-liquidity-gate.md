# Prospective liquidity capture — 2026-10-07

The cohort is the 67 normal-lane symbols that passed the eligibility lane gates. Session close is `2026-10-08T00:00:00Z`. This measurement is still before that close. It is not a finalized session and it is not a daily scan.

Source contract: `docs/plans/2026-10-07-prospective-liquidity-capture.md`.

## Clocks

CoinGecko `market_cap` and `total_volume` are the retained 14:21:30Z `/coins/markets` body. That endpoint was not requested again.

| Evidence | When |
|---|---|
| CoinGecko `last_updated` | `2026-10-07T14:21:30Z` |
| USDCUSDT 1h retrieval | `2026-10-07T14:55:22.234019Z` |
| Binance 24h ticker retrieval | `2026-10-07T14:55:25.970898Z` |
| Daily klines and books | `2026-10-07T14:55:26.943136Z` through `2026-10-07T14:57:44.410802Z` |

Each symbol has 31 consecutive daily bars from `2026-09-06` through `2026-10-06`. The open session bar, `2026-10-07`, is not in the series. Each book is one snapshot. The USDCUSDT reading kept 47 hourly closes whose candle had already finished.

## What was filled

| Field | Filled | Missing |
|---|---:|---:|
| `median_quote_volume_30d_usd` | 67 | 0 |
| `day_quote_volume_usd` | 67 | 0 |
| `median_spread_bps` | 67 | 0 |
| `spread_snapshots` | 67, every count is 1 | 0 |
| `depth_usd_per_side` | 67 | 0 |
| `turnover` | 67 | 0 |
| `volume_zscore` | 67 | 0 |
| `price_move` | 67 | 0 |
| `trade_size_stdev` | 67 | 0 |
| `binance_volume_share` | 67 | 0 |
| `stablecoin_peg_deviation` and `peg_deviation_hours` | 67 | 0 |
| `taker_buy_ratio` | 0 | 67 |
| `spike_candle_count` | 0 | 67 |

`taker_buy_ratio` stays missing because the session bar has not closed. Closed bars keep their taker volume in the raw series and are not copied into that field. `spike_candle_count` stays missing because the repository has no formula for which candle is a spike. Neither field was stored as zero.

The peg observation is a deviation of `0.00026` and `0` hours beyond `0.005`. Those are readings of the 47 closed hours, not a stand-in for a failed fetch. `manipulation_blocked_until` stays null. The gate does not treat that null as a missing input.

## Funnel

**67 entering liquidity → 0 passing liquidity/manipulation.**

Every symbol carries `spread_snapshots_below_minimum`, `missing_taker_buy_ratio`, and `missing_spike_candle_count`. The snapshot minimum is 6. This slice stored the one pre-close book and did not take more snapshots to reach that count.

The 67 rejections, as combinations:

| Symbols | Reasons |
|---:|---|
| 16 | `spread_snapshots_below_minimum`, `missing_taker_buy_ratio`, `missing_spike_candle_count` |
| 11 | those three, plus `turnover_below_minimum`, `binance_volume_share_outside_band` |
| 4 | those three, plus `median_quote_volume_below_minimum`, `day_quote_volume_below_minimum` |
| 4 | those three, plus `median_quote_volume_below_minimum`, `day_quote_volume_below_minimum`, `turnover_below_minimum` |
| 3 | those three, plus `median_quote_volume_below_minimum`, `day_quote_volume_below_minimum`, `turnover_below_minimum`, `binance_volume_share_outside_band` |
| 3 | those three, plus `median_quote_volume_below_minimum` |
| 3 | those three, plus `binance_volume_share_outside_band` |
| 3 | those three, plus `turnover_below_minimum` |
| 2 | those three, plus `median_quote_volume_below_minimum`, `day_quote_volume_below_minimum`, `median_spread_above_maximum`, `turnover_below_minimum`, `binance_volume_share_outside_band` |
| 2 | those three, plus `median_quote_volume_below_minimum`, `day_quote_volume_below_minimum`, `depth_below_minimum` |
| 2 | those three, plus `median_quote_volume_below_minimum`, `day_quote_volume_below_minimum`, `depth_below_minimum`, `turnover_below_minimum` |
| 2 | those three, plus `trade_size_outlier` |
| 1 | `DCRUSDT`: those three, plus median and day volume, spread, depth, and turnover |
| 1 | `GNOUSDT`: the `DCRUSDT` set, plus `binance_volume_share_outside_band` |
| 1 | `NEXOUSDT`: those three, plus median and day volume, spread, and turnover |
| 1 | `LDOUSDT`: those three, plus median and day volume, depth, and Binance share |
| 1 | `ATOMUSDT`: the `LDOUSDT` set, plus turnover |
| 1 | `PENDLEUSDT`: those three, plus median and day volume, and Binance share |
| 1 | `SHIBUSDT`: those three, plus median volume, spread, turnover, and Binance share |
| 1 | `RAYUSDT`: those three, plus median volume and depth |
| 1 | `POLUSDT`: those three, plus median volume, depth, turnover, and Binance share |
| 1 | `ICPUSDT`: those three, plus median volume and turnover |
| 1 | `JUPUSDT`: the `ICPUSDT` set, plus Binance share |
| 1 | `PEPEUSDT`: those three, plus `median_spread_above_maximum` |

The 11-symbol turnover and share combination is `ADAUSDT`, `BCHUSDT`, `BTCUSDT`, `DOTUSDT`, `ETHUSDT`, `LINKUSDT`, `LTCUSDT`, `SOLUSDT`, `TRXUSDT`, `XLMUSDT`, `XRPUSDT`. BTCUSDT's 30-day median quote volume is about `$1.20B` and its thinner-side depth is about `$798k`. Its turnover is about `0.0011` and its Binance share of the retained CoinGecko aggregate is about `0.046`. Both sit outside the existing bands. The thresholds were not changed.

No symbol was rejected for a missing median, missing day volume, missing spread, missing depth, missing turnover, missing z-score, missing price move, missing trade-size distance, missing Binance share, or a missing peg. `wash_volume`, `taker_buy_vertical`, `spike_candles`, `manipulation_turnover`, `stablecoin_peg`, and `manipulation_block` did not occur.

## Next gate

No symbol passed liquidity and manipulation, so none reached the fundamental gate. The next-gate distribution is empty. That gate was not implemented.

## Sealed session

2026-10-06 session tree before `e8f97d5b70dc4540ac513c20e66cf66bfa60dafb9dacbc8049cae3238836d249`, after the same hash.
2026-10-06 decision tree before `a5ce59f7e5a5815f73d7cf93d760ed3f47a43e09033cd9d0d124840f35199d5e`, after the same hash.
The 2026-10-07 classification and lane trees were read and not rewritten. `run_daily_scan` was not called. `prod_decisions_started_at` and `score_v2_shadow_started_at` were not written.
