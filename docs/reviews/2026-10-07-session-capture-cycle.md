# Session capture cycle — 2026-10-07

The workload derives the session from the UTC clock and stores the evidence that already has a contract. It does not call `run_daily_scan`, does not add a DecisionRecord schedule, and does not write `prod_decisions_started_at`, `score_v2_shadow_started_at`, or `prod_shadow_started_at`. A request for 2026-10-06 is refused. The existing Oct 7 book files were not modified.

Validation ran locally at 2026-10-07T19:57:17Z against public market data and a copy of the existing session files. The dev Lambda was not updated.

## Clock

| | |
|---|---|
| Now | 2026-10-07T19:57:17Z |
| Open session | 2026-10-07 |
| Pre-close | 2026-10-07 |
| Post-close | none |

2026-10-07 closes at 2026-10-08T00:00:00Z. The previous session is the sealed 2026-10-06 session, so the live cycle has nothing to capture after the close. A requested date of 2026-10-08 is refused as a future session. A requested date of 2026-10-06 raises `sealed session stays sealed`.

## Pre-close (live)

The live run stored one symbol, BTCUSDT, plus the session-level inputs. Provider calls on the first pass: **6**. The retry made **0** additional provider calls.

| Call | Stored |
|---|---|
| `GET /api/v3/exchangeInfo` | universe, 504 TRADING USDT symbols |
| `GET /api/v3/ticker/24hr` | BTCUSDT 24h quote volume and retrieval time |
| `GET /api/v3/klines?symbol=USDCUSDT&interval=1h` | 29 closed peg hours, with the provider close time |
| `GET https://api.coingecko.com/api/v3/global` | `btc_dominance`, provider time and retrieval time |
| `GET https://stablecoins.llama.fi/stablecoincharts/all` | `stablecoin_supply`, provider time and retrieval time |
| `GET /api/v3/depth?symbol=BTCUSDT&limit=100` | one book: 1 spread snapshot, 100 bid levels, 100 ask levels |

Classification stayed unstored on both attempts. That is an unimplemented producer in the validation client, not a provider failure and not an orchestration defect. The diagnosis is below. No absence file was written. The session was not finalized, and no manifest was created.

A second pass skipped the stored universe, ticker, peg, both regime series, and the book. It asked for classification again. The same client raised before any HTTP call, so the retry still made no provider call and still stored nothing.

## Classification

The live client implements universe, the 24h ticker, the peg, both regime series, and one book. Every other kind, including `classification`, hits the final `raise TemporaryFailure(kind)` before a provider is contacted. The cycle catches that, records `classification:BTCUSDT` as a retry, and writes no file. The second pass finds no classification file, asks again, and gets the same exception. Readiness does not advance.

The prospective classification contract already covers this. `classification.py` does not call a provider. Unknown stays unknown. `store_classification` persists a `SymbolClassification` the caller has already built, and it refuses a sealed session, a finalized manifest, and a capture after the close. The cycle calls that store from `_keep_classification` when the source returns a record. The pre-close unit test supplies one for BTCUSDT; the identical retry then performs zero fetches. A `TemporaryFailure` is not written as an absence.

No mapping was attempted, and no fail-closed classification rule ran, because the source returned no payload. The catalogs remain the caller's producer, as in the classification module. This slice does not add one, and it does not invent an absence to move finalization forward.

## Duplicate books

The existing 2026-10-07 liquidity tree has 67 symbols, and each already has one spread snapshot. A cycle over a copy of that tree, with every provider call refused, skipped all 67 books (`copy_book_calls = 0`) and recorded the other pre-close inputs as retries. The original `symbol=BTCUSDT.json` bytes were unchanged after the run.

The fresh live root stored one BTCUSDT snapshot. The retry skipped that book instead of fetching another. A different second book is a conflicting observation and leaves the first file in place.

## Post-close (not yet due)

Post-close does not run for the live clock. A controlled clock of 2026-10-08T01:00:00Z, limited to session 2026-10-07, ran only the closed-session stage:

| Case | Provider calls | Result |
|---|---|---|
| Reconciling 31 daily bars and 24 exact hours | `daily_bars`, `hour_bars` | derived `spike_candle_count` 1 |
| Same daily total with 24 hours that do not sum to it | `daily_bars`, `hour_bars` | 24 raw hours kept, derived count null, daily bars kept |
| Daily bars unavailable | one failed `daily_bars` attempt | no file, no absence |
| Fewer than 24 exact session-day hours | `hour_bars`, then the same call again on retry | no hour file, so the retry stays open |
| 24 exact hours plus an hour outside that day | `hour_bars` | only the 24-hour grid is stored |
| Retry after the reconciling capture | none | both bar files skipped |

That controlled run did not finalize, because the candidate packet for the closed session was not part of the stage. Finalization runs only after the universe, ticker, peg, both regime series, and, for every universe symbol, the book, classification, completed bars, hourly bars, and candidate packet are already stored. A finalized manifest is not written again.

## Suite

`uv run pytest --cov --cov-report=term-missing`: 961 passed, 9 deselected, 100% branch coverage.
