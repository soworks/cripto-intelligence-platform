import pytest

from cip.domain.errors import EvaluationError
from cip.evaluation.classification_producer import CatalogIncomplete, produce_classifications


def _get(pages: dict[str, object]):
    def get(url: str) -> object:
        if url not in pages:
            raise AssertionError(url)
        return pages[url]

    return get


_EUR = (
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
    "&category=eur-stablecoin&per_page=250&page=1"
)


def _pages() -> dict[str, object]:
    return {
        "https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset": {
            "data": [
                {
                    "assetCode": "BTC",
                    "delisted": False,
                    "preDelist": False,
                    "swapTag": "no",
                    "tags": ["fan_token", "Monitoring"],
                },
                {
                    "assetCode": "ETH",
                    "delisted": False,
                    "oldAssetCode": "ETH",
                    "newAssetCode": "ETH2",
                    "preDelist": True,
                    "swapTag": "sw",
                    "tags": [],
                },
            ],
            "success": True,
        },
        "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll": {
            "data": [
                {"coin": "BTC", "depositAllEnable": False, "withdrawAllEnable": True},
            ],
            "success": True,
        },
        "https://api.coingecko.com/api/v3/exchanges/binance/tickers?page=1": {
            "tickers": [
                {
                    "base": "BTC",
                    "coin_id": "bitcoin",
                    "market": {"identifier": "binance"},
                    "target": "USDT",
                }
            ]
        },
        _EUR: [{"id": "eurite"}],
        "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&per_page=250&ids=bitcoin": [
            {"id": "bitcoin", "last_updated": "2026-10-07T18:00:00Z", "market_cap": "10"}
        ],
        "https://www.binance.com/bapi/composite/v1/public/marketing/symbol/list": {
            "data": [{"cmcUniqueId": 1, "symbol": "BTCUSDT"}]
        },
    }


def test_catalog_fields_stay_missing_when_the_asset_is_absent() -> None:
    classified = {
        item.symbol: item
        for item in produce_classifications(
            (("BTCUSDT", "BTC"), ("SOLUSDT", "SOL")),
            _get(_pages()),
        )
    }
    btc = classified["BTCUSDT"]
    assert btc.fan_token is True
    assert btc.monitoring_tag is True
    assert btc.delisting is False
    assert btc.deposits_suspended is True
    assert btc.withdrawals_suspended is False
    assert btc.pending_migration is False
    assert btc.eur_stable is False
    assert btc.mapping == "unambiguous"
    assert btc.market_cap_usd is not None
    assert btc.cmc_market_cap_usd is None
    sol = classified["SOLUSDT"]
    assert sol.fan_token is None
    assert sol.deposits_suspended is None
    assert sol.mapping == "unmapped"
    assert sol.eur_stable is None
    eth = produce_classifications((("ETHUSDT", "ETH"),), _get(_pages()))[0]
    assert eth.delisting is True
    assert eth.pending_migration is True


def test_an_incomplete_catalog_stores_nothing() -> None:
    pages = _pages()
    full = {
        "tickers": [
            {
                "base": "BTC",
                "coin_id": "bitcoin",
                "market": {"identifier": "binance"},
                "target": "USDT",
            }
        ]
        * 100
    }

    def get(url: str) -> object:
        if "tickers?page=" in url:
            return full
        return _get(pages)(url)

    with pytest.raises(CatalogIncomplete):
        produce_classifications((("BTCUSDT", "BTC"),), get)


def test_a_catalog_page_that_is_not_a_list_is_incomplete() -> None:
    pages = _pages()
    pages["https://api.coingecko.com/api/v3/exchanges/binance/tickers?page=1"] = {"tickers": {}}
    with pytest.raises(CatalogIncomplete):
        produce_classifications((("BTCUSDT", "BTC"),), _get(pages))


def test_a_malformed_catalog_is_unusable() -> None:
    pages = _pages()
    pages["https://www.binance.com/bapi/composite/v1/public/marketing/symbol/list"] = {"data": {}}
    with pytest.raises(EvaluationError, match="capture is unusable"):
        produce_classifications((("BTCUSDT", "BTC"),), _get(pages))
