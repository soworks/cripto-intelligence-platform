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


class HistoryError(CipError):
    """A market-history dump or continuity file is unusable; callers must fail closed."""


class BacktestError(CipError):
    """A backtest input or result is unusable; callers must fail closed."""


class RecorderError(CipError):
    """A provider response cannot become an observation; callers must not invent one."""


class EvaluationError(CipError):
    """A decision or outcome cannot be stored; callers must not rewrite the original."""


class PortfolioError(CipError):
    """A portfolio book cannot be stored; callers must not invent holdings."""


class PositionError(CipError):
    """A position cannot change state; callers must not invent an order."""


class ExitError(CipError):
    """An exit cannot be named; callers must not invent a fill."""


class SizeError(CipError):
    """A position cannot be sized; callers must not invent an order."""


class LimitError(CipError):
    """A limit cannot be judged; callers must not invent an order."""


class FilterError(CipError):
    """A symbol filter cannot be judged; callers must not invent an order."""


class FillError(CipError):
    """A shadow fill cannot be priced; callers must not invent an order."""
