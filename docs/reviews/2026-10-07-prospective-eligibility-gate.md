# Prospective eligibility gate — 2026-10-07

Retrieved `2026-10-07T13:39:01.162294+00:00`. Session close is `2026-10-08T00:00:00Z`.
This is a pre-close measurement. It is not a finalized session and it is not a daily scan.

## Sealed session

2026-10-06 session tree before `e8f97d5b70dc4540ac513c20e66cf66bfa60dafb9dacbc8049cae3238836d249`, after `e8f97d5b70dc4540ac513c20e66cf66bfa60dafb9dacbc8049cae3238836d249`.
2026-10-06 decision tree before `a5ce59f7e5a5815f73d7cf93d760ed3f47a43e09033cd9d0d124840f35199d5e`, after `a5ce59f7e5a5815f73d7cf93d760ed3f47a43e09033cd9d0d124840f35199d5e`.
Neither tree was written.

## Universe

Trading USDT symbols: **504**.
CoinGecko Binance ticker pages completed: `True`.
EUR-stablecoin category completed: `True`.
CoinGecko market-cap batch errors: `0`.
CMC quotes were not requested. No CMC API key is configured, so every CMC cap stays missing.

## Classification coverage

| Flag | True | False | Missing |
|---|---:|---:|---:|
| `eur_stable` | 1 | 497 | 6 |
| `fan_token` | 13 | 491 | 0 |
| `monitoring_tag` | 32 | 472 | 0 |
| `delisting` | 0 | 504 | 0 |
| `deposits_suspended` | 3 | 501 | 0 |
| `withdrawals_suspended` | 3 | 501 | 0 |
| `pending_migration` | 0 | 504 | 0 |

## Market cap

Unambiguous CoinGecko ids: **498** (98.8%).
Ambiguous pairs: **0**. Unmapped pairs: **6**.
Dated CoinGecko `market_cap_usd`: **498** (98.8%).
Unambiguous CMC ids with no quote: **503**. Dated CMC caps: **0**.

The six unmapped pairs have no CoinGecko id on Binance's ticker catalog, so euro-stable and market cap stay missing: `EURUSDT`, `INTCBUSDT`, `JPMBUSDT`, `LLYBUSDT`, `SECZBUSDT`, `USDEBUSDT`. The one `eur_stable` true value is `EURIUSDT` (`eurite`).

## First eligibility gate

A symbol passes when none of the classification, policy-list, or `missing_market_cap` reasons are present and a dated CoinGecko cap is stored.
Passing: **441** of 504.

Reason counts across the whole universe:

| Reason | Symbols |
|---|---:|
| `below_market_cap` | 329 |
| `missing_circulating_ratio` | 169 |
| `missing_history` | 169 |
| `missing_market_cap_rank` | 90 |
| `missing_fdv_to_market_cap` | 90 |
| `missing_unlock_schedule` | 79 |
| `monitoring_tag` | 32 |
| `fan_token` | 13 |
| `missing_eur_stable_classification` | 6 |
| `missing_market_cap` | 6 |
| `stablecoin` | 4 |
| `wrapped` | 4 |
| `deposits_suspended` | 3 |
| `withdrawals_suspended` | 3 |
| `eur_stable` | 1 |

## Next blocking gate

Among the 441 symbols that passed the first gate, the next reason is one of two kinds.

287 are under the high-risk market-cap floor. That is the existing threshold, not a missing input.

154 clear that floor and then stop on fields this slice did not capture. All 154 are missing circulating ratio and history. 80 of them are in the normal lane and are also missing rank and FDV. 74 are in the high-risk lane and are also missing a known unlock schedule.

| Reason | Symbols |
|---|---:|
| `below_market_cap` | 287 |
| `missing_circulating_ratio` | 154 |
| `missing_history` | 154 |
| `missing_market_cap_rank` | 80 |
| `missing_fdv_to_market_cap` | 80 |
| `missing_unlock_schedule` | 74 |

Rank, circulating ratio, FDV, history, and unlocks were not captured.
No production schedule was added. `run_daily_scan` was not called.
`prod_decisions_started_at` and `score_v2_shadow_started_at` were not written.
