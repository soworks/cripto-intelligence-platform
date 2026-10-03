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

## Amendment (2026-10-03): append-only enforcement and its IAM limits (PR #1 review M3)
- Workload boundary: denies `UpdateItem`, `DeleteItem`, `BatchWriteItem`, PartiQL
  update/delete, `UpdateTable`, `DeleteTable`, `UpdateTimeToLive`,
  `UpdateContinuousBackups`, `RestoreTableFromBackup`, `RestoreTableToPointInTime`,
  `ImportTable`, `DeleteBackup` and resource-policy changes on `cip-<env>-ledger` (and its
  backups).
- Deploy roles: deny `PutItem`, `UpdateItem`, `DeleteItem`, `BatchWriteItem`,
  `PartiQLInsert/Update/Delete` and `DeleteTable` on `cip-<env>-ledger`. CI manages the
  table, never its items. Deletion protection is on in prod.
- IAM limit: `dynamodb:PutItem` cannot be restricted to "new items only". A principal
  allowed to `PutItem` can overwrite an existing item unconditionally. Append-only for
  the pipeline role therefore also relies on the application: every ledger write is a
  `TransactWriteItems` with `attribute_not_exists` conditions on both the event item and
  its `IDEMP#<event_id>` guard. PITR (35 days) is the recovery path for an overwrite.
- Detecting MODIFY/REMOVE needs DynamoDB Streams plus a consumer; tracked as a follow-up
  issue rather than built in Phase 1.
