"""Exceptions raised by latent-wiki-mcp.

Trimmed from latent-wiki's `errors.py` (28db78d) to the three the server raises, under
the same names, so the copied reader raises what it always raised.
"""


class LatentWikiError(Exception):
    """Base for every error this package raises."""


class StoreError(LatentWikiError):
    """The published store is missing, unstamped, or unusable."""


class DeploymentError(LatentWikiError):
    """A deployment folder is missing, malformed, or asks for what cannot be done."""
