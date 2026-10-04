# M3-C — Score v2 Calibration & Exit Validation

**Date:** 2026-10-05
**Status:** Dedicated plan for sub-phase M3-C. [#21](https://github.com/soworks/crypto-intelligence-platform/issues/21) stays open. M3 slices 1–9 are implemented. This document does not change policy, thresholds, or scan behavior. It does not choose or freeze Score v2 weights, and it does not mint a policy version.

The parent plan is `docs/plans/2026-10-04-m3-deterministic-engine.md`. The pre-pilot weight-freeze blocker in that plan still stands. Calibration is now a formal sub-phase, not an undefined wait.

## Lifecycle

M3 code is complete. Weights stay unfrozen until this sequence finishes, in this order:

1. **M3 CODE COMPLETE.** Slices 1–9 are implemented.
2. **C1 Prospective Data Collection.** Starts immediately. Universe snapshots, daily bars, regime inputs, tokenomics as-of snapshots, features, and immutable decisions.
3. **C2 Data Maturation.** Forward outcomes at 7, 14, 30, and 60 days.
4. **C3 Calibration Readiness Gate.** Sufficient observations, regime coverage, feature completeness, no leakage, and pilot exclusion.
5. **C4 IC Study.** Feature information coefficients by regime, stability, sample sizes, and candidate weights.
6. **C5 Weight Selection / Freeze.** `investment-policy` vNext, only after review.
7. **C6 Out-of-Sample Validation.** Replay without retuning.
8. **C7 M3 Exit Validation.**
9. Only then may [#21](https://github.com/soworks/crypto-intelligence-platform/issues/21) be completed.

## Checkpoints

Recorded on #21 and here. None of these are done.

| Checkpoint | Meaning | State |
|---|---|---|
| C1 Capture operational | Prospective point-in-time capture is running | Not done. C1 has not been built. |
| C2 Outcomes maturing | 7/14/30/60-day outcomes are accruing on those captures | Not done. |
| C3 Calibration ready | Every required cell passes the readiness gate | Not done. |
| C4 IC study complete | Frozen dataset, documented ICs, weight proposal | Not done. |
| C5 Weights frozen | Reviewed weights in `investment-policy` vNext | Not done. |
| C6 Holdout validated | Out-of-sample replay with no retune | Not done. |
| C7 M3 exit validated | Exit criteria met after C6 | Not done. |

The dev bucket still lacks as-of tokenomics history. Regime observations are essentially for 2026-10-04 only, plus an 11-symbol verification kline set. That is the starting evidence for C1, and it is not a calibration sample.

## C1 — Prospective data collection

C1 starts now. A missed point-in-time tokenomics, regime, or universe snapshot cannot be reconstructed safely later. Current tokenomics must never be projected backward onto historical sessions.

Each closed session persists, as of that session:

- universe snapshot
- daily bars
- regime inputs
- tokenomics as-of snapshot
- feature values
- the immutable decision

A daily alarm if the capture pipeline fails belongs in this phase. A missed tokenomics snapshot is not repaired the next day by copying a later value. The next session stores its own as-of reading. The gap stays a gap.

Binance Vision catalog backfill may later improve price and universe coverage. It does not solve historical tokenomics.

The capture pipeline, the readiness monitor, and the alarm are later implementation. This document only specifies them.

## Calibration record

The original observation is immutable. Maturation adds separate outcome records, which M3 already does. The decision file is not rewritten when a horizon elapses.

Each calibration observation connects:

- session
- cohort or research-set id
- symbol
- `published_regime`
- `raw_regime`
- the weighted feature values listed below
- source timestamps
- source provenance
- policy version
- git SHA
- 7/14/30/60 outcomes
- BTC-relative outcomes
- eligible-universe-relative outcomes
- `calibration_eligible`
- `exclusion_reason`

## Calibration Readiness Monitor

The immediate implementation, later and not this task, is a Calibration Readiness Monitor. It does not choose weights. For each weighted feature and each regime (`RISK_ON`, `NEUTRAL`, `RISK_OFF`) it reports progress toward the existing minimum of 30.

An example count table is illustrative only. It is not CIP data, and this plan does not record sample counts.

The denominator is not a raw row count. The monitor distinguishes collected, matured, and eligible. The report shape, with labels and no measurements:

- collected observations
- 30d outcomes matured
- 7d outcomes matured
- 14d outcomes matured
- 60d outcomes matured
- excluded because the Alpha Pilot window applies
- excluded because provenance is missing
- calibration eligible
- required: 30
- status: not ready until eligible is at least required, and the independent-session and asset-diversity floors are met, once those floors exist

Until the floors exist, a cell that reaches 30 eligible observations is still not ready.

## Weighted features

Exactly these eleven. The monitor reports each one in each regime:

- `rs_30d_vol_skip_1`
- `rs_30d_vol_skip_2`
- `rs_30d_vol_skip_3`
- `rs_90d_vol_skip_1`
- `rs_90d_vol_skip_2`
- `rs_90d_vol_skip_3`
- `extension_count`
- `circulating_ratio`
- `fdv_to_market_cap`
- `unlock_pct_14d`
- `unlock_pct_90d`

A weak feature may receive no weight. Presence in this list does not force a feature into Score v2.

## Statistical unit

The 30-observation minimum stays unchanged. It is the existing floor from the information-coefficient study: fewer than 30 observations produce no coefficient.

Thirty observations are not defined as thirty calendar days. One session with many symbols is cross-sectional and correlated. Requiring 30 distinct `RISK_OFF` days may take a long time.

Readiness will require all three:

- N observations ≥ 30
- N independent sessions ≥ X
- N distinct assets ≥ Y

X and Y are unknown today. This plan does not set them. The IC methodology must set them before C4. Until that methodology exists, `INSUFFICIENT_SESSIONS` and `INSUFFICIENT_ASSET_DIVERSITY` have no numeric floor, and the readiness status stays not ready.

## Pilot isolation

Pilot isolation is mechanical, not a convention. The calibration selector rejects an observation when either of these is true:

- the decision session is inside 2026-10-19 through 2026-10-31
- the evaluated forward window overlaps that period

Both the decision session and the forward-outcome window must be outside that range. `ALPHA_PILOT_2026_10` decisions and outcomes are excluded from weight derivation and tuning.

Pilot data may later feed Engine Assurance. It cannot feed Score v2 calibration.

## Readiness reason codes

Exact names:

- `INSUFFICIENT_OBSERVATIONS`
- `INSUFFICIENT_SESSIONS`
- `INSUFFICIENT_ASSET_DIVERSITY`
- `MISSING_TOKENOMICS`
- `MISSING_REGIME`
- `MISSING_UNIVERSE_SNAPSHOT`
- `MISSING_FORWARD_OUTCOME`
- `PILOT_WINDOW_OVERLAP`
- `STALE_SOURCE`
- `PROVENANCE_INCOMPLETE`

## C3 — Calibration readiness gate

A cell is ready only when every check below holds:

- calibration-eligible observations are at least 30
- independent sessions meet X, after the IC methodology has set X
- distinct assets meet Y, after the IC methodology has set Y
- regime coverage includes the regime being reported (`RISK_ON`, `NEUTRAL`, `RISK_OFF`)
- feature completeness: the weighted feature is present with source timestamps and provenance
- no leakage: values are as-of the decision session, and current tokenomics are not projected backward
- pilot exclusion: the selector has rejected every overlapping session and forward window

The gate does not choose weights.

## C4 — IC study

The IC study is a controlled event, not a continuous retune:

1. calibration dataset freeze
2. dataset SHA / manifest
3. IC study
4. documented results
5. weight proposal
6. review
7. `investment-policy` vNext
8. weights frozen

The study records sample counts, regimes, date range, excluded observations, feature ICs, and stability. Candidate weights come out of that record. They are not written into policy in the same step.

## C5 — Weight selection / freeze

Weights freeze only after review, as `investment-policy` vNext. A feature with a weak or unstable coefficient may receive no weight.

Once frozen, the dataset SHA and the study artifact are part of the policy provenance. No policy version is minted until defensible weights exist. `score_weights_not_frozen` remains the expected fail-closed scan state until then. That state is not a scored `RISK_OFF` result and not a ranked `BUY`.

## C6 — Out-of-sample validation

C6 is mandatory before C7. In-sample fit is not success.

Freeze weights first. Then evaluate on data that was not used to derive them: a later prospective window, or a clean holdout. No retuning after seeing that result. A failure is a new calibration version, not a silent weight edit.

## C7 — M3 exit validation

C7 runs only after C6 has been recorded. Exit validation confirms the frozen weights, the dataset SHA, the study artifact, and the holdout result. Completing C7 is what may complete [#21](https://github.com/soworks/crypto-intelligence-platform/issues/21). Until then, #21 stays open.

## What this document does not do

This document does not change policy, thresholds, or scan behavior. It does not implement the monitor, the capture pipeline, the daily alarm, or any policy edit. It does not choose Score v2 weights. It does not mint a policy version. It does not start M4.
