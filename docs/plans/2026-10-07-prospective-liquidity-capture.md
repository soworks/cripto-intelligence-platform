# Prospective liquidity capture

Session 2026-10-06 stays sealed. This capture is for the 67 normal-lane symbols that passed the 2026-10-07 eligibility gates, and it is taken before the 2026-10-08T00:00:00Z close. Thresholds are unchanged. The next gate after liquidity is not implemented.

The session daily bar, open date 2026-10-07, has not closed. It is not an input. Completed evidence ends at the 2026-10-06 daily bar. A missing sample stays missing. It is not shortened, and it is not written as zero.

## Metrics

| Gate field | Existing definition | Raw source | Window and minimum sample | Cutoff |
|---|---|---|---|---|
| `median_quote_volume_30d_usd` | Median of daily quote volume. The median is the same even-count midpoint used for the 30-day trade-count median. | Binance daily kline `quote_volume` | 30 consecutive completed days. Fewer than 30, or a gap, stays missing. | `open_date` strictly before the session |
| `day_quote_volume_usd` | The gap table's minimum day in that same 30-day volume window, compared with `minimum_day_quote_volume_usd` | The same 30 quote volumes | The window above. The stored value is the minimum of those 30. | Same cutoff |
| `median_spread_bps`, `spread_snapshots` | Recorder spread: `(ask - bid) / mid * 10000`. The gate wants the median across at least 6 snapshots. | Binance order book, best bid and best ask | Each captured book is one snapshot. The median is defined for one or more. Zero snapshots stay missing, not zero. This slice has one pre-close book, so the count is 1. | `observed_at` at or before the close |
| `depth_usd_per_side` | Depth within ±2% of mid, which is the recorder band of 200 bps. The gate's single per-side number is the smaller of the bid and ask notionals. | The same book levels inside that band | One book is a complete sample. There is no snapshot minimum. An empty or crossed book stays missing. | Same book observation |
| `turnover` | 24h quote volume divided by market cap | Binance 24h `quoteVolume`, and the retained CoinGecko `market_cap` | Both numbers present and the cap positive. Otherwise missing. | Each keeps its own timestamp |
| `volume_zscore` | Standard deviations of the latest completed day's quote volume from the prior 30 days. Sample standard deviation, n−1. A zero deviation stays missing. | The same daily quote volumes | 31 consecutive completed days: 30 baseline days plus the latest completed day. | Latest day is 2026-10-06 |
| `price_move` | Signed close-to-close return of that latest completed day. The gate uses the absolute value. | The last two completed closes | Both closes present and the earlier close positive. | Same day |
| `taker_buy_ratio` | `taker_buy_quote_volume / quote_volume` of the session bar, the feature definition | The session daily bar | The session bar is not closed, so the ratio stays missing. Closed bars keep their taker volume in the raw series and are not copied into this field. | Session bar close |
| `spike_candle_count` | The gate rejects a count below 3. No repository formula says which candles or what size a spike is. | None | Stays missing. A missing count is not zero. | — |
| `trade_size_stdev` | Absolute distance of average trade size, `quote_volume / trade_count`, from the prior 30-day baseline. The gate compares that distance with greater-than 3. Volume z-score stays signed. | The same daily bars | 31 consecutive days, every `trade_count` positive, sample deviation not zero. Otherwise missing. | Latest day is 2026-10-06 |
| `binance_volume_share` | Binance volume divided by CoinGecko aggregate volume | Binance 24h `quoteVolume` and the retained CoinGecko `total_volume` | Both present and the aggregate positive. A Binance volume above the aggregate is not a share and stays missing. | Each keeps its own timestamp |
| `stablecoin_peg_deviation`, `peg_deviation_hours` | USDC/USDT deviation beyond 0.5% for an hour or more. The threshold is the policy fraction 0.005. | Binance `USDCUSDT` 1h closes | Deviation is the absolute distance of the latest closed hour from 1. Hours are the consecutive closed hours, ending at that hour, whose close is beyond 0.005. One closed hour is enough to report hours of 0. No hour stays missing. | Hours whose close time is at or before the retrieval |
| `manipulation_blocked_until` | An active escalation block | No block record exists | Stays null. The gate does not treat null as a missing input. | — |

The retained CoinGecko body is the 14:21:30Z `/coins/markets` response already stored for the lane-field slice. It is not requested again. `total_volume` and `market_cap` are read from it. Binance 24h volume is not in that body, so the ticker is a new pre-close retrieval.

A stored daily bar has to cover one UTC day, and its open date has to be before the session. The retrieval time has to be at or after that bar's close. A filled book, ticker, market-cap pair, or peg keeps the clock that dates it. A peg stores the closed hourly prices it was read from, and the stored deviation and hour count have to match that reading. No closed hour leaves the peg missing.

## What this does not do

Eligibility thresholds and the liquidity thresholds stay as they are. Session 2026-10-06 is not written. The 2026-10-07 classification and lane documents are not rewritten. Fundamentals, the score, and a production schedule are not added. `run_daily_scan` is not called. No shadow clock is written.
