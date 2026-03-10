"""ida:// URL handler base class, concrete implementations, and registry.

Each handler implements ``matches`` (predicate) and ``handle`` (action).
The dispatcher in ``open.py`` iterates over ``HANDLERS`` and calls the
first one whose ``matches`` returns *True*.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import ParseResult

from hcli.lib.ida.handler.default import DefaultURLHandler
from hcli.lib.ida.handler.ke import KEURLHandler


class URLHandler(ABC):
    """Base class for ida:// URL handlers."""

    @abstractmethod
    def matches(self, parsed: ParseResult) -> bool:
        """Return *True* if this handler should process the URL."""

    @abstractmethod
    def handle(self, uri: str, parsed: ParseResult, no_launch: bool, timeout: float, skip_analysis: bool) -> None:
        """Process the ida:// URL."""


# Registry — order matters: first match wins
HANDLERS: list[URLHandler] = [
    KEURLHandler(),
    DefaultURLHandler(),
]

__all__ = ["HANDLERS", "DefaultURLHandler", "KEURLHandler", "URLHandler"]
