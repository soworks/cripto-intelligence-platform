from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.policy import load_policy
from cip.evaluation.fundamentals import (
    CmcReading,
    CoinGeckoReading,
    FundamentalsDecision,
    UnlockReading,
    assess_fundamentals,
    parse_cmc_quote,
    parse_coingecko_market,
    parse_defillama_fees,
)

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
GATES = load_policy(_POLICY).policy.hypotheses.fundamentals
NOW = datetime(2026, 10, 4, tzinfo=UTC)
IDS = {"SOL": "solana"}


def _cg(**overrides: object) -> CoinGeckoReading:
    values: dict[str, object] = {
        "coingecko_id": "solana",
        "market_cap_usd": Decimal("100"),
        "circulating_supply": Decimal("50"),
        "total_supply": Decimal("100"),
        "fully_diluted_valuation_usd": Decimal("200"),
        "source_timestamp": NOW,
    }
    values.update(overrides)
    return CoinGeckoReading(**values)  # type: ignore[arg-type]


def _cmc(**overrides: object) -> CmcReading:
    values: dict[str, object] = {"market_cap_usd": Decimal("100"), "source_timestamp": NOW}
    values.update(overrides)
    return CmcReading(**values)  # type: ignore[arg-type]


def _unlocks(**overrides: object) -> UnlockReading:
    values: dict[str, object] = {
        "schedule_known": True,
        "pct_circ_14d": Decimal("0"),
        "pct_circ_90d": Decimal("0"),
    }
    values.update(overrides)
    return UnlockReading(**values)  # type: ignore[arg-type]


def _assess(**overrides: object) -> FundamentalsDecision:
    values: dict[str, object] = {
        "base_asset": "SOL",
        "verified_ids": IDS,
        "coingecko": _cg(),
        "cmc": _cmc(),
        "unlocks": _unlocks(),
        "as_of": NOW,
        "hypotheses": GATES,
    }
    values.update(overrides)
    return assess_fundamentals(**values)  # type: ignore[arg-type]


def test_agreeing_readings_are_accepted_and_are_not_a_buy() -> None:
    payload = {
        "id": "solana",
        "market_data": {
            "market_cap": {"usd": "100"},
            "fully_diluted_valuation": {"usd": 200},
            "circulating_supply": Decimal("50"),
            "total_supply": "100",
            "last_updated": "2026-10-04T00:00:00Z",
        },
    }
    reading = parse_coingecko_market(payload)
    quote = {"market_cap": "100", "last_updated": NOW.isoformat()}
    cmc = parse_cmc_quote({"data": {"SOL": {"quote": {"USD": quote}}}}, "SOL")
    fees = parse_defillama_fees({})
    assert fees.fees_24h_usd is None
    assert fees.revenue_24h_usd is None
    result = _assess(coingecko=reading, cmc=cmc)
    assert result.accepted is True
    assert result.reason_codes == ("fundamentals_ok",)
    assert "disposition" not in FundamentalsDecision.model_fields


def test_a_missing_key_stays_missing() -> None:
    bare = {"id": "solana", "market_data": {"last_updated": NOW.isoformat()}}
    reading = parse_coingecko_market(bare)
    assert reading.market_cap_usd is None
    assert reading.circulating_supply is None
    result = _assess(coingecko=reading)
    assert result.accepted is False
    assert "missing_market_cap" in result.reason_codes
    assert "missing_circulating_supply" in result.reason_codes
    assert Decimal("0") not in result.reason_codes


def test_the_cross_check_and_supply_gates_fail_closed() -> None:
    disagreed = _assess(coingecko=_cg(market_cap_usd=Decimal("200")))
    assert disagreed.reason_codes == ("mcap_disagreement",)
    almost = Decimal("100") * (1 + Decimal(str(GATES.mcap_disagreement)))
    assert _assess(coingecko=_cg(market_cap_usd=almost)).accepted is True
    zero_cap = _assess(coingecko=_cg(market_cap_usd=Decimal("0")))
    assert zero_cap.reason_codes == ("invalid_market_cap",)
    missing_cmc = _assess(cmc=_cmc(market_cap_usd=None))
    assert missing_cmc.reason_codes == ("missing_cmc_market_cap",)
    zero_cmc = _assess(cmc=_cmc(market_cap_usd=Decimal("0")))
    assert zero_cmc.reason_codes == ("invalid_cmc_market_cap",)
    assert _assess(coingecko=_cg(circulating_supply=Decimal("101"))).reason_codes == (
        "circulating_exceeds_total",
    )
    ceiling = Decimal("100") * Decimal(str(GATES.maximum_fdv_to_market_cap))
    assert _assess(coingecko=_cg(fully_diluted_valuation_usd=ceiling)).accepted is True
    assert _assess(coingecko=_cg(fully_diluted_valuation_usd=ceiling + 1)).reason_codes == (
        "fdv_to_market_cap_above_maximum",
    )
    assert _assess(coingecko=_cg(fully_diluted_valuation_usd=None)).reason_codes == (
        "missing_fully_diluted_valuation",
    )
    assert _assess(coingecko=_cg(total_supply=None)).accepted is True
    zero_supply = _assess(coingecko=_cg(total_supply=Decimal("0")))
    assert zero_supply.reason_codes == ("invalid_total_supply",)
    assert _assess(coingecko=_cg(circulating_supply=Decimal("0"))).reason_codes == (
        "invalid_circulating_supply",
    )
    assert _assess(coingecko=_cg(fully_diluted_valuation_usd=Decimal("0"))).reason_codes == (
        "invalid_fully_diluted_valuation",
    )


def test_ids_and_clocks_fail_closed() -> None:
    assert _assess(verified_ids={}).reason_codes == ("coingecko_id_unverified",)
    assert _assess(coingecko=_cg(coingecko_id=None)).reason_codes == ("coingecko_id_unverified",)
    assert _assess(coingecko=_cg(coingecko_id="bitcoin")).reason_codes == ("coingecko_id_mismatch",)
    limit = timedelta(hours=GATES.data_max_age_hours)
    assert _assess(as_of=NOW + limit).accepted is True
    stale = _assess(as_of=NOW + limit + timedelta(seconds=1))
    assert stale.reason_codes == ("stale_coingecko", "stale_cmc")
    assert _assess(coingecko=_cg(source_timestamp=None)).reason_codes == (
        "missing_coingecko_timestamp",
    )
    future = _assess(coingecko=_cg(source_timestamp=NOW + timedelta(seconds=1)))
    assert future.reason_codes == ("coingecko_timestamp_in_the_future",)
    assert _assess(cmc=_cmc(source_timestamp=NOW + timedelta(seconds=1))).reason_codes == (
        "cmc_timestamp_in_the_future",
    )


def test_unknown_unlocks_are_not_treated_as_zero() -> None:
    floor = Decimal(str(GATES.minimum_circulating_ratio_without_unlocks))
    known_missing = _assess(unlocks=_unlocks(pct_circ_14d=None))
    assert known_missing.reason_codes == ("missing_unlock_14d",)
    at_limit = _assess(unlocks=_unlocks(pct_circ_14d=Decimal(str(GATES.unlock_pct_circ_14d))))
    assert at_limit.reason_codes == ("unlock_14d",)
    at_90 = _assess(unlocks=_unlocks(pct_circ_90d=Decimal(str(GATES.unlock_pct_circ_90d))))
    assert at_90.reason_codes == ("unlock_90d",)
    unknown = _assess(
        unlocks=_unlocks(schedule_known=False, pct_circ_14d=None, pct_circ_90d=None),
        coingecko=_cg(circulating_supply=floor * Decimal("100"), total_supply=Decimal("100")),
    )
    assert unknown.accepted is True
    thin = _assess(
        unlocks=_unlocks(schedule_known=False, pct_circ_14d=None, pct_circ_90d=None),
        coingecko=_cg(circulating_supply=Decimal("30"), total_supply=Decimal("100")),
    )
    assert thin.reason_codes == ("circulating_ratio_without_unlocks",)
    assert _assess(unlocks=_unlocks(schedule_known=None)).reason_codes == (
        "missing_unlock_schedule",
    )
    zero_total = _assess(
        unlocks=_unlocks(schedule_known=False),
        coingecko=_cg(total_supply=Decimal("0")),
    )
    assert "invalid_total_supply" in zero_total.reason_codes
    assert "missing_circulating_ratio" not in zero_total.reason_codes
    zero_circulating = _assess(
        unlocks=_unlocks(schedule_known=False),
        coingecko=_cg(circulating_supply=Decimal("0")),
    )
    assert "invalid_circulating_supply" in zero_circulating.reason_codes
    assert "circulating_ratio_without_unlocks" not in zero_circulating.reason_codes
    no_supply = _assess(
        unlocks=_unlocks(schedule_known=False),
        coingecko=_cg(circulating_supply=None),
    )
    assert "missing_circulating_ratio" in no_supply.reason_codes
    assert "missing_unlock_14d" not in no_supply.reason_codes


def test_parsers_reject_malformed_payloads_and_keep_absent_quotes() -> None:
    assert parse_coingecko_market({"id": "solana"}).market_cap_usd is None
    undated = {"market_data": {"market_cap": {"usd": "1"}}}
    assert parse_coingecko_market(undated).source_timestamp is None
    dated = {"market_data": {"last_updated": NOW}}
    assert parse_coingecko_market(dated).source_timestamp == NOW
    undated = {"market_data": {"market_cap": {"usd": "1"}}}
    assert parse_coingecko_market(undated).source_timestamp is None
    dated = {"market_data": {"last_updated": NOW}}
    assert parse_coingecko_market(dated).source_timestamp == NOW
    assert parse_cmc_quote({"data": {}}, "SOL").market_cap_usd is None
    assert parse_cmc_quote({}, "SOL").market_cap_usd is None
    assert parse_cmc_quote({"data": {"SOL": {"quote": {}}}}, "SOL").market_cap_usd is None
    fees = parse_defillama_fees({"total24h": "12", "totalRevenue24h": 3})
    assert fees.fees_24h_usd == Decimal("12")
    assert fees.revenue_24h_usd == Decimal("3")
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market([])
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"id": 1})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": []})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"market_cap": {"usd": 0.1 + 0.2}}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"market_cap": {"usd": True}}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"market_cap": {"usd": "nope"}}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"market_cap": {"usd": "NaN"}}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"market_cap": {"usd": Decimal("-1")}}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"market_cap": {"usd": object()}}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"last_updated": "yesterday"}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_coingecko_market({"market_data": {"last_updated": 1}})
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_cmc_quote([], "SOL")
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_cmc_quote({"data": {"SOL": []}}, "SOL")
    with pytest.raises(ValueError, match=r"is|UTC"):
        parse_defillama_fees([])
    naive = datetime(2026, 10, 4)  # noqa: DTZ001
    with pytest.raises(ValueError, match=r"is|UTC"):
        _assess(as_of=naive)
    ahead = timezone(timedelta(hours=1))
    with pytest.raises(ValueError, match=r"is|UTC"):
        _assess(as_of=datetime(2026, 10, 4, tzinfo=ahead))
    with pytest.raises(ValidationError):
        _cg(market_cap_usd=Decimal("-1"))
    with pytest.raises(ValidationError):
        _cg(fully_diluted_valuation_usd=0.1 + 0.2)
    with pytest.raises(ValidationError):
        _cg(source_timestamp=datetime(2026, 10, 4))  # noqa: DTZ001
    with pytest.raises(ValidationError):
        _cg(source_timestamp="2026-10-04T00:00:00Z")
    with pytest.raises(ValidationError):
        _unlocks(pct_circ_14d=Decimal("-0.01"))
    with pytest.raises(ValidationError):
        _unlocks(schedule_known=0)
    with pytest.raises(ValidationError):
        FundamentalsDecision(accepted=True, reason_codes=("missing_market_cap",))
    with pytest.raises(ValidationError):
        FundamentalsDecision(accepted=False, reason_codes=("fundamentals_ok",))
    with pytest.raises(ValidationError):
        FundamentalsDecision(accepted=False, reason_codes=("Nope",))
    with pytest.raises(ValidationError):
        FundamentalsDecision(accepted=False, reason_codes=())
