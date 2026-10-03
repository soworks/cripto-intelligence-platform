from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cip.adapters.market import Kline
from cip.domain.errors import ExchangeBannedError, ExchangeGeoBlockedError
from cip.handlers import market_probe
from cip.handlers.market_probe import run_market_probe

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"


class _Market:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    def klines(self, symbol: str, *, interval: str, limit: int) -> tuple[Kline, ...]:
        assert (symbol, interval, limit) == ("BTCUSDT", "1d", 1)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return tuple(self.outcome)  # type: ignore[arg-type]

    def exchange_info(self) -> object:
        raise AssertionError("the probe must not download exchange info")

    def ticker_24hr(self) -> tuple[object, ...]:
        raise AssertionError("the probe must not download tickers")

    def depth(self, symbol: str, *, limit: int) -> object:
        raise AssertionError("the probe must not download a book")


def _candle() -> Kline:
    price = Decimal("1")
    return Kline(1, price, price, price, price, price, 2, price, 1)


def test_probe_reports_a_candle_when_the_host_answers() -> None:
    assert run_market_probe(_Market((_candle(),))) == {"candle_count": 1, "geo_blocked": 0}


def test_probe_reports_geo_block_without_raising() -> None:
    assert run_market_probe(_Market(ExchangeGeoBlockedError("451"))) == {
        "candle_count": 0,
        "geo_blocked": 1,
    }


def test_probe_lets_a_ban_abort_the_call() -> None:
    with pytest.raises(ExchangeBannedError):
        run_market_probe(_Market(ExchangeBannedError("418")))


@dataclass(frozen=True)
class _Context:
    function_name: str = "cip-test-market-probe"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:cip-test"
    aws_request_id: str = "req-1"


class _Client:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.closed = False

    def __enter__(self) -> _Market:
        return _Market(self.outcome)

    def __exit__(self, *_args: object) -> None:
        self.closed = True


def _spy_metric(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    original = market_probe.metrics.add_metric

    def _record(**kwargs: Any) -> None:
        recorded.append(kwargs)
        original(**kwargs)

    monkeypatch.setattr(market_probe.metrics, "add_metric", _record)
    return recorded


def test_lambda_records_zero_when_the_host_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client((_candle(),))
    monkeypatch.setattr(market_probe, "_client", lambda: client)
    recorded = _spy_metric(monkeypatch)

    assert market_probe.probe({}, _Context()) == {"candle_count": 1, "geo_blocked": 0}
    assert recorded == [{"name": "GeoBlocked", "unit": market_probe.MetricUnit.Count, "value": 0}]
    assert client.closed is True


def test_lambda_records_one_when_the_host_returns_451(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_probe, "_client", lambda: _Client(ExchangeGeoBlockedError("451")))
    recorded = _spy_metric(monkeypatch)

    assert market_probe.probe({}, _Context())["geo_blocked"] == 1
    assert recorded[0]["value"] == 1


def test_lambda_does_not_record_a_metric_when_banned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_probe, "_client", lambda: _Client(ExchangeBannedError("418")))
    recorded = _spy_metric(monkeypatch)

    with pytest.raises(ExchangeBannedError):
        market_probe.probe({}, _Context())
    assert recorded == []


def test_client_factory_uses_the_policy_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _Capture:
        def __init__(self, base_url: str) -> None:
            captured["base_url"] = base_url

    monkeypatch.setenv("POLICY_PATH", str(REPO_POLICY))
    monkeypatch.setattr(market_probe, "BinanceMarketClient", _Capture)

    market_probe._client()

    assert captured["base_url"] == "https://data-api.binance.vision"
