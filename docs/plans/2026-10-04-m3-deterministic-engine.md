# M3 — Deterministic engine

**Date:** 2026-10-04
**Status:** Slices 1–9 are implemented. Decision schema is version 2. Score weights are not frozen. [#21](https://github.com/soworks/crypto-intelligence-platform/issues/21) stays open. M3-C, Score v2 Calibration & Exit Validation, is `docs/plans/2026-10-05-m3-score-v2-calibration.md`. No orders, no LLM, no M4 exits, no M7.

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
9. **Simulator and baselines.** Event-driven daily replay, equal-weight eligible universe, 1,000-run random baseline with a persisted seed. Uses slices 2, 5, and 6, and the M4 exit contract when that contract exists. M4 still owns exits. This slice.

Pilot capital stays out of these records. `ALPHA_PILOT_2026_10` is a cohort label. The $800 monthly budget starts 2026-11-01. Available capital does not require a BUY.

The dataset in slices 1, 7, and 8 has to be writing before 2026-10-19. Slices 2–6 are what the scan records. Slice 9 does not block that date.

## Slice 1 schema

A decision and its outcomes are different objects.

`DecisionRecord` is written once. The identity is schema version, cohort, symbol, evaluation timestamp, policy version, and git SHA. A later payload with that identity is refused. The first file stays.

Required fields: cohort (`BACKTEST`, `ALPHA_PILOT_2026_10`, `SHADOW`, `LIVE`), symbol, evaluation timestamp, policy version, 40-character git SHA, disposition (`INELIGIBLE`, `SCORED`, `BUY`), at least one reason code, feature values, score and score components, rank, published regime, raw regime, and source stamps (name, source time, provenance). Schema version 2 is the version that stores the raw regime.

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

## Slice 9 simulator and baselines

`replay` is a daily event loop. It calls `Eligibility.eligible`, `Scorer.score`, and, when an exit contract is passed, `ExitRules.exit_due`. It does not import eligibility, scoring, regime classification, or sizing, and it does not replace the BTC and BTC/ETH DCA engine. The caller supplies the published regime as a date-to-new-entries flag. Omitting the map leaves entries open. A missing date blocks entries. No exit contract means no invented sell. The sum of the fee, half-spread, and slippage must stay below one.

A signal is formed at the close. The fill is the open of the next supplied session, which need not be the next calendar day. The last session can form a signal and does not fill it. Cost is the taker fee plus half the spread plus slippage, all non-negative decimal fractions. A buy pays the open times one plus that drag. A sell receives the open times one minus that drag. Cash is deployed equally across the target names. The last name receives any remainder so the cash balance is fully deployed.

The strategy holds the top `selection_count` eligible names by score, then by symbol. A shorter eligible set is taken whole. The equal-weight book holds every eligible name. The random book draws 1,000 runs from a caller seed. Each run seed is drawn with `Random(seed).randrange(2**63)`, and that run samples only after the eligible names are sorted. The result stores the master seed and the run seeds. An eligible set no larger than the selection count is taken whole, with no draw.

A book trades when its membership changes. An unchanged set holds, and weights then drift with price. When entries are allowed, a new set is sold and bought back to equal weight, including names that stayed. When entries are blocked, a partial exit sells only the names that left, leaves the other quantities unchanged, and leaves the sale proceeds in cash. The equal-weight and random books follow eligibility only; they are the opportunity set, not the regime policy. Each random run draws again every session.

Walk-forward splits the sessions into contiguous folds. Extra days stay on the earlier folds. Each fold starts from the original cash and restarts each random stream. A fold shorter than two sessions is refused. One fold is the whole window. The result's strategy book is the continuous window. The folds are the independent replays.

The result is equity, the signal-day picks, and fee drag. It has no hit rate, payoff, or expectancy. Those wait until M4 has real exits.

## Decision schema version 2

Version 1 stored the published regime only. That is not enough to explain hysteresis. A tighter published state can be held while the raw state is already looser, and a session with no confirmed prior can have a raw state and no published state. Version 2 adds `raw_regime`. New decisions are version 2. A version 1 document is refused rather than rewritten. Forward outcomes stay on schema version 1.

## Closure finding: score weights

The information-coefficient function reports Spearman correlation by feature and regime and still refuses to turn a coefficient into a weight. `policies/investment-policy.yaml` has no score weights, so the scan records `score_weights_not_frozen` instead of a score.

The stored dev dataset cannot supply the missing catalog study. Daily klines under `klines/interval=1d/quote=USDT/` are the 11-symbol verification set, not the USDT catalog. Market-regime observations exist only for 2026-10-04. A coefficient needs at least 30 observations in a regime, and those observations would sit in the October pilot window. That window is not a source of weights. No new policy version was written.

M3 stays open until a survivorship-free history can produce the study and the reviewed weights are frozen in policy. Issue #21 stays open with it.

## Pre-pilot weight freeze is blocked

[#21](https://github.com/soworks/crypto-intelligence-platform/issues/21) stays open. This is a documentation record. It does not change policy, weights, thresholds, or scan behavior.

- M3 slices 1–9 are implemented.
- #21 remains open because Score v2 weights cannot yet be defensibly frozen.
- The blocker is insufficient point-in-time calibration data, particularly historical tokenomics.
- Current tokenomics must never be projected backward onto historical sessions.
- The 30-observation minimum remains unchanged.
- `ALPHA_PILOT_2026_10` decisions and outcomes are excluded from weight derivation and tuning.
- Both the calibration decision session and its forward-outcome window must be outside 2026-10-19 through 2026-10-31.
- `score_weights_not_frozen` is the expected fail-closed state until calibration is possible.
- A Binance Vision catalog backfill may later improve historical price and universe coverage. It does not solve historical tokenomics.
- No policy version should be minted until defensible weights exist.

The weighted features are the six volatility-adjusted relative-strength series (`rs_{30,90}d_vol_skip_{1,2,3}`), `extension_count`, and four tokenomics fields (`circulating_ratio`, `fdv_to_market_cap`, `unlock_pct_14d`, `unlock_pct_90d`). `combine` refuses a weighted feature that is missing, so a frozen tokenomics weight without a point-in-time value fails the score closed.

What can be reconstructed from stored or officially dated history:

- The six relative-strength features and `extension_count` come from daily bars. Binance Vision monthly klines are point-in-time, and a symbol contributes a bar only on or after its first open date. The dev bucket holds that history for 11 verification symbols, including BTCUSDT back to 2017. Eleven names are not the USDT universe. Breadth and the extension percentile on that set would mislabel both regimes and penalties. The existing history sync can ingest the Vision catalog later. That ingestion is not look-ahead, and it still does not supply tokenomics.
- BTC's 200-day average and 90-day drawdown are on the stored BTCUSDT bars.
- DefiLlama `stablecoincharts/all` returns a dated supply chart. The recorder keeps the last point. Past points in that chart are reconstructable. Their magnitude is not a regime gate. Presence is.

What cannot be used:

- `btc_dominance` in the bucket exists for 2026-10-04. The recorder reads CoinGecko `/global`, which is the current percentage. A past session with no stored reading is `missing_btc_dominance`, and `classify` then publishes no regime. Deriving a stand-in from today's percentage, or from a ratio we have not stored as the observation, does not satisfy that gate.
- `circulating_ratio` and `fdv_to_market_cap` are current CoinGecko market fields. No as-of supply or FDV history is stored. Today's supplies stamped onto an old session are look-ahead.
- `unlock_pct_14d` and `unlock_pct_90d` need the unlock schedule as it was known on the session. No as-of schedule is stored. A current schedule applied to a past date uses cliffs and amounts that may have been announced later.

`classify` drops the session when either regime observation is missing. Without a published regime, those rows cannot enter the per-regime study. New daily snapshots from 2026-10-04 through 2026-10-18 are fewer than 30 sessions, and a forward excess that reaches 2026-10-19 or later is inside the pilot window. Calendar time before the pilot cannot fill the sample.

The scan therefore keeps failing closed with `score_weights_not_frozen`. That is not a scored `RISK_OFF` result, and it is not a ranked `BUY`.

Safest calibration set: persist point-in-time bars, regime observations, and candidate tokenomics from each closed session into a research record that is not cohort `ALPHA_PILOT_2026_10`. Leave the pilot decisions and their outcomes out of the join. Freeze only after a study on that research record has at least 30 observations for every weighted feature in every regime that occurs, with both the session and the forward window outside 2026-10-19 through 2026-10-31. A later Vision catalog sync can support the price features and breadth. It does not unlock a tokenomics weight.

That calibration is sub-phase M3-C. The plan is `docs/plans/2026-10-05-m3-score-v2-calibration.md`. The blocker in this section still stands.

## Closure evidence

Local unit, type, lint, and coverage on this closure: `ruff format --check`, `ruff check`, and `mypy` are clean. `pytest --cov` is 602 passed, 9 deselected, 100% branch coverage (4224 statements, 1264 branches).

Dev deploys already green for the merged slices: daily scan [37229553414](https://github.com/soworks/crypto-intelligence-platform/actions/runs/37229553414), forward outcomes [37230418343](https://github.com/soworks/crypto-intelligence-platform/actions/runs/37230418343), simulator [37232476030](https://github.com/soworks/crypto-intelligence-platform/actions/runs/37232476030).

One scan from the dev bucket, with no provider calls, used session 2026-10-02. That is the latest stored daily bar before 2026-10-04. The point-in-time listings for that session are `1000SATSUSDT`, `BTCUSDT`, `FTTUSDT`, `LUNAUSDT`, `POLUSDT`, and `SUSDT`. There are no stored candidate packets. Market-regime observations are dated 2026-10-04, so they are not inputs for 2026-10-02. Weights were left unset. The scan wrote six decisions, one per snapshot symbol. Every disposition is `INELIGIBLE` with reason `missing_candidate`, score and rank unset, and both regime fields unset. A second run from the same inputs wrote byte-identical files, decision ids, and SHA-256 values. Each returned ledger event's `sha256` matches the stored file and its `decision_key` matches the object key. There is no BUY, and this is not a RISK_OFF score: the session never reached scoring. Those six objects were not uploaded. A different later payload for the same decision id is refused, so writing this incomplete result would block a complete scan of that session. `s3://cip-dev-data-258485600712/decisions/` was empty before this check and was left empty.

Issues #2, #3, #4, #5, #7, #9, #10, and #12 are platform hardening. They are not the reason #21 stays open. They move to milestone M4 and stay open.

## Out of this milestone

M4 owns sizing, exits, shadow fills, and the Engine Assurance Scorecard that reads these records. M5 owns the LLM. M7 owns live orders. October purchases stay manual.
