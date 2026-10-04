# M3 — Deterministic engine

**Date:** 2026-10-04
**Status:** Started. Slice 1 is the immutable decision record. No orders, no LLM, no M4 exits, no M7.

The roadmap in `docs/plans/2026-10-03-roadmap.md` is the requirements source. This plan only sequences the work and locks the decision schema. KPI formulas and the scan storage layout beyond slice 1 stay in the slice that needs them.

## Order

Each slice is one implementation issue. Later slices call the replay contracts in `cip.backtest.contracts`. They do not grow a second copy of eligibility or scoring.

1. **Decision records.** Immutable point-in-time decision, plus a separate forward-outcome document. This slice.
2. **Eligibility and exclusions.** Normal and high-risk lanes, exclusion list, reason codes. No BUY when a required input is missing.
3. **Liquidity, manipulation, and the new-listing lane.** 30-day medians, spread and depth, manipulation block, 90/200-day listing lanes. A listing does not create a BUY.
4. **Fundamentals.** CoinGecko primary, CMC cross-check, DefiLlama. Missing data is a rejection, not a filled value.
5. **Regime.** Hysteresis and the per-regime discovery policy already in `hypotheses`. RISK_OFF produces no BUY.
6. **Features, score v2, and the information-coefficient study.** Weights freeze in policy after the study. Liquidity and portfolio fit stay out of the score.
7. **Daily scan.** Discover → gates → features → regime → score → record, after the 00:00 UTC close. Every evaluated symbol, including rejections, gets one decision. The ledger stores the decision key and its SHA-256. Zero provider calls on the score path.
8. **Forward outcomes.** At 7/14/30/60 days, read the stored decision and later bars. Write an outcome document. Do not change the decision.
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

## Out of this milestone

M4 owns sizing, exits, shadow fills, and the Engine Assurance Scorecard that reads these records. M5 owns the LLM. M7 owns live orders. October purchases stay manual.
