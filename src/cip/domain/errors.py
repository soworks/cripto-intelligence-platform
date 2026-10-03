class CipError(Exception):
    """Base class for platform errors."""


class PolicyError(CipError):
    """Investment policy is missing or invalid; callers must fail closed."""


class DuplicateEventError(CipError):
    """A ledger event with the same key already exists."""
