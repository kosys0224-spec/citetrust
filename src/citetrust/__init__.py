"""citetrust - verify the references in a document actually exist, say what you think they say, and aren't retracted."""

__version__ = "0.1.0"

from .model import Reference, Result, Status  # noqa: E402,F401
from .extract import extract_references  # noqa: E402,F401
from .verify import verify_references  # noqa: E402,F401

__all__ = ["__version__", "Reference", "Result", "Status", "extract_references", "verify_references"]
