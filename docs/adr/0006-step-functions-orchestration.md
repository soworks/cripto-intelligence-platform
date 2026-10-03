# ADR-0006: Step Functions Standard orchestrates the scan pipeline

Status: Accepted (2026-10-03)

## Decision
EventBridge Scheduler starts a Step Functions Standard state machine. Each stage is
a small Lambda that calls pure domain code. Failures are caught and recorded as a
`PIPELINE_FAILED` ledger event, and CloudWatch alarms fire on failures or a
missed schedule. Callers may supply `correlation_id`; otherwise the state machine
generates one with `States.UUID()`.

## Consequences
Each run gets a visual audit trail, and retries need no custom code. Hourly
cadence costs cents per month.
