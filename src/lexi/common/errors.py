"""Exception types shared across components. Every one maps to a fail-closed path (P1)."""


class LexiError(Exception):
    """Base class."""


class CatalogError(LexiError):
    """config/*.yaml failed validation at startup."""


class AuditError(LexiError):
    """Audit append or verification failed (F5)."""


class LedgerError(LexiError):
    """Ledger write or invariant failed (F6)."""


class CustodyError(LexiError):
    """Someone other than C15 asked for the Test split (P5)."""


class RegistryError(LexiError):
    """Scorer/registry pair mismatch or corrupt registry (F9, F14)."""


class ProviderError(LexiError):
    """Hosted provider failed or timed out (F1, F3, F4)."""
