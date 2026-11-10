"""Exceptions raised by latent-wiki-mcp.

Trimmed from latent-wiki's `errors.py` (45b3974) to the four the server raises, under
the same names, so the copied reader raises what it always raised.
"""


class LatentWikiError(Exception):
    """Base for every error this package raises."""


class StoreError(LatentWikiError):
    """The published store is missing, unstamped, or unusable."""


class Refused(LatentWikiError):
    """A wiki tool will not answer this call: one line to the caller, no footer."""


class DeploymentError(LatentWikiError):
    """A deployment folder is missing, malformed, or asks for what cannot be done.
    ``reason`` names a refusal by the shared storage cases' code, as the store's."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
