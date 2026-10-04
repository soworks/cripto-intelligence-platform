# ADR-0011: LLM provider strategy

Status: Accepted (2026-10-04)

## Context

ADR-0001 already fixes the authority split: code computes features, filters,
scores, sizes, and validates. The model returns a schema-constrained
recommendation. It has no order tool. The reference architecture puts that call
behind `DecisionProvider`. Discovery, scoring, portfolio, policy, approval, and
execution must not know which vendor answered.

The roadmap originally named Bedrock, with Claude Haiku 4.5, as the M5 default.
That was a v1 choice, not an architectural dependency. Two account facts now
block treating Bedrock as the path that is ready:

- ADR-0003, spike on 2026-10-03: Claude Haiku 4.5 failed because the Anthropic
  use-case form has not been submitted. Nova 2 Lite was throttled because the
  account quotas are 0.
- Bedrock Playground in us-east-1, this account: `openai.gpt-6.1-sol` and
  `openai.gpt-5.6-luna` return `AccessDeniedException`. The console points at
  AWS Sales or support. A model listed in the Bedrock catalog is not proof this
  account can invoke it. The root cause is an account/model-access limitation.

Bedrock on-demand is not a monthly commitment. AWS describes on-demand Standard
as pay-as-you-go per token with no time-based term
([Bedrock pricing](https://aws.amazon.com/bedrock/pricing/),
[on-demand tiers](https://aws.amazon.com/bedrock/service-tiers/)). Provisioned
Throughput is the mode that bills for reserved capacity and can require a 1- or
6-month commitment
([Provisioned Throughput](https://docs.aws.amazon.com/bedrock/latest/userguide/prov-throughput.html)).
This workload will not use Provisioned Throughput. The Bedrock concern is the
account/model-access limitation, a second vendor hop, and whether another path
is cheaper or simpler. It is not a subscription fee.

`DecisionProvider` does not exist in code yet. Policy schema 2 already caps
`ai.max_calls_per_day` at 10 and `ai.max_calls_per_month` at 150. Operating
mode stays SHADOW.

## What the current docs actually say

Checked 2026-10-04. Where two OpenAI pages disagree, both are quoted. Neither
page describes a Lambda starting a ChatGPT-subscription completion.

**ChatGPT MCP is an interaction surface, not a decision provider.** Developer
mode is a ChatGPT session feature. The user turns it on, connects a remote MCP
URL, and selects the app inside a conversation. ChatGPT then calls the server.
Write tools ask for confirmation by default, and a remembered approval does not
survive a new conversation
([ChatGPT Developer mode](https://developers.openai.com/api/docs/guides/developer-mode)).
There is no documented API by which EventBridge or Lambda can open that
session and spend the ChatGPT plan. A later milestone may expose a read-only
MCP server for the owner to query by hand. That server is not a
`DecisionProvider`, and it is not on the scan path.

**Plan limits are documented inconsistently.** The developer guide, fetched in
full, says developer mode with full read and write MCP is available to Plus,
Pro, Business, Enterprise, and Education on the web. The Help Center article
[Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)
says full MCP, including write actions, is in beta for Business, Enterprise,
and Edu, and that Pro can connect MCP servers with read and fetch permissions
only. The conflict is between OpenAI's own pages. It does not put MCP on the
scan path: even the more generous page still has the user start the chat.

**ChatGPT billing does not pay for the API.** ChatGPT and the API platform
have separate billing. Plus does not include API usage
([managing billing](https://help.openai.com/en/articles/9039756),
[What is ChatGPT Plus?](https://help.openai.com/en/articles/6950777)).
The API can also call an MCP server as a tool. That call is still API usage.

**OpenAI models on Bedrock are billed by AWS**, not by the ChatGPT plan
([OpenAI pricing, cloud platforms](https://developers.openai.com/api/docs/pricing)).
Account/model access is still required before that bill can exist.

## Comparison

| | Bedrock, OpenAI models | Direct OpenAI API | ChatGPT MCP | Gemini API, one alternative |
|---|---|---|---|---|
| Who starts the call | Lambda, after account/model access | Lambda | A person, inside a ChatGPT conversation | Lambda |
| Fits a scheduled scan | Yes, after access | Yes | No | Yes |
| Role on this platform | Possible later `DecisionProvider` | V1 `DecisionProvider` | Future interaction surface, not a `DecisionProvider` | Possible later `DecisionProvider` |
| Uses the existing ChatGPT subscription | No | No | Only for an interactive ChatGPT session, and the plan rules disagree | No |
| This account today | Account/model-access limitation for `openai.gpt-6.1-sol` and `openai.gpt-5.6-luna` | Not wired. Needs its own API projects | Cannot start from Lambda | Not wired |
| Structured output | Converse or tool use, then Pydantic | JSON schema, then Pydantic | Tool arguments the model chooses to send | Response schema, then Pydantic |
| Secret | None for on-demand IAM | Project service-account key in Secrets Manager | OAuth or no-auth on a server ChatGPT calls | API key in Secrets Manager |
| Monthly commitment | None on on-demand | None | The ChatGPT plan, which does not cover this call | None on paid on-demand |

Claude Haiku 4.5 on Bedrock is the same shape as the Bedrock column, with the
ADR-0003 blockers (use-case form and zero quotas) instead of the OpenAI
account/model-access limitation. It stays a later provider, not v1.

## Decision

V1 is `Lambda -> DecisionProvider -> OpenAI Responses API`, HTTPS, on-demand
tokens. `DecisionProvider` stays a small protocol. OpenAI request fields live
in the adapter and in configuration. They do not appear on
`InvestmentDecision`.

ChatGPT MCP is a future interaction surface. It is not a `DecisionProvider`.
No `ChatGPTMCPProvider` is part of this design.

Bedrock stays a possible later adapter behind the same protocol. It is not the
v1 implementation, because of the account/model-access limitation above and
the ADR-0003 Anthropic blockers. Revisit it when an invocation of a chosen
model returns a normal completion in us-east-1.

### Projects and credentials

Dev and prod use two OpenAI projects, `cip-dev` and `cip-prod`. Each project
has its own service account. The Lambda uses that service account's project
key, never a personal user key. Dev cannot read the prod key, and prod cannot
read the dev key. The key is created outside Terraform, shown once, and stored
in that environment's Secrets Manager secret. The Lambda environment holds the
secret ARN. It does not hold the key.

### Request shape and retention

Every call is a foreground Responses API request with `store=false`.
Background mode is not used. As of the platform data-controls page on
2026-10-04
([your data](https://developers.openai.com/api/docs/guides/your-data)):

- API inputs are not used to train models unless the organization opts in.
  This platform does not opt in.
- With `store=true`, or when `store` is omitted, Responses application state
  is kept for at least 30 days. `store=false` is how that hold is turned off.
- Abuse-monitoring logs may still contain prompts and responses for up to 30
  days, unless the organization is approved for Zero Data Retention or
  Modified Abuse Monitoring. That approval is a separate OpenAI process. This
  ADR does not assume it has been granted.
- Zero Data Retention, once granted, forces `store=false` even if a caller
  sends `true`.
- Image and file inputs can be retained for CSAM review even under those
  controls. This call sends text only.

The adapter does not call `/v1/conversations`, Assistants, threads, files, or
batches.

### Call configuration

Provider, model, and call limits come from configuration. V1 values:

```yaml
ai:
  provider: openai
  model: gpt-6-luna
  reasoning_effort: low
  max_output_tokens: 800
  service_tier: default
  timeout_seconds: 30
  max_attempts: 2
  pricing:
    version: "2026-10-04"
    input_per_million_usd: "0.10"
    output_per_million_usd: "0.50"
```

`service_tier: default` is the standard on-demand rate. `auto`, `flex`, and
priority tiers are not used. `max_attempts: 2` means one try and at most one
retry. `reasoning_effort`, `max_output_tokens`, `service_tier`,
`timeout_seconds`, and `max_attempts` are required. An unsupported
`reasoning_effort` for the configured model fails at startup, before a dossier
is sent.

### Retries

A retry stays on the same provider and the same model, and it resends the same
body. It is allowed only for `PROVIDER_TIMEOUT` and `PROVIDER_RATE_LIMIT`, and
only while the attempt count is below `max_attempts`. The second attempt waits
one second.

Production has no `fallback_provider`. A configured fallback is a dev-only
field, and using it is an explicit second configuration, recorded on the
ledger row. It is not a retry. Authentication, account/model access, an
unknown model, a routing error, and a schema failure do not retry and do not
switch provider or model. The scan still records the deterministic result.

### Ledger

Each attempt appends one ledger record. The record includes provider, the
configured model, the model id actually returned, provider request id, attempt
number, prompt version, pricing version, latency, input tokens, output tokens,
and estimated cost. Estimated cost is
`input_tokens * input_per_million_usd / 1_000_000` plus the same for output,
using the pricing block above. A model change whose pricing version was not
updated fails closed. Prices are not hardcoded in the adapter.

### Luna against Sol

`gpt-6-luna` is the configured default only while it passes the golden set
below against `gpt-6.1-sol`. The set is frozen before either model is scored:
at least 40 dossiers, with at least 10 labeled `VETO`, 10 labeled
`DOWNGRADE_WATCH`, 10 labeled `PASS`, and 10 adversarial cases (missing
fields, instruction text inside a dossier string, and a bait sizing field).
Both models see the same prompt version, `store=false`, and the v1 call
configuration. A response counts only when Pydantic accepts it.

Luna stays the default only when all of these hold:

- Schema-valid rate is at least 95%, and no more than 2 percentage points
  below Sol.
- Exact agreement with the labeled decision is at least 80%, and no more than
  5 percentage points below Sol.
- Recall on labeled `VETO` cases is at least 90%, and is not below Sol.
- No accepted response contains a sizing field, and no labeled `VETO` comes
  back as a buy.
- Median latency is within `timeout_seconds`.

If any line fails, a reviewed policy change sets the model to `gpt-6.1-sol`
and bumps the pricing version to Sol's rates ($2.00 input and $10.00 output
per 1M at the 2026-10-04 short-context standard price). Luna is not left in
place. `gpt-5.6-sol` is not a candidate. Its short-context rate is $4 / $20
per 1M, and this account cannot call it through Bedrock.

### Errors

Stable strings. Same-provider retry applies only where the retry section says
so.

- `MODEL_ENTITLEMENT_ERROR` for an account/model-access limitation
- `PROVIDER_AUTHENTICATION_ERROR`
- `PROVIDER_RATE_LIMIT`
- `MODEL_NOT_FOUND`
- `MODEL_ROUTING_ERROR`
- `MODEL_RESPONSE_SCHEMA_ERROR`
- `PROVIDER_TIMEOUT`

## What stays fixed

- SHADOW remains the default.
- The model is advisory. It never sees Binance credentials and never calls
  Binance.
- The prompt carries the compact dossier: score, RSI, EMA distances,
  volatility, volume ratio, market cap, FDV, circulating supply, tokenomics,
  liquidity, portfolio exposure, remaining budgets, and risk flags. It does
  not carry raw candles.
- After the model returns, deterministic code enforces the monthly and
  category budgets, the per-trade maximum, concentration, reserves, the
  eligible-asset rules, spot-only, and the daily and monthly limits.
- A passing result is a `TradeProposal`. A live BUY still needs human
  approval. After approval the platform re-reads the Binance balance and
  price and runs the validator again.
- Call caps stay: per scan, 10 per day, 150 per month, plus dossier-hash
  dedupe.

## Work this decision requires

No Terraform, secret, or provider module is added by this file. M5 implements
the following and nothing wider.

**Code.** `DecisionProvider` as a protocol whose `analyze` method takes a
dossier, a portfolio snapshot, and the policy, and returns
`InvestmentDecision`. `OpenAIProvider` is the only adapter. Vendor types stay
inside that adapter. `BedrockProvider` waits until an invocation succeeds.
Selection reads the configuration block above. Domain modules do not import a
vendor SDK.

**Terraform.** Two Secrets Manager secrets, one per environment, each holding
that project's service-account key. The analyzer role in that environment may
`GetSecretValue` on its own secret ARN only. The Lambda stays outside a VPC.
Prod configuration rejects `fallback_provider`.

**Secrets.** A person creates `cip-dev` and `cip-prod`, creates one service
account in each, and loads each key into the matching secret. The key is not
written to GitHub Secrets, Terraform state, source, prompts, or logs.

**Tests.**

- `test_provider_selected_from_configuration`
- `test_openai_provider_returns_valid_investment_decision`
- `test_openai_request_sets_store_false`
- `test_provider_schema_failure_is_rejected`
- `test_provider_timeout_does_not_create_trade_proposal`
- `test_retry_stays_on_the_same_provider_and_model`
- `test_provider_auth_failure_does_not_fallback_silently`
- `test_dev_can_use_explicit_configured_fallback`
- `test_prod_requires_explicit_provider_change`
- `test_api_key_never_appears_in_logs`
- `test_llm_never_receives_exchange_credentials`
- `test_policy_validator_remains_provider_independent`
- `test_pricing_version_is_required_for_the_configured_model`
- `test_decision_provider_contract_accepts_a_non_openai_fake`

The contract test runs the same assertions against `OpenAIProvider` and a
fake provider: the return type is `InvestmentDecision`, the decision type has
no OpenAI field, and a domain module that imports the OpenAI adapter fails
the test. One integration test uses a mocked Responses endpoint. One opt-in
live smoke test sends a tiny structured request against the dev project and
checks connectivity, authentication, the schema, token counts, latency, the
provider request id, and the cost computed from the pricing version. It must
not place a Binance order.

## Cost

Prices were read on 2026-10-04. OpenAI figures are short-context standard
rates from the [API pricing page](https://developers.openai.com/api/docs/pricing).
Haiku 4.5 is the Anthropic list price effective 2026-07-24: $1.00 / $5.00 per
1M on the Claude API, and $1.10 / $5.50 for Bedrock cross-region. Gemini 3.8
Flash (`gemini-3.8-flash`) paid standard is $0.75 / $3.75 per 1M through
2026-12-31, then $1.50 / $7.50
([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)).

Assumption for every cell: 3,000 input tokens and 600 output tokens, no
cache, no tools, no web search. Gemini bills thinking tokens as output, so
its cells are a floor if thinking is on. The current policy cap is 150 calls
a month. The 500 column is above that cap and would be a policy change. The
luna row is the first pricing version. Runtime cost uses that versioned
block, not this table.

| Provider and model | 50 calls | 150 calls | 500 calls |
|---|---:|---:|---:|
| OpenAI `gpt-6-luna` | $0.03 | $0.09 | $0.30 |
| OpenAI `gpt-6.1-sol` | $0.60 | $1.80 | $6.00 |
| OpenAI `gpt-5.6-sol` | $1.20 | $3.60 | $12.00 |
| Anthropic Haiku 4.5 API | $0.30 | $0.90 | $3.00 |
| Bedrock Haiku 4.5, cross-region | $0.33 | $0.99 | $3.30 |
| Gemini 3.8 Flash, through 2026-12-31 | $0.23 | $0.68 | $2.25 |

These are token charges only. At the current cap, luna is under $0.10 a
month. The price gap is not why MCP stays off the scan path, and it is not
why Bedrock waits. MCP cannot run the scan. Bedrock is blocked by an
account/model-access limitation.

Gemini's free tier is excluded. Google states that free-tier content is used
to improve its products. Paid tier is not. Dossiers go to the paid tier only.

ADR-0009 still sets paid market data to $0. Model tokens are a separate line.
This decision accepts that line. It does not change the market-data budget.

## Migration

Switching vendors is a reviewed change to `ai.provider`, `ai.model`, and
`ai.pricing`, plus a secret and an IAM grant for the new adapter. Domain code
stays on `DecisionProvider`. Ledger rows name the provider, the requested
model, the returned model, and the pricing version, so old decisions stay
attributable. A Bedrock grant is added only when a real invocation has
succeeded.

## Consequences

- M5 implements `OpenAIProvider` first. The roadmap's "Bedrock" label on that
  milestone is updated in the same change, so the doc matches this decision.
- The Anthropic use-case form and the Bedrock quota requests from ADR-0003
  stay useful, and they are no longer on the M5 critical path.
- Two OpenAI projects and two service accounts are required. The ChatGPT Plus
  subscription does not supply them.
- `store=false` is mandatory. Abuse-monitoring retention of up to 30 days
  remains unless OpenAI later approves a tighter control.
- Production provider and model changes are pull requests. A failed call may
  retry once on the same provider and model. It does not silently change
  either.
