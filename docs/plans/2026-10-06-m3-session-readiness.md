# Session readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the closed-session input contract and a deterministic readiness check so `run_daily_scan` is not scheduled until a session's stored inputs are complete and free of look-ahead.

**Architecture:** Add one pure module, `cip.evaluation.session`, that reads already-stored documents and returns a readiness result. It reuses `UniverseSnapshot`, `ScanCandidate`, `DailyBar`, and `Observation`. It does not score, rank, write a `DecisionRecord`, call a provider, or schedule a Lambda. Producers and the daily workload are later plans, written only after this contract is accepted.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, the existing `cip.evaluation` and `cip.history` types.

## Global Constraints

- Part of #83. This plan does not complete #83, #21, or #22.
- Do not rewrite eligibility, regime, scoring, outcomes, or M4 lifecycle code.
- `run_daily_scan` stays a stored-input function and keeps making zero provider calls.
- Missing Score v2 weights block scoring, ranking, and `BUY`. They do not block a future `DecisionRecord`. The reason remains `INELIGIBLE` / `score_weights_not_frozen`.
- Do not attach anything to hourly `cip-prod-scan`. Do not write `PROD_SHADOW_STARTED`. Do not modify `prod_shadow_started_at` (`2026-10-06T01:22:36.255669Z`).
- Do not write `prod_decisions_started_at` or `score_v2_shadow_started_at` in this plan.
- Do not fill a missing historical or point-in-time value with a later observation.
- Funding and open-interest HTTP 451 stay on #28 and are not readiness inputs.
- The Monday assurance document is unchanged.
- Percentages stay fractions. Decimals stay `Decimal`. Booleans stay exact bools. Naive datetimes are refused.

---

## What this plan is

#83 has four planes: input, decision, outcome, assurance. This document is the review checkpoint for the input contract and the session-readiness rules. It is the first plan because a look-ahead or provenance mistake here would be copied into every later decision.

These plans are not this document, and they are not started until this contract is accepted:

- **Producers.** Write the universe snapshot, candidate packets, and explicit absences from captures taken at or before the close. No live `exchangeInfo` after the close.
- **Decision plane.** A separate daily-after-close workload calls `run_daily_scan` only when `SessionReadiness.ready` is true, then records `prod_decisions_started_at` on the first natural production success.
- **Outcome plane.** Existing 7/14/30/60-day outcomes mature from the immutable decisions.
- **Assurance plane.** No change. Monday reads whatever is stored.

## File structure

- Create: `src/cip/evaluation/session.py` — close instant, presence, and `assess_session`.
- Test: `tests/unit/evaluation/test_session.py` — the rules below, one failure mode per test.
- Do not modify: `src/cip/evaluation/scan.py`, `score.py`, `regime.py`, `eligibility.py`, `fundamentals.py`, `src/cip/handlers/pipeline.py`, `src/cip/handlers/assurance.py`.

## Contract

The session date is the daily bar's open date, the same date `run_daily_scan` already uses. The close is 00:00 UTC on the next day, the same instant `_close` already computes. A file may be written after the close. A value inside the file may not be from after the close.

`InputPresence` is one of `present`, `absent`, `not_produced`, `lookahead`.

- `present`: the object for this session exists, validates, and every value timestamp is at or before the close.
- `absent`: the producer wrote an explicit absence for this session and this input. Absence is not inferred from a missing key inside some other file.
- `not_produced`: neither the value nor an explicit absence exists. This blocks the session. It is not a `DecisionRecord`.
- `lookahead`: the object exists and a value timestamp is after the close. This blocks the session. The value is not rewritten to an earlier time.

`ready` is true only when every block is empty. These block:

- `as_of` is naive, not UTC, or earlier than the close.
- The universe snapshot is `not_produced`, its `session` differs, its symbols repeat, or its `observed_at` is after the close.
- The BTC daily-bar month object for the session month is `not_produced`.
- The BTC month object exists and does not contain the session `open_date` (`btc_session_bar_absent`). Do not substitute the next available day.
- A snapshot symbol's kline month object is `not_produced`.
- A snapshot symbol's candidate packet is `not_produced` or `lookahead`.
- `btc_dominance` or `stablecoin_supply` for the session date is `not_produced`, `lookahead`, or only a collection failure.
- A present CoinGecko or CMC market-cap value has no `source_timestamp` (`undated_fundamental`). An undated cap is not treated as the close.
- Any present `MarketSnapshot.as_of`, observation `source_timestamp`, or fundamental `source_timestamp` is after the close.

These do not block, and the result names them so the decision plane can still write one record per symbol:

- An explicit candidate absence. The later record reason is `missing_candidate`.
- A symbol month file that exists but has no bar for the session date. The bar is absent. It is not replaced.
- A short BTC history inside a month file that does contain the session date. Regime already returns `missing_btc_sma` or `missing_breadth`. That is a decision reason, not a skipped session.
- Score weights absent. The result reports `score_weights_not_frozen`. `ready` stays true. No rank and no `BUY` are implied.
- Funding or open interest, whether present, failed, or HTTP 451.

An explicit absence is this document and nothing else:

```json
{
  "kind": "absent_input",
  "session": "2026-10-05",
  "input": "candidate",
  "symbol": "SOLUSDT",
  "produced_at": "2026-10-06T00:05:00Z"
}
```

`produced_at` may be after the close. `session` must equal the session being assessed. `input` is `candidate` or `daily_bar`. A candidate absence has a symbol. A daily-bar absence has a symbol. Regime observations do not use this document; a required regime series is either a stored observation for that session date or a block.

Candidate bytes, when present, are a `ScanCandidate` document: `CandidateFacts`, `MarketSnapshot`, `CoinGeckoReading`, `CmcReading`, `UnlockReading`, and `Tokenomics` or null. The same object carries fundamentals and tokenomics. There is no second, later overlay.

---

### Task 1: Close instant and presence

**Files:**
- Create: `src/cip/evaluation/session.py`
- Test: `tests/unit/evaluation/test_session.py`

**Interfaces:**
- Consumes: `datetime`, `date`, `timedelta` from the standard library.
- Produces: `session_close(session: date) -> datetime` and `InputPresence`.

- [ ] **Step 1: Write the failing test**

```python
from datetime import UTC, date, datetime

import pytest

from cip.domain.errors import EvaluationError
from cip.evaluation.session import InputPresence, session_close


def test_the_close_is_the_next_utc_midnight() -> None:
    assert session_close(date(2026, 10, 5)) == datetime(2026, 10, 6, tzinfo=UTC)


def test_presence_names_the_four_states() -> None:
    assert [item.value for item in InputPresence] == [
        "present",
        "absent",
        "not_produced",
        "lookahead",
    ]


def test_a_boolean_session_is_refused() -> None:
    with pytest.raises(EvaluationError, match="session is a date"):
        session_close(True)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/unit/evaluation/test_session.py -q --no-cov`

Expected: FAIL, `cip.evaluation.session` cannot be imported.

- [ ] **Step 3: Implement the close**

```python
"""Closed-session readiness. A missing input stays missing."""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from cip.domain.errors import EvaluationError


class InputPresence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    NOT_PRODUCED = "not_produced"
    LOOKAHEAD = "lookahead"


def session_close(session: date) -> datetime:
    """00:00 UTC on the day after the bar open date."""
    if type(session) is not date:
        raise EvaluationError("session is a date")
    return datetime.combine(session + timedelta(days=1), time.min, tzinfo=UTC)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/unit/evaluation/test_session.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cip/evaluation/session.py tests/unit/evaluation/test_session.py
git commit -m "$(cat <<'EOF'
Name the closed-session instant before any decision is scheduled.

EOF
)"
```

### Task 2: Readiness result

**Files:**
- Modify: `src/cip/evaluation/session.py`
- Modify: `tests/unit/evaluation/test_session.py`

**Interfaces:**
- Consumes: `session_close`, `InputPresence`.
- Produces: `SessionReadiness` with `ready: bool`, `blocks: tuple[str, ...]`, `decision_notes: tuple[str, ...]`, `score_weights: str`.

`decision_notes` are reasons a later record would carry. They do not flip `ready` to false. `score_weights` is `absent` or `present`. This task only constructs the result; Task 3 fills it from stored inputs.

- [ ] **Step 1: Write the failing test**

```python
from cip.evaluation.session import SessionReadiness


def test_weights_are_named_without_blocking_the_session() -> None:
    result = SessionReadiness(
        ready=True,
        blocks=(),
        decision_notes=("score_weights_not_frozen",),
        score_weights="absent",
    )
    assert result.ready is True
    assert result.score_weights == "absent"


def test_a_block_is_not_ready() -> None:
    result = SessionReadiness(
        ready=False,
        blocks=("snapshot_not_produced",),
        decision_notes=(),
        score_weights="absent",
    )
    assert result.ready is False
```

Add a model validator so `ready=True` with a non-empty `blocks` raises `EvaluationError` (`a ready session has no blocks`). Test that refusal with `SessionReadiness(ready=True, blocks=("snapshot_not_produced",), decision_notes=(), score_weights="absent")`.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/unit/evaluation/test_session.py::test_a_block_is_not_ready -q --no-cov`

Expected: FAIL, `SessionReadiness` is not defined.

- [ ] **Step 3: Implement the result**

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class SessionReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    blocks: tuple[str, ...]
    decision_notes: tuple[str, ...]
    score_weights: Literal["absent", "present"]

    @model_validator(mode="after")
    def _ready_has_no_blocks(self) -> "SessionReadiness":
        if any(item == "" for item in self.blocks + self.decision_notes):
            raise EvaluationError("readiness reasons are non-empty")
        if self.ready and self.blocks:
            raise EvaluationError("a ready session has no blocks")
        return self
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/unit/evaluation/test_session.py -q --no-cov`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cip/evaluation/session.py tests/unit/evaluation/test_session.py
git commit -m "$(cat <<'EOF'
Keep an absent score weight from hiding a session that is otherwise ready.

EOF
)"
```

### Task 3: Assess one closed session

**Files:**
- Modify: `src/cip/evaluation/session.py`
- Modify: `tests/unit/evaluation/test_session.py`

**Interfaces:**
- Consumes: `UniverseSnapshot` from `cip.evaluation.scan`, `ScanCandidate` from `cip.evaluation.scan`, `DailyBar` from `cip.history.bars`, `Observation` from `cip.recorders.observation`, `session_close`, `SessionReadiness`.
- Produces: `assess_session(session: date, as_of: datetime, *, snapshot: UniverseSnapshot | None, bars: Mapping[str, Sequence[DailyBar]], bar_months_present: Mapping[str, bool], candidates: Mapping[str, ScanCandidate | None], candidate_files_present: Mapping[str, bool], observations: Sequence[Observation], regime_failures: frozenset[str], weights_present: bool) -> SessionReadiness`.

`candidates[symbol] is None` means an explicit absence file was stored. A symbol in the snapshot that is missing from `candidate_files_present` or mapped to false is `not_produced`. `bar_months_present[symbol]` is true when that symbol's month parquet exists. Do not accept a `float` timestamp or a boolean where a count is required; reuse the existing model validators by constructing `ScanCandidate` and `UniverseSnapshot` in the tests.

- [ ] **Step 1: Write the failing tests**

Cover these cases, each as its own test:

1. `as_of` one second before the close blocks with `session_not_closed`. `ready` is false.
2. `snapshot is None` blocks with `snapshot_not_produced`.
3. A snapshot whose `observed_at` is after the close blocks with `snapshot_lookahead`.
4. Duplicate snapshot symbols are refused by `UniverseSnapshot` itself. Do not reimplement that check. Pass two identical symbols and expect the existing validation error.
5. BTC month file absent blocks with `btc_bars_not_produced`.
6. BTC month file present, session date missing, blocks with `btc_session_bar_absent`.
7. A snapshot symbol whose month file is absent blocks with `bars_not_produced:{symbol}`.
8. A snapshot symbol whose month file exists but has no session bar does not block. `decision_notes` contains `daily_bar_absent:{symbol}`.
9. A candidate file missing blocks with `candidate_not_produced:{symbol}`.
10. An explicit candidate absence does not block. `decision_notes` contains `missing_candidate:{symbol}`.
11. `MarketSnapshot.as_of` after the close blocks with `candidate_lookahead:{symbol}`.
12. A CoinGecko market cap with `source_timestamp=None` blocks with `undated_fundamental:{symbol}`.
13. `btc_dominance` whose UTC date is the session date, with `source_timestamp` at or before the close, does not block. The same series dated the next day blocks with `regime_lookahead:btc_dominance`. A missing series blocks with `regime_not_produced:btc_dominance`. The same rules apply to `stablecoin_supply`.
14. A regime collection failure for `btc_dominance` blocks with `regime_failed:btc_dominance` even if no observation exists.
15. Funding and open-interest observations and failures do not appear in `blocks` or `decision_notes`.
16. `weights_present=False` yields `score_weights="absent"` and `decision_notes` containing `score_weights_not_frozen`, with `ready` true when nothing else blocks.
17. A naive `as_of` raises `EvaluationError` (`as_of must be timezone-aware UTC`).

Use session `date(2026, 10, 5)`, close `datetime(2026, 10, 6, tzinfo=UTC)`, and `as_of` equal to that close unless the test says otherwise. Build the smallest `DailyBar`, `ScanCandidate`, and `Observation` the existing constructors accept. A one-bar BTC series whose `open_date` is the session date is enough for cases that are not about the 200-day window.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/unit/evaluation/test_session.py -q --no-cov`

Expected: FAIL, `assess_session` is not defined.

- [ ] **Step 3: Implement `assess_session`**

Walk the rules in the Contract section. Return one `SessionReadiness`. Sort `blocks` and `decision_notes` so the same inputs produce the same tuple order. Do not call `run_daily_scan`, `combine`, `classify`, or `assess`. Do not import a network client.

A regime observation is for this session when `observed_at` in UTC has the session date. Its `source_timestamp`, when present, must be at or before the close. A `source_timestamp` after the close is `lookahead` even if `observed_at` is on the session date.

- [ ] **Step 4: Run the tests and the suite**

Run: `uv run pytest tests/unit/evaluation/test_session.py -q --no-cov`

Expected: PASS.

Run: `uv run ruff check src/cip/evaluation/session.py tests/unit/evaluation/test_session.py && uv run ruff format --check src/cip/evaluation/session.py tests/unit/evaluation/test_session.py && uv run mypy && uv run pytest --cov=cip --cov-branch -q`

Expected: ruff and mypy clean, coverage 100%.

- [ ] **Step 5: Commit**

```bash
git add src/cip/evaluation/session.py tests/unit/evaluation/test_session.py
git commit -m "$(cat <<'EOF'
Refuse a closed session whose stored inputs are missing or from later.

EOF
)"
```

## Review checklist

Accept this plan only if each answer is yes:

- A producer that has not run is `not_produced` and blocks, rather than becoming an immutable rejection.
- An explicit absence still allows one later `DecisionRecord` with the existing reason.
- No value timestamp after the close can be stored as if it were known at the close.
- An undated fundamental figure cannot pass.
- Absent weights do not make `ready` false and do not suppress the later record.
- Funding and open interest cannot block the session.
- `prod_shadow_started_at` is not written or moved.
- `run_daily_scan` is not called from this module.

## Self-review

- Spec coverage: input contract and readiness are Tasks 1–3. Producers, the daily workload, `prod_decisions_started_at`, outcomes, and Monday assurance are named as later plans and have no tasks here, because this checkpoint is the schema.
- The hourly scan, the shadow clock, the futures 451, and the assurance document are unchanged.
- `assess_session` is the only new entry point. Task 2's `SessionReadiness` fields match Task 3's return type.
