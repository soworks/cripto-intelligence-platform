"""Daily-candle features for score v2. A missing input stays missing."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from cip.domain.errors import EvaluationError
from cip.history.bars import DailyBar

# Windows named by the roadmap and the gap analysis. These are not score weights.
_RS_WINDOWS = (30, 90)
_SKIPS = (1, 2, 3)
_EMA_PERIOD = 20
_RSI_PERIOD = 14
_RETURN_DAYS = 7
_BETA_DAYS = 90
_TRADE_DAYS = 30
_EXTENSION_ATR = Decimal("2.5")
_EXTENSION_RSI = Decimal("78")
_EXTENSION_PERCENTILE = Decimal("0.95")
_BTC = "BTCUSDT"
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

TREND_FEATURES = tuple(f"rs_{window}d_vol_skip_{skip}" for window in _RS_WINDOWS for skip in _SKIPS)
TOKENOMICS_FEATURES = (
    "circulating_ratio",
    "fdv_to_market_cap",
    "unlock_pct_14d",
    "unlock_pct_90d",
)
PENALTY_FEATURE = "extension_count"
WEIGHT_FEATURES = frozenset((*TREND_FEATURES, PENALTY_FEATURE, *TOKENOMICS_FEATURES))


def _finite_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ValueError("decimal values are Decimal or decimal strings")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("decimal values are Decimal or decimal strings") from error
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise ValueError("decimal values are Decimal or decimal strings")
    if not parsed.is_finite():
        raise ValueError("decimal values are finite")
    return parsed


def _feature_map(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        raise ValueError("features are an object")
    parsed: dict[str, Decimal] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("feature names are strings")
        parsed[key] = _finite_decimal(item)
    return parsed


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _finite_decimal(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Tokenomics(_Strict):
    """Caller-supplied fundamentals. None stays missing."""

    circulating_ratio: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    fdv_to_market_cap: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    unlock_pct_14d: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    unlock_pct_90d: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]


class FeatureSet(_Strict):
    """Point-in-time features. Liquidity and portfolio fit are not features of the score."""

    features: Annotated[dict[str, Decimal], BeforeValidator(_feature_map)]
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _codes(self) -> Self:
        if any(_CODE.fullmatch(code) is None for code in self.reason_codes):
            raise ValueError("reason codes are lowercase snake_case")
        return self


def measure(
    *,
    symbol: str,
    as_of: date,
    bars: Sequence[DailyBar],
    btc_bars: Sequence[DailyBar],
    universe_bars: Mapping[str, Sequence[DailyBar]],
    atr_period: int,
    tokenomics: Tokenomics | None,
) -> FeatureSet:
    """Read one closed session from stored bars. No provider calls and no filled zeros."""
    if type(atr_period) is not int or atr_period < 1:
        raise EvaluationError("ATR period must be a positive integer")
    tail = _tail(bars, symbol, as_of)
    if tail is None:
        return _result({}, ("missing_close",))
    if _bad_prices(tail):
        return _result({}, ("invalid_price",))
    features: dict[str, Decimal] = {}
    reasons: list[str] = []
    _symbol_features(tail, as_of, atr_period, universe_bars, features, reasons)
    _relative_features(tail, btc_bars, as_of, features, reasons)
    _tokenomics(tokenomics, features, reasons)
    if not reasons:
        reasons.append("features_ready")
    return _result(features, tuple(reasons))


def _symbol_features(
    tail: tuple[DailyBar, ...],
    as_of: date,
    atr_period: int,
    universe_bars: Mapping[str, Sequence[DailyBar]],
    features: dict[str, Decimal],
    reasons: list[str],
) -> None:
    closes = tuple(bar.close for bar in tail)
    _level_features(tail, closes, atr_period, features, reasons)
    _extension(tail, closes, as_of, atr_period, universe_bars, features, reasons)
    _activity(tail, features, reasons)


def _level_features(
    tail: tuple[DailyBar, ...],
    closes: tuple[Decimal, ...],
    atr_period: int,
    features: dict[str, Decimal],
    reasons: list[str],
) -> None:
    if len(closes) >= _EMA_PERIOD:
        features["ema_20"] = _ema(closes, _EMA_PERIOD)
    else:
        reasons.append("missing_ema")
    if len(tail) > atr_period:
        features[f"atr_{atr_period}"] = _atr(tail, atr_period)
    else:
        reasons.append("missing_atr")
    if len(closes) > _RSI_PERIOD:
        rsi = _rsi(closes, _RSI_PERIOD)
        if rsi is None:
            reasons.append("undefined_rsi")
        else:
            features["rsi_14"] = rsi
    else:
        reasons.append("missing_rsi")


def _extension(
    tail: tuple[DailyBar, ...],
    closes: tuple[Decimal, ...],
    as_of: date,
    atr_period: int,
    universe_bars: Mapping[str, Sequence[DailyBar]],
    features: dict[str, Decimal],
    reasons: list[str],
) -> None:
    own_return = _span_return(tail, as_of, _RETURN_DAYS, 0)
    if own_return is None:
        reasons.append("missing_return_7d")
        return
    features["return_7d"] = own_return
    if "ema_20" not in features or f"atr_{atr_period}" not in features:
        reasons.append("missing_extension")
        return
    atr = features[f"atr_{atr_period}"]
    if atr == 0:
        reasons.append("invalid_atr")
        return
    if "rsi_14" not in features:
        reasons.append("missing_extension")
        return
    distance = (closes[-1] - features["ema_20"]) / atr
    features["extension_atr"] = distance
    others = _peer_returns(universe_bars, tail, as_of, own_return)[1:]
    triggered = int(distance > _EXTENSION_ATR) + int(features["rsi_14"] > _EXTENSION_RSI)
    triggered += int(_above_peers(own_return, others))
    features[PENALTY_FEATURE] = Decimal(triggered)


def _activity(tail: tuple[DailyBar, ...], features: dict[str, Decimal], reasons: list[str]) -> None:
    latest = tail[-1]
    if latest.quote_volume <= 0 or not Decimal(0) <= _ratio(latest) <= 1:
        reasons.append("invalid_taker_buy")
    else:
        features["taker_buy_ratio"] = _ratio(latest)
    if latest.trade_count < 0:
        reasons.append("invalid_trade_count")
    else:
        features["trade_count"] = Decimal(latest.trade_count)
    if len(tail) >= _TRADE_DAYS and all(bar.trade_count >= 0 for bar in tail[-_TRADE_DAYS:]):
        counts = [Decimal(bar.trade_count) for bar in tail[-_TRADE_DAYS:]]
        features["trade_count_median_30d"] = _median(counts)
    else:
        reasons.append("missing_trade_count_median")


def _relative_features(
    tail: tuple[DailyBar, ...],
    btc_bars: Sequence[DailyBar],
    as_of: date,
    features: dict[str, Decimal],
    reasons: list[str],
) -> None:
    btc = _tail(btc_bars, _BTC, as_of)
    if btc is None:
        reasons.append("missing_btc")
        return
    if _bad_prices(btc):
        reasons.append("invalid_btc_price")
        return
    volatile = False
    for window in _RS_WINDOWS:
        for skip in _SKIPS:
            raw = _relative_return(tail, btc, as_of, window, skip)
            name = f"rs_{window}d_skip_{skip}"
            if raw is None:
                reasons.append(f"missing_{name}")
                continue
            features[name] = raw
            adjusted = _volatility_adjusted(tail, as_of, window, skip, raw)
            if adjusted is None:
                volatile = True
            else:
                features[f"rs_{window}d_vol_skip_{skip}"] = adjusted
    if volatile:
        reasons.append("invalid_volatility")
    _beta(tail, btc, as_of, features, reasons)


def _tokenomics(
    tokenomics: Tokenomics | None, features: dict[str, Decimal], reasons: list[str]
) -> None:
    if tokenomics is None:
        reasons.extend(f"missing_{name}" for name in TOKENOMICS_FEATURES)
        return
    for name in TOKENOMICS_FEATURES:
        value = getattr(tokenomics, name)
        if value is None:
            reasons.append(f"missing_{name}")
        else:
            features[name] = value


def _peer_returns(
    universe_bars: Mapping[str, Sequence[DailyBar]],
    tail: tuple[DailyBar, ...],
    as_of: date,
    own_return: Decimal,
) -> list[Decimal]:
    returns = [own_return]
    for symbol in sorted(universe_bars):
        if symbol == tail[-1].symbol:
            _same_history(universe_bars[symbol], tail, as_of)
            continue
        peer = _tail(universe_bars[symbol], symbol, as_of)
        if peer is None or _bad_prices(peer):
            continue
        peer_return = _span_return(peer, as_of, _RETURN_DAYS, 0)
        if peer_return is not None:
            returns.append(peer_return)
    return returns


def _same_history(bars: Sequence[DailyBar], tail: tuple[DailyBar, ...], as_of: date) -> None:
    other = _tail(bars, tail[-1].symbol, as_of)
    if other is None:
        return
    if tuple(bar.close for bar in other) != tuple(bar.close for bar in tail):
        raise EvaluationError("universe bars disagree with the symbol")


def _beta(
    tail: tuple[DailyBar, ...],
    btc: tuple[DailyBar, ...],
    as_of: date,
    features: dict[str, Decimal],
    reasons: list[str],
) -> None:
    paired = _paired_returns(tail, btc, as_of, _BETA_DAYS)
    if paired is None:
        reasons.append("missing_beta")
        return
    asset, market = paired
    covariance = _covariance(asset, market)
    variance = _covariance(market, market)
    asset_variance = _covariance(asset, asset)
    if variance == 0 or asset_variance == 0:
        reasons.append("invalid_beta")
        return
    features["beta_90d"] = covariance / variance
    features["correlation_90d"] = covariance / (variance.sqrt() * asset_variance.sqrt())


def _tail(bars: Sequence[DailyBar], symbol: str, as_of: date) -> tuple[DailyBar, ...] | None:
    if any(bar.symbol != symbol for bar in bars):
        raise EvaluationError("bar symbol mismatch")
    dated = (bar for bar in bars if bar.open_date <= as_of)
    usable = tuple(sorted(dated, key=lambda bar: bar.open_date))
    dates = [bar.open_date for bar in usable]
    if len(dates) != len(set(dates)):
        raise EvaluationError("duplicate bar date")
    if not usable or usable[-1].open_date != as_of:
        return None
    kept = [usable[-1]]
    for bar in reversed(usable[:-1]):
        if bar.open_date != kept[-1].open_date - timedelta(days=1):
            break
        kept.append(bar)
    kept.reverse()
    return tuple(kept)


def _bad_prices(bars: Sequence[DailyBar]) -> bool:
    return any(_malformed(bar) for bar in bars)


def _malformed(bar: DailyBar) -> bool:
    amounts = (
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
        bar.quote_volume,
        bar.taker_buy_base_volume,
        bar.taker_buy_quote_volume,
    )
    if any(type(item) is not Decimal or not item.is_finite() or item < 0 for item in amounts):
        return True
    if type(bar.trade_count) is not int:
        return True
    if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
        return True
    return bar.high < max(bar.low, bar.open, bar.close) or bar.low > min(bar.open, bar.close)


def _span_return(bars: tuple[DailyBar, ...], as_of: date, window: int, skip: int) -> Decimal | None:
    end = as_of - timedelta(days=skip)
    start = end - timedelta(days=window)
    indexed = {bar.open_date: bar.close for bar in bars}
    if start not in indexed or end not in indexed:
        return None
    return indexed[end] / indexed[start] - 1


def _relative_return(
    asset: tuple[DailyBar, ...],
    btc: tuple[DailyBar, ...],
    as_of: date,
    window: int,
    skip: int,
) -> Decimal | None:
    asset_return = _span_return(asset, as_of, window, skip)
    btc_return = _span_return(btc, as_of, window, skip)
    if asset_return is None or btc_return is None:
        return None
    return asset_return - btc_return


def _volatility_adjusted(
    bars: tuple[DailyBar, ...], as_of: date, window: int, skip: int, raw: Decimal
) -> Decimal | None:
    end = as_of - timedelta(days=skip)
    start = end - timedelta(days=window)
    closes = tuple(bar.close for bar in bars if start <= bar.open_date <= end)
    volatility = _stdev(_daily_returns(closes))
    if volatility == 0:
        return None
    return raw / volatility


def _paired_returns(
    asset: tuple[DailyBar, ...], btc: tuple[DailyBar, ...], as_of: date, window: int
) -> tuple[list[Decimal], list[Decimal]] | None:
    asset_closes = {bar.open_date: bar.close for bar in asset}
    btc_closes = {bar.open_date: bar.close for bar in btc}
    days = [as_of - timedelta(days=offset) for offset in range(window, -1, -1)]
    if any(day not in asset_closes or day not in btc_closes for day in days):
        return None
    asset_series = tuple(asset_closes[day] for day in days)
    btc_series = tuple(btc_closes[day] for day in days)
    return _daily_returns(asset_series), _daily_returns(btc_series)


def _daily_returns(closes: tuple[Decimal, ...]) -> list[Decimal]:
    return [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]


def _stdev(values: Sequence[Decimal]) -> Decimal:
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    return variance.sqrt()


def _covariance(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal:
    mean_left = sum(left, Decimal(0)) / Decimal(len(left))
    mean_right = sum(right, Decimal(0)) / Decimal(len(right))
    paired = zip(left, right, strict=True)
    total = sum((one - mean_left) * (other - mean_right) for one, other in paired)
    return total / Decimal(len(left) - 1)


def _ema(closes: tuple[Decimal, ...], period: int) -> Decimal:
    value = sum(closes[:period], Decimal(0)) / Decimal(period)
    weight = Decimal(2) / Decimal(period + 1)
    for close in closes[period:]:
        value = (close - value) * weight + value
    return value


def _atr(bars: tuple[DailyBar, ...], period: int) -> Decimal:
    ranges: list[Decimal] = []
    for index in range(1, len(bars)):
        previous = bars[index - 1].close
        current = bars[index]
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous),
                abs(current.low - previous),
            )
        )
    value = sum(ranges[:period], Decimal(0)) / Decimal(period)
    for true_range in ranges[period:]:
        value = (value * Decimal(period - 1) + true_range) / Decimal(period)
    return value


def _rsi(closes: tuple[Decimal, ...], period: int) -> Decimal | None:
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    average_gain = sum((change for change in changes[:period] if change > 0), Decimal(0))
    average_loss = sum((-change for change in changes[:period] if change < 0), Decimal(0))
    average_gain /= Decimal(period)
    average_loss /= Decimal(period)
    for change in changes[period:]:
        gain = change if change > 0 else Decimal(0)
        loss = -change if change < 0 else Decimal(0)
        average_gain = (average_gain * Decimal(period - 1) + gain) / Decimal(period)
        average_loss = (average_loss * Decimal(period - 1) + loss) / Decimal(period)
    if average_gain == 0 and average_loss == 0:
        return None
    if average_loss == 0:
        return Decimal(100)
    return Decimal(100) - Decimal(100) / (1 + average_gain / average_loss)


def _above_peers(own: Decimal, others: Sequence[Decimal]) -> bool:
    if not others:
        return False
    below = sum(1 for value in others if value < own)
    return Decimal(below) / Decimal(len(others)) > _EXTENSION_PERCENTILE


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    half = Decimal(len(ordered) - 1) / 2
    low = int(half.to_integral_value(rounding="ROUND_FLOOR"))
    high = int(half.to_integral_value(rounding="ROUND_CEILING"))
    return (ordered[low] + ordered[high]) / 2


def _ratio(bar: DailyBar) -> Decimal:
    return bar.taker_buy_quote_volume / bar.quote_volume


def _result(features: dict[str, Decimal], reasons: tuple[str, ...]) -> FeatureSet:
    return FeatureSet(features=features, reason_codes=reasons)
