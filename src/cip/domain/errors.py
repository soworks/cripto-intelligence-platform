class CipError(Exception):
    """Base class for platform errors."""


class PolicyError(CipError):
    """Investment policy is missing or invalid; callers must fail closed."""


class DuplicateEventError(CipError):
    """A ledger event with the same identity already exists."""


class InvalidEventError(CipError):
    """A ledger event payload cannot be stored losslessly."""
