# Prospective eligibility capture

Session 2026-10-06 stays sealed. This capture is for a later session, before its close. It does not change eligibility thresholds, and it does not fill rank, circulating supply, FDV, history, or unlocks.

A missing flag stays missing. A negative value is stored only when the source below gives evidence for that negative. Unknown is never written as false.

## Clocks

`captured_at` is when CIP retrieved the catalog. It must be at or before the session close. These catalogs do not carry a provider as-of for the tag and status flags, so their `source_timestamp` stays null. A market-cap value is stored only with the provider's own timestamp. An undated cap is not a cap. Retrieval after the close is not stored. A second identical document is a no-op. A different document for the same symbol is refused. A session that already has a finalized manifest is refused. Session 2026-10-06 is refused even when this root has no manifest.

Refresh is once per prospective session, before the close. Nothing here is written back onto an earlier session.

## Flags

| Flag | Source | Observed or derived | As-of | Cadence | When unavailable |
|---|---|---|---|---|---|
| `eur_stable` | CoinGecko category `eur-stablecoin`, joined through the Binance ticker map below | Derived | Category `updated_at` is the catalog time. The flag itself has no separate provider as-of. `captured_at` is CIP retrieval. | Once, pre-close | Null when the coin is unmapped or ambiguous, or when the category pages do not finish. A partial category is not used to assert false. |
| `fan_token` | Binance public asset catalog, tag `fan_token` | Observed | No provider as-of. `captured_at` is retrieval. | Once, pre-close | Null when the base asset is absent or `tags` is not a list. False only when `tags` is a list and the tag is absent. |
| `monitoring_tag` | Same catalog, tag `Monitoring` | Observed | Same as `fan_token` | Once, pre-close | Same as `fan_token` |
| `delisting` | Same catalog, `delisted` and `preDelist` | Observed | Same | Once, pre-close | True when either boolean is true. False only when both are false. Otherwise null. |
| `deposits_suspended` | Binance public coin catalog, `depositAllEnable` | Derived from that boolean | No provider as-of. `captured_at` is retrieval. Network rows are not read. | Once, pre-close | True when `depositAllEnable` is false. False when it is true. Null when the coin is absent or the field is not a boolean. One disabled network is not a suspension. |
| `withdrawals_suspended` | Same catalog, `withdrawAllEnable` | Derived from that boolean | Same | Once, pre-close | Same rule, using `withdrawAllEnable`. |
| `pending_migration` | Same asset catalog, `swapTag` plus `oldAssetCode` and `newAssetCode` | Derived | Same | Once, pre-close | `no` is false. `ps` is true. `sw` is true only on the predecessor (`assetCode` equals `oldAssetCode` and differs from `newAssetCode`) and false only on the successor (`assetCode` equals `newAssetCode` and differs from `oldAssetCode`). Any other tag, or a swap whose old and new codes are missing or equal, stays null. |

The asset catalog is `https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset`. The coin catalog is `https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll`. Both are unauthenticated. Balances on the coin catalog are ignored. The signed capital endpoint is not called.

Duplicate asset codes or duplicate coin codes make the catalog unusable. Nothing from that catalog is turned into false.

## Market capitalization

CoinGecko is the eligibility value. CMC is a separate reading and is not copied into `market_cap_usd`.

The Binance symbol is mapped from CoinGecko's Binance exchange tickers (`/exchanges/binance/tickers`). A ticker counts only when `market.identifier` is `binance`, `base` and `target` are symbols, and `coin_id` is present. The pair is `base` + `target`. One distinct `coin_id` is unambiguous. Two distinct ids are ambiguous and produce no id and no cap. A pair with no `coin_id` is unmapped. Ticker text alone is not a mapping. Pagination that does not end on a short page makes the whole map unusable, because an unseen later id could contradict an earlier one.

The value comes from `/coins/markets` for that id: `id`, `market_cap`, and `last_updated`. The provider id and `last_updated` are kept. `captured_at` is retrieval. Rank, circulating supply, total supply, and FDV from that payload are not copied onto the candidate. A cap without `last_updated` is refused. A null cap stays missing, including when CMC has a number.

CMC uses Binance's `cmcUniqueId` on the public symbol catalog, not a ticker search. One symbol with two ids is ambiguous. The quote is CMC `/v2/cryptocurrency/quotes/latest` keyed by that id. No key is configured in this environment, so the live CMC value stays missing. A missing CMC quote does not block a dated CoinGecko cap and does not fill one.

## What this does not do

Eligibility thresholds stay as they are. Session 2026-10-06 is not populated. The next lane fields stay null so the following measurement can show the next bottleneck. No production schedule, no daily scan, and no shadow clock is written.
