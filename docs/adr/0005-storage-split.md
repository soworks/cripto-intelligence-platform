# ADR-0005: Storage split - S3 snapshots, DynamoDB ledger/state/counters

Status: Accepted (2026-10-03)

## Decision
- S3 `cip-<env>-data-<account>`: raw exchange responses and per-scan universe and
  feature snapshots under `snapshots/dt=YYYY-MM-DD/scan=<correlation_id>/`, plus
  historical datasets. Versioned.
- DynamoDB `cip-<env>-ledger`: append-only decision events. PK `ASSET#<symbol>`,
  SK `EVENT#<ts>#<id>`; GSI1 by correlation ID; GSI2 by event type and day.
  UpdateItem, DeleteItem, BatchWriteItem, and PartiQL mutations are denied by the
  permission boundary.
- DynamoDB `cip-<env>-state`: mutable materialized state (positions, shadow
  portfolio, approvals, watchlist, listing lifecycle, dossier dedupe).
- DynamoDB `cip-<env>-counters`: atomic budget counters (LLM calls, trade USD)
  with conditional ceilings. This is the hard guardrail.

## Consequences
Ledger events reference snapshot S3 keys with SHA-256 hashes, which keeps items small.
