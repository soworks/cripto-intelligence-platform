# Spike-candle count — approved formula

The comparison and this formula are in the policy. The producer is `spike_candle_count`. It does not call a provider. Session `2026-10-06` is still refused by the store before a count is derived, and this change does not write a count for that session.

## Gate

`spike_candle_floor` is 0 and `spike_candle_ceiling` is 3. The gate appends `spike_candles` only when `0 < spike_candle_count < 3`.

| Count | Result |
|---:|---|
| null | `missing_spike_candle_count` |
| 0 | no `spike_candles` reason |
| 1 or 2 | `spike_candles` |
| 3 or more | no `spike_candles` reason |

Null is ordinary missing evidence. It is not an integrity failure.

## Parameters recorded on the policy

`hypotheses.universe.manipulation.spike_count` records the calculation. The z-score threshold is not copied: `zscore_threshold: volume_zscore_above` means the producer reads the existing value 4.

| Parameter | Recorded value |
|---|---|
| Baseline | `baseline_days: 30` |
| Daily sample | `minimum_daily_bars: 31`, which must be the baseline plus the event day |
| Abnormal-event interval | `event_interval: 1d` |
| Concentration interval | `concentration_interval: 1h` |
| Hour grid | `exact_hours: 24`, and that 24 must be the UTC day |
| Z-score threshold | reference to `volume_zscore_above` |
| Variance | `sample_n_minus_1` |
| Material hour | `daily_mean_over_exact_hours` |
| Comparisons | `strict_greater_than` |
| Zero variance | `missing` |
| Daily and hourly quote volume | `exact_equal` |
| Mismatch, including an abnormal day with no material hour | `integrity_failure` |

## Calculation

1. The event day is the latest completed UTC day the cutoff allows. Before `session_close` that day is the session date minus one day, and only after that day has itself closed. At or after `session_close` the session date may be the event day. A bar that has not closed is not the event day.
2. The sample is that day plus the 30 immediately preceding UTC dates, 31 consecutive daily bars. The baseline is those 30 days. Sample variance divides the sum of squared deviations by 29. The standard deviation is the square root of that variance.
3. The day is abnormal when `event quote volume - mean` is strictly greater than `volume_zscore_above` times that standard deviation. The comparison is not a rounded quotient, so a day sitting exactly on the line is not abnormal.
4. The hour grid is the 24 exact completed UTC hours of the event day. Open time is an hour boundary and close time is one millisecond before the next hour. Quote volume is Binance kline index 7. Daily quote volume is the same field on the 1d kline.
5. An hour is material when its quote volume is strictly greater than `mean / 24`.
6. When the day is abnormal, `spike_candle_count` is the number of material hours.
7. When the sample is complete and the day is not abnormal, the count is 0.

## Evidence integrity and ordinary missing

Ordinary missing returns null. The gate then reports `missing_spike_candle_count`. That is a short daily sample, a gap, a day that has not closed, fewer than 24 exact hours, or a complete sample whose baseline variance is 0.

An integrity failure raises `spike evidence is inconsistent` and is not stored as null or as 0. It is raised only after both series are structurally complete, when the sum of the 24 hourly quote volumes is not exactly the event day's quote volume. There is no tolerance.

An abnormal day with zero material hours is that integrity failure. If every hour were at or below `mean / 24`, the hourly sum would be at most the baseline mean. An abnormal day is strictly above `mean + 4 * standard deviation`, so it is above the mean. Those two statements cannot both be true when the sums are equal and every quote volume is non-negative. The producer therefore does not have a separate zero-material result: the inputs that look like "abnormal, but no hot hour" do not reconcile, and the error is the mismatch error rather than a count.

A quiet day, and a flat baseline, use the same rule. Matching sums can still return 0 or null. Differing sums are an integrity failure even when the day would not have been abnormal, and even when the baseline variance is 0. A contradictory series is not reported as "no event" and is not reported as missing history.

## Worked baseline

The tests use 28 days of quote volume 2400, one day of 1200, and one day of 3600. The mean is 2400 and the hourly pace is 100. A day is abnormal only above the strict line from that baseline.

| Case | Result |
|---|---|
| Day 2400, 24 hours of 100 | 0 |
| Day 3000 held in one hour | 0 |
| Day exactly on the z-score line, held in one hour | 0 |
| Day 0.01 above that line, held in one hour | 1 |
| Day 4800 in one hour | 1 |
| Day 4800 in two hours of 2400 | 2 |
| Day 4800 with one hour of 4700 and one hour of 100 | 1, because 100 is not above the pace |
| Day 4800 in three, five, or 24 equal hours | 3, 5, or 24 |
| Day 3672 in 24 hours of 153 | 24 |
| Day 4800 with 24 hours of 100 | integrity failure |
| Flat 31 days of 2400 with hours that sum to 2400 | null |
| Flat days whose hours do not sum to the day | integrity failure |
| 30 days, a gap, 23 hours, or a cutoff before the day closes | null |
| Session bar present before `session_close` | ignored |
| Same session bar at `session_close`, with its own 24 hours | counted |
