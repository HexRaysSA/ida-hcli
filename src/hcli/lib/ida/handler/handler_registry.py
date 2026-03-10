"""Ordered handler registry for ida:// URLs.

Handlers are tried in order — the first whose ``matches()`` returns *True*
handles the URL.  More specific handlers (e.g. KE) come before the
catch-all ``DefaultURLHandler``.
"""

from __future__ import annotations

from hcli.lib.ida.handler.default_url_handler import DefaultURLHandler
from hcli.lib.ida.handler.ke_url_handler import KEURLHandler
from hcli.lib.ida.handler.url_handler import URLHandler

HANDLERS: list[URLHandler] = [
    KEURLHandler(),
    DefaultURLHandler(),
]
