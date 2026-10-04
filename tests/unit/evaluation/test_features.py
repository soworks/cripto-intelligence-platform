from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cip.domain.errors import EvaluationError
from cip.evaluation.features import PENALTY_FEATURE, FeatureSet, Tokenomics, measure
from cip.evaluation.score import ScoreResult, combine
from cip.evaluation.study import MINIMUM_SAMPLE, StudyRow, freeze_weights, information_coefficients
from cip.history.bars import DailyBar

AS_OF = date(2026, 10, 4)
HISTORY = 94


def _bar(symbol: str, day: date, close: str, *, quote: str = "10", taker: str = "4") -> DailyBar:
    price = Decimal(close)
    return DailyBar(
        symbol=symbol,
        open_date=day,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        quote_volume=Decimal(quote),
        trade_count=2,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal(taker),
    )


def _series(symbol: str, closes: list[str]) -> tuple[DailyBar, ...]:
    start = AS_OF - timedelta(days=len(closes) - 1)
    return tuple(
        _bar(symbol, start + timedelta(days=offset), close) for offset, close in enumerate(closes)
    )


def _wave(symbol: str, days: int = HISTORY) -> tuple[DailyBar, ...]:
    closes = ["110" if offset % 2 else "100" for offset in range(days)]
    return _series(symbol, closes)


def _tokenomics() -> Tokenomics:
    return Tokenomics(
        circulating_ratio=Decimal("0.50"),
        fdv_to_market_cap=Decimal("1.5"),
        unlock_pct_14d=Decimal("0"),
        unlock_pct_90d=Decimal("0.01"),
    )


def test_features_come_from_stored_bars_and_leave_liquidity_out() -> None:
    reading = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=_wave("SOLUSDT"),
        btc_bars=_wave("BTCUSDT"),
        universe_bars={},
        atr_period=14,
        tokenomics=_tokenomics(),
    )
    assert reading.reason_codes == ("features_ready",)
    assert "liquidity" not in reading.features
    assert "portfolio_fit" not in reading.features
    assert reading.features["taker_buy_ratio"] == Decimal("0.4")
    assert reading.features["circulating_ratio"] == Decimal("0.50")
    assert reading.features["rs_30d_vol_skip_1"].is_finite()
    assert reading.features[PENALTY_FEATURE] in {Decimal(count) for count in range(4)}


def test_relative_strength_skips_the_recent_close() -> None:
    closes = ["100"] * HISTORY
    closes[-32] = "50"
    bars = _series("SOLUSDT", closes)
    btc = _series("BTCUSDT", ["100"] * HISTORY)
    reading = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=bars,
        btc_bars=btc,
        universe_bars={},
        atr_period=14,
        tokenomics=_tokenomics(),
    )
    assert reading.features["rs_30d_skip_1"] == Decimal("1")
    assert reading.features["return_7d"] == Decimal("0")
    assert reading.features["rs_30d_vol_skip_1"] > 0
    assert reading.reason_codes == ("invalid_beta",)
    flat = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=_series("SOLUSDT", ["100"] * HISTORY),
        btc_bars=btc,
        universe_bars={},
        atr_period=14,
        tokenomics=_tokenomics(),
    )
    assert flat.features["rs_30d_skip_1"] == Decimal("0")
    assert "rs_30d_vol_skip_1" not in flat.features
    assert "invalid_volatility" in flat.reason_codes
    assert "invalid_atr" in flat.reason_codes


def test_a_short_or_invalid_history_is_not_filled() -> None:
    short = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=_series("SOLUSDT", ["100"] * 5),
        btc_bars=_series("BTCUSDT", ["100"] * 5),
        universe_bars={},
        atr_period=14,
        tokenomics=None,
    )
    assert "missing_ema" in short.reason_codes
    assert "missing_circulating_ratio" in short.reason_codes
    assert all(value != 0 for value in short.features.values())
    missing = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=_series("SOLUSDT", ["100"] * 5)[:-1],
        btc_bars=(),
        universe_bars={},
        atr_period=14,
        tokenomics=None,
    )
    assert missing.reason_codes == ("missing_close",)
    assert missing.features == {}
    invalid = _series("SOLUSDT", ["100"] * 5)
    broken = (*invalid[:-1], _bar("SOLUSDT", AS_OF, "0"))
    assert measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=broken,
        btc_bars=_series("BTCUSDT", ["100"] * 5),
        universe_bars={},
        atr_period=14,
        tokenomics=None,
    ).reason_codes == ("invalid_price",)


def test_bad_clocks_books_and_copies_are_refused() -> None:
    bars = _wave("SOLUSDT")
    with pytest.raises(EvaluationError, match="ATR"):
        measure(
            symbol="SOLUSDT",
            as_of=AS_OF,
            bars=bars,
            btc_bars=_wave("BTCUSDT"),
            universe_bars={},
            atr_period=0,
            tokenomics=None,
        )
    with pytest.raises(EvaluationError, match="symbol"):
        measure(
            symbol="SOLUSDT",
            as_of=AS_OF,
            bars=(*bars, _bar("ETHUSDT", AS_OF, "100")),
            btc_bars=_wave("BTCUSDT"),
            universe_bars={},
            atr_period=14,
            tokenomics=None,
        )
    with pytest.raises(EvaluationError, match="duplicate"):
        measure(
            symbol="SOLUSDT",
            as_of=AS_OF,
            bars=(*bars, _bar("SOLUSDT", AS_OF, "100")),
            btc_bars=_wave("BTCUSDT"),
            universe_bars={},
            atr_period=14,
            tokenomics=None,
        )
    disagreed = _series("SOLUSDT", ["90"] * HISTORY)
    with pytest.raises(EvaluationError, match="disagree"):
        measure(
            symbol="SOLUSDT",
            as_of=AS_OF,
            bars=bars,
            btc_bars=_wave("BTCUSDT"),
            universe_bars={"SOLUSDT": disagreed},
            atr_period=14,
            tokenomics=None,
        )
    with pytest.raises(ValidationError):
        Tokenomics(
            circulating_ratio=0.1 + 0.2,
            fdv_to_market_cap=None,
            unlock_pct_14d=None,
            unlock_pct_90d=None,
        )


def test_the_extension_penalty_uses_the_documented_triggers() -> None:
    closes = ["100"] * HISTORY
    closes[-1] = "200"
    peers = {f"P{index}USDT": _series(f"P{index}USDT", ["100"] * 8) for index in range(19)}
    reading = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=_series("SOLUSDT", closes),
        btc_bars=_series("BTCUSDT", ["100"] * HISTORY),
        universe_bars=peers,
        atr_period=14,
        tokenomics=_tokenomics(),
    )
    assert reading.features["rsi_14"] == Decimal(100)
    assert reading.features["extension_atr"] > Decimal("2.5")
    assert reading.features["return_7d"] > 0
    assert reading.features[PENALTY_FEATURE] == Decimal(3)


def test_a_score_needs_frozen_weights_and_excludes_liquidity() -> None:
    features = {
        "rs_30d_vol_skip_1": Decimal("2"),
        PENALTY_FEATURE: Decimal("2"),
        "circulating_ratio": Decimal("0.5"),
    }
    scored = combine(
        features,
        {
            "rs_30d_vol_skip_1": Decimal("3"),
            PENALTY_FEATURE: Decimal("-4"),
            "circulating_ratio": Decimal("10"),
        },
    )
    assert scored.components["trend_rs"] == Decimal("6")
    assert scored.components["extension_penalty"] == Decimal("-8")
    assert scored.components["tokenomics"] == Decimal("5")
    assert scored.score == Decimal("3")
    with pytest.raises(EvaluationError, match="not frozen"):
        combine(features, {})
    with pytest.raises(EvaluationError, match="liquidity"):
        combine(features, {"liquidity": Decimal("1")})
    with pytest.raises(EvaluationError, match="unknown"):
        combine(features, {"beta_90d": Decimal("1")})
    with pytest.raises(EvaluationError, match="non-zero"):
        combine(features, {"rs_30d_vol_skip_1": Decimal("0")})
    with pytest.raises(EvaluationError, match="non-zero"):
        combine(features, {"rs_30d_vol_skip_1": Decimal("NaN")})
    with pytest.raises(EvaluationError, match="missing"):
        combine({}, {"rs_30d_vol_skip_1": Decimal("1")})


def test_the_study_reports_rank_correlation_and_does_not_choose_weights() -> None:
    perfect = _rows(MINIMUM_SAMPLE, reverse=False)
    assert information_coefficients(perfect)[0].coefficient == Decimal(1)
    reversed_rows = _rows(MINIMUM_SAMPLE, reverse=True)
    assert information_coefficients(reversed_rows)[0].coefficient == Decimal(-1)
    short = information_coefficients(_rows(MINIMUM_SAMPLE - 1, reverse=False))
    assert short[0].coefficient is None
    assert short[0].reason_codes == ("sample_below_minimum",)
    flat = [
        StudyRow(
            name="rs_30d_vol_skip_1",
            regime="NEUTRAL",
            value=Decimal("1"),
            forward_excess_return=Decimal(index),
        )
        for index in range(MINIMUM_SAMPLE)
    ]
    undefined = information_coefficients(flat)[0]
    assert undefined.coefficient is None
    assert undefined.reason_codes == ("undefined_correlation",)
    tied = _rows(MINIMUM_SAMPLE, reverse=False)
    tied[0] = StudyRow(
        name="rs_30d_vol_skip_1",
        regime="RISK_ON",
        value=tied[1].value,
        forward_excess_return=tied[0].forward_excess_return,
    )
    assert information_coefficients(tied)[0].coefficient is not None
    with pytest.raises(EvaluationError, match="cannot freeze"):
        freeze_weights(short)
    with pytest.raises(EvaluationError, match="does not choose"):
        freeze_weights(information_coefficients(perfect))
    with pytest.raises(ValidationError):
        StudyRow(
            name="rs",
            regime="RISK_ON",
            value=0.1 + 0.2,
            forward_excess_return=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        StudyRow(
            name="rs",
            regime="RISK_ON",
            value="nope",
            forward_excess_return=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        StudyRow(
            name="rs",
            regime="RISK_ON",
            value=object(),
            forward_excess_return=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        StudyRow(
            name="rs",
            regime="RISK_ON",
            value=Decimal("NaN"),
            forward_excess_return=Decimal("0"),
        )
    with pytest.raises(EvaluationError, match="cannot freeze"):
        freeze_weights(())


def test_missing_inputs_stay_missing_instead_of_becoming_zero() -> None:
    bars = _series("SOLUSDT", ["100"] * 10)
    bad = _series("BADUSDT", ["100"] * 8)
    peers = {
        "SOLUSDT": bars[:-1],
        "OLDUSDT": _series("OLDUSDT", ["100"] * 3),
        "BADUSDT": (*bad[:-1], _bar("BADUSDT", AS_OF, "0")),
    }
    reading = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=bars,
        btc_bars=(_bar("BTCUSDT", AS_OF, "0"),),
        universe_bars=peers,
        atr_period=14,
        tokenomics=Tokenomics(
            circulating_ratio="0.5",
            fdv_to_market_cap=None,
            unlock_pct_14d=None,
            unlock_pct_90d=None,
        ),
    )
    assert "missing_extension" in reading.reason_codes
    assert "invalid_btc_price" in reading.reason_codes
    assert "missing_fdv_to_market_cap" in reading.reason_codes
    assert PENALTY_FEATURE not in reading.features
    gapped = (
        _bar("SOLUSDT", AS_OF - timedelta(days=2), "100"),
        _bar("SOLUSDT", AS_OF, "100"),
    )
    assert (
        "missing_ema"
        in measure(
            symbol="SOLUSDT",
            as_of=AS_OF,
            bars=gapped,
            btc_bars=(),
            universe_bars={},
            atr_period=14,
            tokenomics=None,
        ).reason_codes
    )
    priced = _wave("SOLUSDT")
    broken = (
        *priced[:-1],
        DailyBar(
            symbol="SOLUSDT",
            open_date=AS_OF,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            quote_volume=Decimal("0"),
            trade_count=-1,
            taker_buy_base_volume=Decimal("1"),
            taker_buy_quote_volume=Decimal("1"),
        ),
    )
    activity = measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=broken,
        btc_bars=_wave("BTCUSDT"),
        universe_bars={"SOLUSDT": broken},
        atr_period=14,
        tokenomics=None,
    )
    assert "invalid_taker_buy" in activity.reason_codes
    assert "invalid_trade_count" in activity.reason_codes
    rich = _bar("SOLUSDT", AS_OF, "100", quote="10", taker="20")
    assert (
        "invalid_taker_buy"
        in measure(
            symbol="SOLUSDT",
            as_of=AS_OF,
            bars=(rich,),
            btc_bars=(),
            universe_bars={},
            atr_period=14,
            tokenomics=None,
        ).reason_codes
    )
    with pytest.raises(ValidationError):
        Tokenomics(
            circulating_ratio=object(),
            fdv_to_market_cap=None,
            unlock_pct_14d=None,
            unlock_pct_90d=None,
        )
    with pytest.raises(ValidationError):
        Tokenomics(
            circulating_ratio=Decimal("NaN"),
            fdv_to_market_cap=None,
            unlock_pct_14d=None,
            unlock_pct_90d=None,
        )
    with pytest.raises(ValidationError):
        Tokenomics(
            circulating_ratio="nope",
            fdv_to_market_cap=None,
            unlock_pct_14d=None,
            unlock_pct_90d=None,
        )
    wave = _wave("SOLUSDT")
    measure(
        symbol="SOLUSDT",
        as_of=AS_OF,
        bars=wave,
        btc_bars=_wave("BTCUSDT"),
        universe_bars={
            "SOLUSDT": wave[:-1],
            "MISSUSDT": _series("MISSUSDT", ["100"] * 3)[:-1],
            "SHORTUSDT": _series("SHORTUSDT", ["100"] * 3),
            "BADUSDT": (*bad[:-1], _bar("BADUSDT", AS_OF, "0")),
        },
        atr_period=14,
        tokenomics=None,
    )


def test_one_component_is_enough_when_its_weight_is_explicit() -> None:
    only = combine({"circulating_ratio": Decimal("0.5")}, {"circulating_ratio": Decimal("4")})
    assert only.components == {"tokenomics": Decimal("2")}
    penalty = combine({PENALTY_FEATURE: Decimal("1")}, {PENALTY_FEATURE: Decimal("2")})
    assert penalty.components == {"extension_penalty": Decimal("-2")}
    with pytest.raises(EvaluationError, match="missing"):
        combine({}, {PENALTY_FEATURE: Decimal("1")})
    with pytest.raises(EvaluationError, match="non-zero"):
        combine({"circulating_ratio": Decimal("1")}, {"circulating_ratio": True})
    with pytest.raises(ValidationError):
        ScoreResult(score=Decimal("1"), components={"trend_rs": Decimal("2")})


def test_a_feature_set_rejects_an_invented_code() -> None:
    with pytest.raises(ValidationError):
        FeatureSet(features={}, reason_codes=("Not A Code",))


def _rows(count: int, *, reverse: bool) -> list[StudyRow]:
    returns = list(range(count))
    if reverse:
        returns.reverse()
    return [
        StudyRow(
            name="rs_30d_vol_skip_1",
            regime="RISK_ON",
            value=Decimal(index + 1),
            forward_excess_return=Decimal(forward),
        )
        for index, forward in enumerate(returns)
    ]
