# M2 slice 3 — Survivorship-free history design

**Date:** 2026-10-03
**Status:** Approved 2026-10-03.
**Scope:** Daily USDT klines from Binance public dumps, including delisted pairs, as Parquet, plus a point-in-time listing table and a continuity file. No forward recorders, no backtest, no scoring.

## Goal

A command can turn Binance's public spot kline dumps into a local dataset a later backtest can read without asking today's `exchangeInfo` which pairs used to exist. Delisted pairs stay in the archive. Known redenominations and 1000x tickers are labeled. Stored prices are the printed dump prices.

## Verified dump facts

Checked against `https://data.binance.vision` on 2026-10-03:

| File | What it shows |
|---|---|
| `LUNAUSDT-1d-2022-05` | 14 bars. 2022-05-01 through 2022-05-13, then one bar on 2022-05-31. The 2022-05-13 close is `0.00005000`. The 2022-05-31 close is `8.87000000`. |
| `FTTUSDT-1d-2022-11` | Has a bar on 2022-11-01. |
| `BTSUSDT-1d-2023-12` | Exists. `BTSUSDT-1d-2024-01` returns 404. |
| `BLZUSDT-1d-2024-12` | Exists. `BLZUSDT-1d-2025-01` returns 404. |
| `ALPACAUSDT-1d-2025-05` | Two bars, 2025-05-01 and 2025-05-02. June and July 2025 return 404. |
| `BTCUSDT-1d-2025-01` | 31 bars. Open times are microseconds. |
| `BTCUSDT-1d-2026-10-01` | A daily zip exists for the open month. |
| `MATICUSDT` / `POLUSDT` | MATIC last bar 2024-09-10. POL first bar 2024-09-13. Both in the September 2024 monthly files. |
| `FTMUSDT` / `SUSDT` | FTM last bar 2025-01-13. S first bar 2025-01-16. Timestamps in these files are microseconds. |
| `1000SATSUSDT-1d-2024-01` | Exists. `1000PEPEUSDT` spot monthly files for 2023-05, 2023-06, and 2024-01 return 404. |
| `LUNCUSDT-1d-2022-09` | Exists. `LUNCUSDT-1d-2022-06` returns 404. |

CSV rows have no header and 12 columns: open time, open, high, low, close, volume, close time, quote volume, trade count, taker-buy base volume, taker-buy quote volume, ignore. A `.CHECKSUM` file sits beside each zip. The LUNA checksum line is a 64-character hex digest, two spaces, then the zip basename.

Spot open times are milliseconds before 2025 and microseconds from 2025-01-01 onward. Monthly files are the closed-month source. Daily files fill a month whose monthly zip is not published yet.

The symbol catalog is the public bucket listing, not `exchangeInfo`. Listing `data/spot/monthly/klines/` with a delimiter returns prefixes for delisted symbols as well as current ones.

## Command

Two entry points, both `python -m cip.history`:

```text
python -m cip.history sync --output DIR [--bucket NAME] [--symbol SYMBOL]
python -m cip.history universe --klines DIR --output FILE
```

`sync` lists USDT symbols, downloads the kline zips, verifies checksums, and writes Parquet under `DIR`. It then writes `DIR/universe/listings.parquet` from that local tree. `--symbol` limits the download to one symbol. The listings file still describes only the symbols present on disk, and it does not claim the remote catalog was fully downloaded. `universe` rebuilds that same file from a local tree without downloading. A bare run has no default bucket and does not touch AWS.

An expected failure (`HistoryError` or `ExchangeGeoBlockedError`) exits 1 and prints the error. Any other exception propagates.

The first population of the dev bucket is an operator run with the `soworks` profile and `--bucket` set to the existing `cip-dev-data-<account>` bucket. This slice adds no Lambda, no schedule, no Terraform, and no GitHub workflow. CI does not download the corpus.

## Hosts and requests

- Catalog: `https://s3-ap-northeast-1.amazonaws.com/data.binance.vision` using the legacy list API (`prefix`, `delimiter=/`, `marker`). Pagination follows `NextMarker` while `IsTruncated` is true.
- Zip and checksum bytes: `https://data.binance.vision/<key>`.
- HTTPS only. Redirects are followed, and the final URL must still be HTTPS.
- Timeout is 30 seconds.
- 404 on a zip means that symbol did not publish that file. The sync skips it.
- 429 is tried at most 3 times. The waits between attempts are 1 second and then 2 seconds.
- 451 raises `ExchangeGeoBlockedError` and stops the sync. This command does not emit the probe's `GeoBlocked` metric.
- Any other status, a network failure, or an empty USDT catalog raises `HistoryError`. An empty catalog is a failure because `BTCUSDT` is always present in a real listing.

Keys:

- Monthly: `data/spot/monthly/klines/<SYMBOL>/1d/<SYMBOL>-1d-YYYY-MM.zip`
- Daily: `data/spot/daily/klines/<SYMBOL>/1d/<SYMBOL>-1d-YYYY-MM-DD.zip`
- Checksum: the zip key plus `.CHECKSUM`

Symbol discovery uses the monthly prefix only. A prefix `data/spot/monthly/klines/<SYMBOL>/` is kept when `<SYMBOL>` ends with `USDT` and is not exactly `USDT`.

## Which file wins

The monthly listing is the existence check. A month whose `1d` zip key is listed is downloaded, and daily keys for that month are not read. Months that are absent from the listing and are older than the open tail are not requested.

The open tail is the current UTC month, plus the previous UTC month when its monthly key is absent. Those are the months whose monthly zip is not published yet. Daily zips in the open tail run from the first of the month through yesterday UTC. Today's file is not requested. A 404 on one day is a day without a file. Seven consecutive daily 404s end that symbol's open-tail walk.

A month built from one monthly zip stores that zip's checksum as `cip.source_sha256`. A month built from daily zips stores the SHA-256 of the `YYYY-MM-DD <digest>` lines, in date order, for the days that existed. A later sync fetches checksums first and skips zip downloads when the stored digest still matches.

The clock is injected. Tests do not depend on the wall clock.

## Parser

`HistoryError` is a `CipError`. It is the failure for a bad dump, a bad checksum, a bad continuity file, or an unexpected HTTP status. `ExchangeGeoBlockedError` stays the 451 signal.

The checksum file must be one line whose first field is 64 hex characters, read case-insensitively and stored lowercase. The remainder of the line, after the first whitespace run, must equal the zip basename. The digest must equal the SHA-256 of the zip bytes. A mismatch writes nothing for that month.

The zip must contain one CSV named `<SYMBOL>-1d-<period>.csv`. Each row has exactly 12 comma-separated fields. The ignore field is discarded. Prices and volumes are finite `Decimal` values with at most 8 fractional digits, fitting `decimal128(38, 8)`. High is greater than or equal to low, open, and close. Low is less than or equal to open and close. Volume, quote volume, and both taker-buy volumes are greater than or equal to zero. Trade count is a non-negative integer.

An open-time integer at or above `10**15` is microseconds. Anything smaller is milliseconds. The open instant must be exactly 00:00:00.000 UTC, so the open time is a whole multiple of one day in that unit. The close time must be strictly after the open time and strictly before the next midnight in the same unit. A close at the open time, a close at or after the next midnight, or a non-midnight open is a failure. The calendar date of the open instant is the bar's `open_date`.

Rows in one file must have unique dates and be sorted ascending. A missing date is allowed. Zero rows is a failure. A failed file leaves any existing Parquet for that month untouched.

The parsed bar is a frozen `DailyBar`: `symbol`, `open_date`, `open`, `high`, `low`, `close`, `volume`, `quote_volume`, `trade_count`, `taker_buy_base_volume`, `taker_buy_quote_volume`. The live `Kline` type and `parse_klines` stay as they are.

## Parquet

`pyarrow>=18` is a `history` dependency group, not a main dependency. `scripts/build_lambda.sh` exports main dependencies only, so the probe artifact does not gain PyArrow. CI already syncs all groups.

One object per symbol-month:

```text
klines/interval=1d/quote=USDT/symbol=<SYMBOL>/year=<YYYY>/month=<MM>/part.parquet
```

File metadata key `cip.source_sha256` stores the verified zip digest. A later sync fetches the checksum first. When it matches the metadata, the zip is not downloaded and the object is not rewritten. When it differs, the object is replaced from the new zip.

Columns: `symbol` (string), `open_date` (date), the eight decimal fields as `decimal128(38, 8)`, and `trade_count` (int64). A round trip returns the same `Decimal` values.

With `--bucket`, each kline object and `universe/listings.parquet` are uploaded to those keys. The local directory remains what `universe` reads. Upload uses an injected S3 client. Tests use a fake client, not a new moto service.

## Listings and continuity

`data/symbol-continuity.yaml` is version 1 and fail-closed: unknown fields, a missing field, or any other version raise `HistoryError`. It is not loaded by the investment-policy loader.

```yaml
schema_version: 1
breaks:
  - symbol: LUNAUSDT
    first_discontinuous_date: 2022-05-31
    reason: Bars through 2022-05-13 are the collapse. The 2022-05-31 bar is a different series.
renames:
  - predecessor: MATICUSDT
    successor: POLUSDT
    predecessor_last_date: 2024-09-10
    successor_first_date: 2024-09-13
    reason: Polygon redenomination. Prices stay as printed.
  - predecessor: FTMUSDT
    successor: SUSDT
    predecessor_last_date: 2025-01-13
    successor_first_date: 2025-01-16
    reason: Fantom to Sonic. Prices stay as printed.
scales:
  - pattern: "^1000[A-Z0-9]+USDT$"
    price_scale: 1000
    reason: 1000x spot ticker. Prices stay as printed and are not joined to the unprefixed symbol.
```

`LUNCUSDT` is its own symbol. It is not a successor of `LUNAUSDT`.

`universe` writes `universe/listings.parquet` with one row per symbol found in the local tree:

| Column | Value |
|---|---|
| `symbol` | Dump symbol |
| `quote_asset` | `USDT` |
| `first_open_date` | Earliest bar |
| `last_open_date` | Latest bar |
| `bar_count` | Bars in the tree |
| `first_discontinuous_date` | From `breaks`, else null |
| `successor` | From `renames`, else null |
| `predecessor` | From `renames`, else null |
| `price_scale` | `1000` when the symbol matches the scale pattern, else `1` |

Applying continuity does not drop, edit, or divide bars. A symbol missing from the continuity file is still listed. When a named symbol is present in the local tree, its bars must agree with the file: `LUNAUSDT` must contain 2022-05-31, a predecessor's `last_open_date` must equal `predecessor_last_date`, and a successor's `first_open_date` must equal `successor_first_date`. A disagreement raises `HistoryError` and does not write the listings file. Symbols absent from a partial tree are not checked.

## Tests

Committed fixtures under `tests/fixtures/binance-vision/` are the recorded zip and checksum bytes:

- `LUNAUSDT-1d-2022-05`
- `FTTUSDT-1d-2022-11`
- `BTSUSDT-1d-2023-12`
- `BLZUSDT-1d-2024-12`
- `ALPACAUSDT-1d-2025-05`
- `BTCUSDT-1d-2025-01`
- `BTCUSDT-1d-2026-10-01` (daily)

Assertions:

- LUNA has a 2022-05-01 bar and a 2022-05-13 bar with close `0.00005000`, plus the 2022-05-31 bar. The listing row sets `first_discontinuous_date` to 2022-05-31 and leaves both closes unchanged.
- FTT has a 2022-11-01 bar.
- BTS listing `last_open_date` falls in December 2023 when the fixture set has no later BTS month.
- BLZ listing `last_open_date` falls in December 2024.
- ALPACA listing `last_open_date` is 2025-05-02 and `bar_count` is 2.
- The January 2025 BTC bar opens on 2025-01-01, parsed from a microsecond timestamp.
- A bad checksum, a short row, a non-midnight open, a duplicate date, a non-HTTPS URL, and an empty catalog are rejected.
- A listed month does not read daily keys. The open tail reads daily keys through yesterday. Seven consecutive daily 404s stop that walk. A hole older than the open tail is not requested.
- MATIC gains successor `POLUSDT` and POL gains predecessor `MATICUSDT`. FTM and S do the same. Stored closes are unchanged.
- A constructed `1000SATSUSDT` bar has `price_scale` 1000. No 1000SATS zip is committed. `BTCUSDT` has `price_scale` 1.
- The catalog parser follows `NextMarker` and drops a non-USDT prefix.
- Parquet round-trips decimals and `cip.source_sha256`. A matching checksum skips the zip download.
- 404 skips a file. 500 raises `HistoryError`. 451 raises `ExchangeGeoBlockedError`.

Coverage stays at 100% branch coverage. Tests inject the HTTP transport, the clock, and the S3 client. No test calls the network. Integration tests are unchanged.

## Roadmap

The implementation marks these three items done in `docs/plans/2026-10-03-roadmap.md`:

- Ingest daily klines for all USDT pairs, including delisted, into S3 Parquet.
- Point-in-time universe table with the continuity file.
- Delisted-coverage tests for LUNA 2022-05, FTT 2022-11, and the 2023–2025 waves (BTS, BLZ, ALPACA).

The recorder item stays open: CoinGecko `/global`, DefiLlama stablecoin supply, Binance futures funding and open interest, and per-scan spread and depth.

## Out of scope

- Forward recorders and any CoinGecko, DefiLlama, or futures client.
- The backtest harness, benchmarks, and metrics.
- Scoring, eligibility gates, regime, and features.
- Historical monitoring tags, seed tags, and deposit or withdrawal status. The dumps do not contain them.
- Athena, Glue, and a sync Lambda.
- Quotes other than USDT, and intervals other than `1d`.
- Rewriting prices for 1000x tickers, migrations, or the LUNA break.
- Treating `LUNCUSDT` as a continuation of `LUNAUSDT`.
