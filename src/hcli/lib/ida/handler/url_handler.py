"""Abstract base class for ida:// URL handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import ParseResult


class URLHandler(ABC):
    """A handler that can match and process a specific flavour of ida:// URL.

    Subclasses implement ``matches`` (predicate) and ``handle`` (action).
    Registered handlers are tried in order — first match wins
    (see ``handler_registry.py``).
    """

    @abstractmethod
    def matches(self, parsed: ParseResult) -> bool:
        """Return *True* if this handler should process *parsed*."""

    @abstractmethod
    def handle(self, uri: str, parsed: ParseResult, no_launch: bool, timeout: float, skip_analysis: bool) -> None:
        """Process the ida:// URL."""
