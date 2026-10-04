# M3 — Deterministic engine

**Date:** 2026-10-04
**Status:** Slices 1–7 are merged. Slice 8 is forward outcomes. No orders, no LLM, no M4 exits, no M7.

The roadmap in `docs/plans/2026-10-03-roadmap.md` is the requirements source. This plan only sequences the work and locks the decision schema. KPI formulas and the scan storage layout beyond slice 1 stay in the slice that needs them.

## Order

Each slice is one implementation issue. Later slices call the replay contracts in `cip.backtest.contracts`. They do not grow a second copy of eligibility or scoring.

1. **Decision records.** Immutable point-in-time decision, plus a separate forward-outcome document. This slice.
2. **Eligibility and exclusions.** Normal and high-risk lanes, exclusion list, reason codes. No BUY when a required input is missing. This slice.
3. **Liquidity, manipulation, and the new-listing lane.** 30-day medians, spread and depth, manipulation block. Listing age is the lane `minimum_history_days` already applied in slice 2; this slice calls that assessment instead of copying it. A listing does not create a BUY. This slice.
4. **Fundamentals.** CoinGecko primary, CMC cross-check, DefiLlama. Missing data is a rejection, not a filled value. This slice.
5. **Regime.** Hysteresis and the per-regime discovery policy already in `hypotheses`. RISK_OFF produces no BUY. This slice.
6. **Features, score v2, and the information-coefficient study.** Weights freeze in policy after the study. Liquidity and portfolio fit stay out of the score. This slice.
7. **Daily scan.** Discover → gates → features → regime → score → record, after the 00:00 UTC close. Every evaluated symbol, including rejections, gets one decision. The ledger stores the decision key and its SHA-256. Zero provider calls on the score path. This slice.
8. **Forward outcomes.** At 7/14/30/60 days, read the stored decision and later bars. Write an outcome document. Do not change the decision. This slice.
9. **Simulator and baselines.** Event-driven daily replay, equal-weight eligible universe, 1,000-run random baseline with a persisted seed. Uses slices 2, 5, and 6, and the M4 exit contract when that contract exists. M4 still owns exits.

Pilot capital stays out of these records. `ALPHA_PILOT_2026_10` is a cohort label. The $800 monthly budget starts 2026-11-01. Available capital does not require a BUY.

The dataset in slices 1, 7, and 8 has to be writing before 2026-10-19. Slices 2–6 are what the scan records. Slice 9 does not block that date.

## Slice 1 schema

A decision and its outcomes are different objects.

`DecisionRecord` is written once. The identity is schema version, cohort, symbol, evaluation timestamp, policy version, and git SHA. A later payload with that identity is refused. The first file stays.

Required fields: cohort (`BACKTEST`, `ALPHA_PILOT_2026_10`, `SHADOW`, `LIVE`), symbol, evaluation timestamp, policy version, 40-character git SHA, disposition (`INELIGIBLE`, `SCORED`, `BUY`), at least one reason code, feature values, score and score components, rank, regime, and source stamps (name, source time, provenance).

`INELIGIBLE` has no score, no components, and no rank. `SCORED` and `BUY` have a score, components, a rank, a regime, and features. `BUY` is refused when the regime is `RISK_OFF` or missing. The record has no order id, quantity, or notional. A BUY is a recommendation.

`ForwardOutcome` references the decision id and a horizon of 7, 14, 30, or 60 days. It stores absolute return, BTC return over the same window, excess return (absolute minus BTC), eligible-universe-relative return when it can be reproduced, MFE, and MAE. It has no disposition and no score. The store writes it only after the horizon has elapsed, and only if that decision file already exists. A second payload for the same decision and horizon is refused.

Local files are `decisions/cohort={cohort}/date={utc-date}/symbol={symbol}/{id}.json` and `decision-outcomes/decision={id}/horizon={days}.json`. The daily scan's S3 layout is slice 7. Bar math for the returns is slice 8. This slice stores a finished outcome document and refuses an early one.

A decimal field is a finite `Decimal` or a decimal string. A float is refused, so a binary artifact cannot be frozen as the only copy. JSON decimals stay strings. Returns are fractions. MAE is a non-positive return and MFE is a non-negative return.

The outcome write loads the one decision file, rebuilds the record, and refuses the outcome unless that file still hashes to the decision id. A second file with the same id is refused. The decision file is not repaired or replaced.

## Slice 2 eligibility

`assess` reads caller-supplied facts and `hypotheses.universe`. It does not call providers. An eligible result is a lane (`normal_lane` or `high_risk_lane`), not a BUY.

Exclusions are the stablecoin and wrapped base-asset lists, plus EUR-stable, fan-token, non-TRADING, monitoring, delisting, suspended deposits, suspended withdrawals, and pending migration. A missing flag is a rejection. The market-cap boundary belongs to the normal lane: a cap equal to the shared floor is normal, and a cap below it but at least the high-risk floor is high-risk.

Normal-lane gates in this slice are rank, circulating ratio, FDV to market cap, and history. High-risk gates are circulating ratio, known unlock schedule, and history. Spread, depth, volume, and turnover stay in slice 3. Listing age is `minimum_history_days`. If `new_listing` disagrees with that gate, assessment raises and records nothing.

## Slice 3 liquidity and manipulation

`assess_market` calls `assess` and then applies the lane's volume, spread, depth, and turnover gates. It does not recompute listing age. A symbol with no lane skips these gates. Any wash, spike, trade-size, taker-buy, Binance-share, stablecoin-peg, or active escalation-block heuristic adds a reason and still does not create a BUY. Open interest and funding stay out until the policy has thresholds for them.

## Slice 4 fundamentals

Parsers read CoinGecko market data, a CMC USD quote, and DefiLlama fees. A missing key stays missing. They do not call providers and do not replace a missing number with zero. Readings reject floats, booleans, negatives, and naive timestamps. `assess_fundamentals` uses CoinGecko as the primary cap and supply, checks it against CMC, and requires the base asset's hand-verified CoinGecko id. A present zero cap, supply, or FDV is invalid, not missing. Total supply is required only when the unlock schedule is unknown; a known schedule does not invent that number. Unknown unlock percentages are not treated as zero. DefiLlama fees are parsed and are not a gate until the policy has a threshold.

## Slice 5 regime

`classify` reads stored BTCUSDT daily bars, stored universe bars, stored `btc_dominance` and `stablecoin_supply` observations, and stored prior sessions. It does not call providers. Dominance and stablecoin supply must be present for the session date; their magnitudes are not gates, because the policy has no threshold for them. A missing or stale observation, a missing BTC window, or a missing breadth sample rejects the session.

The raw state uses the hypothesis thresholds. BTC above its 200-day average and breadth at or above `breadth_risk_on` is RISK_ON. Exactly one of those two is NEUTRAL. BTC below that average and breadth below `breadth_risk_off`, or a 90-day drawdown above `btc_drawdown_90d_risk_off`, is RISK_OFF. Drawdown wins when both a risk-on tape and a drawdown breach are true. Anything else is unclassified, not a filled NEUTRAL.

Breadth is the share of symbols whose close is above the EMA50 of a contiguous daily tail of at least 50 sessions. A symbol without that tail is left out of the ratio. A present non-positive price rejects the session.

Hysteresis uses `hysteresis_days`. RISK_OFF and any tighter state publish on the session they appear. A looser state publishes only after that many consecutive raw sessions, or it keeps the tighter published state. The first looser session does not publish a regime and does not allow entries. The decision keeps that raw state, and a prior session may have a raw state with nothing published, so the next session can continue the streak. Close equal to the 200-day average is not below it: with narrow breadth that session is unclassified. The discovery fields on the decision are the published state's fields from `hypotheses`. RISK_OFF has `new_entries` false. An acceptance is not a BUY.

## Slice 6 features and score

`measure` reads stored daily bars and caller-supplied tokenomics. It does not call providers and does not fill a missing number with zero. Relative strength is the asset return minus the BTC return over 30 and 90 days, each ending 1, 2, and 3 days before the session, plus the same return divided by the standard deviation of daily returns in that window. A flat window has no volatility-adjusted value. Also stored: EMA20, Wilder ATR at the caller-supplied period, Wilder RSI-14, 7-day return, 90-day beta and correlation to BTC, the session taker-buy ratio, and the 30-day median trade count.

`extension_count` is how many of the three documented triggers are true: more than 2.5 ATR above EMA20, RSI above 78, or a 7-day return strictly above more than 95% of the other symbols that have one. A single name does not fire that leg. A zero ATR or an undefined flat RSI leaves the count missing. The count is only 0, 1, 2, or 3, and the score refuses any other value. A listing or a feature does not create a BUY.

`combine` applies caller-supplied weights to the volatility-adjusted relative-strength features, the extension count, and the tokenomics fields. The extension weight is always a penalty. An empty weight map is refused. Liquidity and portfolio fit are refused. The study reports Spearman correlation with later excess return for each feature and regime. Fewer than 30 observations produce no coefficient. A complete study still does not choose or write weights; the policy has none until a catalog study can freeze them.

## Slice 7 daily scan

`run_daily_scan` reads a stored universe snapshot, stored bars, stored observations, and stored candidate packets. It does not call a provider. The session is the daily bar's open date. The decision time is the next 00:00 UTC, when that bar has closed. An earlier clock writes nothing.

Every snapshot symbol gets one decision, including a symbol with no packet. A symbol that fails a gate is `INELIGIBLE` and has no score or rank. Features are still stored when the bars can produce them. BTCUSDT is the index and is left out of breadth. A published regime with no frozen weights is `INELIGIBLE` with `score_weights_not_frozen`. The policy still has no weights.

A score is ranked only among scored symbols. `RISK_OFF` stays `SCORED`. `NEUTRAL` also requires `rs_30d_skip_1` to be positive. A score below the published state's `min_score` stays `SCORED`. Otherwise the disposition is `BUY`, which is a recommendation and still has no order id, quantity, or notional.

The object key is `decisions/cohort={cohort}/date={utc-close-date}/symbol={symbol}/{id}.json`. The ledger event `DECISION_RECORDED` stores that key and the SHA-256 of the stored bytes.

## Slice 8 forward outcomes

`measure_outcome` reads one stored decision and later daily bars. It does not call a provider and it does not import eligibility or scoring. The decision file is not rewritten.

The decision time is the 00:00 UTC close. The entry price is that session's close. A horizon of 7, 14, 30, or 60 days ends on the close that many days later. Absolute return and the BTC return are close to close over that same pair of bars. Excess return is the asset return minus the BTC return. Bars after the horizon, and the high and low of the entry bar, stay out of the excursion. MFE is the largest high-to-entry gain in the later bars, or zero when price never trades above the entry. MAE is the largest low-to-entry loss, or zero when price never trades below it. A missing day, a non-positive price, or a mismatched symbol writes nothing.

The eligible-universe-relative return is the asset return minus the equal-weight mean of stored `SCORED` and `BUY` decisions from the same cohort and the same close. `INELIGIBLE` decisions are not members. A missing peer window leaves the field empty instead of dropping that name. The outcome still has no disposition and no score.

## Out of this milestone

M4 owns sizing, exits, shadow fills, and the Engine Assurance Scorecard that reads these records. M5 owns the LLM. M7 owns live orders. October purchases stay manual.
