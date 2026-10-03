# ADR-0002: Dynamic research universe; narrow execution policy

Status: Accepted (2026-10-03)

## Decision
Research discovers the Binance Spot universe dynamically and narrows it through
deterministic filters. Execution eligibility is narrower and governed by policy:
core assets, discovery caps, and a separate high-risk lifecycle for new listings.
A listing event alone never creates a BUY proposal.

## Consequences
Universe snapshots are stored per scan, including delisted symbols, so backtests
avoid survivorship bias.
