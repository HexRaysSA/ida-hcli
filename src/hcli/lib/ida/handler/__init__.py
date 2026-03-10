"""ida:// URL handlers."""

from hcli.lib.ida.handler.default_url_handler import DefaultURLHandler
from hcli.lib.ida.handler.handler_registry import HANDLERS
from hcli.lib.ida.handler.ke_url_handler import KEURLHandler
from hcli.lib.ida.handler.url_handler import URLHandler

__all__ = ["HANDLERS", "DefaultURLHandler", "KEURLHandler", "URLHandler"]
