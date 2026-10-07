# Prospective lane fields

Session 2026-10-06 stays sealed. This capture is for 2026-10-07, before its close, and only for the 154 symbols that already cleared the market-cap floor on that session. It does not change eligibility thresholds. It does not capture spread, depth, volume, or turnover.

The 13:39Z CoinGecko `/coins/markets` body was not retained. The classification files keep `coin_id`, the dated cap, and `last_updated` only. Rank, supply, and FDV are a later pre-close reading of the same endpoint for those stored ids. They are not backdated to 13:39Z. No second ticker map is requested. A symbol whose stored mapping is not unambiguous is not given these fields.

## Supply, rank, and FDV

Source: CoinGecko `/coins/markets` for the stored `coin_id`. Provider id and `last_updated` are kept. `captured_at` is this retrieval.

| Field | Who | Rule |
|---|---|---|
| `circulating_ratio` | All 154 | `circulating_supply / total_supply` on that row. Missing when either supply is missing, or when total supply is zero. Max supply is not a substitute. |
| `market_cap_rank` | 80 normal-lane | The row's rank when it is an integer of 1 or more. Null stays missing. Not stored for the high-risk lane. |
| `fdv_to_market_cap` | 80 normal-lane | `fully_diluted_valuation / market_cap` on that same row. Missing when either value is missing or the cap is zero. The 13:39Z cap is not the denominator. Not stored for the high-risk lane. |

A repeated coin id makes the batch unusable, including when one row has a null supply. A present value without `last_updated` is refused. A null value stays missing.

## History

`history_days` is listing age for the exact Binance symbol. The start date is the UTC open date of the earliest daily kline Binance Vision publishes for that symbol (`/api/v3/klines`, interval `1d`, `startTime=0`, `limit=1`). `history_days` is the number of UTC midnights from that open date to the session date. The session date itself is not a completed day.

This is not the oldest candle in the local store. Local history is incomplete and is not an input. It is not CoinGecko `ath_date`, `atl_date`, or `genesis_date`. It is not Binance marketing `listingTime`: that catalog stamps 198 symbols at 2023-01-01T00:00:00Z, including BTC, ETH, and SOL, whose first daily kline is 2017-08-17.

An empty kline page stays missing. It is not zero. A first open after the session is refused. An open time that is not UTC midnight is refused.

## Unlock schedule

Three states, stored as text:

| State | Meaning | `unlock_schedule_known` |
|---|---|---|
| `future_unlocks` | The source tracks this id and lists an event after the session close. | `true` |
| `no_applicable_future_unlock` | The source tracks this id and lists no event after the session close. | `true` |
| `unavailable` | The source does not track this id, or it returned no schedule. | `null` |

`unavailable` is not `false`. The eligibility reason for `false` is `unlock_schedule_unknown`, which is a decided claim. A provider that returns no rows does not prove that claim.

Only the 74 high-risk symbols record a state. Normal-lane symbols do not.

No free source in this environment publishes that schedule. Tokenomist and CryptoRank were declined on budget. DefiLlama emissions answers HTTP 402 and is not called. The live state is therefore `unavailable` for all 74, with a null provider id and null source timestamp. The retrieval does not invent a schedule.

## What this does not do

Thresholds stay as they are. Session 2026-10-06 is not populated. Symbols under the high-risk floor are not filled. Liquidity gates are not implemented. No production schedule, no daily scan, and no shadow clock is written.
