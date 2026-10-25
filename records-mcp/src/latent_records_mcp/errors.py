# Verbatim copy of latent-records at 5a75235, below this header:
#   src/latent_records/errors.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Exceptions raised by latent-records."""


class LatentRecordsError(Exception):
    """Base for every error this package raises."""


class DeploymentError(LatentRecordsError):
    """A deployment is missing, malformed, or asks for what cannot be done."""


class InputError(LatentRecordsError):
    """An upstream package's output does not have the shape its format promises."""


class StoreError(LatentRecordsError):
    """A store is missing, or a run would leave it inconsistent."""


class FormatError(LatentRecordsError):
    """A store does not carry a format stamp this package can read."""


class EmbedderError(LatentRecordsError):
    """An embedder cannot be loaded or cannot do what a space asks of it."""
