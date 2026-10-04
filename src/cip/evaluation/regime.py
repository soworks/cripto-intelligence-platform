from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cip.domain.errors import EvaluationError
from cip.domain.policy import RegimeHypotheses
from cip.history.bars import DailyBar
from cip.recorders.observation import Observation

# Indicator windows from the regime rules: SMA200, EMA50, and the 90-day high.
_SMA_DAYS = 200
_EMA_DAYS = 50
_DRAWDOWN_DAYS = 90
_BTC = "BTCUSDT"
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RANK = {"RISK_ON": 0, "NEUTRAL": 1, "RISK_OFF": 2}

RegimeName = Literal["RISK_ON", "NEUTRAL", "RISK_OFF"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PriorSession(_Strict):
    """One stored session. It is not recomputed from a provider."""

    session: date
    raw: RegimeName
    published: RegimeName | None


class RegimeDecision(_Strict):
    """Published regime and the discovery policy for that state. Not a BUY."""

    regime: RegimeName | None
    raw: RegimeName | None
    reason_codes: tuple[str, ...] = Field(min_length=1)
    new_entries: bool
    size_mult: Decimal | None
    min_score: int | None
    require_rs_vs_btc_30d_positive: bool | None
    trailing_atr_mult: Decimal | None

    @model_validator(mode="after")
    def _no_buy_without_permission(self) -> Self:
        if any(_CODE.fullmatch(code) is None for code in self.reason_codes):
            raise ValueError("reason codes are lowercase snake_case")
        if self.regime is None and (
            self.new_entries
            or self.size_mult is not None
            or self.min_score is not None
            or self.require_rs_vs_btc_30d_positive is not None
            or self.trailing_atr_mult is not None
        ):
            raise ValueError("an unclassified session has no discovery policy")
        if self.regime == "RISK_OFF" and self.new_entries:
            raise ValueError("RISK_OFF produces no BUY")
        if self.regime is not None and self.raw is None:
            raise ValueError("a published regime has a raw state")
        if self.regime is not None and self.regime.lower() not in self.reason_codes:
            raise ValueError("a published regime carries its reason")
        return self


def classify(
    *,
    as_of: date,
    btc_bars: Sequence[DailyBar],
    universe_bars: Mapping[str, Sequence[DailyBar]],
    observations: Sequence[Observation],
    prior: Sequence[PriorSession],
    hypotheses: RegimeHypotheses,
) -> RegimeDecision:
    """Classify one closed session from stored bars and observations."""
    _prior_sessions(prior, as_of)
    if any(bar.symbol != _BTC for bar in btc_bars):
        raise EvaluationError("BTC bars must be BTCUSDT")
    reasons = _observation_reasons(observations, as_of)
    sma_window = _fixed_window(btc_bars, as_of, _SMA_DAYS)
    draw_window = _fixed_window(btc_bars, as_of, _DRAWDOWN_DAYS)
    if sma_window is None:
        reasons.append("missing_btc_sma")
    if draw_window is None:
        reasons.append("missing_btc_drawdown")
    if _non_positive(sma_window) or _non_positive(draw_window):
        reasons.append("invalid_btc_price")
    breadth = _breadth(universe_bars, as_of, reasons)
    if reasons:
        return _unclassified(tuple(reasons), raw=None)
    above_sma, below_sma = _sma_side(cast(tuple[DailyBar, ...], sma_window))
    raw = _raw_regime(
        above_sma,
        below_sma,
        cast(Decimal, breadth),
        _drawdown(cast(tuple[DailyBar, ...], draw_window)),
        hypotheses,
    )
    if raw is None:
        return _unclassified(("regime_unclassified",), raw=None)
    published = _publish(raw, as_of, prior, hypotheses)
    if published is None:
        return _unclassified(("hysteresis",), raw=raw)
    codes = [published.lower()]
    if published != raw:
        codes.append("hysteresis")
    return _from_policy(published, raw, tuple(codes), hypotheses)


def _observation_reasons(observations: Sequence[Observation], as_of: date) -> list[str]:
    reasons: list[str] = []
    for series, missing, stale in (
        ("btc_dominance", "missing_btc_dominance", "stale_btc_dominance"),
        ("stablecoin_supply", "missing_stablecoin_supply", "stale_stablecoin_supply"),
    ):
        status = _session_status(observations, series, as_of)
        if status != "ready":
            reasons.append(missing if status == "missing" else stale)
    return reasons


def _session_status(observations: Sequence[Observation], series: str, as_of: date) -> str:
    dates = [_utc_date(item.identity_time) for item in observations if item.series == series]
    if as_of in dates:
        return "ready"
    if any(day < as_of for day in dates):
        return "stale"
    return "missing"


def _utc_date(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def _fixed_window(bars: Sequence[DailyBar], as_of: date, days: int) -> tuple[DailyBar, ...] | None:
    usable = _usable(bars, as_of)
    if len(usable) < days:
        return None
    window = usable[-days:]
    expected = [as_of - timedelta(days=days - 1 - offset) for offset in range(days)]
    if [bar.open_date for bar in window] != expected:
        return None
    return window


def _usable(bars: Sequence[DailyBar], as_of: date) -> tuple[DailyBar, ...]:
    usable = tuple(
        sorted(
            (bar for bar in bars if bar.open_date <= as_of),
            key=lambda bar: bar.open_date,
        )
    )
    dates = [bar.open_date for bar in usable]
    if len(dates) != len(set(dates)):
        raise EvaluationError("duplicate bar date")
    return usable


def _breadth(
    universe_bars: Mapping[str, Sequence[DailyBar]],
    as_of: date,
    reasons: list[str],
) -> Decimal | None:
    eligible = 0
    above = 0
    for symbol in sorted(universe_bars):
        bars = universe_bars[symbol]
        if any(bar.symbol != symbol for bar in bars):
            raise EvaluationError("universe bar symbol mismatch")
        history = _contiguous_tail(bars, as_of)
        if history is None:
            continue
        if _non_positive(history):
            reasons.append("invalid_breadth")
            return None
        eligible += 1
        if history[-1].close > _ema(tuple(bar.close for bar in history), _EMA_DAYS):
            above += 1
    if eligible == 0:
        reasons.append("missing_breadth")
        return None
    return Decimal(above) / Decimal(eligible)


def _contiguous_tail(bars: Sequence[DailyBar], as_of: date) -> tuple[DailyBar, ...] | None:
    usable = _usable(bars, as_of)
    if not usable or usable[-1].open_date != as_of:
        return None
    kept = [usable[-1]]
    for bar in reversed(usable[:-1]):
        if bar.open_date != kept[-1].open_date - timedelta(days=1):
            break
        kept.append(bar)
    kept.reverse()
    if len(kept) < _EMA_DAYS:
        return None
    return tuple(kept)


def _ema(closes: tuple[Decimal, ...], period: int) -> Decimal:
    ema = sum(closes[:period], Decimal(0)) / Decimal(period)
    weight = Decimal(2) / Decimal(period + 1)
    for close in closes[period:]:
        ema = (close - ema) * weight + ema
    return ema


def _sma_side(window: tuple[DailyBar, ...]) -> tuple[bool, bool]:
    closes = tuple(bar.close for bar in window)
    average = sum(closes, Decimal(0)) / Decimal(len(closes))
    close = window[-1].close
    return close > average, close < average


def _drawdown(window: tuple[DailyBar, ...]) -> Decimal:
    peak = max(bar.high for bar in window)
    close = window[-1].close
    if peak <= 0 or close > peak:
        raise EvaluationError("BTC drawdown is not a price")
    return (peak - close) / peak


def _non_positive(window: tuple[DailyBar, ...] | None) -> bool:
    if window is None:
        return False
    return any(bar.close <= 0 or bar.high <= 0 for bar in window)


def _raw_regime(
    above_sma: bool,
    below_sma: bool,
    breadth: Decimal,
    drawdown: Decimal,
    hypotheses: RegimeHypotheses,
) -> RegimeName | None:
    if drawdown > Decimal(str(hypotheses.btc_drawdown_90d_risk_off)):
        return "RISK_OFF"
    wide = breadth >= Decimal(str(hypotheses.breadth_risk_on))
    if below_sma and breadth < Decimal(str(hypotheses.breadth_risk_off)):
        return "RISK_OFF"
    if above_sma and wide:
        return "RISK_ON"
    if above_sma != wide:
        return "NEUTRAL"
    return None


def _publish(
    raw: RegimeName,
    as_of: date,
    prior: Sequence[PriorSession],
    hypotheses: RegimeHypotheses,
) -> RegimeName | None:
    previous = _published_yesterday(prior, as_of)
    if raw == "RISK_OFF" or (previous is not None and _RANK[raw] > _RANK[previous]):
        return raw
    if previous == raw or _streak(raw, as_of, prior, hypotheses.hysteresis_days):
        return raw
    if previous is not None and _RANK[previous] > _RANK[raw]:
        return previous
    return None


def _published_yesterday(prior: Sequence[PriorSession], as_of: date) -> RegimeName | None:
    yesterday = as_of - timedelta(days=1)
    found = [item.published for item in prior if item.session == yesterday]
    if not found:
        return None
    return found[0]


def _streak(raw: RegimeName, as_of: date, prior: Sequence[PriorSession], days: int) -> bool:
    by_session = {item.session: item.raw for item in prior}
    return all(by_session.get(as_of - timedelta(days=offset)) == raw for offset in range(1, days))


def _prior_sessions(prior: Sequence[PriorSession], as_of: date) -> None:
    sessions = [item.session for item in prior]
    if len(sessions) != len(set(sessions)):
        raise EvaluationError("duplicate prior regime")
    if any(item.session >= as_of for item in prior):
        raise EvaluationError("prior regime is not before the session")


def _from_policy(
    regime: RegimeName,
    raw: RegimeName,
    reason_codes: tuple[str, ...],
    hypotheses: RegimeHypotheses,
) -> RegimeDecision:
    state = {
        "RISK_ON": hypotheses.risk_on,
        "NEUTRAL": hypotheses.neutral,
        "RISK_OFF": hypotheses.risk_off,
    }[regime]
    return RegimeDecision(
        regime=regime,
        raw=raw,
        reason_codes=reason_codes,
        new_entries=state.new_entries,
        size_mult=None if state.size_mult is None else Decimal(str(state.size_mult)),
        min_score=state.min_score,
        require_rs_vs_btc_30d_positive=state.require_rs_vs_btc_30d_positive,
        trailing_atr_mult=(
            None if state.trailing_atr_mult is None else Decimal(str(state.trailing_atr_mult))
        ),
    )


def _unclassified(reason_codes: tuple[str, ...], *, raw: RegimeName | None) -> RegimeDecision:
    return RegimeDecision(
        regime=None,
        raw=raw,
        reason_codes=reason_codes,
        new_entries=False,
        size_mult=None,
        min_score=None,
        require_rs_vs_btc_30d_positive=None,
        trailing_atr_mult=None,
    )
