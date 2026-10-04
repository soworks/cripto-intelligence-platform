# M2 slice 4 — DCA benchmark design

**Date:** 2026-10-03
**Status:** Approved 2026-10-03.
**Scope:** A schedule engine that replays two dollar-cost-averaging books on the stored daily klines. No scoring, no eligibility gates, no equal-weight book, no random baseline, no sells.

## Goal

One command reproduces two buy-only books that share a contribution calendar: all cash into `BTCUSDT`, and the same cash split by `core_mix`. The report is JSON on stdout. Later slices can append orders to the same ledger. This slice does not mark the roadmap's backtest boxes done.

## Command

```text
python -m cip.backtest run --klines DIR [--policy policies/investment-policy.yaml]
```

`--klines` is required. `--policy` defaults to `policies/investment-policy.yaml` in the current directory. Success prints one JSON object to stdout and exits 0. `BacktestError` and `PolicyError` print `error: …` to stderr and exit 1. A missing `--klines` exits 2.

A live run needs `BTCUSDT` and `ETHUSDT` months in the kline tree. The dev verification sync stored BTC and did not store ETH. Fetching ETH is an operator step with the existing history command. This slice does not download data.

## Inputs the engine reads

From the loaded policy, via `Decimal(str(...))` on the floats:

- `portfolio.starting_value_usd`
- `portfolio.monthly_contribution_usd`
- `portfolio.core_mix` (`BTCUSDT` and `ETHUSDT`)
- `venue.taker_fee_rate`

`portfolio.contribution_split` is not applied. The mixed book splits the full contribution by `core_mix`. On the repository file that is 70/30 of 650 and of 800, which is 455/195 and 560/240. It is not `core_btc_monthly_usd` / `core_eth_monthly_usd` (392/168).

`strategy.kill_after_closed_trades` and `strategy.kill_after_months` stay unused. There is no discovery book to judge.

The report includes `policy_sha256`, which is `LoadedPolicy.version` (SHA-256 of the raw policy file).

Bars come from the local tree only:

```text
klines/interval=1d/quote=USDT/symbol=BTCUSDT/year=*/month=*/part.parquet
klines/interval=1d/quote=USDT/symbol=ETHUSDT/year=*/month=*/part.parquet
```

The engine calls `read_month` on those files. It does not call `load_bars`, so a tree that also holds other symbols is not loaded.

## Schedule

The clock starts on the first day both symbols have a bar and ends on the last day both have a bar. Every calendar day in that inclusive range must exist for both symbols.

The schedule is a list of `(date, usd)` pairs:

- The first shared day buys `starting_value_usd`.
- Each later month buys `monthly_contribution_usd` on the 1st, when that 1st is on or before the last shared day.
- The start month does not also receive `monthly_contribution_usd`. When the first shared day is the 1st, that day is still only the opening buy. The next buy is the 1st of the following month.

## Books and fills

Both books receive each scheduled amount in full.

- `btc` weights are `{BTCUSDT: 1}`.
- `btc_eth` weights are `core_mix`.

A weight map must sum to 1 within `1e-9`. Legs are cents, `ROUND_HALF_UP`. Symbols are allocated in alphabetical order. Every symbol except the last is `quantize`d. The last symbol receives the residual so the legs sum to the contribution. A non-positive leg raises `BacktestError`. For a 70/30 map the residual symbol is `ETHUSDT`. Example: `1.05` at 70/30 is `BTCUSDT` `0.74` and `ETHUSDT` `0.31`.

A scheduled buy fills at that day's open. Base quantity for a leg is `notional * (1 - taker_fee_rate) / open`. The contribution arrives and is spent the same morning, so cash stays zero. Equity that day is the sum of `quantity * close` over legs with a positive quantity. The fee is taken in the base quantity. `fee_drag_usd` for a book is the sum of `contribution * taker_fee_rate` across buys. Both books invest the same notional, so the two fee-drag figures match.

Every open and every close in the window must be positive, including days that are not buy days, and including a symbol the book does not hold.

## Metrics

Decimal values are JSON strings. A metric that is undefined is JSON `null`. The run still exits 0 in that case.

Daily return on day `t` (every day after the first) is `(equity_t - contribution_t) / equity_{t-1} - 1`. `contribution_t` is zero on a day with no buy. The first day has no return. Fewer than two daily returns raises `BacktestError`, so a one-day or two-day overlap stops the run.

- **Time-weighted return** is the product of `(1 + daily return)` minus 1.
- **IRR** is XIRR. Flows are the negated contribution on each buy date and the final equity on the last day. When the last day is also a buy day, that date is one net flow: final equity minus the contribution. The exponent is `(date - first).days / 365`. Newton starts at `0.1`, allows 100 iterations, and stops when the absolute step is below `1e-12`. No convergence, a candidate rate less than or equal to `-1`, or a zero derivative raises `BacktestError`. The iteration cap is an argument so a test can force the exhausted-iteration failure with a cap of 0.
- **Sharpe** is `mean(daily returns) / sample_stdev(daily returns) * sqrt(365)`. The sample divisor is `n - 1`. The risk-free rate is zero. A zero standard deviation stores `null`.
- **Sortino** uses downside deviation `sqrt(sum(min(r, 0)^2) / (n - 1))`, with positive returns contributing zero. A zero downside deviation stores `null`.
- **Max drawdown** is the largest `(peak - index) / peak` on the time-weighted index. The index starts at 1 on the first day and multiplies by `(1 + daily return)` after that. A contribution cannot hide a price drop, because the return strips the contribution out. A max drawdown of zero stores Calmar as `null`.
- **Calmar** is the annualized time-weighted return divided by max drawdown. Annualized return is `(1 + twr) ** (365 / span_days) - 1`, where `span_days` is `(last - first).days`.
- **Excess, beta, alpha** are on the mixed book against the BTC book's daily returns. Excess is mixed time-weighted return minus BTC time-weighted return. Beta is sample covariance divided by the BTC book's sample variance. Alpha is the daily intercept `(mean(mixed) - beta * mean(btc)) * 365`. A zero BTC variance stores beta and alpha as `null`.

## Report

```json
{
  "policy_sha256": "<64 hex chars>",
  "start": "YYYY-MM-DD",
  "end": "YYYY-MM-DD",
  "contributed_usd": "0.00",
  "books": {
    "btc": {
      "equity": [{"date": "YYYY-MM-DD", "equity_usd": "0"}],
      "twr": "0",
      "irr": "0",
      "sharpe": "0",
      "sortino": "0",
      "max_drawdown": "0",
      "calmar": "0",
      "fee_drag_usd": "0"
    },
    "btc_eth": {
      "equity": [{"date": "YYYY-MM-DD", "equity_usd": "0"}],
      "twr": "0",
      "irr": "0",
      "sharpe": "0",
      "sortino": "0",
      "max_drawdown": "0",
      "calmar": "0",
      "fee_drag_usd": "0",
      "excess_twr": "0",
      "beta": "0",
      "alpha": "0"
    }
  }
}
```

`contributed_usd` is the exact sum of the schedule amounts. Equity strings are the exact `quantity * close` decimal, not rounded to cents. A quoted zero in the shape above stands for a decimal string. `sharpe`, `sortino`, `calmar`, `beta`, and `alpha` may be JSON `null`. Key order is the order above.

## Failures

`BacktestError` subclasses `CipError`. The run raises it when:

- either symbol has no bars
- a symbol repeats an `open_date`
- the shared range is empty
- a calendar day inside the shared range is missing for either symbol
- a scheduled buy falls on a day that symbol has no bar
- an open or a close in the window is not positive
- a weight map does not sum to 1 within `1e-9`
- a rounded leg is not positive
- there are fewer than two daily returns
- XIRR does not converge, the candidate rate is less than or equal to `-1`, or the derivative is zero

## Tests

Engine tests build `DailyBar` values in memory. No test calls the network. Coverage stays at 100% branch coverage.

- A window that opens on 2017-08-17 buys `650` that day and `800` on 2017-09-01. It does not buy `800` on 2017-08-17.
- A window that opens on the 1st still buys only the opening amount that day.
- Repository policy amounts at `core_mix` split `650` into `455.00` / `195.00` and `800` into `560.00` / `240.00`.
- `1.05` at weights `0.70` / `0.30` splits into `BTCUSDT` `0.74` and `ETHUSDT` `0.31`.
- A Hypothesis property: for a positive cent amount and positive weights that sum to 1, the returned legs are positive and sum to the amount. Draws use amounts of at least `1` and weights of at least `0.05`, so the residual leg stays positive.
- Quantity equals `notional * (1 - 0.00075) / open`. Cash after the fill is zero.
- A second-day contribution is absent from that day's return: `(equity - contribution) / previous_equity - 1` uses the previous coins only.
- A price drop on a contribution day increases max drawdown on the index.
- A gap, a missing symbol, a duplicate date, a non-positive price, a weight map that does not sum to 1, a buy date absent from that symbol's bars, a one-day window, and a two-day window each raise `BacktestError`.
- A two-flow XIRR, `-100` then `+110` 365 days later, is `0.1`. When the last day is a buy day, that date's flow is final equity minus the contribution. An iteration cap of 0 raises `BacktestError`. Flows of `-100` on day 0 and `+1` one day later raise `BacktestError` because the next rate is below `-1`. A single flow on day 0 raises `BacktestError` because the derivative is zero.
- Constant daily returns store Sharpe as `null`. A series with no negative return stores Sortino as `null`. A series that never falls stores Calmar as `null`. Constant BTC daily returns store beta and alpha as `null`.
- The CLI test points `--klines` at a directory with no BTC month and expects exit 1 with stderr starting `error:`.

## Roadmap

Leave these three boxes unchecked in `docs/plans/2026-10-03-roadmap.md`:

- Event-driven daily simulator using the same feature, scoring, and exit code as production.
- Benchmarks, including the equal-weight universe and the 1,000-run random baseline.
- The full metrics list, including hit rate, payoff, expectancy, profit factor, MAE/MFE, and turnover.

This slice delivers the BTC book, the BTC/ETH book, and the portfolio metrics in the report section. Those three remaining pieces are why the boxes stay open.

## Out of scope

- Equal-weight and random-selection baselines. They plug in when the M3 eligibility gates exist.
- Walk-forward splits. These two books have no tuned parameter.
- Scoring, features, regime, exits, sizing, and the kill-rule evaluator.
- Spread, slippage, and a BNB fee balance. The taker fee is the only cost.
- Writing the report to S3 or to a file.
- Terraform, Lambda, and Step Functions.
- Downloading klines.
