# Verbatim copy of kb-store at e859313, below this header:
#   src/kb_store/errors.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Exceptions raised by kb-store."""


class KbStoreError(Exception):
    """Base for every error this package raises."""


class DeploymentError(KbStoreError):
    """A deployment is missing, malformed, or asks for what cannot be done.

    ``reason`` names a location refusal by the shared storage cases' code, such as
    ``variable-missing``, ``vars-invalid`` or ``parent-segment``.
    """

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class InputError(KbStoreError):
    """An upstream package's output does not have the shape its format promises."""


class StoreError(KbStoreError):
    """A store is missing, or a run would leave it inconsistent."""


class FormatError(KbStoreError):
    """A store does not carry a format stamp this package can read."""


class EmbedderError(KbStoreError):
    """An embedder cannot be loaded or cannot do what a space asks of it."""


class LockedError(StoreError):
    """Another writer holds the store's lock."""
