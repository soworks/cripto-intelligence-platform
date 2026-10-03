class CipError(Exception):
    """Base class for platform errors."""


class PolicyError(CipError):
    """Investment policy is missing or invalid; callers must fail closed."""


class DuplicateEventError(CipError):
    """A ledger event with the same identity already exists."""


class InvalidEventError(CipError):
    """A ledger event payload cannot be stored losslessly."""


class MarketDataError(CipError):
    """Public market data could not be read; callers must fail closed."""


class ExchangeBannedError(MarketDataError):
    """Binance returned 418. The scan must stop and must not retry."""


class ExchangeGeoBlockedError(MarketDataError):
    """Binance returned 451. The configured host is blocking this region."""
