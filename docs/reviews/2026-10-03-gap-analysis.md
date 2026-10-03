# Gap Analysis: Crypto Intelligence Platform Reference Architecture v1.0

**Date:** 2026-10-03
**Reviewed document:** [`docs/architecture/reference-architecture-v1.0.md`](../architecture/reference-architecture-v1.0.md)
**Reviewers:** AWS Solutions Architect (AI solutions), Crypto Investor / Market Structure

## Owner decisions recorded during review

| Topic | Decision |
|---|---|
| AWS region | `us-east-1` (account `258485600712`, CLI profile `soworks`) |
| Account isolation | Single AWS account; dev/prod separated by naming, IAM, tags and Terraform state |
| Approval channel | Telegram bot, restricted to the owner's Telegram user ID |
| Owner jurisdiction | Colombia (Binance.com is the intended venue) |

## Verdict

The design's core principles are sound and should be kept: deterministic risk
authority, LLM as analyst only, SHADOW-first, append-only ledger, provider
abstraction, OIDC-only CI, and cost as a first-class metric. The gaps are
concentrated in four areas:

1. **Reachability and venue** - the chosen region cannot talk to Binance.com as designed.
2. **Undefined state** - portfolio, positions, exits and approvals have no data model.
3. **Undefined strategy** - the doc specifies a scanner and a gate, not an investable
   strategy with exits, regime awareness and an evaluation bar.
4. **Operational plumbing** - orchestration, storage of large snapshots, counters,
   notification channels and the AWS account baseline are left implicit.

Severity scale: **Critical** blocks the build or creates unacceptable risk;
**High** must be resolved before the milestone that depends on it;
**Medium** should be resolved during v1; **Low** is an improvement.

---

## Part A - AWS / AI architecture gaps

### A1. Binance.com blocks AWS US regions - Critical

Binance.com returns HTTP 451 to US IP ranges, which include Lambda egress in
`us-east-1`. The scanner would fail on its first call, and a live executor could
never run from this region. Local testing from Colombia succeeds
(`api.binance.com`, `data-api.binance.vision` and `api.binance.us` all returned
200 on 2026-10-03), which will mask the problem until the first cloud deploy.

**Recommendation**
- Make a reachability spike the first infrastructure task: a throwaway Lambda in
  `us-east-1` calls `api.binance.com/api/v3/exchangeInfo` and
  `data-api.binance.vision/api/v3/exchangeInfo` and records status codes.
- If blocked, keep the platform in `us-east-1` and add a single
  **exchange gateway** Lambda in a Binance-permitted region (candidate:
  `sa-east-1`). Only the Binance adapters run there; the core platform invokes it
  cross-region with a narrow IAM permission. This preserves the region decision
  for Bedrock, DynamoDB, Step Functions and observability.
- Binance.US is not an alternative: it serves US persons only, and the owner is in Colombia.
- The `BinanceMarketAdapter` interface must not care where it runs, so the gateway
  is a deployment decision, not a code fork.

### A2. Exchange API key protection vs. serverless egress - High (blocks M6)

Binance strongly favors IP-restricted API keys for trading permissions (verify
the current key policy at M6 time; it has changed over the years). Lambda has
no static egress IP. A managed NAT Gateway with an Elastic IP costs roughly
US$35+/month per AZ, which dominates the platform's budget.

**Recommendation**
- Decide before M6: (a) a NAT instance (for example, fck-nat on `t4g.nano`,
  around US$3-4/month) in the gateway region only, with an EIP whitelisted in
  Binance; or (b) an unrestricted key with trade-only permission, withdrawals
  disabled, small balance, and frequent rotation. Option (a) is preferred.
- Use Binance Ed25519 API keys, and store the private key in Secrets Manager
  with the secret's resource policy limited to the executor role.

### A3. Orchestration between stages is implicit - High

The diagram shows Scanner -> Decision Lambda -> Validator, but not how work moves
between them, how retries work, or what happens on partial failure. A single
scanner Lambda that pulls klines for hundreds of symbols, enriches, scores and
calls Bedrock risks timeouts and poor replayability.

**Recommendation**
- Use **Step Functions Standard** for the scan pipeline: Discover -> Filter ->
  Features (Map over symbol batches) -> Score -> Gate -> Analyze (Map, max
  concurrency 1-2) -> Validate -> Record. At hourly cadence this costs cents per
  month, and the execution history gives a visual audit trail per correlation ID.
- Each state is a small Lambda that calls pure domain code (already a principle in §8.1).
- DLQ / failure states write a `PIPELINE_FAILED` ledger event and raise an alarm.

### A4. DynamoDB is the wrong home for bulk market snapshots - High

Storing per-scan market snapshots and feature sets for hundreds of assets in the
ledger risks the 400 KB item limit, and it inflates write cost and complicates
backtests. The doc marks S3 as optional, but both replayability (§2) and
backtesting (§14) depend on it.

**Recommendation**
- **S3 (required, not optional):** raw exchange responses, per-scan universe and
  feature snapshots (gzip JSON or Parquet), partitioned
  `s3://<bucket>/snapshots/dt=YYYY-MM-DD/scan=<correlation_id>/`. Versioning on;
  lifecycle to Glacier IR after 90 days.
- **DynamoDB:** decision events, references (S3 keys plus SHA-256 hashes) to the
  snapshots used, counters, idempotency and approvals.
- Historical candles for backtests come from Binance public data dumps
  (`data.binance.vision`) into S3, queried with DuckDB locally or Athena.

### A5. Ledger access patterns, counters and immutability are undefined - High

`PK=ASSET#<symbol>` alone cannot answer the queries the system needs: everything
for a correlation ID, pending proposals, LLM calls today, trade spend this month,
open positions, and outcomes due.

**Recommendation** - three tables, each with a single responsibility:
- `ledger` (append-only): `PK=ASSET#<symbol>`, `SK=EVENT#<ts>#<ulid>`;
  GSI1 `PK=CORR#<correlation_id>` / `SK=<ts>`; GSI2 `PK=TYPE#<event_type>#<yyyy-mm-dd>` / `SK=<ts>`.
  IAM denies `UpdateItem` and `DeleteItem` for every role except a break-glass
  role. PITR and deletion protection are on.
- `state` (mutable, materialized): positions, shadow portfolio, approvals,
  watchlist, new-listing lifecycle, dossier dedupe hashes.
- `counters` (atomic): `PK=BUDGET#llm#day#2026-10-03`, `BUDGET#trade_usd#month#2026-10`,
  using conditional `ADD` with a ceiling check. This is the hard budget guardrail.
- Use AWS Lambda Powertools idempotency (backed by DynamoDB) for handler-level
  idempotency, and keep the domain-level order idempotency key in `state`.

### A6. Single account with a long-lived IAM user - High

Dev and prod share one account, and the CLI uses an IAM user access key. The
production trading secret would live next to dev experiments.

**Recommendation** (within the single-account decision)
- Separate Terraform state keys and resource prefixes (`cip-dev-*`, `cip-prod-*`);
  tag `env` on everything; IAM conditions scope each role to its prefix.
- The prod trading secret exists only in prod, and its resource policy allows
  only `cip-prod-executor`.
- Two GitHub OIDC roles: `cip-gha-dev` (trust `ref:refs/heads/main` and PRs, plan
  only for PRs) and `cip-gha-prod` (trust `environment:prod` only).
- Account baseline before M1: root MFA, MFA on `asolano`, CloudTrail (single
  trail to S3), AWS Budgets with alerts at US$10 / US$25 / US$50, Cost Anomaly
  Detection, IAM Access Analyzer. GuardDuty is optional because of cost.
- Plan to replace the `asolano` access key with IAM Identity Center or
  `aws login` short-lived credentials once bootstrap is complete.

### A7. Terraform bootstrap is circular - Medium

CI cannot create the OIDC provider and state bucket that CI itself needs.

**Recommendation**
- `terraform/bootstrap/` is applied once locally with `AWS_PROFILE=soworks`. It
  creates the state bucket (versioned, encrypted, public access blocked), the
  GitHub OIDC provider, the two deploy roles, Budgets and CloudTrail. It uses the
  S3 backend with `use_lockfile = true` (Terraform 1.10+), so no DynamoDB lock
  table is needed.

### A8. Human approval workflow (Telegram) is unspecified - High (blocks M5)

**Recommendation**
- Telegram bot webhook -> API Gateway HTTP API -> `approval` Lambda. Verify the
  `X-Telegram-Bot-Api-Secret-Token` header and accept callbacks only from the
  owner's numeric Telegram user ID.
- Proposal messages include asset, side, amount, limit price, policy-check
  summary, thesis and risks, invalidation level, expiry time, and two buttons.
- Callback data carries `proposal_id` plus an HMAC (KMS-backed key) and a nonce,
  so approvals are single-use and expire. The TTL and drift values come from B14
  (240-minute TTL with ATR-based drift limits, suited to daily-timeframe signals).
- On approval: re-read the portfolio, price, regime, and gates; reject on
  excessive drift; then hand off to the executor along with the pre-authorized
  stop and target orders (B3).
- The bot token lives in Secrets Manager and only the approval Lambda can read it.
- The same bot provides `/kill` (sets the SSM kill switch) and `/status`, so the
  kill switch is reachable from a phone.

### A9. Kill switch and config authority are ambiguous - Medium

The doc puts policy in repo YAML and flags in SSM/AppConfig without saying which wins.

**Recommendation**
- Policy YAML in the repo is the only source of limits. It is packaged with each
  deploy and its SHA-256 is recorded as `policy_version` on every event.
- SSM holds only `EXECUTION_MODE`, `TRADING_ENABLED` and `KILL_SWITCH`. Logic
  fails closed: a missing or unreadable parameter means no trading.
- The executor reads these flags on every invocation, with no cache.

### A10. LLM layer details missing - Medium

**Recommendation**
- Default model: **Claude Haiku 4.5** via the `us.` cross-region inference
  profile (available in this account). Alternative candidate: Nova 2 Lite. Record
  the model ID and inference profile ARN per decision.
- Structured output: use Converse with structured outputs where the model
  supports them; otherwise use forced tool-use with a JSON schema. Always
  validate locally with Pydantic (§6.3 already requires this).
- Enable Bedrock model invocation logging to S3 for audit, with short retention in dev.
- Build an offline **eval harness**: a golden set of 20-40 recorded dossiers with
  expected decision classes. Any change to a prompt or model must run it in CI
  (stubbed) and on demand (live).
- Run an **ablation**: log what the deterministic-only path would have done, so
  the LLM's added value can be measured.
- Expected cost at 150 calls/month of about 3k input and 600 output tokens each
  is under US$1/month. The call caps exist to prevent runaway behavior, not
  because normal use is expensive.

### A11. Exchange rate limits and bans - Medium

Binance escalates repeated 429 responses into 418 IP bans. A scanner that fans
out kline requests across hundreds of symbols can trigger this.

**Recommendation**
- Central rate limiter in the adapter that respects `X-MBX-USED-WEIGHT-1M`.
  Back off on 429 and stop the scan entirely on 418.
- Prefer one `ticker/24hr` call for the whole universe before fetching klines,
  and fetch klines only for symbols that pass the liquidity filter.

### A12. Lambda packaging on Apple Silicon - Low

numpy wheels must target `manylinux` for Lambda.

**Recommendation**
- Lambda `arm64` with Python 3.13. Build with
  `uv pip install --python-platform aarch64-manylinux2014 --only-binary=:all:`
  into the artifact directory. CI builds the canonical artifact.

### A13. Observability specifics - Medium

**Recommendation**
- AWS Lambda Powertools for structured logging (with correlation ID), EMF
  metrics, and tracing.
- One CloudWatch dashboard. Alarms go to SNS, which forwards to the same
  Telegram bot (critical) and email.
- Log retention: 14 days in dev, 90 days in prod.
- Add an alarm on `ExecutionAttempts > 0` when `EXECUTION_MODE=SHADOW`. §15.2
  already lists it; it must be implemented with metric math against the SSM state.

### A14. Policy units are inconsistent - Medium

In §4.2 `maximum_spread_pct: 0.50` means 0.50%, while in §7 `max_trade_portfolio_pct: 0.05`
means 5%. One of them will be misread in code.

**Recommendation**
- All `*_pct` fields are fractions (`0.05` = 5%). Spreads and slippage are basis points
  (`maximum_spread_bps: 50`). The Pydantic policy model enforces fraction ranges `(0, 1]`.

### A15. Data protection and DR - Low

**Recommendation**
- DynamoDB PITR, S3 versioning, deletion protection on prod tables, and
  KMS-encrypted secrets.
- A documented restore drill before M6.

---

## Part B - Investor / market-behavior gaps

Bottom line from the investor review: the document is a well-engineered funnel for
*safe execution*. It is not yet an *investment system*. It never states what edge it
harvests, when to sell, how regime changes behavior, or what evidence would show it
beats simply buying BTC.

### B1. No defined strategy or edge - Critical
"Score >= 70 means the LLM looks at it" is not a testable hypothesis.

**Recommendation:** Write ADR-0008 (strategy spec) before any scoring code. It
should commit to a single strategy, for example: *regime-filtered, cross-sectional
momentum among liquid Binance alts relative to BTC, with 2-8 week holds, positive
excess return after costs*. It defines the universe, entry, exit, sizing, holding
period, and **kill criteria**. Example kill criterion: after 40 or more closed
trades or 6 months, if discovery underperforms the random baseline or BTC DCA net
of costs, the sleeve stops and its capital goes to core.

### B2. Buy-only design: no exits, no position lifecycle - Critical
The decision schema is `BUY | WATCH | REJECT`, and nothing monitors `invalidation`.
In crypto, exits decide most of the return.

**Recommendation**
- Position state machine:
  `PROPOSED -> APPROVED -> ENTRY_PENDING -> OPEN -> PARTIAL_EXIT -> EXIT_PENDING -> CLOSED`.
- Deterministic exits. The LLM never decides an exit.
- An hourly position monitor.

```yaml
exits:
  atr_period_days: 14
  initial_stop_atr_mult: 2.5
  initial_stop_max_pct: 0.15
  partial_take_profit: {at_r_multiple: 2.0, fraction: 0.33}
  trailing_stop: {type: chandelier, atr_mult: 3.0, activate_after_r: 1.0}
  time_stop: {days: 21, condition: "return_vs_btc <= 0"}
  max_holding_days: 90
  forced_exit_triggers: [delisting_announced, monitoring_tag_added, regime_risk_off,
                         unlock_pct_circ_within_7d_ge_0.02, rs_rank_percentile_below_0.40]
```

### B3. Exits must never be blocked by buy budgets or human latency - Critical
- Risk-reducing orders are exempt from daily and monthly trade budgets.
- Exchange-native stop orders (`STOP_LOSS_LIMIT` or OCO) are placed at entry. The
  human approves the **whole trade plan** (entry, stop, and target), so the stop
  is pre-authorized.
- New property test: *a sell can never be blocked by a buy budget*.

### B4. No regime model; a relative score always produces candidates - Critical
A 0-100 score normalized against the current universe always ranks something near
the top, even in a crash. "Stay in cash" must be a first-class outcome.

**Recommendation:** A regime classifier with hysteresis runs before scoring. Its
inputs are free and backtestable: BTC versus SMA200, the SMA50/SMA200 cross,
universe breadth above EMA50, ETH/BTC trend, BTC dominance excluding stablecoins,
stablecoin supply change (DefiLlama), and BTC drawdown from its 90-day high.

```yaml
regime:
  states:
    RISK_ON:  "btc > sma200 AND breadth >= 0.50"
    NEUTRAL:  "exactly one of the above holds"
    RISK_OFF: "(btc < sma200 AND breadth < 0.30) OR btc_drawdown_90d > 0.20"
  hysteresis_days: 3
  discovery_policy:
    RISK_ON:  {new_entries: true,  size_mult: 1.0, min_score: 70}
    NEUTRAL:  {new_entries: true,  size_mult: 0.5, min_score: 80, require_rs_vs_btc_30d_positive: true}
    RISK_OFF: {new_entries: false, trailing_atr_mult: 1.5}
```

Also: 90-day beta and correlation to BTC per candidate, beta-weighted discovery
exposure of 12% of the portfolio or less, at most 2 positions per sector, and an
event-calendar blackout (FOMC, CPI, Binance maintenance, token unlocks and migrations).

### B5. The scoring model chases pumps and mixes non-signals in - High
- Momentum, trend, and relative strength make up 50% of the score but are one
  correlated factor. Short-horizon (1h-24h) crypto returns tend to **reverse**, so
  treat spikes as a penalty, not a reward.
- Liquidity (20%) is already a gate. Portfolio fit (15%) depends on current
  holdings, which breaks replayability.
- The candle timeframe is undefined. EMA200 on daily bars needs 200 or more days
  of data, but the minimum history is only 90 days.

**Recommendation**
- Decisions use **daily candles closed at 00:00 UTC**: one scan per day after the
  close, plus the hourly position monitor.
- Score v2 = trend/relative-strength (30d/90d return vs BTC, volatility-adjusted,
  skipping the last 1-3 days, about 40%) + an extension penalty (more than 2.5 ATR
  above EMA20, RSI over 78, or 7-day return above the universe's 95th percentile)
  + a small tokenomics component.
- Liquidity becomes a gate and sizing input only. Portfolio fit moves to sizing
  and the validator.
- **Validate each component's information coefficient** (its correlation with
  future returns) per regime on a survivorship-free backtest before fixing
  weights. Freeze weights for 3 months or more. No re-weighting on fewer than 30
  closed trades.
- Use the free kline fields already being fetched: trade count and taker-buy volume.

### B6. Universe thresholds are loose and measured on the wrong window - High

| Threshold | Doc | Normal lane | High-risk lane (half size) |
|---|---|---|---|
| Volume | $5M (24h) | **30-day median** >= $10M and minimum day >= $2M | median >= $3M |
| Market cap | $50M | >= $300M or top 150 | $75M-$300M |
| Supply overhang | none | circulating/total >= 0.40 or FDV/MC <= 2.5 | >= 0.25 with a known unlock schedule |
| History | 90 days | >= 200 daily bars | >= 90 days |
| Spread | 0.50% | median <= 10 bps over 6 or more snapshots | <= 25 bps |
| Depth (within ±2%) | none | >= $100k per side | >= $25k |
| Turnover (24h volume / market cap) | none | 1%-50% | flag above 50% |

**Exclusions to add:** all stablecoins (maintained list: FDUSD, TUSD, USDe, USD1,
PYUSD, EUR stables), wrapped, pegged, and liquid-staking tokens (WBTC, WBETH, BNSOL,
PAXG), fan tokens, any `status != TRADING`, Monitoring Tag assets, announced or
voted delistings, suspended deposits or withdrawals, and pending migrations or
redenominations (these break price-history continuity).

**Wash-trading and pump heuristics** (any one sets `manipulation_suspect`, which
blocks escalation for 7 days): turnover above 50% of market cap; volume z-score
above 4 with a price move under 2%; a spike concentrated in fewer than 3 candles;
average trade size more than 3 standard deviations from the token's baseline;
taker-buy ratio above 0.7 on a vertical move; open interest and funding spiking
with price.

**Also:** flag a Binance volume share above 80% or below 10% of aggregate volume
(CoinGecko). Freeze new entries if USDC/USDT deviates more than 0.5% for over an
hour. Lengthen the new-listing lane to 90 days before the high-risk lane and 200
days before the normal lane.

### B7. Tokenomics data plan is unrealistic for the budget - High
- CoinGecko Demo is the primary source and CoinMarketCap Basic the cross-check.
  DefiLlama (free) covers DeFi fees and revenue plus stablecoin supply.
- Unlock-data APIs (Tokenomist, CryptoRank) are paid and hard to justify at this
  capital level. Do a **manual unlock check on the approval card** instead.
- Keep a hand-verified Binance-asset-to-CoinGecko-ID mapping, because tickers collide.
- Free fundamentals history is short (about a year), so **fundamentals cannot be
  backtested across 2021-22**.

```yaml
fundamentals_gates:
  block_if_missing: [market_cap, circulating_supply, coingecko_id_verified]
  block_if:
    - "circ_ratio < 0.40 AND unlock_schedule_unknown"
    - "known_unlock_pct_circ_next_14d >= 0.01"
    - "known_unlock_pct_circ_next_90d >= 0.05"
    - "abs(cg_mcap / cmc_mcap - 1) > 0.25"
    - "fdv_to_mcap > 4"
  data_max_age_hours: 36
```

### B8. Sizing math is undefined and internally inconsistent - High
Portfolio value is never defined. At a $1k portfolio, a 1% discovery position is
$10. A one-third partial exit of that falls below Binance's typical $5 minimum
order, which leaves unsellable dust.

```yaml
sizing:
  risk_per_trade_pct_of_portfolio: 0.0025
  size_usd: "min(risk_usd / stop_distance_pct, max_trade_usd, max_discovery_asset_pct * portfolio) * regime_size_mult / max(beta_btc, 1)"
  min_position_usd: "max(25, 4 * symbol_min_notional)"   # skip the trade if smaller
limits:
  max_open_discovery_positions: 5
  max_positions_per_sector: 2
  beta_weighted_discovery_exposure_max: 0.12
  averaging_down_in_discovery: false
circuit_breakers:
  discovery_sleeve_dd_from_hwm: {halt_new_entries: 0.20, review_and_flatten: 0.30}
  consecutive_losses: {count: 3, pause_days: 14}
  same_asset_reentry_cooldown_days: 10
  monthly_realized_loss_pct_portfolio: 0.03
```

### B9. Core BTC/ETH sleeve and cash reserve have no logic - High
- The core is a deterministic weekly DCA (BTC/ETH 70/30, limit orders, no LLM,
  no scoring). Rebalancing is band-based, at most monthly.
- Core and discovery get **separate monthly budgets**, so they don't compete for
  the single $750 cap.
- The reserve gets a deployment rule (for example, 25% into core at BTC -30% from
  its 1-year high, another 25% at -50%) and is held in **USDC**, converted to USDT
  just in time for each trade.

### B10. Execution realities at small size - High
- The validator checks the current `exchangeInfo` filters: `PRICE_FILTER`,
  `LOT_SIZE` (round down), `NOTIONAL`/`MIN_NOTIONAL`, `MARKET_LOT_SIZE`,
  `PERCENT_PRICE_BY_SIDE`, `MAX_NUM_ALGO_ORDERS`, and the allowed `orderTypes`.
  It also simulates full and partial exits after fees.
- Pay fees in BNB from a tracked BNB float. Run periodic dust conversion, logged as trades.
- Entries use `LIMIT_MAKER` or a limit at mid with a 10-minute timeout, then cross
  once (slippage capped at 0.3%) or cancel. Exits use stop-limit orders with the
  limit 1.5-2% beyond the trigger. Never use raw market orders outside the top 50.
  Use `newClientOrderId` equal to the idempotency key. Handle partial fills.
- Report discovery returns **net of platform and data costs** allocated to the
  sleeve. US$5-20/month on a US$1k sleeve is a 6-24% annual hurdle.

### B11. Evaluation design cannot detect an edge - Critical
- 30 days of shadow produces no mature 30-day outcomes, only 5-20 correlated
  proposals, and effectively one market regime.
- **Three evaluation layers:** (1) a signal study (forward returns vs BTC at 7, 14,
  30, and 60 days for *every* scored candidate, with information coefficient by
  bucket and regime); (2) trade simulation with the real exit rules and costs,
  filling at the next bar; (3) a portfolio equity curve with the real contribution schedule.
- **Benchmarks:** money-weighted BTC DCA and BTC/ETH DCA with *identical cash
  flows*, an equal-weight eligible universe, a **random-selection baseline**
  (1,000 simulations), and deterministic-only vs deterministic+LLM.
- **Metrics:** excess return vs BTC, IRR and time-weighted return, Sharpe and
  Sortino (annualized over 365 days), max drawdown and Calmar ratio, hit rate,
  payoff ratio, expectancy in R-multiples (gain or loss as a multiple of the amount
  risked), profit factor, exposure-adjusted return, beta and alpha, worst and best
  unrealized move per trade (MAE/MFE), turnover, fee drag, implementation
  shortfall, and approval-latency cost.
- **Point-in-time universe including delisted pairs** (LUNA, FTT, and the
  2023-25 delisting waves), walk-forward tuning, and no historical use of today's
  market caps, tags, or sectors.
- **LLM decisions cannot be backtested** for any period before the model's
  training cutoff. Evaluate the LLM only going forward.
- **Go-live gate:** at least 90 days of shadow **and** at least 30 closed shadow
  trades, results within the backtest's expected band, and no worse than the
  random baseline. "It was positive" is not enough; in a bull market that is just beta.

### B12. LLM role should be veto-and-explain only - High
- With derived numbers as its only input, the LLM is a noisy re-weighting of the
  score, with narrative bias toward famous tickers and hot sectors.
- The LLM can move **BUY down to WATCH or REJECT, never up**. Remove
  `suggested_amount_usd` from the schema; sizing is deterministic.
- Invalidation becomes machine-checkable fields computed by code (`stop_price`,
  `time_stop_date`, `rs_rank_floor`, `regime_exit`). The LLM may only tighten them.
- The LLM's real value: turning qualitative evidence the score can't see
  (announcements, unlocks, migrations, project-text red flags, treated as
  untrusted input) into risk flags, and writing the bear case for the human.
- **Ablation:** shadow-track both the deterministic-only decision and the LLM
  decision, including LLM-vetoed candidates. Measure veto-set vs pass-set
  expectancy, Brier calibration, and the decision flip rate across 3 reruns of the
  same dossier. If 50 or more vetoes show no meaningful benefit, demote the LLM to
  explanation-only.
- Dossier additions: regime, score-component percentiles, distance from EMAs in
  ATR units, beta and correlation, drawdown from all-time high, liquidity stats,
  manipulation flags, taker ratio, funding and open interest, unlock calendar,
  listing age and tags, sector exposure, the deterministic trade plan, the
  bucket's historical base rate, and missing-data flags.

### B13. Venue, tax and custody - High
- **Venue:** the owner is in Colombia, so Binance.com is the venue. Binance.US
  serves US persons only. The HTTP 451 block from AWS US regions is addressed by
  A1 (exchange gateway). Record the venue in ADR-0009 and make `execution_venue`
  configuration.
- **Tax-grade fill records:** exchange trade ID and order ID, timestamp, quantity,
  price, fee amount **and fee asset**, USD value at fill time, lot ID, and
  cost-basis method. BNB fee payments and dust conversions are disposals too. Add
  a Koinly/CoinTracker-compatible CSV export and retain records for 7 years or
  more. Colombian (DIAN) treatment of crypto gains depends on the holding period
  (*ganancia ocasional* vs ordinary income); confirm with a local tax advisor.
  Report discovery performance after estimated tax.
- **Custody:** cap the exchange balance (for example, the larger of $2k or 3
  months of planned buys) and sweep core holdings to self-custody manually each
  month. The portfolio snapshot must include off-exchange holdings (manual entry
  or read-only address balance), or every percentage limit is wrong.

### B14. Approval UX specifics - High
- **Approval card:** asset, regime, top 3 deterministic reasons plus the LLM bear
  case, trade plan (limit, size in USD and % of portfolio, stop, 1R in USD,
  partial target, time stop), 90-day unlocks with a manual-verify link, liquidity,
  manipulation flags, sector and beta exposure, recent discovery P&L and loss
  streak, fee and slippage estimate, chart link, and **reason codes** on Reject.
- **TTL and drift (supersedes the 15-minute default in A8):**

```yaml
approval:
  ttl_minutes: 240
  max_adverse_drift: "max(0.5 * ATR_daily_pct, 0.02)"
  max_favorable_drift: "1.0 * ATR_daily_pct"
  revalidate_on_execute: [regime, gates, filters, portfolio, stablecoin_peg]
  exits_preauthorized_with_entry: true
```

- Batch proposals once a day after the 00:00 UTC close (19:00 local). Track the
  approval rate: above 95% means rubber-stamping; below 20% means the system is noisy.

---

## Part C - Consolidated priorities

### Must resolve before Phase 1 ships
1. **A1** Binance reachability spike and ADR-0003 (exchange gateway if blocked).
2. **A6/A7** Account baseline and Terraform bootstrap (budgets, CloudTrail, OIDC, boundaries).
3. **A3/A4/A5** Orchestration, storage split, and ledger access patterns (built into Phase 1).

### Must resolve before strategy code (new M2)
4. **B1** Strategy spec (ADR-0008), with **B13** venue ADR-0009.
5. **B11** Point-in-time historical dataset including delisted pairs, plus the backtest harness.
6. **B4/B5/B6/B7** Regime classifier, score v2 with information-coefficient
   validation, tightened universe and exclusions, fundamentals gates.

### Must resolve before shadow evaluation starts
7. **B2/B3/B8/B9/B10** Position lifecycle, deterministic exits exempt from buy
   budgets, sizing and circuit breakers, core DCA sleeve, symbol filters.
8. **B11** Benchmarks (identical cash flows, random baseline) and the metric set.

### Must resolve before live execution
9. **B12** LLM as veto-only with an ablation arm; **A10** eval harness.
10. **A2/A8/B14** Static egress or key policy, Telegram approval with
    pre-authorized exits, and **B13** tax-grade fills and custody limits.

### What to keep (both reviewers)
Deterministic risk authority; SHADOW first; spot-only with withdrawals disabled; an
append-only replayable ledger with policy, prompt, and model versions and token
cost; "missing data never counts as positive"; the separate new-listing lane;
thresholds as versioned hypotheses; revalidation before execution; "challenge the
bullish thesis" in the prompt; LLM call budgets and dossier dedupe; property tests
such as "increasing size can never turn a rejection into approval."

## Open questions for the owner
1. **Capital:** expected starting portfolio value and monthly contribution. All
   sizing caps depend on it (B8).
2. **Tax residency:** confirm Colombian tax residency for the tax-record design (B13).
3. **GitHub plan:** is the repo private on a free plan? Branch protection and
   environment reviewers need GitHub Pro for private repos (Phase 1, Task 10).
4. **Data spend:** confirm US$0/month for paid data in v1, with manual unlock checks (B7).
