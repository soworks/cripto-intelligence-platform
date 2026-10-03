# ADR-0007: Package layout `src/cip`

Status: Accepted (2026-10-03)

## Decision
The reference doc's `src/adapters`, `src/domain`, ... become `src/cip/adapters`,
`src/cip/domain`, ... so the code is an importable, typed package
(`import cip.domain.policy`). Lambda handlers are referenced as
`cip.handlers.<module>.<function>`.
